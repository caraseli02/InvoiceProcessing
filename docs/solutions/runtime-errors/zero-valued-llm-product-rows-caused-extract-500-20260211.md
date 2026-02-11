---
module: System
date: 2026-02-11
problem_type: runtime_error
component: extraction_pipeline
symptoms:
  - "POST /extract returned 500 Internal Server Error with Pydantic ValidationError for products.<index>.quantity/unit_price"
  - "LLM output occasionally contained product rows with quantity=0.0 and unit_price=0.0"
root_cause: normalization_gap
resolution_type: code_fix
severity: high
tags: [llm-output, pydantic, fastapi, extraction, validation]
related:
  - docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md
  - tests/test_error_paths.py
---

# Troubleshooting: Zero-Valued LLM Product Rows Caused `/extract` 500

## Problem

Invoice extraction failed with `500` even though malformed rows were already being filtered in normalization. The failing payload included a row with `quantity=0.0` and `unit_price=0.0`, which then failed strict `Product` model validation (`gt=0`).

## Symptoms

Response body from failing API call:

```json
{
  "detail": "Processing failed: 2 validation errors for InvoiceData\nproducts.42.quantity ... input_value=0.0 ...\nproducts.42.unit_price ... input_value=0.0"
}
```

Observed behavior:
- `/extract` returned `500`.
- Frontend logged generic HTTP failure without clear user-actionable outcome.

## Root Cause

`LLMExtractor._normalize_invoice_payload()` filtered `None` values but did not treat non-positive numeric values as malformed.

This allowed zero-valued rows to pass normalization and crash later during Pydantic model construction:
- `Product.quantity` requires `> 0`
- `Product.unit_price` requires `> 0`

## Solution

Extended malformed-row filtering in normalization to reject non-positive numeric values:

- Drop rows when:
  - `quantity <= 0`
  - `unit_price <= 0`
  - `total_price < 0`
- Keep resilient behavior:
  - if at least one row remains valid, continue extraction
  - if all rows are malformed, raise integrity error

Code references:
- `src/invproc/llm_extractor.py`
- `tests/test_error_paths.py`

## Why This Works

- Prevents invalid rows from reaching strict Pydantic model validation.
- Preserves robust extraction for mixed-quality LLM output.
- Keeps failure explicit when output is entirely unusable.

## Verification

Targeted tests:

```bash
python3 -m pytest -q \
  tests/test_error_paths.py::test_llm_filters_zero_quantity_and_unit_price_rows \
  tests/test_error_paths.py::test_llm_filters_malformed_product_rows \
  tests/test_error_paths.py::test_llm_raises_if_all_rows_malformed
```

Full suite:

```bash
python3 -m pytest -q
```

Result at time of fix:
- `76 passed`

## Prevention

1. Normalize against model constraints, not only nullability.
2. Add regression tests for boundary numerics (`0`, negative, NaN/inf where relevant).
3. Keep parser resilience explicit: partial malformed rows can be dropped, fully malformed outputs must fail fast.
4. When extraction resilience behavior changes, update API troubleshooting docs for frontend teams.

## Related

- Prior related incident and handling strategy:
  `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`
