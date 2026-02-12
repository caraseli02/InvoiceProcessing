---
title: "feat: Add category suggestion to /extract products"
type: "feat"
date: "2026-02-12"
source_brainstorm: "docs/brainstorms/2026-02-12-invoice-product-category-autofill-brainstorm.md"
---

# feat: Add category suggestion to /extract products

## Overview

Extend `POST /extract` to optionally return a best-effort per-product `category_suggestion` derived from the extracted product name, constrained to the frontend’s fixed category set:

`General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale`.

Conflict resolution stays in the frontend. Backend only emits suggestions.

## Problem Statement / Motivation

Invoice import UI currently defaults category to `General`, forcing manual category selection. Frontend already has inference, but having the extractor emit a suggestion enables:
- better initial defaults (especially when product name is clear)
- fewer user clicks during invoice review
- a stable contract for future improvements (rules/memory/AI) without changing the extractor shape again

## Local Research Summary

### Internal references
- `/extract` endpoint and response model: `src/invproc/api.py:178`
- LLM extraction + payload normalization: `src/invproc/llm_extractor.py:27`
- Pydantic response schema (Product fields): `src/invproc/models.py:7`
- Prompt regression tests: `tests/test_llm_prompt_guidance.py:1`
- `/extract` API tests: `tests/test_api.py:1`

### Institutional learnings applied
- Contract alignment matters; keep changes additive + explicit:
  `docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md`
- Avoid letting malformed model output crash extraction; normalize before Pydantic:
  `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`
  `docs/solutions/runtime-errors/zero-valued-llm-product-rows-caused-extract-500-20260211.md`

## Research Decision

External research skipped.

Reason: this is a low-risk additive contract change in a well-understood local flow (Pydantic schema + prompt + normalization + tests).

## SpecFlow Analysis

### Primary flow
1. Client uploads PDF to `POST /extract`.
2. Backend runs the existing pipeline (PDF -> text grid -> LLM -> validation).
3. LLM may include optional `category_suggestion` per product.
4. Backend normalizes the field:
   - unknown values -> `null`
   - known values -> pass through
5. Response includes the optional field(s). Frontend decides whether/how to apply.

### Edge cases
- LLM omits category entirely: response field absent or `null` (both acceptable).
- LLM outputs unknown string: backend coerces to `null` to protect UI filters/i18n.
- Prompt changes regress extraction quality: mitigate via small prompt changes and unit tests on normalization.

## Proposed Solution

### Contract (additive)
Add optional fields on each product in `/extract` response:
- `category_suggestion?: <official category> | null`

Optional (recommended) extra metadata fields if you want richer frontend behavior later:
- `category_confidence?: float | null` (0-1)
- `category_source?: "llm" | null`

If you want to keep MVP minimal, ship `category_suggestion` only.

### Prompt strategy
No new model calls.

We extend the existing extraction prompt to *optionally* classify `name` into the fixed category set and include it in each product object. The prompt must explicitly forbid free-form categories.

## Technical Approach

### 1) Update Pydantic models (response schema)
File: `src/invproc/models.py`
- Extend `Product` with `category_suggestion: Optional[str] = None`
- If including extra metadata:
  - `category_confidence: Optional[float] = Field(None, ge=0, le=1)`
  - `category_source: Optional[Literal["llm"]] = None`

Note: keep the field optional to remain backwards compatible and avoid failing on missing category output.

### 2) Normalize and validate category values
File: `src/invproc/llm_extractor.py`
- Add a constant set/list of allowed categories.
- In `_normalize_invoice_payload()`:
  - Read product["category_suggestion"] if present.
  - Coerce to string and strip.
  - If not in allowed set, set to `None`.
  - For `category_confidence`, use `_to_float()` and clamp to `[0, 1]`.
  - For `category_source`, coerce to `"llm"` or `None`.

### 3) Update system prompt schema (optional output)
File: `src/invproc/llm_extractor.py`
- In `_get_system_prompt()`:
  - Add a short instruction: infer `category_suggestion` from product name when confident.
  - Provide the exact allowed list.
  - Clarify: if uncertain, set `null`.
- In OUTPUT FORMAT:
  - Add the optional keys in each product object.

Keep this prompt addition small to reduce risk of regression in column extraction.

### 4) Tests

Files:
- `tests/test_llm_prompt_guidance.py`
  - Add assertions that the prompt contains:
    - the allowed category list
    - the “choose one of these or null” rule
- Add new test file (or extend an existing one):
  - `tests/test_llm_category_normalization.py`
  - Directly call `LLMExtractor(InvoiceConfig(mock=True))._normalize_invoice_payload(...)` with:
    - valid category -> preserved
    - invalid category -> coerced to null
    - non-string category -> coerced to null
    - confidence out of range -> clamped (if implemented)
- `tests/test_api.py`
  - Add an assertion that `category_suggestion` exists as a key on product rows (can be `null`) OR explicitly allow absence.
    - Prefer: ensure response stays 200 and includes the new key only if present to avoid brittle tests.

### 5) Docs (optional)
File: `README.md`
- Update `/extract` response example to include `category_suggestion` (optional).

## Acceptance Criteria

### Functional
- [x] `POST /extract` response supports optional `category_suggestion` per product without breaking existing clients.
- [x] `category_suggestion` is always one of the official categories or `null` (never arbitrary strings).
- [x] Unknown/invalid category values from LLM are coerced to `null`.

### Non-functional
- [x] No new 4xx/5xx due to category parsing (normalization prevents schema failures).
- [x] Prompt additions are minimal and do not materially increase token usage.

### Testing
- [x] Unit coverage for category normalization rules.
- [x] Prompt guidance test updated.
- [x] API test suite remains green.

## Dependencies & Risks

Dependencies:
- none (uses the existing LLM call)

Risks:
- prompt change could reduce extraction accuracy
- category suggestions may be noisy early on

Mitigations:
- keep prompt change small + explicit “null if unsure”
- treat `category_suggestion` as advisory only; frontend decides application

## References

- Brainstorm: `docs/brainstorms/2026-02-12-invoice-product-category-autofill-brainstorm.md`
- Extract endpoint: `src/invproc/api.py:178`
- Extract normalization: `src/invproc/llm_extractor.py:73`
- Prompt tests: `tests/test_llm_prompt_guidance.py:1`
- Contract alignment learning: `docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md`
