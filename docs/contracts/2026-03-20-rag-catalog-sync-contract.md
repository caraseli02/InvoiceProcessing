# RAG Catalog Sync Contract

Date: 2026-03-20
Status: updated for Phase 5 retrieval quality validation
Related plans:
- `docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md`
- `docs/plans/2026-03-20-002-feat-rag-whatsapp-catalog-sync-phase-3-backend-rag-plan.md`
Origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`

## Purpose

Lock the backend-owned contract for catalog freshness, embedding generation, vector persistence, retrieval, and evaluation before React integration.

This document is the source of truth for:

- when sync work is created
- what `invproc` is responsible for end to end in Phase 3
- what the React WhatsApp app is responsible for later
- which fields define a unique product snapshot
- how retryable sync state and vector rows are represented
- which backend-only retrieval surfaces validate RAG quality before frontend integration

## Current Decisions

1. Durable sync rows in Supabase remain the freshness trigger and replay source.
2. The trigger point remains successful product persistence inside `InvoiceImportService.import_rows()`.
3. Sync metadata stays out of public extraction/import response schemas.
4. `invproc` now owns the consumer side of the pipeline: claim loop, embeddings, vector writes, retries, retrieval, and evaluation.
5. The React WhatsApp app is a downstream consumer only; it does not own embeddings, vector writes, retry workers, or retrieval jobs.
6. Deduplication remains based on `product_id + product_snapshot_hash`.
7. Vector rows are uniquely keyed by `product_id + product_snapshot_hash + embedding_model`.

## Ownership Boundary

### `invproc` owns

- deciding whether a product change happened after a successful import write
- assembling the canonical snapshot payload for that persisted product version
- computing a deterministic snapshot hash
- writing a durable sync row
- claiming claimable sync rows atomically
- generating embeddings with the stored `embedding_model`
- upserting vector rows into `product_catalog_embeddings`
- marking sync rows as `processing`, `synced`, or `failed`
- lease expiry, retry backoff, replay safety, and worker crash recovery
- embedding user queries with the same model as catalog vectors
- semantic retrieval, top-K selection, and explicit miss behavior
- backend-only evaluation harnesses and retrieval quality reporting

### React WhatsApp app owns

- calling a backend retrieval surface or future chat API
- presenting grounded matches to end users
- conversation orchestration and UI behavior once backend RAG is validated

### Explicit non-goals for React

- no embedding API calls from the frontend
- no pgvector writes from the frontend
- no retry workers or claim loops in the frontend
- no ownership of vector freshness or replay logic

## Trigger Contract

`invproc` creates sync work only after a row has been successfully persisted as either:

- `created`
- `updated`

`invproc` must not create sync work for:

- `/extract` preview responses
- preview-pricing responses
- import rows that end in `error`
- unchanged products whose newly computed snapshot hash matches the latest known hash

For partial imports, only successfully persisted product rows create sync intents.

## Canonical Snapshot Payload

The producer writes one logical product snapshot payload per persisted product version.

### Required fields

- `product_id`
- `product_snapshot_hash`
- `name`
- `supplier`
- `embedding_model`
- `source_import_id`
- `source_row_id`
- `sync_status`
- `attempt_count`

### Optional fields

- `barcode`
- `category`
- `uom`
- `price_eur`
- `price_50`
- `price_70`
- `price_100`
- `markup`
- `invoice_number`
- `last_error`
- `claimed_at`
- `claimed_by`
- `last_synced_at`
- `next_retry_at`

### Canonical embedding text

V1 embedding text is assembled by the backend consumer from these fields, omitting null or empty values:

```text
{name} {barcode} {category} {uom}
```

Notes:

- `name` is the only always-required semantic field.
- `barcode`, `category`, and `uom` are optional in V1; missing values are skipped.
- `supplier` and price fields are persisted for metadata, filtering, and future rendering, but are not part of the default V1 embedding text.

## Snapshot Hash Contract

The snapshot hash must be deterministic and stable across retries.

### Hash input fields

The hash input is normalized JSON over:

- `product_id`
- `name`
- `barcode`
- `category`
- `uom`
- `supplier`
- `price_eur`
- `price_50`
- `price_70`
- `price_100`
- `markup`
- `embedding_model`

### Rules

- Serialize with stable key ordering.
- Normalize missing optional values to `null`.
- Trim surrounding whitespace from strings before hashing.
- Do not include operational fields such as status, timestamps, claim metadata, or retry counters.
- If the hash matches the latest successful or pending snapshot for the same `product_id`, no new sync row should be created.

## Sync State Machine

Allowed states:

- `pending`: durable intent exists and is available for claiming
- `processing`: a worker has claimed the row and holds a lease
- `synced`: embeddings and vector write completed for that exact snapshot
- `failed`: the last attempt failed and the row is eligible for retry or inspection

### Transitions

- `pending -> processing`
- `failed -> processing` when retry is due
- `processing -> synced`
- `processing -> failed`
- `processing -> processing` when an expired lease is reclaimed by another worker

Workers must never delete sync rows as part of normal processing.

## Claim, Lease, and Retry Semantics

### Claim contract

- Claiming is row-based and atomic.
- Workers may claim rows in `pending`.
- Workers may also reclaim rows in `failed` when `next_retry_at <= now()`.
- Workers may reclaim rows in `processing` when the existing lease has expired.
- Each claim updates `sync_status = processing`, `claimed_at`, `claimed_by`, and `updated_at`.

### Lease contract

- `processing` rows carry lease metadata in `claimed_at` and `claimed_by`.
- Lease expiry is implementation-configurable, but the default Phase 3 worker assumes a 10-minute timeout.
- A crashed worker must not block progress forever; expired `processing` rows become claimable again.

### Retry contract

- Every failed attempt increments `attempt_count`.
- Failures record a human-readable `last_error`.
- Retry scheduling uses bounded backoff written to `next_retry_at`.
- Retrying a row must not create a new logical snapshot row.
- Successful completion clears any stale error state and stamps `last_synced_at`.

## Storage Contract

### Sync table: `product_embedding_sync`

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid/text | yes | primary key |
| `product_id` | text | yes | stable product identity from product persistence |
| `product_snapshot_hash` | text | yes | dedupe key for product version |
| `embedding_model` | text | yes | required for model parity |
| `name` | text | yes | canonical product name |
| `barcode` | text | no | optional product code or EAN |
| `category` | text | no | optional catalog category |
| `uom` | text | no | optional unit of measure |
| `supplier` | text | no | supplier or context metadata |
| `price_eur` | numeric | no | current base price |
| `price_50` | numeric | no | current pricing tier |
| `price_70` | numeric | no | current pricing tier |
| `price_100` | numeric | no | current pricing tier |
| `markup` | integer | no | persisted markup used during import |
| `source_import_id` | text | yes | import execution that produced this snapshot |
| `source_row_id` | text | yes | source row within the import payload |
| `invoice_number` | text | no | import or invoice traceability |
| `sync_status` | text | yes | `pending`, `processing`, `synced`, `failed` |
| `attempt_count` | integer | yes | defaults to `0` |
| `last_error` | text | no | most recent failure reason |
| `claimed_at` | timestamptz | no | worker lease timestamp |
| `claimed_by` | text | no | worker identity |
| `next_retry_at` | timestamptz | no | retry scheduling |
| `last_synced_at` | timestamptz | no | success timestamp |
| `created_at` | timestamptz | yes | defaults to now |
| `updated_at` | timestamptz | yes | maintained on every state change |

Constraints and indexes:

- primary key on `id`
- unique constraint on `product_id, product_snapshot_hash`
- index on `sync_status, next_retry_at, created_at`
- index on `product_id, created_at desc`
- check constraint limiting `sync_status` to the four allowed values
- check constraint ensuring `attempt_count >= 0`

### Vector table: `product_catalog_embeddings`

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid/text | yes | primary key |
| `product_id` | text | yes | stable product identity |
| `product_snapshot_hash` | text | yes | joins back to sync snapshot |
| `embedding_model` | text | yes | must match query model |
| `embedding_text` | text | yes | exact text used for embedding |
| `embedding` | vector/float array | yes | embedding payload for similarity search |
| `metadata` | json/jsonb | yes | product fields used for retrieval or rendering |
| `created_at` | timestamptz | yes | defaults to now |
| `updated_at` | timestamptz | yes | maintained on write or update |

Constraints and indexes:

- unique constraint on `product_id, product_snapshot_hash, embedding_model`
- vector index deferred until scale or latency requires it
- btree index on `product_id, updated_at desc`

## Backend Worker Contract

The backend worker processes `product_embedding_sync` rows under this handshake:

1. Claim one eligible row from `pending`, retry-due `failed`, or expired-lease `processing`.
2. Atomically move it to `processing` and stamp lease metadata.
3. Build `embedding_text` from the row payload.
4. Generate an embedding using the row's `embedding_model`.
5. Upsert into `product_catalog_embeddings` using `product_id + product_snapshot_hash + embedding_model`.
6. Mark the sync row `synced`, stamp `last_synced_at`, and preserve the same logical row id.
7. On failure, mark the row `failed`, increment `attempt_count`, preserve `last_error`, and compute `next_retry_at`.

All worker writes must be idempotent across retries.

## Retrieval Contract

Phase 3 retrieval lives in `invproc` and exists to validate the catalog RAG path before frontend integration.

### Query contract

- Embed the user query with the same `embedding_model` used for the target catalog vectors.
- Use cosine similarity as the safe default for V1.
- Start with top `5`.
- Return an explicit backend result shape containing:
  - the query text
  - the embedding model
  - the `search_mode` used (`semantic`, `lexical`, or `hybrid`)
  - the top matches with score/similarity metadata
  - enough product metadata for later React rendering
  - an explicit empty-result shape when there is no confident match

### Search modes

Three modes are supported. Default is `hybrid`.

| Mode | Description |
| --- | --- |
| `semantic` | Vector cosine similarity only. Best for natural-language queries. |
| `lexical` | BM25/full-text search only. Best for exact barcodes, SKUs, product codes. |
| `hybrid` | Both searches run in parallel; results merged via Reciprocal Rank Fusion (RRF, k=60). Deduplicates by `product_id`. |

**Why hybrid is the default:** wholesale ordering queries mix fuzzy natural language ("re-order the metro yogurt") with exact product references ("barcode 8001480015630"). Semantic search alone misses exact-code lookups; lexical alone misses paraphrase queries. RRF merging handles both without threshold tuning.

### Supabase SQL RPC: `search_product_catalog_embeddings_lexical`

Required for the Supabase-backed lexical search path.

```sql
CREATE OR REPLACE FUNCTION search_product_catalog_embeddings_lexical(
    p_query_text TEXT,
    p_embedding_model TEXT,
    p_match_count INT
)
RETURNS TABLE (
    product_id UUID,
    product_snapshot_hash TEXT,
    embedding_model TEXT,
    embedding_text TEXT,
    metadata JSONB,
    score FLOAT
)
LANGUAGE SQL STABLE AS $$
    SELECT
        product_id,
        product_snapshot_hash,
        embedding_model,
        embedding_text,
        metadata,
        ts_rank(
            to_tsvector('simple', embedding_text),
            plainto_tsquery('simple', p_query_text)
        )::FLOAT AS score
    FROM product_catalog_embeddings
    WHERE
        embedding_model = p_embedding_model
        AND to_tsvector('simple', embedding_text) @@ plainto_tsquery('simple', p_query_text)
    ORDER BY score DESC
    LIMIT p_match_count;
$$;
```

Notes:
- `'simple'` dictionary tokenizes without language-specific stemming — correct for multilingual product names, barcodes, and SKU codes.
- A GIN index on `to_tsvector('simple', embedding_text)` is recommended for scale.
- The `semantic` path continues to use the existing `match_product_catalog_embeddings` RPC (pgvector cosine).

### Retrieval guarantees

- Query and catalog embeddings must use the same model identifier.
- Retrieval misses must remain explicit and inspectable.
- Retrieval quality is validated in this repo before any React dependency is introduced.
- `search_mode` is always present in the response so callers can inspect which strategy was used.

## Backend Validation Surfaces

At least the following backend-owned validation flow should be supported in Phase 3:

- a worker execution surface for processing pending sync rows
- a query surface for semantic catalog lookup
- an evaluation harness for representative WhatsApp-style product queries

Representative examples:

```text
python -m invproc rag sync-pending
python -m invproc rag query "metro yogurt"
python -m invproc rag eval tests/fixtures/rag_queries.json
```

Equivalent API surfaces may exist for manual testing, but CLI and service-level validation are the primary requirement.

## Replay and Reconciliation

The contract must support these operations without special-case code:

- replay all failed rows for a time window
- replay all rows for one `product_id`
- resume after worker crash without creating duplicate vectors
- re-run evaluation against a stable query fixture set

## Operational Visibility

Phase 3 must make the following states visible to operators:

- count of `pending`, `processing`, `failed`, and `synced` rows
- age of oldest claimable and processing rows
- repeated failures for the same snapshot or model
- top-1 and top-5 evaluation results for representative catalog queries

Healthy signals:

- pending backlog drains after imports
- processing rows are short-lived
- most rows sync on the first attempt
- imported products become searchable within the freshness window

Failure signals:

- processing rows older than the lease timeout
- repeated failures for the same worker or model
- imported products missing from top-5 retrieval results

## Freshness SLO

The target window from successful product import to searchable catalog vector is **5 minutes** under normal operating conditions.

This window assumes:
- the sync worker is running continuously or on a scheduled loop
- the embedding API (OpenAI) is reachable with normal latency
- the `product_embedding_sync` claim-and-process cycle completes within one loop iteration

Healthy signal: a product imported via `invproc rag ingest-invoice` or `/invoice/import` is returned in `rag query` results within 5 minutes.

Failure signal: `product_embedding_sync` rows remain in `pending` or `processing` beyond 10 minutes after import. Check worker health and embedding API reachability.

## Retrieval Tuning Parameters

These are configurable at runtime via `InvoiceConfig`:

| Parameter | Config field | Default | Notes |
| --- | --- | --- | --- |
| Top-K | `rag_top_k` (CLI `--top-k`) | `5` | Number of matches returned per query |
| Match threshold | `rag_match_threshold` | `0.0` | Minimum score to include a match. `0.0` = no filtering. Raise to prune low-confidence results. |
| Search mode | `--mode` CLI flag / `mode` API param | `hybrid` | `semantic`, `lexical`, or `hybrid` (RRF) |

Threshold guidance:
- RRF scores are not probabilities. For hybrid mode, useful filtering typically starts around `0.01`–`0.05`.
- For semantic-only (cosine similarity), typical useful range is `0.5`–`0.8`.
- Default `0.0` preserves backward-compatible behavior: all top-K matches are returned regardless of score.

## Scope Boundary

This contract intentionally does not define:

- WhatsApp conversation orchestration
- frontend rendering details
- product delete/tombstone handling

Previously excluded but now shipped (Phase 4, PR #29):
- hybrid retrieval: BM25 lexical + pgvector semantic + Reciprocal Rank Fusion merging
- `search_mode` field in all retrieval responses (`semantic`, `lexical`, `hybrid`)
