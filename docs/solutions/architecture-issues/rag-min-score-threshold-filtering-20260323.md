---
title: "RAG hybrid search returns noise results without score threshold"
category: "architecture-issues"
date: "2026-03-23"
tags: ["rag", "hybrid-search", "rrf", "score-threshold", "retrieval-quality", "bm25", "pgvector"]
components: ["src/invproc/api.py", "src/invproc/cli.py", "CatalogRetrievalService", "CatalogQueryRequest"]
last_refreshed: "2026-03-28"
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
- Traced the code: `CatalogQueryRequest` had no per-request threshold field; `retrieval_service.query()` was called without `match_threshold`
- Confirmed `match_threshold` was already implemented in `CatalogRetrievalService.query()` and used in the filter predicate — just never exposed

## Fix

**`src/invproc/api.py` — add a public per-request threshold field to the query request model:**

```python
class CatalogQueryRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    search_mode: Literal["semantic", "lexical", "hybrid"] = Field(default="hybrid")
    match_threshold: float = Field(default=0.02, ge=0.0, le=1.0)  # canonical public field
```

**Pass it through to the service call:**

```python
result = await run_in_threadpool(
    retrieval_service.query,
    payload.query,
    top_k=payload.top_k,
    mode=payload.search_mode,
    match_threshold=payload.match_threshold,
)
```

**`src/invproc/cli.py` — add `--min-score` option for the CLI surface:**

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

This learning still applies to query-time retrieval surfaces:

- `POST /internal/rag/query`
- `python -m invproc rag query ...`

As of March 28, 2026, the public API contract names this field `match_threshold` while still accepting legacy `min_score` as an input alias for backward compatibility. The CLI still uses `--min-score`.

It does **not** define the default behavior for `rag eval` or `POST /internal/rag/eval`. As of March 27, 2026, eval inherits the retrieval service's runtime threshold when `min_score` is omitted, and only overrides it when the caller passes an explicit value.

## Caveats

- Default `0.02` is tuned for a ~20-product catalog. As the catalog grows, RRF score distributions shift — re-evaluate the threshold after significant catalog growth and after running `./scripts/eval_rag.sh`.
- RRF scores are relative rank weights, not raw cosine similarity. The right threshold value depends on catalog size, not on semantic distance.
- Applying the per-request threshold too aggressively can drop valid matches and push eval metrics below documented thresholds (top-1 ≥ 55%, top-5 ≥ 85%). Re-run baseline eval after changing query defaults or runtime threshold configuration.
- Query and eval now intentionally differ in one important way:
  query surfaces still default the query threshold to `0.02`, while eval surfaces treat their threshold override as optional and inherit the runtime retrieval threshold when omitted.

## Prevention

**Rule:** Every numeric threshold or policy knob inside a service method must appear in the corresponding request model AND CLI within the same PR. "Internal default" is not a valid permanent state.

**Detection test:** Integration test — call the API with `match_threshold=0.9` on a catalog where all products score < 0.9 and assert the result set is empty. Fails immediately if the field is not wired through.

**Checklist item:** "Every service-level threshold param is exposed in the API request model AND the CLI command — verify both, not just one. If one surface intentionally inherits the runtime default while another sets an explicit default, document that difference."

## See Also

- [Hybrid RAG BM25 + pgvector + RRF scoring model](hybrid-rag-bm25-vector-rrf-search-mode.md) — explains RRF score computation; threshold behaviour differs by mode
- [API/CLI parity checklist](../integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md) — governs how new params must propagate through all surfaces
- [Eval endpoint contract](hybrid-search-concurrent-dispatch-rag-eval-endpoint.md) — use `/internal/rag/eval` to validate threshold impact on hit rates
- [Compare-first RAG eval needed snapshot compatibility and threshold parity](rag-eval-snapshot-baseline-comparison-cli-workflow-20260327.md) — documents why eval must inherit the runtime threshold when `min_score` is omitted and why explicit baselines must be compatibility-checked
- [Eval baselines](../../eval-baselines/README.md) — re-run `./scripts/eval_rag.sh` after changing query defaults or runtime threshold configuration; eval uses the effective runtime threshold unless explicitly overridden
