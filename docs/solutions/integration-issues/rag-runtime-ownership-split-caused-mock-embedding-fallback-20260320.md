---
module: Invoice Processing API
date: 2026-03-20
problem_type: integration_issue
component: backend_rag
symptoms:
  - "Phase 3 RAG sync/query/eval surfaces operated on a separate CLI-owned repository instead of the app-owned import repository"
  - "Backend validation did not exercise the real import-to-sync-to-query runtime path"
  - "Embedding generation silently produced deterministic mock vectors when mock mode was disabled and no real client/API key was available"
  - "RAG commands and endpoints could appear healthy while operating on disconnected or fake data"
root_cause: integration_gap
resolution_type: code_fix
severity: high
tags: [rag, fastapi, dependency-injection, app-factory, resource-lifecycle, embeddings, fail-fast, configuration, reliability, validation]
related:
  - docs/contracts/2026-03-20-rag-catalog-sync-contract.md
  - docs/plans/2026-03-20-002-feat-rag-whatsapp-catalog-sync-phase-3-backend-rag-plan.md
  - docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md
  - docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md
  - docs/solutions/security-issues/fail-fast-production-config-guards-system-20260227.md
  - todos/057-complete-p1-rag-cli-bypasses-app-repository.md
  - todos/058-complete-p1-rag-embeddings-silently-fallback-to-mock.md
---

# Troubleshooting: RAG Runtime Ownership Split Caused Silent Mock Embedding Fallback

## Problem

Phase 3 moved catalog RAG into `invproc`, but the first implementation still had two different runtime ownership models.

The import flow wrote sync rows into the app-owned repository built by FastAPI `AppResources`, while the new CLI validation commands built their own private repository stack. At the same time, `OpenAIEmbeddingClient` treated missing real embedding configuration as an implicit mock path, so non-mock runs could still produce vectors instead of failing fast.

That combination made backend validation misleading: sync/query/eval could return plausible results without proving the actual import-backed runtime path or real embedding configuration were correct.

## Environment

- Module: Invoice Processing API
- Affected Component: backend RAG worker/query/eval composition, CLI integration, embedding client initialization
- Key files:
  - `src/invproc/api.py`
  - `src/invproc/cli.py`
  - `src/invproc/rag.py`
  - `tests/test_rag_backend.py`
- Date: 2026-03-20

## Symptoms

- `rag sync-pending`, `rag query`, `rag eval`, and `rag status` could operate on a module-global CLI repository instead of the repository populated by the app import flow.
- Import-side sync rows existed, but the validation surface did not necessarily see or process them.
- Service-level tests looked correct because they manually constructed workers and retrieval services with the intended repository, masking the runtime composition gap.
- Missing `OPENAI_API_KEY` outside explicit mock mode did not stop RAG execution at the embedding boundary.
- Retrieval and evaluation could therefore appear healthy while using fake embeddings or disconnected state.

## What Didn't Work

**Attempted solution 1:** Prove the worker and retrieval logic in isolated repository tests.
- **Why it failed:** The problem was not only feature logic. It was runtime ownership. Isolated tests could pass while the actual CLI/API entrypoints used different repository instances.

**Attempted solution 2:** Add backend RAG CLI commands with a private cached repository.
- **Why it failed:** This created a second ownership model for the same feature. The CLI and the app could each be internally consistent while disagreeing about the actual catalog state.

**Attempted solution 3:** Let the embedding client fall back to deterministic mock embeddings whenever no OpenAI client existed.
- **Why it failed:** This is fail-open behavior in a correctness-sensitive path. Non-mock runs could silently degrade into fake vectors instead of surfacing a configuration error.

## Solution

Make backend RAG fully app-owned and make non-mock embeddings fail closed.

### 1) Route RAG through app-owned resources

The FastAPI app already had a clear ownership model: long-lived resources are created in `build_app_resources()` and injected through dependencies. The fix was to make RAG use that same graph.

In `src/invproc/api.py`, add app-owned RAG builders:

```python
def get_rag_worker(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
) -> CatalogSyncWorker:
    return build_rag_worker(
        repository=repository,
        config=config,
        worker_id=f"api-{INSTANCE_ID}",
    )


def get_rag_retrieval_service(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
) -> CatalogRetrievalService:
    return build_retrieval_service(repository=repository, config=config)
```

This ensures sync/query/status flows use the same `import_repository` instance that the import service already owns.

### 2) Expose internal backend RAG surfaces on the real app graph

Add internal endpoints that operate through those app-owned dependencies instead of ad hoc helpers:

```python
@router.post("/internal/rag/sync-pending")
async def sync_pending_catalog_embeddings(...):
    results = await run_in_threadpool(worker.sync_pending, limit=limit)
    return {"processed": len(results), "results": [result.__dict__ for result in results]}


@router.post("/internal/rag/query")
async def query_catalog_embeddings(...):
    result = await run_in_threadpool(
        retrieval_service.query,
        payload.query,
        top_k=payload.top_k,
    )
    return serialize_query_result(result)


@router.get("/internal/rag/status")
async def rag_status(...):
    snapshot = await run_in_threadpool(build_sync_status_snapshot, repository)
    return serialize_sync_status_snapshot(snapshot)
```

These endpoints validate the same resource graph the app uses at runtime instead of a parallel harness.

### 3) Make the CLI build from `AppResources`, not a private repository

The CLI still needed a local validation path, but it had to follow the same composition pattern as the API.

In `src/invproc/cli.py`, replace the standalone repository with cached `AppResources`:

```python
_CLI_RAG_RESOURCES: AppResources | None = None
_CLI_RAG_RESOURCES_KEY: tuple[bool, str] | None = None


def _get_cli_rag_resources(*, mock: bool) -> AppResources:
    config = get_config_unvalidated()
    config.mock = mock
    config.validate_config()
    ...
    _CLI_RAG_RESOURCES = build_app_resources(config)
    return _CLI_RAG_RESOURCES
```

Then derive services from `resources.import_repository` and `resources.config`:

```python
def _build_rag_services(
    *,
    mock: bool,
) -> tuple[AppResources, CatalogSyncWorker, CatalogRetrievalService]:
    resources = _get_cli_rag_resources(mock=mock)
    worker = build_rag_worker(
        repository=resources.import_repository,
        config=resources.config,
        worker_id="cli",
    )
    retrieval_service = build_retrieval_service(
        repository=resources.import_repository,
        config=resources.config,
    )
    return resources, worker, retrieval_service
```

This brought the CLI back under the same ownership model as the app.

### 4) Fail closed when real embeddings are not configured

In `src/invproc/rag.py`, `OpenAIEmbeddingClient` now raises if mock mode is disabled and no real client was initialized:

```python
def embed(self, *, model: str, text: str) -> list[float]:
    if self._client is None:
        if not self._config.mock:
            raise ValueError("OpenAI embedding client not initialized (missing API key)")
        return self._mock_embed(model=model, text=text)

    response = self._client.embeddings.create(model=model, input=text)
    return list(response.data[0].embedding)
```

That aligns the embedding path with the repo’s broader fail-fast config guidance: deterministic mock vectors are only acceptable in explicit mock mode.

## Why This Works

The bug lived at the boundary between feature logic and runtime composition, so the fix had to close both sides of that boundary.

1. **Runtime ownership is now explicit.** Imports, sync processing, queries, and status inspection use the same app-owned repository instance.
2. **CLI and API follow the same composition rule.** Both derive backend RAG services from `AppResources` instead of inventing their own repository state.
3. **Validation now checks the real path.** The internal API and CLI surfaces exercise the actual backend ownership model instead of a disconnected harness.
4. **Embedding trust boundaries are explicit.** Non-mock runs without valid OpenAI configuration fail immediately instead of producing plausible-but-fake vectors.

This mirrors two existing repo learnings:

- resource ownership should be explicit and app-scoped for long-lived dependencies
- runtime behavior should derive from validated config and should not silently substitute developer-friendly defaults

## Regression Coverage

Add tests that verify runtime composition, not only isolated logic:

```python
def test_embedding_client_requires_api_key_when_not_in_mock_mode() -> None:
    client = OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=False, openai_api_key=None))

    with pytest.raises(ValueError, match="OpenAI embedding client not initialized"):
        client.embed(model="text-embedding-3-small", text="hello")
```

```python
def test_api_rag_endpoints_use_app_owned_repository() -> None:
    config = InvoiceConfig(_env_file=None, mock=True, catalog_sync_enabled=True)
    resources = build_app_resources(config)
    app_instance = create_app(resources=resources)
    ...
    service.import_rows(payload, idempotency_key="idem-api-rag")
    ...
    assert sync_response.json()["processed"] == 1
    assert query_response.json()["matches"][0]["product_id"] == "prod_1"
    assert status_response.json()["counts"]["synced"] == 1
```

These tests matter because they prove:

- app-owned imports and app-owned RAG surfaces share the same repository
- fake embeddings are not silently accepted in non-mock mode

## Prevention

- Make app-owned resources the only runtime path for backend RAG. Workers, validators, and query surfaces should receive repositories and config through `AppResources` or explicit constructors, never by instantiating fresh repositories in entrypoints.
- Treat DI scope as part of the feature contract. If a feature depends on shared state, every entrypoint should compose it through the same builder path.
- Ban silent config fallbacks on correctness-sensitive paths. If storage ownership, embedding mode, or auth behavior changes, require explicit validated config and fail fast.
- Verify the thing that matters, not just the HTTP shape. Validation tests should assert that the expected app-owned repository was mutated and queried.
- Add negative-path tests whenever a new backend validation surface is introduced: wrong repository, missing lifespan resources, and missing real credentials should all fail loudly.

## Verification

Commands used to verify the final fix:

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

Focused checks that proved the regression coverage:

```bash
python -m pytest -q tests/test_rag_backend.py -k "app_owned_repository or requires_api_key_when_not_in_mock_mode"
```

## Related Issues

- App resource ownership pattern: `docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md`
- Earlier catalog-sync runtime wiring gap: `docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md`
- Fail-fast config guidance: `docs/solutions/security-issues/fail-fast-production-config-guards-system-20260227.md`
- Backend-owned RAG contract: `docs/contracts/2026-03-20-rag-catalog-sync-contract.md`
- Phase 3 execution plan: `docs/plans/2026-03-20-002-feat-rag-whatsapp-catalog-sync-phase-3-backend-rag-plan.md`
- Review findings resolved in this fix:
  - `todos/057-complete-p1-rag-cli-bypasses-app-repository.md`
  - `todos/058-complete-p1-rag-embeddings-silently-fallback-to-mock.md`
