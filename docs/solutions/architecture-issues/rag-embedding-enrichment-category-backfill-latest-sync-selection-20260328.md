---
title: "RAG embedding enrichment needed canonical category backfill and latest-sync selection"
category: "architecture-issues"
date: "2026-03-28"
tags: ["rag", "embeddings", "catalog-sync", "category-backfill", "retrieval-quality", "supabase", "deduplication"]
components: ["src/invproc/rag/sync.py", "src/invproc/catalog_sync.py", "src/invproc/repositories/base.py", "src/invproc/repositories/memory.py", "src/invproc/repositories/supabase.py", "src/invproc/api.py", "tests/test_rag_backend.py"]
symptoms:
  - "Broad tea queries such as 'ceai de fructe' ranked dried-fruit snacks above tea products when lexical recall was weak and semantic similarity overweighted 'fruit'"
  - "Null-category products could retrieve better after enrichment, but API responses still showed category: null because the inferred category was never written back canonically"
  - "Live retrieval could surface older synced embedding rows for the same product, so a good result could come from an outdated embedding_text_version instead of the latest snapshot"
---

## Problem

The catalog RAG pipeline had enough raw product text to find exact tea SKUs, but it did not have enough family/category context to resolve broader intent like `ceai de fructe`. Products with `category = null` were especially weak because embedding text only contained the raw name, barcode, and UOM. Even after retrieval enrichment improved the match, response metadata remained incomplete and live search could still return stale synced rows for the same product.

## Root Cause

Three contract gaps stacked together:

- embedding text did not inject safe family/category hints for null-category products, so lexical search missed broad tea phrases and semantic search drifted toward generic fruit snacks
- inferred categories were used only as retrieval-time hints, not written back to canonical product records, so `category` stayed null even when the system had high-confidence structured knowledge
- repository search paths scored whatever synced embedding rows were available and did not consistently collapse historical rows to the newest record per product

## Investigation Steps

- Reproduced the live miss with `ceai de fructe` and verified that lexical search contributed little while semantic search over-weighted generic `fruit` semantics.
- Added embedding-text enrichment for tea-family products and reran local and live evals to confirm the target case moved from fruit snacks to tea at rank 1.
- Tested a narrower refinement that removed standalone fruit tokens, then discarded it after live top-1 regressed from tea back to a fruit snack.
- Queried live metadata and confirmed the top hit still reported `embedding_text_version: "v2"` after later syncs, which proved historical embedding rows were still eligible during retrieval.
- Verified that the visible `category: null` was not a query bug: the source product record itself had never been backfilled even though retrieval metadata already carried `effective_category`.

## Fix

### 1. Enrich embedding text with safe family/category hints

[`src/invproc/rag/sync.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/sync.py) now infers lightweight retrieval context from product name and category:

- tea family products receive `family = "tea"` plus anchored hints such as `tea`, `ceai`, `bauturi`
- fruit and herbal tea variants add phrase-level hints such as `ceai de fructe`, `fruit tea`, `ceai de plante`, `herbal tea`
- produce-like products with no category can safely fall back to `Produce`

The embedding text builder now prefers:

```python
category_override or record.category or enrichment["effective_category"]
```

and keeps tea hints even when the canonical category has already been backfilled to `Beverages`. That last part mattered because backfill alone would otherwise have removed the exact tea-family terms that fixed `ceai de fructe`.

### 2. Backfill canonical category when inference is safe

[`CatalogSyncWorker.process_one()`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/sync.py) now calls `repository.backfill_product_category(...)` when:

- the current product category is null
- the enrichment layer inferred a concrete safe category such as `Beverages` or `Produce`

That backfill is implemented in both repositories:

- [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py)
- [`src/invproc/repositories/supabase.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/supabase.py)

As a result, query responses can now expose:

- `category` as the canonical stored value
- `effective_category` as the same resolved value for retrieval inspection

instead of leaving `category` null forever.

### 3. Prefer the latest embedding row per product during retrieval

Historical sync rows remain valuable as audit history, but retrieval should not rank all of them independently.

The in-memory repository now collapses embedding rows to the newest `(updated_at, created_at)` pair per `product_id` before scoring. The Supabase repository applies the same rule after pulling a candidate product set from the existing RPCs. This preserved the database search entrypoints while avoiding the earlier timeout-prone approach of scanning every embedding row just to deduplicate in Python.

### 4. Align the public threshold contract with the internal one

Swagger and the API request models now expose `match_threshold` while still accepting legacy `min_score` as an input alias. That keeps the public contract aligned with the retrieval service terminology without breaking older callers.

## Result

The target retrieval path now behaves the way the system claimed it did:

- `ceai de fructe` and `ceai de fructe sau plante` return the tea SKU at rank 1 in live queries
- the top hit now reports `category: "Beverages"` and `embedding_text_version: "v6"`
- retrieval prefers the latest synced row for the product instead of surfacing arbitrary historical versions

This did not make broad category retrieval universally strong. Cross-category manual sweeps still showed weak results for generic intents such as `alcool`, `legume`, and `cereale`. But the solved problem here was narrower and important: safe enrichment is now reflected in both canonical metadata and live retrieval ranking for the tea-family miss that motivated the work.

## Verification

Code verification passed after the final `v6` rollout:

- `python -m ruff check src/ tests/`
- `python -m mypy src/`
- `python -m pytest -q`

Targeted regression coverage now includes:

- safe canonical category backfill for null-category products
- no overwrite when a product already has a category
- tea hint preservation when category is present
- latest embedding snapshot preference per product
- API acceptance of `match_threshold` with legacy `min_score` compatibility

Live verification confirmed:

- `ceai de fructe` -> top-1 tea SKU with `category = "Beverages"` and `embedding_text_version = "v6"`
- `ceai de fructe sau plante` -> top-1 tea SKU

## Prevention

- If retrieval depends on inferred structured metadata, decide explicitly whether that metadata is inspection-only or should be backfilled canonically. Half-doing both creates confusing API responses.
- Any sync pipeline that keeps historical rows must define retrieval-time deduplication semantics. "All synced rows are searchable" is almost always wrong once snapshot versions evolve.
- When improving embeddings for a known miss, preserve the phrase-level terms that fixed the miss before pruning generic tokens. Narrowing too early can move the exact bad case backwards.
- Keep public API field names aligned with service-level terminology. If compatibility aliases are needed, treat them as migration shims and document the canonical field name.

## See Also

- [RAG hybrid search returns noise results without score threshold](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-min-score-threshold-filtering-20260323.md)
- [Compare-first RAG eval needed snapshot compatibility and threshold parity](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/rag-eval-snapshot-baseline-comparison-cli-workflow-20260327.md)
- [Hybrid RAG BM25 + pgvector + RRF scoring model](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-rag-bm25-vector-rrf-search-mode.md)
