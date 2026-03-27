---
date: 2026-03-26
topic: rag-subsystem-refactor
---

# RAG Subsystem Refactor

## Problem Frame

The current RAG subsystem is functional, but too much responsibility is concentrated in a small set of large files, especially `src/invproc/rag.py`, `src/invproc/cli.py`, and `src/invproc/api.py`. The result is not just a readability problem. The larger issue is that sync, retrieval, evaluation, and transport wiring are mixed together, which increases coupling and makes it easier for changes in one area to break another.

The refactor should not be treated as cosmetic file-splitting. The real goal is to make future RAG changes safer and easier to test in isolation, while keeping CLI behavior, API behavior, and repository/storage contracts as stable as possible.

## Requirements

- R1. The refactor must primarily optimize for change safety and testability, with neither treated as secondary.
- R2. The refactor may be aggressive internally and may redesign the internal architecture where needed.
- R3. External behavior should remain broadly stable across the current CLI surfaces, API surfaces, and repository/storage contracts unless a change is clearly justified.
- R4. The subsystem must be split into clearer responsibility boundaries so that sync/embedding lifecycle, retrieval/query behavior, evaluation, and transport wiring are no longer mixed in the same main module.
- R5. CLI and API code should become thinner orchestration layers over the refactored core RAG domain rather than continuing to host substantial RAG coordination logic.
- R6. The refactored structure should be organized primarily by workflow stage, not only by technical artifact type.
- R7. The preferred organization model is hybrid: workflow-based modules at the top level, with smaller technical helpers, serializers, and local types inside each workflow area where useful.
- R8. Shared types should be redistributed selectively: types that are clearly local to one workflow area should move there, while truly cross-cutting contracts may remain shared.
- R9. The refactor must make it easier to test sync, retrieval, and evaluation independently with smaller setup surfaces than the current structure requires.

## Success Criteria

- A developer can change retrieval logic without needing to mentally load sync and evaluation internals at the same time.
- A developer can change evaluation behavior without risking unintended impact on retrieval orchestration or transport wiring.
- Core RAG behavior is covered by more focused tests that exercise smaller units with less incidental setup.
- CLI commands and API endpoints continue to behave consistently from a caller perspective after the refactor.
- The main symptom of “giant files with mixed responsibilities” is materially reduced.

## Scope Boundaries

- Out of scope: redesigning user-facing product behavior for retrieval itself.
- Out of scope: intentional breaking changes to existing CLI/API contracts unless later justified separately.
- Out of scope: changing repository or storage contracts just for stylistic consistency.
- Out of scope: treating this as a purely superficial file move with no architectural improvement.
- Out of scope: broad non-RAG refactors elsewhere in the codebase unless directly required by the RAG split.

## Key Decisions

- Aggressive internal refactor: this should be allowed to reshape internal architecture instead of being constrained to file extraction only.
- Stable edges: the CLI, API, and repository/storage contracts should remain as stable as possible so the refactor pays down internal risk without creating unnecessary external churn.
- Workflow-first structure: the primary organizing concept should be sync, retrieval, and evaluation flows, because that matches the current behavioral seams and failure modes better than purely technical folders.
- Hybrid module strategy: top-level workflow separation should be combined with small local helper/type files inside each area when helpful.
- Selective type redistribution: obviously local types should move closer to their workflow area, but genuinely shared contracts may remain centralized to avoid unnecessary churn.

## Dependencies / Assumptions

- The current behavior is valuable enough that preserving external contracts is the right default.
- Existing tests and docs provide enough behavioral coverage to support a large internal refactor safely.
- The repo is mature enough in its RAG direction that clearer subsystem boundaries will compound value rather than being premature.

## Outstanding Questions

### Deferred to Planning

- [Affects R4][Technical] What exact target module map best reflects the workflow-first split while keeping import paths and ownership clear?
- [Affects R5][Technical] Which RAG coordination responsibilities should stay in CLI/API as transport concerns, and which must move into core services?
- [Affects R8][Technical] Which current dataclasses and serializers are truly cross-cutting versus only incidentally shared because they live in one big module today?
- [Affects R9][Technical] What is the safest migration sequence for moving code while preserving tests and minimizing large mechanical churn in one step?
- [Affects R3][Needs research] Are there any undocumented external dependencies on current import paths or helper functions that should be treated as compatibility constraints?

## Next Steps

-> /prompts:ce-plan for structured implementation planning
