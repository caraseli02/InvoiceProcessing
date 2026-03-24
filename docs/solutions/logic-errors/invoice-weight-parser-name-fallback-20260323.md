---
title: "Invoice ingestion fails MISSING_WEIGHT when weight is embedded in product name"
category: "logic-errors"
date: "2026-03-23"
tags: ["invoice-ingestion", "weight-parsing", "llm-extraction", "fallback", "product-name-parsing", "missing-weight"]
components: ["src/invproc/cli.py", "_build_import_request_from_invoice", "weight_parser.parse_weight_candidate"]
symptoms:
  - "All 4 test invoices had missing_weight_count equal to product_count"
  - "import_status = 'failed' for every invoice"
  - "LLM leaves weight_kg_candidate empty for products with weight in name (e.g. '370G LAPTE CONDEN')"
  - "Catalog stays at 44 synced embeddings despite re-importing invoices"
---

## Problem

Running `invproc rag ingest-invoice` on any of the 4 test invoices returned `import_status: "failed"` with `missing_weight_count = product_count`. Products like "370G LAPTE CONDEN FIERT IRISK" and "200G UNT CIOCOLATA JLC" all had `weight_kg_candidate = None`, causing `MISSING_WEIGHT` on every row.

## Root Cause

**Incomplete fallback chain.** `_build_import_request_from_invoice()` in `cli.py` resolved `weight_kg` with only two tiers:

```python
# before fix
"weight_kg": product.weight_kg_candidate
    if product.weight_kg_candidate is not None
    else default_weight_kg,
```

The LLM does not reliably extract `weight_kg_candidate` when the weight is encoded as a prefix token in the product name (e.g., `"370G"`, `"200G"`, `"0.5L"`, `"24X2G"`). `parse_weight_candidate()` in `weight_parser.py` handles exactly this pattern:

```
"370G LAPTE CONDEN FIERT IRISK"  → weight_kg=0.37
"200G UNT CIOCOLATA JLC"         → weight_kg=0.20
"0.5L DIVIN 5 ANI"               → weight_kg=0.50
"24X2G CEAI LOVARE"              → weight_kg=0.048
```

But the parser was never called as a fallback. When the LLM returned `None`, the code jumped straight to `default_weight_kg` (which is `None` by default), triggering `MISSING_WEIGHT`.

This is the **incomplete fallback chain** anti-pattern: a utility exists and works correctly but is disconnected from the production path that needs it.

## Investigation Steps

- Ran `invproc rag ingest-invoice test_invoices/invoice-test.pdf --sync` → 42 errors, `import_status: "failed"`
- Checked extracted product data: `weight_kg_candidate = None` for all products despite names like "370G LAPTE CONDEN"
- Tested `parse_weight_candidate("370G LAPTE CONDEN FIERT IRISK")` directly → `WeightParseResult(weight_kg=0.37, size_token='370G', parse_confidence=0.98)`
- Traced `_build_import_request_from_invoice`: `parse_weight_candidate` was never called — the two-tier chain jumped LLM → default
- Confirmed `parse_weight_candidate` import wasn't in `cli.py`

## Fix

**`src/invproc/cli.py` — add import and three-tier fallback chain:**

```python
from .weight_parser import parse_weight_candidate
```

Before:
```python
"weight_kg": product.weight_kg_candidate
    if product.weight_kg_candidate is not None
    else default_weight_kg,
```

After:
```python
"weight_kg": product.weight_kg_candidate
    or parse_weight_candidate(product.name).weight_kg
    or default_weight_kg,
```

Resolution order:
1. **LLM-extracted** `weight_kg_candidate` (from invoice's explicit weight column)
2. **Name parser** `parse_weight_candidate(name)` (handles `"370G"`, `"0.5L"`, `"24X2G"` prefix tokens)
3. **CLI fallback** `--default-weight-kg` (manual override for products neither source can resolve)

## Result

- All 4 invoices ingested successfully: 0 errors for package-size products
- Catalog grew from 44 → 171 synced embeddings
- Products with weight tokens in names resolve automatically without any manual flag

## Remaining Gap

Deli/meat products sold by weight on the invoice (e.g., `"SUNCA DE VITA ROGOB"`, `"PARIZER DOCTORSKAIA CARMEZ"`, `"CRENVURSTI URSULET FILLETTI"`) have no weight token in the product name. They still require `--default-weight-kg 1.0` or a future fix to extract weight from the invoice's quantity/weight column.

Future fix: when `uom == "KG"`, use `product.quantity` as `weight_kg` (the invoice's `Cant.` column is the actual weight for these products). See the KG-mode weight chain docs.

## Prevention

**Rule:** Any `parse_*` or `extract_*` utility that handles a known data-quality edge case must be listed explicitly in the fallback chain of the feature that owns that field. New parsers must be added to the chain in the same PR that introduces them.

**Detection test:**
```python
@pytest.mark.parametrize("name", [
    "370G LAPTE CONDEN FIERT IRISK",
    "200G UNT CIOCOLATA JLC",
    "0.5L DIVIN 5 ANI",
    "24X2G CEAI LOVARE",
])
def test_ingest_no_missing_weight_for_package_name_products(name):
    # Build import row without LLM weight or default_weight_kg
    row = _build_import_row(name, weight_kg_candidate=None, default_weight_kg=None)
    assert row["weight_kg"] is not None, f"MISSING_WEIGHT for '{name}'"
```

**Checklist item:** "For every sentinel/error value (MISSING_*, UNKNOWN_*, etc.), confirm the fallback chain calls all available parsers before surfacing the error — trace the chain in the diff."

## See Also

- [KG-mode weight chain context](../../plans/2026-02-24-fix-kg-weighed-items-invoice-import-plan.md) — documents the full weight resolution hierarchy; this fallback is the third tier
- [Parser history: multipack and comma-decimal patterns](../integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md) — `parse_weight_candidate` handles `24X2G`, `1,5KG`, etc.
- [Idempotency key derives from effective payload](../integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md) — changing fallback behavior changes the effective payload and therefore the idempotency key for the same invoice
- [Column alignment prevents weight column swaps](../best-practices/llm-column-swap-prevention-spatial-layout-invoice-extraction-20260202.md) — why the LLM sometimes misses `weight_kg` from the invoice's weight column
