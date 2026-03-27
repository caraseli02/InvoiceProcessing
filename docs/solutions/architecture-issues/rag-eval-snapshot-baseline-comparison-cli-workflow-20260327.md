---
title: "Compare-first RAG eval needed snapshot compatibility and threshold parity"
category: "architecture-issues"
date: "2026-03-27"
tags: ["rag", "eval-harness", "snapshots", "baselines", "cli-api-parity", "match-threshold", "regression-reporting"]
components: ["src/invproc/cli.py", "src/invproc/api.py", "src/invproc/rag/eval.py", "src/invproc/rag/transport.py", "tests/test_rag_backend.py", "docs/eval-baselines/README.md"]
symptoms:
  - "RAG eval baselines could drift from live retrieval behavior when eval forced min_score instead of inheriting the runtime threshold"
  - "Explicit --compare-to baseline files could be diffed even when they came from incompatible fixture content or eval settings"
  - "Eval snapshots, JSON payloads, and comparison logic had duplicate serialization paths that increased schema drift risk"
---

## Problem

The RAG eval harness was upgraded into a compare-first workflow, but two correctness gaps made the resulting reports less trustworthy than they looked.

First, eval no longer matched live retrieval behavior by default. CLI and API eval calls started passing a concrete `min_score` value into the evaluator, which could override the retrieval service's configured `rag_match_threshold` even when the caller had not asked for a custom threshold.

Second, explicit `--compare-to` baselines were not validated for compatibility before diffing. A readable snapshot produced from different fixture content, mode, `top_k`, threshold, or mock/live environment could still be compared and presented as a meaningful regression report.

## Root Cause

This was a parity-and-contract bug, not a ranking bug.

- Eval treated `min_score` as an eval-owned default instead of an optional override layered on top of the retrieval service's runtime default.
- Snapshot compatibility checks existed for auto-discovered baselines, but the explicit `--compare-to` path bypassed them.
- Eval payload shaping had grown duplicate serialization and normalization hops across `rag.eval`, `rag.transport`, and the CLI, which made it easier for snapshot logic and API/CLI behavior to drift apart.

## Investigation Steps

- Ran live Supabase evals and verified that the retrieval metrics themselves were plausible, which narrowed the issue to harness correctness rather than search quality.
- Reproduced the local mock-path confusion and confirmed that the compare-first workflow depended heavily on trustworthy snapshot metadata and threshold semantics.
- Reviewed the new `rag eval` flow in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py) and found that `min_score` defaulted to a concrete value and was always forwarded into `CatalogRagEvaluator`.
- Traced `/internal/rag/eval` in [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) and found the same behavior there.
- Compared the auto-discovered baseline path with the explicit `--compare-to` path and confirmed that only the former enforced compatibility checks.
- Simplified the serialization flow after the bug fix to keep one canonical eval serializer and avoid normalizing a newly built current snapshot.

## Fix

### 1. Let omitted `min_score` inherit the live retrieval threshold

The key rule is:

```python
effective_threshold = match_threshold if match_threshold is not None else self.match_threshold
```

The fix was to preserve that rule end-to-end instead of overriding it at the CLI/API boundary.

In practice:

- `EvalRequest.min_score` became optional in [`src/invproc/api.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py)
- `rag eval --min-score` became optional in [`src/invproc/cli.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/cli.py)
- both surfaces now pass `None` through when the user does not supply a threshold
- `CatalogRagEvaluator.evaluate()` records the effective threshold actually used in the result payload and snapshot metadata

That restores the intended contract: eval matches live retrieval behavior by default, and only diverges when the caller explicitly opts into a custom threshold.

### 2. Reject incompatible explicit baselines before diffing

The explicit baseline path now uses the same compatibility contract as auto-discovery.

```python
if not _snapshot_is_compatible(
    baseline_snapshot,
    fixture_name=fixture_path.name,
    fixture_hash=fixture_hash,
    search_mode=mode,
    top_k=top_k,
    match_threshold=effective_match_threshold,
    mock=mock,
):
    raise typer.Exit(code=1)
```

The comparison now fails closed when the baseline does not match the current run's:

- fixture name and fixture hash
- search mode
- `top_k`
- effective threshold
- mock/live environment

This prevents polished-but-invalid regression summaries.

### 3. Reduce serializer drift

After the blocker fixes, eval payload shaping was tightened so the code has one canonical eval serializer in [`src/invproc/rag/eval.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/eval.py). [`src/invproc/rag/transport.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/transport.py) now reuses it, and the CLI compares the freshly built current snapshot directly instead of normalizing it again.

This did not change user-facing behavior, but it lowered the maintenance cost of the snapshot/reporting path.

## Result

The compare-first eval workflow is now trustworthy in the two places that mattered most:

- saved baselines reflect the threshold the live runtime would actually use unless the caller overrides it
- explicit baseline comparisons fail clearly instead of generating misleading deltas

The snapshot/reporting flow is also simpler to evolve because there is less duplicate serialization logic across the RAG package.

## Verification

Targeted tests were added for the fixed behavior in [`tests/test_rag_backend.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_backend.py):

- `test_api_rag_eval_endpoint_uses_service_default_threshold_when_min_score_omitted`
- `test_cli_eval_uses_service_default_threshold_when_min_score_omitted`
- `test_cli_eval_compare_to_rejects_incompatible_snapshot`

Repo quality gates also passed after the fixes:

- `python -m ruff check src/ tests/`
- `python -m mypy src/`
- `python -m pytest -q`

## Prevention

Treat eval settings as part of the runtime contract, not as CLI-only convenience defaults.

- If a parameter exists in the retrieval service and affects observable behavior, every API and CLI surface must either inherit that default or explicitly justify overriding it.
- When a new eval parameter is added, trace it through parser, request model, evaluator, retrieval service, serializer, snapshot logic, and comparison logic in the same review.
- Baseline comparison should always fail closed. If two runs were not produced under the same retrieval contract, do not diff them.
- Keep one canonical serializer per payload shape. Duplicate serializers are cheap to add and expensive to keep consistent.

Useful regression tests:

- omitted threshold inherits the configured service default
- explicit threshold overrides the service default
- incompatible baseline files are rejected for each compatibility dimension
- snapshot round-trips preserve enough metadata to explain why two runs are or are not comparable

## See Also

- [RAG hybrid search returns noise results without score threshold](rag-min-score-threshold-filtering-20260323.md)
- [Two architectural improvements to the RAG pipeline — concurrent hybrid dispatch and POST /internal/rag/eval](hybrid-search-concurrent-dispatch-rag-eval-endpoint.md)
- [Feature propagation gaps: SQL param collision, dataclass defaults, and CLI parity](../integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md)
- [RAG runtime ownership split caused mock embedding fallback and empty eval state](../integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md)
