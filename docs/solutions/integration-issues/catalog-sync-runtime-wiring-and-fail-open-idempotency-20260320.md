---
module: Invoice Processing API
date: 2026-03-20
problem_type: integration_issue
component: import_pipeline
symptoms:
  - "Phase 2 catalog sync code existed at the service and repository layers, but normal FastAPI runtime wiring still injected a noop producer"
  - "Catalog sync configuration implied the feature was active even though app resources never constructed the real producer"
  - "A strict fail-closed sync mode raised after product and stock-movement persistence, which would replay side effects on idempotent retry"
root_cause: integration_gap
resolution_type: code_fix
severity: high
tags: [catalog-sync, fastapi, dependency-injection, idempotency, import-service, rag]
related:
  - docs/contracts/2026-03-20-rag-catalog-sync-contract.md
  - docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md
  - docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md
  - todos/055-pending-p1-wire-real-catalog-sync-producer.md
  - todos/056-pending-p2-fail-closed-sync-breaks-import-idempotency.md
---

# Troubleshooting: Catalog Sync Phase 2 Was Implemented but Not Reachable at Runtime

## Problem

Phase 2 added a catalog sync producer, snapshot hashing, sync-row persistence, and tests around the import service, but the actual FastAPI app still constructed `NoopCatalogSyncProducer` unconditionally. That meant the backend looked complete in isolation while the real runtime path silently disabled sync emission.

At the same time, the first implementation also offered a strict fail-closed path for sync emission. That branch raised only after product writes and stock-movement persistence, which violated the import service's idempotency guarantees on retry.

## Environment

- Module: Invoice Processing API
- Affected Component: FastAPI app resources, import service orchestration, catalog sync producer wiring
- Date: 2026-03-20

## Symptoms

- `build_app_resources()` returned a noop sync producer even when `catalog_sync_enabled` was true.
- `get_import_service()` depended on a producer, but normal app runtime could only inject the noop instance.
- Tests passed because they manually constructed `InvoiceImportService` with `RepositoryCatalogSyncProducer`, masking the app-wiring gap.
- The fail-closed branch raised `CATALOG_SYNC_FAILED` after `create_product()` / `update_product()` and `add_stock_movement_in()`, but before `save_idempotent_result()`.
- A retried request with the same idempotency key could therefore replay import side effects if strict mode were enabled.

## What Didn't Work

**Attempted solution 1:** Add the producer protocol and repository sync persistence, then keep app resources on `NoopCatalogSyncProducer`.
- **Why it failed:** This only proved the producer worked in manually constructed service tests. It did not activate the feature in the real dependency graph.

**Attempted solution 2:** Keep an optional fail-closed config path for sync emission.
- **Why it failed:** The import flow is not transactional across product write, stock movement, sync emission, and idempotent response persistence. Raising after partial persistence made retries unsafe.

## Solution

Wire a real app-scoped import repository and conditionally build `RepositoryCatalogSyncProducer` in app resources when `catalog_sync_enabled` is true. At the same time, keep Phase 2 explicitly fail-open by removing the strict fail-closed branch until transactional or retry-safe semantics exist.

**Key runtime fix**:

```python
# src/invproc/api.py
def build_app_resources(config: InvoiceConfig) -> AppResources:
    extract_cache = InMemoryExtractCache(
        ttl_sec=config.extract_cache_ttl_sec,
        max_entries=config.extract_cache_max_entries,
    )
    supabase_client_provider = SupabaseClientProvider(config)
    import_repository = InMemoryInvoiceImportRepository()

    if config.catalog_sync_enabled:
        catalog_sync_producer = RepositoryCatalogSyncProducer(
            import_repository,
            embedding_model=config.catalog_sync_embedding_model,
        )
    else:
        catalog_sync_producer = NoopCatalogSyncProducer()

    return AppResources(
        config=config,
        extract_cache=extract_cache,
        supabase_client_provider=supabase_client_provider,
        import_repository=import_repository,
        catalog_sync_producer=catalog_sync_producer,
    )
```

```python
# src/invproc/dependencies.py
@dataclass
class AppResources:
    config: InvoiceConfig
    extract_cache: InMemoryExtractCache
    supabase_client_provider: SupabaseClientProvider
    import_repository: InvoiceImportRepository
    catalog_sync_producer: CatalogSyncProducer
```

```python
# src/invproc/api.py
def get_import_service(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
    catalog_sync_producer: CatalogSyncProducer = Depends(get_catalog_sync_producer),
) -> InvoiceImportService:
    return InvoiceImportService(
        config=config,
        repository=repository,
        catalog_sync_producer=catalog_sync_producer,
    )
```

**Idempotency-preserving fix**:

```python
# src/invproc/import_service.py
try:
    self.catalog_sync_producer.emit_product_sync(...)
except Exception:
    logger.exception(
        "Catalog sync emission failed for import_id=%s row_id=%s product_id=%s",
        import_id,
        row.row_id,
        product.product_id,
    )

# Import still completes and save_idempotent_result() still runs.
```

**Regression coverage added**:

```python
def test_build_app_resources_wires_repository_backed_sync_producer_when_enabled() -> None:
    config = InvoiceConfig(_env_file=None, mock=True, catalog_sync_enabled=True)
    resources = build_app_resources(config)

    assert isinstance(resources.catalog_sync_producer, RepositoryCatalogSyncProducer)
    assert resources.catalog_sync_producer.repository is resources.import_repository


def test_import_rows_sync_failure_still_allows_idempotent_replay_without_new_side_effects():
    first_response = service.import_rows(payload, idempotency_key="idem-fail-open-replay")
    replay_response = service.import_rows(payload, idempotency_key="idem-fail-open-replay")

    assert first_response.model_dump(mode="json") == replay_response.model_dump(mode="json")
    assert len(repository._movements) == 1
```

**Commands run**:

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

## Why This Works

The actual bug was not in snapshot hashing or sync-row persistence. It was at the boundary between implementation and runtime composition.

The fix works because it closes both sides of the integration gap:

1. **Runtime honesty:** app resources now construct the same producer shape that service-level tests exercised, so `catalog_sync_enabled` actually changes behavior.
2. **Resource ownership clarity:** the import repository and producer are both app-scoped resources, which matches the repo's existing FastAPI DI/lifespan pattern.
3. **Idempotency safety:** sync emission stays non-blocking in this phase, so the import flow still persists and stores its idempotent response even if downstream sync recording fails.
4. **Test coverage at the correct layer:** runtime wiring tests now verify app-resource construction, not just manual service assembly.

## Prevention

- When adding a new side-effectful dependency, test both the service object and the real app resource graph that constructs it.
- Treat config flags as a contract: if a new config knob is added, add a test that proves runtime behavior actually changes when the flag flips.
- Do not offer strict failure modes for a multi-step persistence flow unless the full transaction or retry model is already safe.
- For idempotent write paths, always trace the failure point relative to side effects and `save_idempotent_result()` before adding new exceptions.
- Add one runtime composition test whenever a feature is introduced through `AppResources`.

## Related Issues

- Phase 1 contract for ownership and sync semantics: `docs/contracts/2026-03-20-rag-catalog-sync-contract.md`
- FastAPI DI/lifespan pattern reference: `docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md`
- Review findings captured during implementation:
  - `todos/055-pending-p1-wire-real-catalog-sync-producer.md`
  - `todos/056-pending-p2-fail-closed-sync-breaks-import-idempotency.md`
