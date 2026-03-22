---
title: Fix RAG Null Category + Expand Eval Fixture
type: fix
status: completed
date: 2026-03-22
---

# Fix RAG Null Category + Expand Eval Fixture

## Overview

Two tightly related improvements to the RAG retrieval pipeline:

1. **Part A** — Thread `category` and `uom` through the product sync pipeline so `embedding_text` is enriched when the LLM returns a category, raising top-1 retrieval accuracy above the current 60% baseline.
2. **Part B** — Expand `tests/fixtures/rag_queries.json` from 2 cases (not 15 as documented in memory) to 30–40, covering barcodes, Romanian names, abbreviations, near-duplicates, and the category-disambiguation weak spot.

**Also Part 0** — A prerequisite 2-line fix: `load_eval_cases` currently raises `TypeError` on unknown keys (`notes`, `expected_fail`), contrary to what was believed. Must be fixed before fixture expansion.

---

## Problem Statement

### Why top-1 is stuck at ~60%

All products synced from METRO Moldova invoices have `category = null` because `category` is never forwarded from `Product.category_suggestion` (LLM output) down to the sync queue. The propagation breaks at `RepositoryCatalogSyncProducer.emit_product_sync` which always passes `category=None, uom=None`.

Without category in `embedding_text`, two similar products (e.g. two types of tea, two types of milk) produce nearly identical embeddings. The LLM returns the right semantic neighbourhood but the wrong rank-1.

### Why `notes` crashes the fixture

`load_eval_cases` (`rag.py` line 427) does:
```python
return [CatalogEvalCase(**raw_case) for raw_case in raw_cases]
```
`CatalogEvalCase` is a frozen dataclass with no `notes` field — `**raw_case` raises `TypeError` on unknown keys. Fix: filter to known keys before constructing.

---

## Root Cause (Full Chain)

```
Product.category_suggestion  ← LLM returns "Dairy" here
         ↓
_build_import_request_from_invoice (cli.py)
         ↓
InvoicePreviewRow  ← ✗ no category field
         ↓
import_service → create_product / update_product
         ↓
ProductRecord (base.py)  ← ✗ no category/uom fields
         ↓
emit_product_sync → build_product_snapshot_hash(category=None)  ← hardcoded
         ↓
ProductSyncRecordInput(category=None, uom=None)  ← hardcoded
         ↓
build_catalog_embedding_text → "Greek Yogurt 123456"  ← category silently dropped
```

`build_product_snapshot_hash` already accepts `category`/`uom` params — they are just never passed. The DB queue table (`product_embedding_sync`) already has `category`/`uom` columns. No migration needed.

---

## Scope Decision

**CLI-path only.** `InvoicePreviewRow` (the API import contract model) will NOT gain category/uom fields in this PR — that would be an API contract change. Category is propagated only via the `rag ingest-invoice` CLI path where the full `InvoiceData.products` list is available. The API import path continues to produce null-category embeddings until a future contract change.

**Stale embeddings: out of scope.** When a product is re-embedded with a new hash (category added), the old embedding row (`product_id, old_hash`) remains in `product_catalog_embeddings`. Duplicate retrieval is possible but harmless for now — both embeddings point to the same `product_id` and RRF deduplicates by product_id before returning. Clean-up is a future concern.

**`"General"` category: suppressed.** Treat `category_suggestion = "General"` as semantically empty — do not include it in `embedding_text`. A product labelled "General" gets no category token, same as null.

---

## Proposed Solution

### Part 0 — Fix `load_eval_cases` (prerequisite, ~5 min)

**File:** `src/invproc/rag.py`

```python
# Before
return [CatalogEvalCase(**raw_case) for raw_case in raw_cases]

# After
known = {"query", "expected_product_id", "expected_name"}
return [
    CatalogEvalCase(**{k: v for k, v in raw_case.items() if k in known})
    for raw_case in raw_cases
]
```

This already exists for loading from the CLI fixture — apply the same pattern here. Enables `notes` and `expected_fail` fields in fixture JSON.

---

### Part A — Category / UOM propagation

#### Step A1 — `repositories/base.py`: add fields to `ProductRecord`

```python
@dataclass
class ProductRecord:
    product_id: str
    barcode: str
    name: str
    normalized_name: str
    supplier: str
    category: Optional[str] = None   # ← new
    uom: Optional[str] = None         # ← new
```

No behavior change. All existing callers pass only positional/keyword args for the first 5 fields — new optional fields default to `None` silently.

#### Step A2 — `catalog_sync.py`: forward in `emit_product_sync`

In `RepositoryCatalogSyncProducer.emit_product_sync`, replace:
```python
snapshot_hash = build_product_snapshot_hash(
    product=product,
    upsert_input=upsert_input,
    embedding_model=self.embedding_model,
)
```
with:
```python
snapshot_hash = build_product_snapshot_hash(
    product=product,
    upsert_input=upsert_input,
    embedding_model=self.embedding_model,
    category=product.category,   # ← new
    uom=product.uom,              # ← new
)
```

And on `ProductSyncRecordInput` construction below, replace the hardcoded `category=None, uom=None` with `category=product.category, uom=product.uom`.

#### Step A3 — `cli.py`: populate `ProductRecord` from `Product`

In `_extract_single` or wherever `ProductRecord` is built from `InvoiceData.products`, forward the fields:

```python
ProductRecord(
    product_id=...,
    barcode=product.barcode,
    name=product.description,
    normalized_name=...,
    supplier=...,
    category=product.category_suggestion if product.category_suggestion != "General" else None,
    uom=product.uom,
)
```

The `!= "General"` guard implements the "General = null" decision.

#### Step A4 — `rag.py`: assert `"General"` is suppressed in `build_catalog_embedding_text`

No code change needed here — the existing null-filter in `build_catalog_embedding_text` already omits empty strings. The guard in A3 converts "General" → `None` before it reaches the embedding builder.

---

### Part B — Fixture expansion

#### Step B1 — Fix `load_eval_cases` (Part 0 above — must land first)

#### Step B2 — Expand `seed_synced_product` calls in `test_rag_backend.py`

Add a shared conftest or test helper that seeds a realistic product catalog (15+ products) covering all the query pattern categories below. Products should cover:
- Multiple Dairy items with similar names (disambiguation)
- Tea products (category: Beverages or null — tests null weak spot)
- Romanian-named products (METRO Moldova catalog style)
- Products with abbreviations in name (e.g., "SEM FL")
- Products with and without category

#### Step B3 — `tests/fixtures/rag_queries.json`: expand to 30–35 cases

Add `notes` to all cases for human readability (stripped by parser).

**Query pattern coverage required:**

| Pattern | # cases | Example |
|---|---|---|
| Fuzzy English intent (existing) | 2 | "need greek yogurt for metro order" |
| Romanian product name (exact) | 6 | "HALVA ARAHIDE" |
| Romanian product name (fuzzy) | 5 | "halva cu arahide" |
| Abbreviations | 3 | "SEM FL" |
| UOM-qualified | 3 | "mineral water 1.5L bottle" |
| Category concept | 4 | "ceai pentru bebelusi" |
| Near-duplicate disambiguation | 4 | "lapte 1L" vs "lapte 2L" — pick correct one |
| Typo variants | 3 | "grek yogurt" |
| Null-category products (weak spot) | 4 | products with no category set |
| **Total** | **34** | |

**Note:** Barcode lookup intentionally excluded — users search by product name/intent, not EAN codes.

Add `"expected_fail": true` on known-weak-spot cases to flag regressions visibly without failing the run.

---

## Technical Considerations

- **No migration needed.** `product_embedding_sync` table already has `category` and `uom` columns.
- **Re-sync triggered automatically.** Because `build_product_snapshot_hash` includes `category`, any product re-imported with a newly-populated category gets a different hash → new pending sync row → fresh embedding with enriched text.
- **`'simple'` FTS dictionary unchanged.** Adding category tokens to `embedding_text` works correctly with `'simple'` — no language-specific stemming needed.
- **Type safety.** `ProductRecord.category` is `Optional[str]`. `Product.category_suggestion` is `Optional[Literal[...]]`. Assignment is safe (Literal is a subtype of str). No runtime cast needed.
- **Protocol compatibility.** `CatalogSyncProducer` protocol signature for `emit_product_sync` does not change — category is read from `product`, not added as a new parameter. Existing test doubles (`NoopCatalogSyncProducer`, `FailingCatalogSyncProducer`) need no changes.

---

## System-Wide Impact

- **Interaction graph:** `ProductRecord` is created in `cli.py → _extract_single`. It flows to `ImportService.import_rows → emit_product_sync → CatalogSyncWorker.process_one → build_catalog_embedding_text → OpenAIEmbeddingClient.embed → upsert_product_catalog_embedding`. Category enrichment affects all steps downstream of `emit_product_sync`.
- **State lifecycle risks:** Old embeddings (null-category hash) remain in `product_catalog_embeddings`. They coexist with new enriched embeddings. RRF deduplicates on `product_id` — both entries contribute to ranking, net effect is the enriched embedding has more chances to surface. No orphan risk.
- **API parity:** The API `POST /import` path is intentionally excluded from this change. This is a documented scope decision, not a parity gap. Future work: add `category`/`uom` to `InvoicePreviewRow` and API contract.

---

## Acceptance Criteria

- [ ] `build_catalog_embedding_text` includes category token when category is non-null and non-"General"
- [ ] `build_catalog_embedding_text` excludes category when null or "General"
- [ ] `build_product_snapshot_hash` called with `category` and `uom` from `ProductRecord` (not hardcoded `None`)
- [ ] Re-import of a product with newly-available category creates a new sync row (different hash)
- [ ] `load_eval_cases` silently ignores `notes`, `expected_fail`, and any other unknown keys
- [ ] `tests/fixtures/rag_queries.json` has ≥ 30 cases
- [ ] Fixture covers: barcodes, Romanian names, abbreviations, UOM queries, category concepts, near-duplicates, typos, null-category weak spot
- [ ] `pytest tests/ -q` passes with coverage ≥ 80%
- [ ] `ruff check src/` and `mypy src/` pass clean

---

## Files to Modify

| File | Change |
|---|---|
| `src/invproc/repositories/base.py` | Add `category: Optional[str]`, `uom: Optional[str]` to `ProductRecord` |
| `src/invproc/catalog_sync.py` | Forward `product.category`/`product.uom` in `emit_product_sync` |
| `src/invproc/cli.py` | Populate `ProductRecord.category` from `Product.category_suggestion` (with "General" guard) |
| `src/invproc/rag.py` | Fix `load_eval_cases` unknown-key filtering |
| `tests/fixtures/rag_queries.json` | Expand to 30–40 cases with `notes` |
| `tests/test_rag_backend.py` | Add product seeds for new fixture cases |

---

## Implementation Order

1. `rag.py` — fix `load_eval_cases` (Part 0, unblocks fixture work independently)
2. `base.py` — add fields to `ProductRecord` (no behavior change, safe first commit)
3. `catalog_sync.py` — forward category/uom in `emit_product_sync`
4. `cli.py` — populate from `Product.category_suggestion`
5. Tests for Part A (unit: verify embedding text enriched, verify re-sync triggered on category change)
6. Expand fixture + seed products (Part B)

---

## Verification

```bash
# Lint + types
ruff check src/
mypy src/

# Full test suite
pytest tests/ -q

# Manual smoke: ingest an invoice and check embedding_text in DB
invproc rag ingest-invoice test_invoices/invoice-test.pdf --mock --sync
invproc rag eval tests/fixtures/rag_queries.json --mock --all-modes

# Verify category appears in embedding_text for a product with known category
invproc rag query "HALVA ARAHIDE" --mock --top-k 5
```

---

## Out of Scope

- Stale embedding cleanup (old null-category rows in `product_catalog_embeddings`)
- API contract change (`InvoicePreviewRow` gaining `category`/`uom` fields)
- Promptfoo integration (see `docs/plans/promptfoo-adoption-decision.md`)
- UOM normalization ("KG" vs "Kilogram") — raw invoice string used as-is
