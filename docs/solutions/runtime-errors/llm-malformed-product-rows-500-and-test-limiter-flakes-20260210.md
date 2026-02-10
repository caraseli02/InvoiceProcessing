---
module: System
date: 2026-02-10
problem_type: runtime_error
component: tooling
symptoms:
  - "POST /extract returned 500 Internal Server Error with Pydantic ValidationError for products.*.quantity/unit_price"
  - "LLM occasionally returned null numeric fields for some product rows"
  - "API tests intermittently failed with unexpected 429 Too Many Requests when suites were run together"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [llm-output, pydantic, fastapi, test-isolation, rate-limiter]
---

# Troubleshooting: Malformed LLM Product Rows Caused 500s and Test Limiter State Caused Flakes

## Problem
Invoice extraction failed intermittently even with valid PDFs. The backend crashed with validation errors when the model returned partial rows, and test runs became nondeterministic because rate-limit state leaked across tests.

## Environment
- Module: System
- Affected Component: FastAPI extraction pipeline (`/extract`) and API test fixtures
- Date: 2026-02-10

## Symptoms
- API logs showed:
  - `ValidationError: products.42.quantity ... input_value=None`
  - `ValidationError: products.42.unit_price ... input_value=None`
- Endpoint response became `500 Internal Server Error` for malformed model payloads.
- Combined test runs (`tests/test_error_paths.py` + `tests/test_api.py`) intermittently failed with `429` due shared limiter state.

## What Didn't Work

**Attempted Solution 1:** Accept malformed rows by dropping them silently during normalization.
- **Why it failed:** It avoided crashes but introduced hidden data loss risk (successful response could omit invoice lines without client-visible signal).

**Attempted Solution 2:** Run affected tests in isolation.
- **Why it failed:** It masked the fixture isolation problem; failures returned when files ran together or in different order.

## Solution

The fix had three coordinated parts:

1. Added explicit integrity error handling for malformed model rows.
2. Mapped that error to a client-visible `422` response instead of generic `500`.
3. Reset SlowAPI limiter state in test fixtures to isolate tests.

**Code changes:**

```python
# llm_extractor.py
class LLMOutputIntegrityError(ValueError):
    pass

if dropped_products:
    raise LLMOutputIntegrityError(
        f"LLM returned {dropped_products} malformed product rows"
    )
```

```python
# api.py
except LLMOutputIntegrityError as e:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(e),
    )
```

```python
# tests/test_api.py and tests/test_error_paths.py (fixtures)
limiter.reset()
```

Also updated `/extract` OpenAPI metadata to include `422` and `504` responses.

## Why This Works
1. The runtime crash path is converted from internal failure (`500`) to explicit client contract (`422`).
2. Data integrity is protected by refusing to silently accept malformed product rows.
3. Test reliability improves because shared limiter counters no longer bleed between test cases/suites.

## Prevention
- Treat malformed LLM structure as a first-class API outcome, not an internal exception.
- Avoid silent normalization that can drop business data without surfacing it.
- For global middleware state (rate limits, caches), reset state in test fixtures or provide test-mode isolation.
- Keep FastAPI `responses` metadata synchronized whenever new exception mappings are introduced.

## Related Issues
- See also: [global-state-thread-safety-race-conditions.md](./global-state-thread-safety-race-conditions.md)
- Related robustness hardening: [multipart-upload-size-enforcement-system-20260210.md](../security-issues/multipart-upload-size-enforcement-system-20260210.md)

## Verification

Commands run after fix:

```bash
python -m pytest tests/test_api.py tests/test_error_paths.py tests/test_config.py -q
python -m pytest tests/test_api.py -q
```

Result:
- `46 passed`
- `11 passed`
