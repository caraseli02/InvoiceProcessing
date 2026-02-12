---
module: Invoice Processing
date: 2026-02-12
problem_type: integration_issue
component: tooling
symptoms:
  - "/extract response did not include any category hint, so UI defaulted to General and required manual edits"
  - "Free-form/incorrect category strings would break strict UI filtering and i18n expectations"
root_cause: missing_validation
resolution_type: code_fix
severity: low
tags: [api-contract, extract, category, enum, llm, pydantic]
---

# Troubleshooting: Add Enum-Only `category_suggestion` To `/extract` Products

## Problem
Frontend invoice review needed a best-effort category hint per extracted product row to reduce manual chip selection, but `/extract` had no category field and no way to guarantee enum correctness.

## Environment
- Module: Invoice Processing
- Affected Component: `/extract` response contract + LLM normalization
- Date: 2026-02-12

## Symptoms
- Invoice review UI defaulted rows to `"General"` (and could persist that), even when the product name strongly implied a different category.
- Any non-enum category strings (wrong case like `general`, localized labels, or new labels) would be unsafe because the UI expects a fixed category set.

## What Didn't Work

**Attempted Solution 1:** Treat `"General"` as a default when unsure.
- **Why it failed:** `"General"` is a real category and can be persisted by existing UI flows; guessing it creates silent misclassification.

**Attempted Solution 2:** Accept free-form category strings from the model.
- **Why it failed:** It breaks strict filtering and makes i18n/labels inconsistent. The UI needs exact enum values.

## Solution

Add an additive-only optional field to each extracted product:
- `category_suggestion`: **must be exactly one of**
  `General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale`
  **or** `null` when unsure.

Enforcement is done in two layers:
1. **Schema:** Pydantic constrains the field to an enum (`Literal[...]`) and keeps it optional.
2. **Normalization:** LLM output is normalized so any unknown/wrong-case value becomes `null`.

Key files:
- Response model: `src/invproc/models.py`
- Normalization + prompt guidance: `src/invproc/llm_extractor.py`
- Tests:
  - `tests/test_api.py`
  - `tests/test_llm_prompt_guidance.py`
  - `tests/test_llm_category_normalization.py`

## Why This Works
1. Frontend can rely on a stable additive contract: existing keys/types unchanged, with a new optional `category_suggestion`.
2. Enum-only + `null` semantics prevent accidental persistence of `"General"` as a guessed default.
3. Normalization makes the contract robust against noisy LLM output (wrong casing, localized values, invented labels).
4. Tests lock the contract:
   - prompt explicitly instructs enum-only output and `null` when unsure
   - normalization enforces the enum at the boundary

## Prevention
- Keep the allowed enum list as a single source of truth (to avoid drift between model type, normalization, and prompt).
- Maintain boundary tests:
  - prompt contains the enum-only/null rule
  - normalization coerces non-enum values to `null`
- Treat advisory fields like this as **additive-only**; never repurpose/rename existing keys in `/extract`.

## Related Issues
- See also: `docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md`
- Related normalization hardening: `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`

