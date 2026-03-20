---
title: feat: Execute Phase 3 backend RAG pipeline in invproc
type: feat
status: completed
date: 2026-03-20
origin: docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md
---

# feat: Execute Phase 3 backend RAG pipeline in invproc

## Overview

Phase 1 locked the sync contract and Phase 2 shipped the producer in `invproc`. This revised Phase 3 plan moves the consumer-side RAG backend into this repository instead of a React WhatsApp app.

This repo should own the full backend retrieval pipeline:

- claim and process pending sync rows
- generate embeddings
- write and update vector rows
- expose retrieval and evaluation surfaces
- validate search quality before any React integration

The React WhatsApp app becomes a downstream consumer of retrieval results or a future chat API. It should not own embedding jobs, vector maintenance, or retry workers.

## Scope Boundary

In scope:

- claim pending catalog sync rows from `product_embedding_sync`
- generate embeddings with the row's `embedding_model`
- upsert `product_catalog_embeddings`
- mark sync rows `synced` or `failed`
- expose a retrieval service or API inside `invproc`
- add an evaluation harness for WhatsApp-style catalog queries

Out of scope:

- full conversational orchestration inside this repo
- WhatsApp UI integration
- hybrid retrieval, reranking, or threshold tuning beyond a safe first pass
- delete/tombstone handling for products that no longer exist

## Inputs Already Available

Phase 3 should assume these inputs already exist from Phases 1 and 2:

- `product_embedding_sync` rows are emitted only after successful product persistence
- deduplication is already keyed by `product_id + product_snapshot_hash`
- each row includes the canonical snapshot payload and `embedding_model`
- producer failures are fail-open and do not block imports

Primary contract reference:

- `docs/contracts/2026-03-20-rag-catalog-sync-contract.md`

Producer implementation reference:

- `src/invproc/catalog_sync.py`

## Execution Strategy

Use **serial implementation units** in this repository:

1. worker claim and processing loop
2. embedding generation and vector upsert
3. retrieval and query surfaces
4. evaluation harness, retries, and operational validation

The worker and retrieval surface should land in separate commits even if they ship in one PR.

## Implementation Units

### Unit 1: Claimable sync worker

Goal:

- add a backend worker that can safely claim one pending sync row at a time without double-processing

Files to add or update in this repo:

- sync worker module under `src/invproc/`
- repository methods for claim/update operations
- tests for claim semantics and lease expiry

Approach:

- select rows in `pending`, plus `failed` rows whose `next_retry_at <= now()`
- atomically transition one row to `processing`
- stamp `claimed_at` and `claimed_by`
- treat expired `processing` rows as reclaimable after a lease timeout

Execution note:

- test-first for claim, reclaim, and duplicate-worker contention behavior

Verification:

- two concurrent worker attempts cannot both claim the same row
- an expired `processing` row becomes claimable again
- a worker restart can safely continue from durable table state

### Unit 2: Embedding generation and vector upsert

Goal:

- turn one claimed sync row into one durable vector row keyed by product identity and snapshot hash

Files to add or update in this repo:

- embedding client/service wrapper
- vector repository or SQL helper
- tests for upsert idempotency and model parity

Approach:

- build embedding text from `{name} {barcode} {category} {uom}` while skipping empty fields
- generate the embedding using the row's `embedding_model`
- upsert `product_catalog_embeddings` on `product_id + product_snapshot_hash + embedding_model`
- persist metadata needed for retrieval and later UI rendering
- mark the sync row `synced` and set `last_synced_at`

Execution note:

- test-first for embedding-text assembly and idempotent upsert behavior

Verification:

- unchanged reruns do not create duplicate vector rows
- the same snapshot can be retried safely after a worker crash
- query and catalog embeddings use the same model identifier

### Unit 3: Retrieval service and query surface

Goal:

- make synced catalog vectors queryable from this repo before any React integration

Files to add or update in this repo:

- retrieval service
- API route and/or CLI command for semantic catalog queries
- tests using representative WhatsApp-style order phrases

Approach:

- embed the user query with the same model used for the catalog vectors
- query `product_catalog_embeddings` using cosine similarity
- start with top `5`
- return a backend retrieval result shape that React can consume later
- keep fallback behavior explicit when retrieval returns no confident match

Execution note:

- test-first for retrieval shape and top-K behavior

Verification:

- a known product import becomes searchable after sync completes
- representative order phrases return the expected product in top 5
- retrieval miss behavior remains explicit and inspectable

### Unit 4: Evaluation harness and retry semantics

Goal:

- make search quality and sync reliability measurable before frontend integration

Files to add or update in this repo:

- retry/backoff helper
- evaluation fixtures for representative product queries
- CLI or script for running retrieval evaluation
- observability hooks for failures

Approach:

- on embedding or upsert failure, set `sync_status = failed`
- increment `attempt_count`
- record a human-readable `last_error`
- compute `next_retry_at` using bounded backoff
- add a repeatable evaluation flow such as:
  - `python -m invproc rag sync-pending`
  - `python -m invproc rag query "metro yogurt"`
  - `python -m invproc rag eval tests/fixtures/rag_queries.json`

Execution note:

- characterization-first if an existing CLI surface can be extended cleanly; otherwise test-first

Verification:

- failure preserves enough state for replay
- retrying a failed row does not create a new logical snapshot row
- repeated failures stay observable instead of silently looping
- evaluation output shows top-1 and top-5 hit rates for representative queries

## Worker Contract Checklist

- only claim rows in `pending` or retry-due `failed`
- claim updates are atomic
- `processing` rows carry lease metadata
- successful completion writes vectors before marking sync `synced`
- failed completion preserves the row and increments retry state
- all writes are idempotent across retries

## Suggested Retrieval Surfaces

At least one of these should ship in Phase 3:

- CLI query command for local testing
- internal API endpoint for semantic catalog lookup
- service object used by future chat integration

Recommended first slice:

- CLI for sync and eval
- internal service for retrieval
- API endpoint only if needed for manual testing or React integration

## Suggested SQL Shape

These queries should live in this repo's repository layer.

Claim one row:

```sql
update product_embedding_sync
set
  sync_status = 'processing',
  claimed_at = now(),
  claimed_by = :worker_id,
  updated_at = now()
where id = (
  select id
  from product_embedding_sync
  where
    (
      sync_status = 'pending'
      or (sync_status = 'failed' and coalesce(next_retry_at, now()) <= now())
      or (
        sync_status = 'processing'
        and claimed_at <= now() - interval '10 minutes'
      )
    )
  order by created_at asc
  limit 1
  for update skip locked
)
returning *;
```

Upsert one vector row:

```sql
insert into product_catalog_embeddings (
  product_id,
  product_snapshot_hash,
  embedding_model,
  embedding_text,
  embedding,
  metadata,
  created_at,
  updated_at
)
values (
  :product_id,
  :product_snapshot_hash,
  :embedding_model,
  :embedding_text,
  :embedding,
  :metadata,
  now(),
  now()
)
on conflict (product_id, product_snapshot_hash, embedding_model)
do update set
  embedding_text = excluded.embedding_text,
  embedding = excluded.embedding,
  metadata = excluded.metadata,
  updated_at = now();
```

## Test Scenarios

1. Claiming the same pending row from two workers results in one winner.
2. A crashed worker leaves a `processing` row that becomes reclaimable after lease expiry.
3. A successful embed writes exactly one vector row and marks the sync row `synced`.
4. A failed embed marks the row `failed`, increments `attempt_count`, and records `last_error`.
5. Retrying the same snapshot after failure does not create duplicate vectors.
6. Query embedding and catalog embedding use the same model identifier.
7. An imported product becomes retrievable through this repo shortly after sync.
8. Representative WhatsApp-style queries return the expected product in top 5.
9. Retrieval miss behavior remains explicit and does not fabricate grounded matches.

## Operational Validation

Post-deploy monitoring for this repo should include:

- count of `pending`, `processing`, `failed`, and `synced` rows over time
- age of oldest `pending` row
- age of oldest `processing` row
- retry volume and repeated-failure rate
- retrieval hit quality on a fixed set of representative catalog prompts

Healthy signals:

- `pending` backlog drains steadily after imports
- `processing` rows are short-lived
- most rows reach `synced` on the first attempt
- new products become searchable within the agreed freshness window

Failure signals:

- `processing` rows older than the lease timeout
- rising `failed` rows for the same worker or model
- imported products not appearing in top 5 retrieval results

## Acceptance Criteria for Phase 3

- [x] Backend worker claims durable sync rows without duplicate processing
- [x] Canonical embedding text is assembled from the contract fields
- [x] Vector rows are upserted by `product_id + product_snapshot_hash + embedding_model`
- [x] Failed jobs are retryable without data corruption
- [x] Retrieval is queryable from this repo before React integration
- [x] Representative WhatsApp-style product queries return the expected item in top 5
- [x] Evaluation tooling exists for local and CI-friendly RAG validation
- [x] Operational queries or dashboards exist for stuck and failed sync rows

## Handoff Note

This repo should become the backend RAG system of record before any React integration work begins. The next code change should therefore happen in `invproc`, with the React WhatsApp app treated as a later consumer of retrieval results rather than the owner of embedding or vector infrastructure.
