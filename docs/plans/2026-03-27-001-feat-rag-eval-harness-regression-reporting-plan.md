---
title: feat: Add compare-first RAG eval harness reporting
type: feat
status: active
date: 2026-03-27
origin: docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md
---

# feat: Add compare-first RAG eval harness reporting

## Overview

The repository already has the core ingredients for retrieval evaluation: fixture-driven cases in [`tests/fixtures/rag_queries.json`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/fixtures/rag_queries.json), evaluator logic in [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py), CLI and API surfaces in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py) and [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py), and timestamped baseline notes in [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md). What is missing is a coherent compare-first workflow that helps a developer answer the real question: did this retrieval change improve quality, regress it, or simply move results around?

This plan carries forward the brainstorm's RAG-first scope, compare-first positioning, dual top-1/top-5 emphasis, fixture reuse, and automatic snapshot requirement (see origin: `docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md`).

## Problem Statement / Motivation

Current evaluation behavior is useful but still too raw for routine decision-making:

- [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py) returns aggregate hit counts and per-case result rows, but it does not classify deltas against a prior run or explain regressions in a way a developer can scan quickly.
- [`src/invproc/cli.py:599`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L599) can run single-mode or all-mode evals, but it prints raw JSON only and does not guide "before vs after" comparison.
- [`src/invproc/api.py:440`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py#L440) exposes eval execution, but not baseline comparison or snapshot selection.
- [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md) documents snapshot intent and thresholds, but snapshot storage is still more convention than productized workflow.

That gap matters because retrieval tuning is already active in this repo. The solution docs show recurring quality work around hybrid search, thresholding, fixture annotations, and CLI/API parity. Without a clearer harness, developers must mentally diff raw outputs or rely on memory, which makes regressions easy to miss and improvements harder to trust.

## Research Summary

### Repo Conventions

- Feature plans in this repo use frontmatter with `title`, `type`, `status`, `date`, and `origin`, then a narrative plan with phases, risks, and verification.
- AGENTS quality gates require:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`
- PR policy requires one `change:*` label and matching evidence section in the PR body. This feature should ultimately ship under `change:feature` with concrete test evidence.

### Existing Eval Assets

- Core evaluator and fixture loading live in [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py).
- Eval serialization lives in [`src/invproc/rag/transport.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/transport.py).
- CLI eval entrypoint already supports `--all-modes` and `--top-k` in [`src/invproc/cli.py:580`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L580).
- Baseline history expectations already exist in [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md).

### Institutional Learnings

- [`docs/solutions/integration-issues/dual-repo-field-omission-api-cli-path-divergence.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/dual-repo-field-omission-api-cli-path-divergence.md) warns that shared eval schema logic must be centralized so CLI and API do not diverge when fixture annotations evolve.
- [`docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md) establishes that eval is a first-class backend capability and that API/CLI parity matters.
- [`docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md) and [`docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md) show that retrieval quality is shaped by ranking mode and thresholds, so snapshots need enough metadata to explain which conditions produced a result.

### External Research Decision

Skipped. The repo already has a concrete eval implementation, project-specific baseline guidance, and recent learnings directly on the target workflow. This feature does not require new third-party APIs or unstable external platform behavior, so local context is stronger than generic external guidance.

## Proposed Solution

Add a compare-first eval harness on top of the existing evaluator instead of replacing it. The first version should:

- keep fixture-driven evaluation as the source dataset (see origin: `docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md`)
- preserve top-1 and top-5 as first-class metrics
- save each run as a timestamped snapshot artifact
- make comparison against a previous snapshot easy by default
- produce both concise summary output and case-level details
- make regressions explainable by showing expected match, actual top result, and the returned ranked list

### Deferred Question Resolutions

- **Comparison workflow**: support both behaviors, but optimize for local use by defaulting to "compare this run to the latest compatible snapshot" while also allowing explicit snapshot selection.
- **Primary output format**: return a human-readable terminal summary plus a persisted JSON artifact. JSON remains the source of truth; the summary is the decision-friendly wrapper.
- **Top results detail shape**: include the top result as a dedicated regression field and keep a bounded `top_results` list with the leading matches and scores. This stays informative without dumping full internal payloads.
- **Snapshot location and metadata**: continue using `docs/eval-baselines/` for human-visible history, and enrich each snapshot with fixture name, mode or comparison mode, timestamp, top-k, threshold, and optional comparison target reference.
- **Reuse vs restructure**: reuse the existing `CatalogRagEvaluator` and serializer foundations, but introduce a comparison/reporting layer rather than forcing comparison logic into transport-only code.

## Technical Design

### Data Model Additions

- Extend eval result modeling in [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py) to carry richer per-case detail needed for comparison:
  - expected identifier/name
  - actual top result
  - bounded top results list
  - hit outcomes for top-1 and top-5
- Add a comparison model that can represent:
  - current snapshot summary
  - baseline snapshot summary
  - metric deltas
  - per-query change classification such as improved, regressed, unchanged, or moved-within-top-k

### Snapshot Storage

- Introduce a snapshot-writing helper that persists timestamped eval artifacts under [`docs/eval-baselines/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/).
- Preserve the existing README as the human-maintained index, but ensure generated snapshot files are self-describing enough to compare directly even if the README is not updated immediately.
- Compatibility rule: existing snapshot files should remain readable, even if new snapshots gain richer metadata.
- Add explicit snapshot metadata fields so auto-comparison can reject incompatible artifacts instead of producing misleading deltas:
  - schema version
  - fixture identifier
  - search mode or comparison mode
  - `top_k`
  - effective match threshold
  - execution environment hint such as `mock` vs real backend when available
  - generated-at timestamp

### CLI Workflow

- Extend the `rag eval` command in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py) to support compare-first behavior without breaking existing raw JSON use:
  - save snapshot by default or via an explicit flag, depending on implementation ergonomics
  - compare against latest compatible snapshot automatically
  - allow explicit `--compare-to <path>`
  - emit a concise summary section followed by detailed regressions
  - retain machine-readable JSON output for automation

### API Workflow

- Keep [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) aligned with CLI behavior by reusing the same comparison helpers and case parsing path.
- Avoid reintroducing inline case construction or duplicate snapshot/comparison logic; the solution doc on eval API divergence is an explicit guardrail here.
- If snapshot persistence is not appropriate for the API's first slice, still expose comparison-capable response shapes so API-triggered evals can participate in the same reporting model.
- Follow the repo's existing parity rule for eval-related behavior: if a comparison parameter or threshold is introduced for one surface, it must be wired through the other in the same change set.

### Serialization

- Extend [`src/invproc/rag/transport.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/transport.py) to serialize richer eval and comparison payloads cleanly.
- Keep serializer boundaries explicit so CLI human formatting does not leak into the API transport layer.

## System-Wide Impact

- **Developer workflow**: retrieval tuning becomes easier to judge because one command can generate both a fresh snapshot and a baseline-aware summary.
- **Docs and artifacts**: `docs/eval-baselines/` becomes a true artifact directory rather than a mostly manual convention.
- **API/CLI parity**: richer eval semantics increase the chance of surface drift, so shared helpers and characterization tests are required.
- **Fixture evolution**: annotated fixture keys like `expected_fail` and `notes` must continue to be ignored safely in all ingestion paths.
- **Backward compatibility**: existing eval callers that expect aggregate hit-rate JSON should continue to work or have a clearly versioned extension path.

## Implementation Phases

### Phase 1: Characterize current eval behavior and artifact expectations

- Add or tighten tests around current evaluator output, fixture loading, and mode comparison behavior in [`tests/test_rag_backend.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_backend.py).
- Add characterization coverage for the current CLI/API eval payload shape so later reporting upgrades do not accidentally break existing consumers.
- Add snapshot fixture tests for reading older and newer artifact shapes if the file schema will evolve.
- Add a regression test that proves annotated fixture keys are still ignored consistently in both file-loaded and API-inline case paths.

### Phase 2: Add richer eval detail and comparison models

- Refactor [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py) so each case records the expected match, actual top result, and top results list needed to explain regressions.
- Add summary-comparison helpers that compute metric deltas and classify each case outcome relative to a baseline snapshot.
- Keep evaluation execution distinct from snapshot persistence and terminal rendering.

### Phase 3: Add snapshot persistence and loading helpers

- Add helpers to save timestamped snapshots under [`docs/eval-baselines/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/).
- Add helpers to locate the latest compatible snapshot for a given fixture/mode combination.
- Define compatibility rules for comparing snapshots generated with different but still readable schema versions.

### Phase 4: Ship compare-first CLI reporting

- Update `rag eval` in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py) to:
  - execute eval
  - optionally save the snapshot
  - auto-select a baseline when requested or by default
  - print a concise summary with deltas
  - print regression-focused case details
- Keep a raw JSON pathway for scripts and future CI use.
- Prefer a report format that starts with the current-vs-baseline summary and then lists only regressed or materially changed cases first, with the full detailed list still available in JSON.

### Phase 5: Align API response shape and documentation

- Update [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) and [`src/invproc/rag/transport.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/transport.py) to expose the richer eval payload safely.
- Update [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md) with the new workflow, artifact expectations, and comparison semantics.
- If the implementation introduces user-facing CLI flags, document those in [`README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/README.md) or another appropriate usage surface.

## Alternative Approaches Considered

- Adopt Promptfoo now.
  Rejected because the repo's current need is retrieval comparison quality, not LLM-judge evaluation, and an existing decision record already recommends deferring Promptfoo for this stage.
- Keep raw JSON only and rely on manual diffing.
  Rejected because it preserves the current usability problem and fails the compare-first success criteria from the origin brainstorm.
- Store snapshots outside the repo or only in temp files.
  Rejected because the repo already uses `docs/eval-baselines/` as the canonical visible history, and local/manual developer use is the v1 priority.
- Build CI gating into the first slice.
  Rejected because the brainstorm explicitly marks merge-blocking CI usage as follow-on work, not the v1 goal.

## Acceptance Criteria

- [ ] A developer can run one local eval flow and receive both a concise summary and case-by-case details for the current run.
- [ ] The harness treats top-1 and top-5 as first-class metrics in both saved artifacts and presented summaries.
- [ ] Each eval run can be saved as a timestamped snapshot under [`docs/eval-baselines/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/).
- [ ] The CLI can compare a current run against a previous snapshot, defaulting to the latest compatible snapshot or allowing explicit selection.
- [ ] For each regressed query, the detailed output includes the expected match, the actual first result, and a readable top-results list.
- [ ] Fixture-based datasets in [`tests/fixtures/rag_queries.json`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/fixtures/rag_queries.json) and [`tests/fixtures/rag_queries_unit.json`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/fixtures/rag_queries_unit.json) remain usable without schema crashes when annotation fields are present.
- [ ] CLI and API eval flows share the same case-parsing and comparison logic rather than duplicating it.
- [ ] Existing raw eval behavior remains available in a machine-readable form for automation and future CI use.
- [ ] Documentation explains how to generate, save, and compare snapshots locally.
- [ ] Required quality gates pass:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`

## Success Metrics

- Developers can tell within one command whether retrieval quality improved, regressed, or stayed roughly flat.
- Regression details are understandable without reading raw internals only.
- Snapshot history becomes durable enough that tuning decisions no longer depend on memory or ad hoc copy/paste.
- The feature feels like a stronger wrapper around the current eval system, not a parallel evaluation framework.

## Verification Strategy

- **Unit coverage**
  - evaluator returns richer per-case details without changing hit-rate math
  - snapshot writer and loader round-trip the new artifact shape
  - compatibility checks reject mismatched fixture/mode/environment artifacts
  - comparison classifier marks improved, regressed, and unchanged cases correctly
- **CLI/API parity coverage**
  - the same annotated cases produce equivalent parsed inputs through file and inline request paths
  - newly introduced comparison or threshold parameters propagate through both CLI and API surfaces
  - machine-readable JSON remains stable enough for scripted consumption
- **Workflow validation**
  - first run with no baseline still succeeds and saves a snapshot
  - second run against the same fixture can auto-select the latest compatible snapshot
  - explicit snapshot selection gives a clear error when the file is missing, malformed, or incompatible
- **Regression validation**
  - existing threshold expectations from [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md) remain inspectable after the harness changes
  - `tests/fixtures/rag_queries_unit.json` continues to load and remains suitable for mock/local verification

## Dependencies & Risks

- **Surface drift risk**: CLI reporting and API payloads may diverge if they do not share helpers. Mitigation: centralize case parsing, comparison logic, and serialization.
- **Artifact schema drift risk**: snapshot files may evolve and become unreadable across versions. Mitigation: version or shape snapshots deliberately and test backward compatibility.
- **Noise risk**: top-results details could become too verbose. Mitigation: bound the reported list and emphasize regressions first.
- **False confidence risk**: compare-against-latest may compare to an unrelated fixture or mode. Mitigation: only auto-select compatible snapshots using fixture and mode metadata.
- **Scope creep risk**: CI gating, new datasets, or extraction-eval ambitions could bloat the slice. Mitigation: keep v1 limited to retrieval compare-first workflow (see origin: `docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md`).

## SpecFlow Notes

Key flows and edge cases to cover during implementation:

- running eval with no prior snapshot still produces a useful current-run summary
- running eval with a compatible prior snapshot produces delta summaries and query classifications
- selecting an explicit snapshot path that is unreadable or incompatible yields a clear failure
- annotated fixture cases continue to parse in both CLI and API flows
- `--all-modes` behavior either remains explicitly unsupported for comparison in v1 or produces a defined multi-mode comparison artifact instead of ambiguous output
- unit/mock runs and real-data runs do not accidentally compare across incompatible datasets
- human-readable CLI output and machine-readable JSON remain available without conflating the two

These gaps are intentionally folded into the phase sequencing and acceptance criteria above.

## Sources & References

- **Origin document:** [`docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/brainstorms/2026-03-26-rag-eval-harness-brainstorm.md)
- Existing evaluator:
  - [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py)
- Existing transport serialization:
  - [`src/invproc/rag/transport.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/transport.py)
- Existing CLI/API eval surfaces:
  - [`src/invproc/cli.py:580`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py#L580)
  - [`src/invproc/api.py:440`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py#L440)
- Snapshot guidance:
  - [`docs/eval-baselines/README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/eval-baselines/README.md)
- Related learnings:
  - [`docs/solutions/integration-issues/dual-repo-field-omission-api-cli-path-divergence.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/dual-repo-field-omission-api-cli-path-divergence.md)
  - [`docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md)
  - [`docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md)
  - [`docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md)
