# Project Status

Last updated: 2026-03-29

This is the control-tower view for the repo.

Use this file to answer:
- what is currently active
- what was completed recently
- what should happen next

Canonical detail still lives in:
- `docs/plans/` for implementation plans
- `docs/contracts/` for locked behavior and ownership boundaries
- `docs/solutions/` for solved problems and learnings

## Current Priorities

1. Finish RAG Phase 5 validation and quality tuning.
2. Decide and implement the hybrid sync/async extraction path for slow invoices.
3. Keep project-control docs aligned with shipped work so priority questions do not require manual reconciliation across many files.

## Active Work

### 1. RAG eval harness and retrieval-quality validation

Status: Active

Why it matters:
- The backend RAG pipeline is already live and materially improved.
- Phase 5 is still open at the validation/tuning layer.
- Broad category-intent retrieval is still weaker than the tea-specific fixes that just shipped.

Canonical files:
- [docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md)
- [docs/plans/2026-03-27-001-feat-rag-eval-harness-regression-reporting-plan.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-27-001-feat-rag-eval-harness-regression-reporting-plan.md)
- [docs/contracts/2026-03-20-rag-catalog-sync-contract.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts/2026-03-20-rag-catalog-sync-contract.md)

Current gaps:
- cross-category eval coverage is still weak and partly ad hoc
- some Phase 5 acceptance items in the main RAG plan are still unchecked
- generic category queries such as `alcool`, `legume`, `cereale`, `bauturi` still need better retrieval quality

Recommended next step:
- build and save a cross-category eval fixture/baseline, then tune retrieval against it

### 2. Hybrid sync/async extraction contract

Status: Active

Why it matters:
- representative invoices still take too long for a clean synchronous frontend experience
- this is the clearest next major product capability after the recent RAG improvements

Canonical file:
- [docs/plans/2026-03-27-002-feat-hybrid-extract-sync-async-plan.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-27-002-feat-hybrid-extract-sync-async-plan.md)

Current gap:
- the plan exists, but the feature has not been implemented yet

Recommended next step:
- decide whether this becomes the next major feature immediately after RAG Phase 5 tightening, or in parallel if the RAG work is kept narrow

## Recently Completed

### March 28 RAG retrieval follow-up

Completed:
- embedding text enrichment for tea-family and other safe inferred cases
- canonical category backfill for safe null-category products
- latest synced embedding selection per product during retrieval
- API naming alignment around `match_threshold`

Canonical files:
- [docs/plans/2026-03-28-001-feat-rag-embedding-text-enrichment-plan.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-28-001-feat-rag-embedding-text-enrichment-plan.md)
- [docs/plans/2026-03-28-002-feat-rag-category-backfill-and-latest-sync-selection-plan.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-28-002-feat-rag-category-backfill-and-latest-sync-selection-plan.md)
- [docs/solutions/architecture-issues/rag-embedding-enrichment-category-backfill-latest-sync-selection-20260328.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-embedding-enrichment-category-backfill-latest-sync-selection-20260328.md)

User-visible outcome:
- `ceai de fructe` now maps back to tea products instead of fruit-snack distractors in the target case

### March 29 PM/documentation alignment

Completed:
- README updated for current backend RAG workflow
- RAG contract updated to reflect current embedding text and retrieval semantics
- March 28 plans marked completed

Canonical files:
- [README.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/README.md)
- [docs/contracts/2026-03-20-rag-catalog-sync-contract.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts/2026-03-20-rag-catalog-sync-contract.md)

## Next Up

### Recommended immediate next task

Create a proper cross-category RAG benchmark.

Scope:
- add 30-50 category-level and broad-intent retrieval cases
- save a trustworthy baseline
- use it to evaluate remaining Phase 5 RAG tuning work

Why first:
- it closes the current “what exactly is still weak?” gap with evidence
- it aligns with the active RAG eval-harness plan
- it reduces the chance of tuning retrieval based only on ad hoc manual checks

### Recommended next major feature after that

Implement the hybrid sync/async extraction contract.

Why:
- it is the clearest open product feature in the active plan set
- it addresses a user-facing latency problem rather than another internal quality refinement

## Decision Notes

- Do not treat `README.md` as the roadmap. It should remain operator/developer-facing.
- Do not treat `docs/solutions/` as the priority queue. It is historical memory.
- Use this file as the first place to check before starting new planning work.
- When a plan is completed or priorities change, update this file in the same PR if the change affects current work sequencing.

## Quick Rules

- If work is in progress: the controlling plan should be `status: active` and listed under `Active Work`.
- If work is done: the plan should be `status: completed` and moved to `Recently Completed` if it matters to current context.
- If someone asks “what next?”: answer from this file first, then follow links into the controlling plan.
