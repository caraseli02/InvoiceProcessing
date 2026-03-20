---
title: feat: Add RAG catalog sync contract for WhatsApp orders agent
type: feat
status: active
date: 2026-03-20
origin: docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md
---

# feat: Add RAG catalog sync contract for WhatsApp orders agent

## Overview

Add a cross-repo plan for grounding the WhatsApp orders agent with catalog retrieval, while keeping this repository focused on what it already does well: extracting invoice product data, normalizing it, and persisting product updates into Supabase. The recommended architecture is to treat this backend as the source of truth for product corpus freshness and to add a narrow sync contract that keeps the agent-side vector index up to date after successful product imports.

This plan intentionally avoids pulling retrieval orchestration into `invproc`. The agent app remains responsible for query embedding, top-K retrieval, and LLM prompt assembly. This backend only owns the catalog-side synchronization event/trigger and any minimal payloads needed to support that flow (see origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`).

## Problem Statement

The WhatsApp orders agent needs reliable product grounding so it can stop hallucinating names, codes, and prices during ordering conversations. The origin document concludes that for a METRO-scale catalog, prompt stuffing is not sufficient and RAG is the right approach because catalog size will likely grow into the thousands of SKUs, Supabase is already in the stack, and the invoice backend already produces a clean product corpus (see origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`).

The missing piece is the operational contract between systems:

- when product data changes, the RAG corpus must refresh
- the refresh mechanism must be idempotent and failure-aware
- ownership must stay clear across repos so this service does not absorb agent concerns
- the solution must fit existing project conventions: dependency injection, explicit app/resource ownership, strict contracts, and testable service boundaries

## Proposed Solution

Implement a lightweight catalog-sync capability centered on successful import writes, not on raw `/extract` responses.

**Decision:** use a **Supabase-side sync table plus async embedding worker** as the default implementation. Treat webhook delivery as a fallback only if the agent stack already has a mature receiver and operational tooling around retries, signatures, and failure visibility.

### Recommended system boundary

- `invproc` remains the authoritative producer of normalized product data.
- The import flow becomes the authoritative trigger point for RAG freshness because it corresponds to actual persistence, not just extraction preview.
- A new sync mechanism emits a post-import change signal or writes directly to a Supabase-backed embeddings table after successful product create/update operations.
- The WhatsApp/React agent consumes the synced vector corpus and performs retrieval at conversation time.

### Why this boundary

This aligns with repo learnings and current architecture:

- data models should stay pure and not absorb infrastructure coupling (`src/invproc/models.py:9`, `docs/solutions/architecture-issues/pydantic-model-layer-violation.md`)
- app-scoped resources and side-effectful dependencies should be created via explicit DI/lifespan wiring (`src/invproc/api.py:72`, `docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md`)
- import-side writes already carry idempotency and product update semantics, making them a safer trigger than `/extract` (`src/invproc/import_service.py:176`)

## Technical Approach

### Architecture

Use a Supabase-side sync table as the primary implementation shape.

#### Primary approach — Supabase-side sync table + embedding worker

`invproc` writes product changes to a dedicated sync table or queue-like table in Supabase. A worker in the agent repo (or Supabase Edge Function) reads pending rows, generates embeddings, updates the vector table, and marks sync state.

Why this is the recommended default:

- keeps OpenAI embedding calls out of this service
- avoids adding new long-latency external calls to the import request path
- preserves clean ownership between backend data production and agent retrieval concerns
- makes retries and dead-letter handling easier
- allows replay/reconciliation from database state without depending on transient HTTP delivery logs
- fits naturally with the existing Supabase-centered architecture from the origin document

#### Secondary fallback — `invproc` emits a signed webhook after import

After successful import, `invproc` sends a small signed payload to the agent-side backend with changed product IDs. The receiver performs embedding refresh.

Why this is acceptable but secondary:

- simpler if the agent repo already has a webhook receiver
- increases cross-service runtime coupling and retry complexity
- adds outbound network/error handling to this service

### Concrete recommendation

Build the first version around a durable sync table with row-level statuses:

- `pending` — product change recorded, awaiting embedding refresh
- `processing` — worker claimed the row
- `synced` — vector row refreshed successfully
- `failed` — refresh failed and should be retried or inspected

Use one sync row per product snapshot, keyed by `product_id` + `product_snapshot_hash`, so the system can skip unchanged product states cleanly.

### Data model additions

Add a minimal sync-oriented persistence contract, not RAG retrieval logic, for example:

- `product_embedding_sync` table or equivalent
- fields such as `product_id`, `product_snapshot_hash`, `sync_status`, `last_synced_at`, `last_error`, `source_import_id`
- optional denormalized embedding text payload assembled from canonical product fields
- optional `embedding_model` field for strict query/catalog model parity
- optional `attempt_count` and `next_retry_at` fields for controlled retries
- optional `claimed_at` / `claimed_by` fields if the worker needs cooperative locking

Recommended embedding text, carried forward from the origin:

```text
"{name} {code} {category} {uom}"
```

Use a snapshot hash so unchanged products do not re-embed unnecessarily.

Also persist the embedding model identifier alongside each snapshot so query-time retrieval can guarantee model parity. External Supabase guidance explicitly notes that embeddings generated by different models should not be compared.

### Trigger point

Trigger sync only after successful persistence in `InvoiceImportService.import_rows()` so the corpus reflects committed catalog state, not speculative extraction output (`src/invproc/import_service.py:176`).

Do not trigger from `/extract` directly by default, even though the origin notes it as a possible hook point, because `/extract` returns extracted data but does not itself guarantee product persistence (`src/invproc/api.py:149`, see origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`).

More specifically:

- emit sync intents only for rows that result in successful `created` or `updated` product writes
- do not emit sync work for row-level import failures
- do not re-enqueue when the newly computed product snapshot hash matches the latest synced snapshot

### Query-time responsibility

The WhatsApp agent repo owns:

- query embedding with `text-embedding-3-small` (carried forward from origin)
- vector similarity search in Supabase/pgvector
- top-K retrieval (origin recommends top 5)
- prompt injection and answer generation
- conversation fallback behavior when retrieval misses

Recommended retrieval guidance to carry into the agent repo:

- use the same embedding model for both catalog rows and user queries
- start with cosine distance as the safe default, then benchmark inner product only if normalization assumptions are confirmed
- treat threshold and top-K as evaluation knobs, not fixed architecture decisions
- consider hybrid retrieval when product codes, exact brand names, or multilingual abbreviations are common

### Implementation Phases

#### Phase 1: Lock contract and schema

Deliverables:

- [x] finalize sync-table approach and document why webhook is not the default
- [x] define canonical product fields used for RAG text assembly
- [x] define snapshot hashing and retry semantics
- [x] define ownership boundary document between repos
- [x] add proposed Supabase schema for sync rows and vector rows

Phase 1 output:

- `docs/contracts/2026-03-20-rag-catalog-sync-contract.md`

Success criteria:

- [x] one documented source of truth for when re-embedding happens
- [x] one documented consumer for performing embeddings
- [x] no ambiguity about whether `/extract`, preview, or import owns the trigger
- [x] sync schema supports replay, deduplication, and failure visibility

Estimated effort:

- 0.5-1 day

#### Phase 2: Add backend sync producer in `invproc`

Deliverables:

- add sync producer abstraction at the service layer
- wire it through DI/config, not model code
- invoke it only after successful import row persistence
- emit per-product sync intents for created/updated products
- persist snapshot hash and embedding payload/input fields needed by the downstream worker

Success criteria:

- import response remains stable
- failed sync emission is observable and policy-driven
- duplicate imports do not create duplicate downstream embedding work
- producer behavior is deterministic under idempotent replays

Estimated effort:

- 1-2 days

#### Phase 3: Add consumer and vector refresh path in agent stack

Deliverables:

- build sync consumer in the agent repo or Supabase Edge Function
- create/update vector rows keyed by product identity + snapshot hash
- support retries and visibility for failed embeddings
- define worker claim/retry semantics so multiple workers cannot process the same row indefinitely

Success criteria:

- changed products are searchable shortly after import
- unchanged products are skipped
- failed embedding jobs can be retried without data corruption
- worker can be re-run safely after crashes or partial failures

Estimated effort:

- 1-2 days

#### Phase 4: Validate retrieval quality and operational behavior

Deliverables:

- measure retrieval precision on representative WhatsApp queries
- verify latency impact and freshness SLO
- test failure and replay paths
- tune match threshold and top-K using real order prompts instead of freezing them up front
- decide whether pure semantic search is sufficient or whether hybrid retrieval (semantic + keyword/barcode matching) is needed

Success criteria:

- top-K retrieval surfaces correct products for common order utterances
- vector sync stays aligned with product table over repeated imports
- no hallucination regressions on known agent scenarios

Estimated effort:

- 1 day

### Rollout recommendation

Ship this in a deliberately narrow order:

1. **Schema + producer only** — record sync intents, but do not yet block on consumer readiness.
2. **Consumer + exact semantic retrieval** — prove freshness and basic relevance first.
3. **Evaluation tuning** — tune threshold/top-K with real WhatsApp phrases.
4. **Hybrid retrieval if needed** — add keyword/SKU fusion only if evaluation shows semantic-only misses.
5. **Indexing/scale work** — add vector indexes and retrieval optimizations when catalog size or latency justifies them.

## Alternative Approaches Considered

### Put the full RAG pipeline in this repo

Rejected because this service is an extraction/import backend, not the conversation runtime. Pulling query embedding and retrieval here would blur service ownership and fight existing layering and DI conventions.

### Trigger embeddings from `/extract`

Rejected as the primary path because `/extract` is extraction-only and may be called without a committed import. That creates freshness noise, duplicate work, and a mismatch between extracted rows and persisted catalog state.

### Store the entire catalog in the system prompt

Rejected by origin decision for larger catalogs. The origin explicitly uses catalog size as the decision point and concludes RAG is the right call for a METRO-scale supplier catalog (see origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`).

## System-Wide Impact

### Interaction Graph

- Invoice import request enters import flow and is normalized/priced via `InvoiceImportService` (`src/invproc/import_service.py:51`).
- For each row, the service computes pricing, resolves match state, and creates or updates product records (`src/invproc/import_service.py:176`).
- Successful writes then emit a sync intent for downstream embedding refresh.
- The downstream consumer creates or updates vector records.
- The WhatsApp agent embeds the incoming message, queries vectors, injects top-K results, and only then calls the generation model.

### Error & Failure Propagation

- Import validation/pricing errors should continue to fail at row level without creating sync intents.
- Sync-emission failures need an explicit policy:
  - preferred: import succeeds and sync row is marked `pending`/`failed` for retry
  - avoid: blocking the entire import on transient embedding infrastructure failures
- If webhook transport is chosen, network failures must not silently swallow the need for re-embedding.
- Worker failures should never delete the underlying sync intent; they should only advance the row into a retryable failed state.

### State Lifecycle Risks

- partial import success can create a mixed catalog state; sync must only reflect rows actually persisted
- repeated imports of unchanged products can create unnecessary embedding churn unless guarded by snapshot hashes
- product updates without deletes create stale vector rows unless upsert semantics are keyed correctly
- if product deletion is added later, vector deletion/tombstoning must be handled explicitly

### API Surface Parity

Interfaces likely affected:

- `src/invproc/import_service.py:176` for post-write sync emission
- `src/invproc/api.py:101` if DI must construct a sync-capable import service
- `src/invproc/config.py:33` for transport config, signing secret, enable/disable flags, or sync table names
- repository layer if sync persistence is implemented via the same data access boundary

No changes are required to `Product`/`InvoiceData` schemas unless the team decides to expose sync metadata outward. Keeping sync metadata out of extraction contracts is preferred (`src/invproc/models.py:9`).

### Integration Test Scenarios

1. Successful import of new products creates sync intents for each newly persisted product.
2. Successful import of existing unchanged products does not enqueue redundant re-embeds when snapshot hashes match.
3. Partial-failure import emits sync intents only for successfully created/updated rows.
4. Downstream sync failure remains observable and retryable without corrupting import idempotency behavior.
5. Agent retrieval returns the expected grounded product for messages like “repeat the METRO yogurt” after an import refresh.
6. Worker crash after claiming a row can be recovered by lease expiry or explicit retry logic.

## SpecFlow Analysis

The origin is strong on the product decision but leaves several execution gaps that this plan closes:

- **Trigger ambiguity:** the brainstorm mentions both post-extract webhook and Supabase trigger; the plan resolves this to post-persistence import as the primary trigger.
- **Ownership ambiguity:** the brainstorm spans two repos; the plan makes `invproc` the producer of freshness signals and the agent repo the owner of retrieval/runtime behavior.
- **Failure ambiguity:** the brainstorm identifies sync complexity but does not define retry or observability; this plan requires explicit sync state and replay paths.
- **Staleness ambiguity:** the brainstorm recommends re-embedding on import but does not address unchanged rows; this plan adds snapshot hashing.
- **Deletion/update parity:** the brainstorm assumes create/update flows; this plan calls out future tombstone/delete handling so vector state cannot drift silently.

## Acceptance Criteria

### Functional Requirements

- [ ] Plan and implementation use the origin decision that RAG is the chosen approach for the WhatsApp orders agent (see origin: `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md`).
- [ ] `invproc` emits a catalog-sync signal only after successful product persistence, not after extraction preview alone.
- [ ] The sync payload includes enough stable identity to upsert vectors deterministically (`product_id` plus snapshot/version/hash).
- [ ] The downstream consumer refreshes embeddings for created/updated products using the canonical embedding text format.
- [ ] The WhatsApp agent retrieves top-K matching products and injects them as grounding context before generation.
- [ ] The first implementation uses a durable sync table as the default transport, with webhook delivery explicitly deferred unless operationally justified.

### Non-Functional Requirements

- [ ] Import latency does not materially regress because embedding generation is decoupled from the request path.
- [ ] Sync failures are observable with durable status and retry support.
- [ ] Duplicate imports do not create duplicate downstream work when product snapshots are unchanged.
- [ ] Service boundaries remain clean: no retrieval orchestration or conversation logic is added to this repo.
- [ ] Query and catalog embeddings always use the same model identifier.
- [ ] Vector search can evolve from exact search to indexed search without changing the sync contract.

### Quality Gates

- [ ] `python -m ruff check src/ tests/`
- [ ] `python -m mypy src/`
- [ ] `python -m pytest -q`
- [ ] Coverage remains at or above the repo’s `80%` fail-under threshold.
- [ ] Any merge-ready PR includes exactly one required change label and the matching evidence section from the PR template.

## Success Metrics

- Retrieval freshness: newly imported or updated products become queryable within the agreed sync window.
- Retrieval quality: representative order queries return a correct product in top 5 at an acceptable rate.
- Hallucination reduction: observed agent mistakes around wrong product names/codes/prices decrease materially against current baseline.
- Operational reliability: sync failures are visible and recoverable without manual catalog reconstruction.
- Search quality tuning: threshold and retrieval mode choices are based on a real evaluation set of ordering phrases, not guesses.

## Dependencies & Prerequisites

- Supabase product persistence path is available and remains the source of truth.
- A downstream place exists to store vectors (`pgvector`) as assumed in the origin.
- The separate WhatsApp/agent repo is available for consumer-side work.
- Shared secret/config or shared database access pattern is agreed if webhook transport is used.

## Risk Analysis & Mitigation

- **Risk: sync tied to the wrong lifecycle event.**
  Mitigation: trigger only after successful import persistence.
- **Risk: embedding churn on every import.**
  Mitigation: compare snapshot hashes before enqueueing refresh work.
- **Risk: backend grows agent-specific logic.**
  Mitigation: keep producer contract narrow; no query embedding or retrieval code in `invproc`.
- **Risk: silent divergence between products and vectors.**
  Mitigation: durable sync status, replay tooling, and reconciliation checks.
- **Risk: webhook outages or transient OpenAI failures.**
  Mitigation: async queue/table-based retries preferred over synchronous outbound calls.
- **Risk: worker rows get stuck in `processing`.**
  Mitigation: use claim timestamps / retry windows so abandoned work becomes reclaimable.

## Immediate Next Decision

The next implementation decision should be:

**Define the sync-table schema and worker contract first.**

Do not start with webhook signing, HTTP retries, or hybrid retrieval. Those are second-order concerns compared with getting the persistence boundary, replay behavior, and ownership split correct.

## Resource Requirements

- Backend engineer for `invproc` sync producer changes
- Agent/frontend engineer for retrieval consumer and prompt integration
- Shared access to Supabase schema management
- Test fixtures or sampled catalog/order utterances for evaluation

## Future Considerations

- Add order-history retrieval as a second corpus once catalog grounding is stable.
- Add business-rule retrieval (minimums, supplier notes, pricing tiers) as a separate indexed source instead of overloading product vectors.
- Consider re-ranking if plain cosine retrieval proves weak for multilingual or abbreviation-heavy product names.
- Add reconciliation jobs that compare product change history with vector sync history.
- If the catalog remains relatively small, validate whether exact vector search stays operationally simpler before adding approximate vector indexes.

## External Best-Practice Validation

The plan direction still looks right after external validation, with these refinements:

- **Async sync is the safer default.** Semantic-search guidance fits a design where embeddings are stored and refreshed separately from generation time, which reinforces keeping embedding work off the import request path.
- **Model parity is mandatory.** Supabase warns that embeddings created by different models should not be compared, so the implementation should persist model/version metadata with each embedding snapshot.
- **Cosine is the safest initial metric.** Supabase recommends cosine distance as a safe default and notes inner product can be faster when embeddings are known to be normalized.
- **Threshold and top-K should be tuned.** Supabase examples present `match_threshold` and result count as application parameters, which supports treating them as evaluation inputs rather than permanent constants.
- **Indexing should follow scale.** Supabase recommends adding vector indexes as the database grows, which fits a phased rollout: exact search first if acceptable, indexed search when latency requires it.
- **Hybrid retrieval is a realistic fit for this use case.** Wholesale ordering queries often mix fuzzy natural language with exact brand, barcode, or SKU references, so hybrid retrieval is worth validating during evaluation.

## Documentation Plan

- Update `README.md` if this repo gains explicit catalog-sync configuration or webhook behavior.
- Add a `docs/solutions/` entry after implementation capturing the chosen transport and any sync/retry lessons.
- Document cross-repo ownership and operational runbook in both repos.
- Phase 1 contract locked in `docs/contracts/2026-03-20-rag-catalog-sync-contract.md`.

## Sources & References

### Origin

- **Origin document:** `docs/brainstorms/2026-03-13-rag-whatsapp-orders-agent-evaluation.md` — key decisions carried forward: use RAG for large catalog scale, use `text-embedding-3-small`, use Supabase/pgvector, keep this repo focused on freshness hooks rather than query-time retrieval.

### Internal References

- `src/invproc/models.py:9` — core product contract and why sync concerns should stay out of domain models.
- `src/invproc/import_service.py:176` — import write path and strongest hook point for post-persistence sync.
- `src/invproc/api.py:149` — `/extract` is extraction-oriented and should not be the primary sync trigger.
- `src/invproc/config.py:33` — configuration boundary for any new sync transport settings.
- `docs/newcomer-guide.md` — layered architecture and request/data flow conventions.
- `docs/solutions/architecture-issues/pydantic-model-layer-violation.md` — preserve clean boundaries between models and infrastructure.
- `docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md` — use DI/lifespan patterns for new side-effectful resources.
- `docs/solutions/workflow-issues/strict-quality-gates-for-prs-development-workflow-20260217.md` — required quality gates and PR evidence policy.

### External References

- Supabase semantic search docs: [https://supabase.com/docs/guides/ai/semantic-search](https://supabase.com/docs/guides/ai/semantic-search)

### Related Work

- `docs/brainstorms/2026-02-12-invoice-product-category-autofill-brainstorm.md` — adjacent product-catalog enrichment thinking.
