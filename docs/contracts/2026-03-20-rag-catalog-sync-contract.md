# RAG Catalog Sync Contract (Phase 1)

Date: 2026-03-20
Status: locked for Phase 1
Related plan: `docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md`
Origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`

## Purpose

Lock the cross-repo contract for catalog-to-RAG freshness before any producer or consumer code is added.

This document is the source of truth for:

- when sync work is created
- what `invproc` is responsible for
- what the WhatsApp agent stack is responsible for
- which fields define a unique product snapshot
- how retryable sync state is represented in Supabase

## Phase 1 Decisions

1. Default transport is a durable Supabase sync table, not a webhook.
2. The trigger point is successful product persistence inside `InvoiceImportService.import_rows()`.
3. Sync metadata stays out of public extraction/import response schemas.
4. `invproc` produces freshness intents only; embedding generation, vector writes, and retrieval stay in the agent stack.
5. Deduplication is based on `product_id + product_snapshot_hash`.

## Ownership Boundary

### `invproc` owns

- deciding whether a product change happened after a successful import write
- assembling the canonical snapshot payload for that persisted product version
- computing a deterministic snapshot hash
- writing a durable sync row with retry-visible status
- preserving import behavior if downstream embedding infrastructure is unavailable

### WhatsApp/agent repo owns

- claiming pending sync rows
- generating embeddings
- writing or updating vector rows
- marking sync rows as `processing`, `synced`, or `failed`
- lease expiry, retry backoff, and reconciliation jobs
- query embedding, top-K retrieval, prompt grounding, and fallback behavior

### Explicit non-goals for `invproc`

- no embedding API calls
- no pgvector similarity queries
- no WhatsApp prompt assembly
- no query-time ranking logic

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

V1 embedding text is assembled by the consumer from these fields, omitting null or empty values:

```text
{name} {barcode} {category} {uom}
```

Notes:

- `name` is the only always-required semantic field.
- `barcode`, `category`, and `uom` are optional in V1; the consumer must skip missing values instead of inserting placeholders.
- `supplier` and price fields are persisted for filtering, replay, or future prompt formatting, but are not part of the default V1 embedding text.

## Snapshot Hash Contract

The snapshot hash must be deterministic and stable across retries.

### Hash input fields

The Phase 1 contract defines the hash input as normalized JSON over:

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
- `processing`: a worker has claimed the row
- `synced`: embeddings or vector write completed for that exact snapshot
- `failed`: the last attempt failed and the row is eligible for retry or inspection

### Transitions

- `pending -> processing`
- `processing -> synced`
- `processing -> failed`
- `failed -> pending` for retry

Workers must never delete sync rows as part of normal processing.

## Retry and Claim Semantics

### Claim contract

- Worker claims must be row-based and atomic.
- A claim records `claimed_at` and `claimed_by`.
- Rows stuck in `processing` become reclaimable after a lease timeout chosen by the consumer implementation.

### Retry contract

- Every failed attempt increments `attempt_count`.
- Failures record a human-readable `last_error`.
- Retry scheduling uses `next_retry_at`.
- Retrying a row must not create a new logical snapshot row.

## Proposed Supabase Schema

This section defines the Phase 1 storage contract. It is intentionally schema-first and code-agnostic.

### Producer-owned table: `product_embedding_sync`

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes | primary key |
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
| `updated_at` | timestamptz | yes | maintained on write or update |

Constraints and indexes:

- primary key on `id`
- unique constraint on `product_id, product_snapshot_hash`
- index on `sync_status, next_retry_at, created_at`
- index on `product_id, created_at desc`
- check constraint limiting `sync_status` to the four allowed values
- check constraint ensuring `attempt_count >= 0`

### Consumer-owned table: `product_catalog_embeddings`

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes | primary key |
| `product_id` | text | yes | stable product identity |
| `product_snapshot_hash` | text | yes | joins back to sync snapshot |
| `embedding_model` | text | yes | must match query model |
| `embedding_text` | text | yes | exact text used for embedding |
| `embedding` | vector | yes | exact dimension set by consumer migration for chosen model |
| `metadata` | jsonb | yes | product fields used for retrieval or rendering |
| `created_at` | timestamptz | yes | defaults to now |
| `updated_at` | timestamptz | yes | maintained on write or update |

Constraints and indexes:

- unique constraint on `product_id, product_snapshot_hash, embedding_model`
- vector index deferred until scale or latency requires it
- btree index on `product_id, updated_at desc`

## Worker Contract

The consumer processes `product_embedding_sync` rows under this handshake:

1. Select a claimable row in `pending` state or a retryable `failed` row whose `next_retry_at` is due.
2. Atomically move it to `processing` and stamp claim metadata.
3. Build `embedding_text` from the row payload.
4. Generate embedding using the same `embedding_model` stored on the row.
5. Upsert into `product_catalog_embeddings` using `product_id + product_snapshot_hash + embedding_model`.
6. Mark the sync row `synced` and stamp `last_synced_at`.
7. On failure, mark the row `failed`, increment `attempt_count`, preserve `last_error`, and compute `next_retry_at`.

## Replay and Reconciliation

The contract must support these operations without special-case code:

- replay all failed rows for a time window
- replay all rows for one `product_id`
- verify that each latest product snapshot has either a `pending`, `processing`, or `synced` sync row
- detect vectors whose snapshot hash no longer matches the latest synced product version

## Open Items Deferred Beyond Phase 1

- exact lease timeout duration
- exact retry backoff schedule
- whether category or uom become guaranteed producer fields in Phase 2
- tombstone or delete handling if product deletion is introduced later
- vector index choice and threshold or top-K tuning
