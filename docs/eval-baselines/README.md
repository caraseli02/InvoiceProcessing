# RAG Eval Baselines

Timestamped retrieval quality snapshots against real Supabase data.
Run after any change to embeddings, search logic, or catalog seeding.

## Baselines

| Date | Fixture | Hybrid top-1 | Hybrid top-5 | Notes |
|------|---------|-------------|-------------|-------|
| 2026-03-23 | rag_queries.json | 60% (9/15) | 93% (14/15) | Post hybrid BM25+pgvector+RRF fixes |

## How to run

```bash
# Integration eval — real Supabase, real embeddings (requires .env)
./scripts/eval_rag.sh

# Same but save to docs/eval-baselines/ automatically
./scripts/eval_rag.sh   # output: docs/eval-baselines/YYYY-MM-DD-rag_queries.json

# Unit fixture (synthetic IDs, mock embeddings)
./scripts/eval_rag.sh --unit --mock
```

## Thresholds

Minimum acceptable hybrid scores for `rag_queries.json`:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| top-1  | ≥ 55%     | Below this means most user queries need scroll to find the product |
| top-5  | ≥ 85%     | Product must appear in first 5 results for UX to be acceptable |

Scores below threshold indicate a regression — investigate before merging.

## Fixture notes

- `rag_queries.json` — 15 cases with real Supabase UUIDs from METRO Moldova invoice #94. **UUIDs become stale if products are re-imported.** Re-run fixture generator or update UUIDs manually after bulk re-imports.
- `rag_queries_unit.json` — 34 cases with synthetic `prod_*` IDs. Safe to run in CI with `--mock`. Known failures documented via `expected_fail: true`.
