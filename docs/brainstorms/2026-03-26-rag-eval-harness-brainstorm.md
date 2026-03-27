---
date: 2026-03-26
topic: rag-eval-harness
---

# RAG Eval Harness

## Problem Frame

The repository already has the beginnings of a RAG evaluation flow, including fixture-based query sets, baseline snapshots, CLI/API eval surfaces, and quality notes in contracts and solution docs. What is missing is a more coherent product around those pieces: something the team can use confidently when tuning retrieval behavior and deciding whether a change actually improved or degraded quality.

The main need is not generic debugging. The higher-priority need is safe comparison: when search logic, thresholds, embeddings, ranking, or catalog shaping change, the team wants to see whether retrieval quality got better or worse in a way that is easy to understand. Today the ingredients exist, but the experience is still closer to a technical tool than a clear decision-making workflow.

## Requirements

- R1. The first version must focus on RAG/search evaluation, not invoice extraction evaluation.
- R2. The primary use case must be comparing search quality before and after changes during normal development work.
- R3. The first version must support both top-1 and top-5 evaluation as first-class metrics, with neither treated as disposable context.
- R4. The output must include a concise summary plus case-by-case details.
- R5. For any query that worsens, the detailed report must show the expected match, the actual first result, and the top results list so the ranking change is understandable.
- R6. The first version must reuse the existing fixture set as its evaluation dataset rather than depending on production logs or a brand-new manually curated corpus.
- R7. Each evaluation run must be saved as a timestamped snapshot so results can be compared over time.
- R8. The initial workflow should be optimized for local/manual use during development. CI or merge-blocking use is a follow-on step, not the primary v1 requirement.

## Success Criteria

- A developer can run one evaluation flow before and after a retrieval change and quickly tell whether quality improved, regressed, or stayed roughly the same.
- The result is understandable without reading raw internals only; the summary provides a fast signal, and the case details explain important changes.
- The workflow preserves history automatically so the team can compare runs over time instead of relying on memory or ad hoc copy/paste.
- The first version feels like a clearer, more trustworthy wrapper around the repo's existing eval capability rather than a parallel system.

## Scope Boundaries

- Out of scope for v1: invoice extraction eval as a primary surface.
- Out of scope for v1: production-log ingestion or automatic creation of eval queries from live traffic.
- Out of scope for v1: merge-blocking CI policy tied to eval outcomes.
- Out of scope for v1: a second independently curated “must-win” dataset beyond the current fixture set.
- Out of scope for v1: positioning this mainly as a debugging-only tool.

## Key Decisions

- Focus on RAG first: the existing repo already has enough retrieval-eval structure to justify tightening and productizing it before broadening to extraction.
- Compare-first positioning: the strongest value is helping the team judge whether tuning changes helped, not merely surfacing raw scores.
- Balanced metrics: top-1 and top-5 should both matter, because they reveal different retrieval behaviors and help avoid overfitting to a single metric.
- Existing fixtures first: starting from the current fixture set keeps v1 grounded, lighter-weight, and aligned with existing repo assets.
- Automatic snapshots: every run should become a saved point-in-time result so improvement and regression can be viewed over time.

## Dependencies / Assumptions

- The existing fixture set is good enough to serve as the starting dataset, even if it may need cleanup or incremental strengthening later.
- The current CLI/API eval surfaces and baseline storage can be evolved rather than replaced wholesale.
- The current solution/contract docs remain a valid source of expected quality framing for RAG behavior.

## Outstanding Questions

### Deferred to Planning

- [Affects R2][Technical] What is the right user-facing workflow for comparison: compare current run to the latest snapshot automatically, allow explicit selection of a prior snapshot, or both?
- [Affects R4][Technical] What output format should be the primary surface for v1: JSON only, a human-readable terminal report plus JSON artifact, or a compact markdown summary generated from the run?
- [Affects R5][Needs research] What is the best shape for “top results list” so it is informative without becoming noisy?
- [Affects R7][Technical] Where should snapshots live, and what metadata should be included to make future comparisons meaningful?
- [Affects R8][Technical] How much of the existing eval implementation can be reused cleanly versus needing restructuring first?

## Next Steps

-> /prompts:ce-plan for structured implementation planning
