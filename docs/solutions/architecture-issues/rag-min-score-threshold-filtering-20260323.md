---
title: "RAG hybrid search returns noise results without score threshold"
category: "architecture-issues"
date: "2026-03-23"
tags: ["rag", "hybrid-search", "rrf", "score-threshold", "retrieval-quality", "bm25", "pgvector"]
components: ["src/invproc/api.py", "src/invproc/cli.py", "CatalogRetrievalService", "CatalogQueryRequest"]
symptoms:
  - "Query for 'lapte' (milk) returned 'TURTA DULCE VISINA' (cherry gingerbread) at position 10"
  - "Top-2 relevant results had RRF scores ~0.033; positions 3-20 had flat noise band 0.013-0.016"
  - "All top_k results returned regardless of semantic relevance"
---

## Problem

Querying the RAG catalog for "lapte" (Romanian: milk) returned irrelevant products like "TURTA DULCE VISINA CAHULP" (cherry gingerbread) in position 10 out of 20 results. All `top_k` results were returned to the caller with no way to filter by score quality.

## Root Cause

`CatalogRetrievalService.query()` already accepted a `match_threshold` parameter (default `0.0`), and already filtered matches at the return site:

```python
matches=[
    m for m in raw_matches
    if m.score >= effective_threshold  # this line existed
]
```

However, neither `CatalogQueryRequest` nor the `invproc rag query` CLI exposed the threshold. It was permanently locked at `0.0` for all callers. This is the **config-complete, API-incomplete** anti-pattern: the capability exists internally but is never reachable from outside.

For a small catalog (~20 products), RRF scores compress into a tight noise band after the top relevant hits:

```
position 1:  score=0.0328  (LAPTE CONDEN FIERT IRISK)  ✅
position 2:  score=0.0323  (LAPTE CONDEN INTEG ICINEA) ✅
position 3:  score=0.0159  ← 50% drop — relevance cliff
position 10: score=0.0143  (TURTA DULCE VISINA) ❌ noise
position 20: score=0.0125  (DIVIN 5 ANI) ❌ noise
```

With `match_threshold=0.0`, all 20 results return. With `match_threshold=0.02`, only the 2 relevant results return.

## Investigation Steps

- Called `POST /internal/rag/query` with `top_k=20`; observed score distribution showing a clear ~50% cliff between positions 2 and 3
- Confirmed all three modes (semantic, lexical, hybrid) produced identical ordering for this query — the issue was not mode-specific
- Traced the code: `CatalogQueryRequest` had no `min_score` field; `retrieval_service.query()` was called without `match_threshold`
- Confirmed `match_threshold` was already implemented in `CatalogRetrievalService.query()` and used in the filter predicate — just never exposed

## Fix

**`src/invproc/api.py` — add `min_score` to request model:**

```python
class CatalogQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    search_mode: Literal["semantic", "lexical", "hybrid"] = Field(default="hybrid")
    min_score: float = Field(default=0.02, ge=0.0, le=1.0)  # added
```

**Pass it through to the service call:**

```python
result = await run_in_threadpool(
    retrieval_service.query,
    payload.query,
    top_k=payload.top_k,
    mode=payload.search_mode,
    match_threshold=payload.min_score,  # added
)
```

**`src/invproc/cli.py` — add `--min-score` option:**

```python
min_score: float = typer.Option(
    0.02,
    "--min-score",
    min=0.0,
    max=1.0,
    help="Minimum RRF score threshold. Results below this are dropped (default: 0.02).",
),
# ...
result = retrieval_service.query(text, top_k=top_k, mode=mode, match_threshold=min_score)
```

## Result

`"lapte"` query returns 2 results (both LAPTE CONDEN variants) instead of 20. The default `0.02` sits above the noise floor (~0.013–0.016) and below the relevant cluster (~0.033).

## Caveats

- Default `0.02` is tuned for a ~20-product catalog. As the catalog grows, RRF score distributions shift — re-evaluate the threshold after significant catalog growth and after running `./scripts/eval_rag.sh`.
- RRF scores are relative rank weights, not raw cosine similarity. The right threshold value depends on catalog size, not on semantic distance.
- Applying `min_score` too aggressively can drop valid matches and push eval metrics below documented thresholds (top-1 ≥ 55%, top-5 ≥ 85%). Re-run baseline eval after changing the default.

## Prevention

**Rule:** Every numeric threshold or policy knob inside a service method must appear in the corresponding request model AND CLI within the same PR. "Internal default" is not a valid permanent state.

**Detection test:** Integration test — call the API with `min_score=0.9` on a catalog where all products score < 0.9 and assert the result set is empty. Fails immediately if the field is not wired through.

**Checklist item:** "Every service-level threshold param is exposed in the API request model AND the CLI command — verify both, not just one."

## See Also

- [Hybrid RAG BM25 + pgvector + RRF scoring model](hybrid-rag-bm25-vector-rrf-search-mode.md) — explains RRF score computation; threshold behaviour differs by mode
- [API/CLI parity checklist](../integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md) — governs how new params must propagate through all surfaces
- [Eval endpoint contract](hybrid-search-concurrent-dispatch-rag-eval-endpoint.md) — use `/internal/rag/eval` to validate threshold impact on hit rates
- [Eval baselines](../../eval-baselines/README.md) — re-run `./scripts/eval_rag.sh` after changing `min_score` default; thresholds are top-1 ≥ 55%, top-5 ≥ 85%
