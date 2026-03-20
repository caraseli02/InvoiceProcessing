---
module: Invoice Processing API
date: 2026-03-20
problem_type: integration_issue
component: backend_rag_persistence
symptoms:
  - "Phase 4 introduced a Supabase-backed repository, but the first persistence slice still left new import and vector tables exposed in public without RLS enabled"
  - "Sync queue creation and claiming used select-then-act flows, so concurrent workers or producers could race on the same row"
  - "Persistent retrieval still pulled queue and embedding state back into Python instead of using database-native operations"
  - "CLI ingest covered extract -> import -> sync, but the HTTP surface still could not run the same import-backed RAG path"
  - "CLI defaults were misleading: idempotency keys ignored effective import payload changes, sync limits were bypassed, cached resources could survive backend changes, and full payloads were printed by default"
root_cause: incomplete_persistence_boundary
resolution_type: code_fix
severity: high
tags: [rag, supabase, pgvector, fastapi, cli, row-level-security, concurrency, idempotency, api-parity, persistence]
related:
  - docs/contracts/2026-03-20-rag-catalog-sync-contract.md
  - docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md
  - docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md
  - docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md
  - docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md
  - todos/059-pending-p1-enable-rls-on-rag-and-import-tables.md
  - todos/060-pending-p1-make-supabase-sync-queue-operations-atomic.md
  - todos/061-pending-p2-fix-cli-ingest-contract-mismatches.md
  - todos/062-pending-p2-add-agent-and-api-parity-for-ingest-flow.md
  - todos/063-pending-p2-push-supabase-queue-and-vector-queries-down-to-the-db.md
  - todos/064-pending-p2-fix-cli-resource-cache-key-for-backend-selection.md
---

# Troubleshooting: Supabase-Backed RAG Persistence Needed RLS, Atomic Queue RPCs, and One Shared Ingest Path

## Problem

Phase 4 moved invoice-backed RAG persistence toward Supabase, but the first pass only replaced the storage adapter. The real persistence boundary was still incomplete.

The review surfaced three classes of gaps at once:

- the new public tables for imports, sync rows, and vectors were not protected by RLS
- the queue contract was still non-atomic under concurrency
- the CLI and API still did not offer the same durable ingest and retrieval path

That meant the code looked like it had persistent storage, but it still had security, correctness, and runtime-parity holes.

## Environment

- Module: Invoice Processing API
- Affected component: Supabase repository adapter, catalog sync queue, backend RAG ingest/query surfaces
- Key files:
  - `src/invproc/repositories/supabase.py`
  - `src/invproc/rag.py`
  - `src/invproc/cli.py`
  - `src/invproc/api.py`
  - `supabase/migrations/001_rag_catalog_sync.sql`
  - `supabase/migrations/003_missing_tables.sql`
- Date: 2026-03-20

## Symptoms

- New `public` tables existed for `invoice_import_runs`, `product_embedding_sync`, and `product_catalog_embeddings`, but nothing in the migration enabled RLS by default.
- Queue producer and worker flows still relied on select-then-act logic, so duplicate insert attempts and double-claims were possible under concurrent access.
- Retrieval still depended on loading embeddings into Python and ranking there, which breaks down once persistence becomes shared and remote.
- `rag ingest-invoice` was useful for backend validation, but Swagger or other API clients still could not drive the same import-backed RAG path.
- CLI behavior was no longer trustworthy enough for durable persistence:
  - the default idempotency key did not change when `--default-weight-kg` changed the effective request
  - `--limit` was not actually constraining sync processing
  - cached CLI resources could ignore backend changes
  - the default output dumped the full ingest payload

## What Didn't Work

**Attempted solution 1:** Swap the repository implementation from memory to Supabase and keep the rest of the queue/search contract mostly unchanged.  
**Why it failed:** persistence is not only a storage concern. Once multiple processes share the same queue and vector state, races and network-heavy read paths become correctness issues.

**Attempted solution 2:** Treat the CLI as the only realistic Phase 4 ingest surface.  
**Why it failed:** that validated one operator path, but it did not give the API graph a matching import-backed route, so backend behavior still depended on which surface happened to be used.

**Attempted solution 3:** Keep CLI defaults optimized for debugging convenience.  
**Why it failed:** once the CLI is a real persistence surface, idempotency identity, output sensitivity, and backend cache invalidation become part of the contract rather than just local ergonomics.

## Solution

Complete the persistence boundary instead of only changing the backing store.

### 1) Secure the new Supabase tables by default

The migrations now enable row-level security on all import and RAG tables:

```sql
alter table products enable row level security;
alter table stock_movements enable row level security;
alter table invoice_import_runs enable row level security;
alter table product_embedding_sync enable row level security;
alter table product_catalog_embeddings enable row level security;
```

This moved the schema away from "public table exists" and toward "public table is protected unless explicitly opened."

### 2) Move queue creation and claiming into SQL RPCs

The queue contract was pushed down into database functions so producers and workers stop doing multi-step select-then-act flows in Python.

```sql
create or replace function create_or_reuse_product_sync_row(...) returns table (...)
```

```sql
create or replace function claim_next_product_sync_row(
    p_worker_id text,
    p_now timestamptz,
    p_lease_timeout_seconds integer
) returns setof product_embedding_sync
```

The Supabase repository now calls those RPCs directly:

```python
result = self.client.rpc(
    "create_or_reuse_product_sync_row",
    self._product_sync_input_payload(data),
).execute().data[0]

rows = self.client.rpc(
    "claim_next_product_sync_row",
    {
        "p_worker_id": worker_id,
        "p_now": now.isoformat(),
        "p_lease_timeout_seconds": int(lease_timeout.total_seconds()),
    },
).execute().data
```

That made the unique snapshot constraint and queue claim semantics part of the database contract instead of best-effort application logic.

### 3) Push vector search into the repository boundary

Once the store became remote and durable, retrieving all embeddings into Python for ranking was the wrong abstraction. The repository contract gained a native search method:

```python
class InvoiceImportRepository(Protocol):
    def search_product_catalog_embeddings(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        ...
```

The in-memory repository still implements this for local tests, but the Supabase adapter now delegates to a database RPC:

```python
rows = self.client.rpc(
    "match_product_catalog_embeddings",
    {
        "p_query_embedding": query_embedding,
        "p_embedding_model": embedding_model,
        "p_match_count": top_k,
    },
).execute().data
```

And `CatalogRetrievalService.query()` now uses the repository-native search path instead of reimplementing ranking at the service layer.

### 4) Give the API the same import-backed RAG path as the CLI

The backend now exposes an internal import endpoint so API-owned resources can run import -> sync without relying on the CLI process:

```python
@router.post("/internal/rag/import")
async def import_catalog_rows(...):
    import_response = await run_in_threadpool(
        import_service.import_rows,
        payload.payload,
        idempotency_key=payload.idempotency_key,
    )
    if payload.sync_after_import:
        results = await run_in_threadpool(worker.sync_pending, limit=payload.sync_limit)
```

This closes the parity gap between:

- CLI validation with `rag ingest-invoice`
- API validation through the app-owned dependency graph

### 5) Harden the CLI contract now that it writes durable state

The CLI ingest path was tightened in four ways:

1. Default idempotency keys are now derived from the effective import payload, not just a PDF hash.
2. `--limit` is honored directly by `worker.sync_pending(limit=limit)`.
3. The cached CLI resource key now includes backend selection, not just mock/model settings.
4. The default output is a redacted summary; `--json` is required for full machine-readable payloads.

Representative changes:

```python
def _build_default_idempotency_key(payload: InvoiceImportRequest) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return f"cli:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
```

```python
cache_key = (
    config.mock,
    config.catalog_sync_embedding_model,
    config.catalog_sync_enabled,
    config.import_repository_backend,
)
```

## Why This Works

The bug was not "Supabase adapter missing a few methods." The real issue was that persistence had crossed a system boundary without promoting the surrounding guarantees.

The final fix works because it upgrades all the boundaries that become important once storage is shared:

1. **Security boundary:** new tables are protected by default with RLS enabled.
2. **Concurrency boundary:** queue creation and claiming are atomic at the database layer.
3. **Performance boundary:** vector matching happens at the repository/database boundary instead of full-table Python ranking.
4. **Runtime ownership boundary:** both CLI and API now have valid import-backed RAG entrypoints.
5. **Contract boundary:** the CLI behaves like a real persistence client, with stable idempotency identity, correct sync limits, backend-aware caching, and safer output defaults.

## Regression Coverage

The final verification covered both the new persistence path and the contract around it:

```python
def test_api_rag_import_endpoint_runs_import_and_sync() -> None:
    response = client.post("/internal/rag/import", json={...})
    query_response = client.post("/internal/rag/query", json={"query": "greek yogurt order", "top_k": 5})

    assert response.json()["import"]["summary"]["created_count"] == 1
    assert response.json()["sync"]["processed"] == 1
    assert query_response.json()["matches"][0]["product_id"] == "prod_1"
```

```python
def test_cli_rag_ingest_invoice_defaults_to_redacted_summary(...) -> None:
    result = runner.invoke(app, ["rag", "ingest-invoice", str(pdf_path), "--mock", "--query", "greek yogurt"])
    payload = json.loads(result.output)

    assert "invoice" not in payload
    assert "import" not in payload
    assert payload["top_match_product_ids"] == ["prod_1"]
```

```python
def test_shared_supabase_repository_state_is_visible_to_worker_and_retrieval() -> None:
    worker = CatalogSyncWorker(repository=repository, ...)
    retrieval = CatalogRetrievalService(repository=SupabaseInvoiceImportRepository(client), ...)

    assert query_result.matches[0].product_id == product.product_id
```

## Prevention

- When moving from process-local state to shared persistence, review security, concurrency, and query placement together. Do not treat it as a storage-only refactor.
- If a queue is shared by multiple workers, claim semantics must live in the database or another atomic coordination primitive.
- If a retrieval feature becomes remote-backed, add a repository-native search method instead of letting the service layer pull full datasets into memory.
- For every CLI command that writes durable state, treat idempotency identity, log/output sensitivity, and cache invalidation as part of the public contract.
- When CLI and API both exercise the same feature, add at least one parity test that proves both surfaces operate on the same conceptual resource graph.

## Verification

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

Result:

- `ruff`: passed
- `mypy`: passed
- `pytest`: `157 passed`

## Related Issues

- Earlier runtime-wiring gap for sync production: `docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md`
- Earlier ownership split between CLI and app-backed RAG services: `docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md`
- FastAPI app-resource ownership pattern used again here: `docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md`

No obvious existing solution doc was contradicted by this fix, so no targeted `ce:compound-refresh` follow-up is needed from this change alone.
