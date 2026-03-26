---
title: refactor: Split RAG subsystem by workflow boundaries
type: refactor
status: completed
date: 2026-03-26
origin: docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md
---

# refactor: Split RAG subsystem by workflow boundaries

## Overview

The RAG subsystem currently works, but too much coordination logic is concentrated in [`src/invproc/rag.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py) and then reassembled again inside the CLI and API layers. The refactor should preserve the current caller-facing contract while making sync, retrieval, evaluation, and transport wiring independently understandable and testable.

This plan carries forward the workflow-first split, stable external edges, and selective type redistribution decisions from the origin brainstorm (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).

## Problem Statement / Motivation

Today one module owns:

- embedding client creation and mock fallback in [`src/invproc/rag.py:99`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L99)
- sync worker logic and embedding upsert metadata assembly in [`src/invproc/rag.py:247`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L247)
- retrieval branching, concurrent hybrid dispatch, and threshold filtering in [`src/invproc/rag.py:332`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L332)
- evaluation models, fixture loading, and serialization helpers in [`src/invproc/rag.py:419`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L419)
- builder functions used by both transport layers in [`src/invproc/rag.py:622`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L622)

Meanwhile CLI and API still contain RAG-specific assembly and orchestration:

- CLI resource caching and RAG service construction in [`src/invproc/cli.py:313`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L313) and [`src/invproc/cli.py:339`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L339)
- API worker and retrieval dependency builders in [`src/invproc/api.py:188`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py#L188) and [`src/invproc/api.py:200`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py#L200)

That structure makes safe change harder than it needs to be. A retrieval change risks touching sync code, an eval change risks dragging transport serializers, and most focused tests still live in the broad integration-heavy [`tests/test_rag_backend.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_backend.py).

## Proposed Solution

Refactor the RAG code into a workflow-first package under `src/invproc/rag/` while keeping the CLI commands, API endpoints, repository protocol, and response payloads materially stable.

Important migration constraint: once `src/invproc/rag/` exists, `invproc.rag` resolves to the package, not the old `src/invproc/rag.py` module. The package entrypoint must therefore become the authoritative compatibility surface during the refactor. This plan assumes:

- `src/invproc/rag.py` is replaced by `src/invproc/rag/__init__.py` as the stable import surface
- moved symbols are re-exported from `src/invproc/rag/__init__.py` during the migration
- repo imports are updated incrementally, but callers that still import from `invproc.rag` continue to work through package re-exports until the final cleanup step

Proposed target module map for the first refactor pass:

- `src/invproc/rag/__init__.py`
  Stable package entrypoint and temporary compatibility re-exports for current imports such as `invproc.rag`.
- `src/invproc/rag/sync.py`
  `CatalogSyncWorker`, retry scheduling, canonical embedding text assembly, sync metadata assembly, and queue status helpers.
- `src/invproc/rag/retrieval.py`
  `CatalogRetrievalService`, `EmbeddingClient`, `OpenAIEmbeddingClient`, `cosine_similarity`, `rrf_merge`, and retrieval result types.
- `src/invproc/rag/eval.py`
  `CatalogRagEvaluator`, eval case/result types, mode comparison, and fixture loading.
- `src/invproc/rag/transport.py`
  JSON serializers plus `build_rag_worker` and `build_retrieval_service` so CLI/API can stay thin and share one wiring layer.

This preserves the workflow-first top level requested in the origin while avoiding premature sub-splitting. If one of these workflow modules is still too large after the first pass, that module can be subdivided in a follow-up refactor rather than front-loading file indirection (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).

## Technical Considerations

- Preserve existing repository/storage contracts in [`src/invproc/repositories/base.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/base.py), [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py), and [`src/invproc/repositories/supabase.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/supabase.py). This refactor should consume those interfaces, not redesign them.
- Keep the canonical snapshot and sync ownership split intact with [`src/invproc/catalog_sync.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/catalog_sync.py) remaining the producer-side boundary and the new `rag/sync/` area remaining the consumer-side boundary (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).
- Maintain existing CLI/API behavior and payload shape for:
  - `rag ingest-invoice`, `rag sync-pending`, `rag query`, `rag eval`, `rag status` in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py)
  - `/internal/rag/import`, `/internal/rag/sync-pending`, `/internal/rag/query`, `/internal/rag/status`, `/internal/rag/eval` in [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py)
- Keep hybrid retrieval behavior unchanged, including concurrent semantic and lexical dispatch and RRF merging documented in the repo learnings.
- Preserve current import compatibility through `src/invproc/rag/__init__.py` re-exports during the migration, and only remove re-exports after repo-wide consumers have been updated.

## System-Wide Impact

- **Interaction graph**: `InvoiceImportService` emits sync intents through [`src/invproc/catalog_sync.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/catalog_sync.py), worker code claims and updates queue rows through repository methods, retrieval code reads repository-native lexical/vector search, and CLI/API transport layers serialize results. The refactor must preserve these handoff points while moving ownership of each workflow into smaller modules.
- **Error propagation**: embedding failures currently flow through `CatalogSyncWorker.process_one()` into `mark_product_sync_failed()` and later surface through status/eval tooling. That behavior must remain visible after the split, with no silent swallowing introduced between workflow packages.
- **State lifecycle risks**: sync rows, embedding rows, and query/eval responses all depend on stable metadata shape. If metadata assembly or threshold behavior moves, tests must prove no partial state or response drift appears across memory and Supabase backends.
- **API surface parity**: any refactor that changes helper locations must keep CLI and API using the same builder/serializer layer so they do not drift. The repo already has learnings warning about parity regressions and transport-specific wiring splits.
- **Integration test scenarios**:
  - `tests/test_rag_backend.py` import -> sync -> query still succeeds with mock resources
  - API `/internal/rag/import` followed by `/internal/rag/query` still exercises the app-owned graph
  - CLI `rag eval --all-modes` and API `/internal/rag/eval` still agree on result structure
  - status snapshots still expose repeated failures and queue ages after sync exceptions
  - imports from `invproc.rag` used by [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py), [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py), [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py), and [`tests/test_rag_backend.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_backend.py) continue to resolve during the migration

## Implementation Phases

### Phase 1: Establish the package entrypoint and compatibility surface

- Replace the single-file `src/invproc/rag.py` layout with a package entrypoint at `src/invproc/rag/__init__.py`.
- Make `src/invproc/rag/__init__.py` the only compatibility surface for `invproc.rag` imports.
- Add explicit characterization tests for the currently imported symbols used by CLI, API, repository helpers, and RAG tests before moving behavior between files.

### Phase 2: Move sync ownership into `rag/sync.py`

- Move `CatalogSyncWorker`, retry delay computation, sync status snapshot logic, canonical embedding text assembly, and sync-only metadata helpers into `src/invproc/rag/sync.py`.
- Extract sync-row-to-embedding metadata assembly into a small helper local to the sync workflow so retrieval/eval code no longer shares sync-only concerns.
- Verify producer-side snapshot hash logic stays in [`src/invproc/catalog_sync.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/catalog_sync.py), not duplicated under the RAG package.

### Phase 3: Move retrieval ownership into `rag/retrieval.py`

- Move `CatalogRetrievalService`, embedding client code, ranking helpers, and retrieval result types into `src/invproc/rag/retrieval.py`.
- Keep semantic, lexical, and hybrid orchestration intact, including threaded hybrid dispatch and threshold filtering.
- Ensure memory and Supabase backends remain unchanged except for import updates if they reference moved helpers.
- Remove direct retrieval-specific branching knowledge from CLI/API beyond transport inputs and outputs.

### Phase 4: Move evaluation and transport wiring into workflow modules

- Move `CatalogRagEvaluator`, mode comparison logic, eval parsing, and eval result types into `src/invproc/rag/eval.py`.
- Move builders and serializers into `src/invproc/rag/transport.py`.
- Update CLI/API to depend on the shared builder/serializer layer only, so transport surfaces become thin orchestration wrappers over the refactored core (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).

### Phase 5: Remove transitional re-exports when no longer needed

- Once repo-wide imports no longer depend on transitional re-exports from `src/invproc/rag/__init__.py`, remove only the extra compatibility exports and keep the package entrypoint minimal.
- Do not add a second facade layer or temporary alias module unless implementation proves it is truly necessary.

## Alternative Approaches Considered

- Keep one `rag.py` file and only extract helper functions.
  Rejected because it reduces file size without meaningfully separating sync, retrieval, evaluation, and transport ownership (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).
- Split strictly by artifact type, such as `models.py`, `services.py`, `serializers.py`, `utils.py`.
  Rejected because it recreates the current mixed-responsibility problem under more folders and weakens the workflow-first mental model chosen in the brainstorm.
- Rewrite transport and repository interfaces during the same refactor.
  Rejected because the goal is internal change safety, not public contract churn.
- Introduce a deeply nested package map on day one.
  Rejected because the first refactor should optimize for clearer ownership with minimal new indirection, not create a mini-framework of small files before there is evidence they are needed.

## Acceptance Criteria

- [ ] The RAG subsystem is reorganized under a workflow-first package structure where sync, retrieval, evaluation, and transport wiring no longer share one main module.
- [ ] CLI commands in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py) keep their existing behavior and output shape from a caller perspective.
- [ ] API endpoints in [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) keep their existing request/response behavior from a caller perspective.
- [ ] Repository and storage contracts in [`src/invproc/repositories/base.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/base.py) remain compatible with both memory and Supabase implementations.
- [ ] Current imports from `invproc.rag` continue to resolve for the known in-repo consumers during the migration, especially in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py), [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py), [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py), and [`tests/test_rag_backend.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_backend.py).
- [ ] CLI and API payload shapes for query, eval, sync, and status remain byte-for-byte compatible where tests already assert structure, or are covered by explicit characterization tests before and after the refactor.
- [ ] Types that are only local to one workflow area move closer to that workflow, while truly shared contracts remain shared (see origin: `docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`).
- [ ] Retrieval logic can be changed without editing sync worker code or eval fixture parsing code.
- [ ] Evaluation logic can be changed without editing retrieval orchestration or transport serializer code.
- [ ] Focused tests are added or reorganized so sync, retrieval, and evaluation can be validated with smaller setup surfaces than the current broad test file requires.
- [ ] Existing high-value regression coverage continues to pass, including:
  - `tests/test_rag_backend.py`
  - `tests/test_cli.py`
  - `tests/test_api.py`
- [ ] Quality gate commands pass before merge:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`

## Success Metrics

- Smaller, workflow-owned modules replace the current single large RAG module.
- A developer can locate sync, retrieval, and evaluation behavior without loading unrelated workflow code.
- The number of tests that need to bootstrap end-to-end RAG context for focused behavior changes decreases.
- No behavior regressions are introduced across CLI/API/query/eval/status surfaces.

## Dependencies & Risks

- **Import-path compatibility risk**: internal tests or helpers may import directly from `invproc.rag`. Mitigation: keep a compatibility facade and migrate imports incrementally.
- **Import-path compatibility risk**: internal tests or helpers may import directly from `invproc.rag`. Mitigation: make `src/invproc/rag/__init__.py` the explicit compatibility surface, characterize those imports early, and remove transitional re-exports only after repo-wide updates land.
- **Transport drift risk**: CLI and API could start assembling different service graphs during the move. Mitigation: centralize builders and serializers in a shared transport layer and update both surfaces together.
- **Hidden dependency risk**: there may be undocumented internal imports of `invproc.rag` symbols outside the obvious files. Mitigation: run repo-wide searches before removing re-exports and keep removals for the final phase.
- **Large-churn review risk**: moving everything in one patch will obscure real behavior changes. Mitigation: phase the work so pure helpers move first, then sync, then retrieval/eval, then transport cleanup.

## SpecFlow Notes

The main flow gaps to explicitly cover during implementation are:

- sync failure followed by status inspection still reports repeated failures correctly
- hybrid retrieval still uses the same ranking and threshold semantics after module moves
- API and CLI continue to share one retrieval path rather than diverging through duplicated builders
- eval fixtures with `expected_product_id` and `expected_name` both keep current matching behavior
- import compatibility and payload-shape characterization tests exist before transitional re-exports are removed

These are reflected in the acceptance criteria and phase sequencing above.

## Sources & References

- **Origin document:** [`docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/brainstorms/2026-03-26-rag-subsystem-refactor-brainstorm.md) — carried forward the aggressive-internal/stable-external stance, workflow-first structure, hybrid local/shared type strategy, and the deferred planning questions this plan answers.
- Similar implementations:
  - [`src/invproc/rag.py:247`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L247)
  - [`src/invproc/rag.py:332`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py#L332)
  - [`src/invproc/cli.py:339`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L339)
  - [`src/invproc/api.py:188`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py#L188)
- Internal learnings:
  - [`docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md)
  - [`docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md)
  - [`docs/solutions/integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md)
  - [`docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md)
  - [`docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md)
  - [`docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md)
- Contract reference:
  - [`docs/contracts/2026-03-20-rag-catalog-sync-contract.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts/2026-03-20-rag-catalog-sync-contract.md)
