---
title: feat: Backfill inferred catalog category and prefer latest synced embedding per product
type: feat
status: active
date: 2026-03-28
plan_depth: deep
---

# feat: Backfill inferred catalog category and prefer latest synced embedding per product

## Overview

Finish the missing parts from the embedding enrichment rollout:

- persist safe inferred categories so null-category products become more complete
- ensure retrieval returns the latest synced embedding for a product instead of mixing historical snapshot versions

## Why This Follow-up Exists

- The first phase improved retrieval by adding derived tea hints into embedding text.
- Live manual verification still shows `category = null` and only `effective_category = "Beverages"` for tea products.
- Live query responses also show mixed embedding versions, which means retrieval can return an older synced row (`v2`) even after newer sync rows (`v4`) exist.

## Problems To Solve

### 1. Metadata completeness gap

- `category` remains the raw source field from imports.
- For safe cases such as tea and produce, the system infers `effective_category` but does not backfill canonical product metadata.
- Debug output is therefore partially enriched but still incomplete.

### 2. Retrieval version-selection gap

- `product_catalog_embeddings` stores multiple rows for one product across snapshot versions.
- Current repository search paths return rows directly from storage without deduping to the latest synced row per product.
- Live behavior can therefore be correct for the target query but still sourced from a stale embedding version.

## Goals

- Backfill canonical product `category` only when the inference is conservative and the existing category is null.
- Keep `effective_category` in metadata for explainability, but avoid disagreement between canonical and derived categories after backfill.
- Ensure semantic and lexical retrieval return at most one row per product, preferring the latest embedding row for that product and model.
- Preserve existing query/output contracts unless explicitly improving them.

## Non-Goals

- Broad taxonomy redesign beyond the current safe inference rules.
- Large reclassification of all product families.
- Changing eval fixture expectations unrelated to category completion or latest-row preference.

## Decisions

### Decision 1: Backfill category during sync processing, not during query time

- Query-time patching would hide the underlying data quality gap.
- Sync-time backfill keeps product records and future sync rows aligned.
- This keeps manual verification simpler: `category` should stop being null for safely inferred cases.

### Decision 2: Restrict backfill to conservative families already implemented

- Tea with missing category backfills to `Beverages`.
- Produce-like tokens with missing category backfill to `Produce`.
- No new broad family inference should be added in this change.

### Decision 3: Deduplicate retrieval results by product, preferring the newest embedding row

- Memory backend can dedupe in Python by selecting the newest record per `product_id`.
- Supabase backend should do the same at repository-query boundaries, ideally in SQL/RPC if already supported; otherwise apply a safe repository-layer collapse after result fetch.
- Preference ordering should be newest `updated_at`/`created_at`, then current score ordering within the chosen row set.

## Implementation Units

### Unit 1: Repository contract for safe category backfill

- Add a repository method for updating category when current category is null.
- Implement it in memory and Supabase repositories.
- Keep the update narrow so existing non-null categories are never overwritten by inferred values.

### Unit 2: Sync worker writes canonical category when inference is safe

- Reuse `infer_catalog_embedding_context()` in the sync worker.
- When `record.category` is null and `effective_category` is present, call the repository backfill method before persisting embedding metadata.
- Ensure synced embedding metadata reflects the canonical category after backfill.

### Unit 3: Latest-row selection in retrieval

- Update memory repository listing/search behavior so one product contributes only its latest embedding row for a model.
- Update Supabase repository search/list behavior to collapse older rows for the same product before returning matches.
- Verify semantic, lexical, and hybrid paths all inherit the same latest-row preference.

### Unit 4: Regression coverage and live verification

- Add tests for:
  - category backfill from null to `Beverages` for tea products
  - category backfill from null to `Produce` for safe produce products
  - no overwrite when category is already non-null
  - retrieval preferring the latest synced embedding row per product
  - manual target case still returning tea at rank 1
- Run full quality gates.
- Requeue and resync live rows, then manually verify `ceai de fructe` shows both:
  - tea at rank 1
  - latest `embedding_text_version`
  - canonical `category` aligned with `effective_category`

## Risks

- Backfill may accidentally overwrite legitimate nulls if inference is too loose.
- Supabase search RPCs may continue surfacing historical rows unless dedupe is applied close to the query boundary.
- Live eval can shift on unrelated broad queries when latest-row preference removes previously duplicated candidates.

## Mitigations

- Only backfill when current category is null and inferred category is one of the explicitly allowed safe values.
- Add regression tests covering “do not overwrite existing category”.
- Verify both repository implementations, not just the in-memory fixture path.
- Re-run live target queries after resync before calling the work complete.

## Acceptance Criteria

- [ ] Products with null category and safe inference are backfilled to canonical `category`.
- [ ] Query responses stop showing stale embedding versions for products that have newer synced rows.
- [ ] `category` and `effective_category` are aligned for the target tea case after resync.
- [ ] `ceai de fructe` remains tea rank 1 after the latest-row preference change.
- [ ] Full repo quality gates pass.
