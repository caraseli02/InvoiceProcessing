---
title: feat: Enrich RAG embedding text with category and family hints
type: feat
status: active
date: 2026-03-28
---

# feat: Enrich RAG embedding text with category and family hints

## Overview

Improve product embedding text so broad queries like `ceai de fructe` map to tea products more reliably when catalog metadata is sparse or missing. The change should stay inside the existing RAG sync and eval workflow.

## Problem

- Some products still reach the embedding table with `category = null`.
- Lexical search can miss broad intent terms when the catalog text only contains the product name and flavor.
- Semantic search can over-weight generic tokens like `fruit` and drift toward fruit snacks or chocolate.
- Hybrid search inherits the miss when lexical contributes nothing.

## Proposed Solution

- Enrich embedding text with normalized category/family hint phrases when they are justified by existing metadata or the product name.
- Backfill embedding metadata with the same inferred hint fields so downstream debugging and eval output can explain why a product matched.
- Keep the enrichment conservative and additive: preserve the existing product text, then append only stable hint words.

## Implementation Units

### Unit 1: Sync enrichment helpers

- Add helper logic in `src/invproc/rag/sync.py` to normalize category text and infer a limited product family from the product name.
- Build embedding text from the existing core fields plus justified hint phrases.
- Include inferred hint fields in embedding metadata.

### Unit 2: Regression coverage

- Add focused tests in `tests/test_rag_backend.py` for:
  - inferred tea-family enrichment when category is missing
  - metadata completeness for inferred hints
  - the `ceai de fructe` broad-query regression case

### Unit 3: Eval verification

- Run the compare-first eval harness against `tests/fixtures/rag_queries_unit.json`.
- Compare the new run to the latest compatible saved baseline under `docs/eval-baselines/`.

## Acceptance Criteria

- [ ] Embedding text contains family/category hint terms for tea products when justified.
- [ ] Products with `category = null` can still persist useful hint metadata when the family can be inferred safely.
- [ ] Targeted regression tests cover the tea broad-query case.
- [ ] Repo quality gates pass.
- [ ] Eval output can be compared against the saved baseline and the result is summarized.
