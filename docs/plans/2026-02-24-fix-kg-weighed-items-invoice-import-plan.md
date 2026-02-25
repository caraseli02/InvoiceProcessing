---
title: "fix: Handle KG weighed-item invoice rows (quantity vs weight)"
type: fix
date: 2026-02-24
---

# fix: Handle KG weighed-item invoice rows (quantity vs weight)

## Overview

Some invoices contain rows where the packaging/UOM column (`Mod amb`) is `KG` and the `Cant.` column contains the measured weight (e.g. `0,878`). Today, extraction + preview/import flows assume weight comes from parsing the product name (e.g. `1 KG`) and that `Cant.` is a unit count. This causes incorrect weight defaults and downstream failures/incorrect computations.

Goal: Make `KG` rows first-class, so we can reliably set `weight_kg` from the invoice table (not from the name), and allow stock movement quantity to remain `1` for these weighed lines when desired.

## Problem Statement / Motivation

- `KG` rows often include a generic size token in the name (`1 KG`) that does **not** match the measured weight in `Cant.`.
- The extractor prompt currently biases quantity toward “usually integers”, which can increase the chance that `Cant.` decimals are mis-mapped.
- The backend currently only derives `weight_kg_candidate` from product names (`src/invproc/api.py:_add_row_metadata`), which is wrong for these `KG` rows.

## Proposed Solution (Recommended)

### 1) Extract `uom` per row

Extend the LLM extraction schema to include an optional `uom` field for each product row, sourced from the packaging/UOM column (commonly labeled `Mod amb`).

Update prompt guidance in `src/invproc/llm_extractor.py:_get_system_prompt` to:
- Explicitly extract `uom` from the UOM column via vertical alignment.
- Clarify that `Cant.` can be a decimal for `KG` rows.

### 2) Set weight candidate from `Cant.` when `uom == "KG"`

Update `src/invproc/api.py:_add_row_metadata` so that:
- If `product.uom == "KG"`, use `product.quantity` as the `weight_kg_candidate` (measured weight from invoice).
- Otherwise, keep existing behavior: parse weight from the product name via `parse_weight_candidate`.

This preserves existing behavior for non-`KG` invoices while fixing the `KG` case deterministically.

### 3) Stock movement semantics (scope clarification)

User decision: only stock movements must reflect `quantity = 1` for `KG` rows.

This repo can support that in one of two ways:

- **Option A (frontend responsibility, minimal backend):** frontend uses extracted `uom` to send `quantity=1` for `KG` rows to `POST /invoice/import`, while sending `weight_kg` as the measured weight.
- **Option B (backend enforcement):** extend `InvoicePreviewRow` to include optional `uom`, and in `InvoiceImportService.import_rows` use `stock_quantity = 1` when `uom=="KG"` (leave pricing computation using `row.quantity` unchanged unless explicitly requested).

Recommend starting with Option A if the frontend is easy to update; implement Option B if we want the backend to guarantee stock semantics.

## Technical Considerations

- **Backward compatibility:** `uom` must be optional; older clients should continue to work.
- **Prompt stability:** keep the column-alignment emphasis from existing best practices to avoid column swaps. See `docs/solutions/best-practices/llm-column-swap-prevention-spatial-layout-invoice-extraction-20260202.md`.
- **Validation:** `product.quantity` currently participates in confidence scoring (math checks). If we keep `quantity = Cant.` for extraction, math remains stable; if we ever rewrite extraction quantity to `1`, validator logic must be updated accordingly.
- **Data model:** `Product` model in `src/invproc/models.py` needs a new optional `uom: Optional[str]` field (string, normalized to uppercase).

## Acceptance Criteria

- For a line like `... 1 KG 0,878 149,92 ... 150,04` where UOM column is `KG`:
  - `/extract` returns the product with `uom == "KG"`.
  - `/extract` returns `weight_kg_candidate == 0.878` (from `Cant.`), not `1.0` (from parsing `1 KG` in the name).
- For non-`KG` rows:
  - `/extract` behavior is unchanged: `weight_kg_candidate` is still derived from `parse_weight_candidate(name)` when parseable.
- (If implementing backend enforcement Option B) importing a `KG` row creates stock movement with `quantity=1`.

## Success Metrics

- Reduction in “missing weight” / manual correction for weighed `KG` rows during import.
- No regression in extraction correctness for existing test invoices (non-`KG`).

## Dependencies & Risks

- Requires updating the LLM prompt + normalization code in `src/invproc/llm_extractor.py` and `src/invproc/models.py`.
- If OCR layout is inconsistent, UOM extraction could be brittle; mitigate by keeping `uom` optional and falling back to name parsing when `uom` is missing.

## Implementation Plan (Phased)

### Phase 1: Extraction + metadata parity (backend-only)

- Add `uom: Optional[str]` to `Product` in `src/invproc/models.py`.
- Update `src/invproc/llm_extractor.py`:
  - Prompt: require `uom` extraction and allow decimal `Cant.` for `KG`.
  - Normalization: pass through `uom` (strip, uppercase, null if empty).
- Update `src/invproc/api.py:_add_row_metadata`:
  - If `uom=="KG"` and `quantity` is present, set `weight_kg_candidate=quantity` and skip name parsing.
  - Else, current behavior.

### Phase 2: Stock movement behavior (optional, if backend enforcement desired)

- Extend `InvoicePreviewRow` in `src/invproc/models.py` to include `uom: Optional[str]`.
- In `InvoiceImportService.import_rows`, use `stock_quantity = 1` when `row.uom == "KG"`.

## Test Plan

- Add unit test ensuring `_add_row_metadata` prefers `Cant.` when `uom=="KG"` and ignores `1 KG` in name.
- Update/extend API tests to assert `uom` is present in `/extract` response (for a mocked extraction payload).

## Quality Gates

Run locally before merge-ready:

- `python -m ruff check src/ tests/`
- `python -m mypy src/`
- `python -m pytest -q`

## References

- Brainstorm: `docs/brainstorms/2026-02-23-kg-weight-quantity-invoices-brainstorm.md`
- Column alignment best practice: `docs/solutions/best-practices/llm-column-swap-prevention-spatial-layout-invoice-extraction-20260202.md`
- Current weight derivation: `src/invproc/api.py:_add_row_metadata`
- Current prompt guidance: `src/invproc/llm_extractor.py:_get_system_prompt`

