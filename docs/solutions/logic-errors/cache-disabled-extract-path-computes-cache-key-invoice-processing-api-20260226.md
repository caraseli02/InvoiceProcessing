---
module: Invoice Processing API
date: 2026-02-26
problem_type: logic_error
component: service_object
symptoms:
  - "Refactor moved extract orchestration into a service and the cache-disabled path still computed cache key/signature data"
  - "Disabled-cache execution unnecessarily depended on cache-signature config fields (including column_headers.model_dump)"
  - "Regression was only visible in review because tests provided a fully populated config object"
root_cause: logic_error
resolution_type: code_fix
severity: medium
tags: [extract-cache, refactor, regression, fastapi, tests]
related:
  - todos/049-pending-p2-extract-cache-disabled-path-computes-cache-key.md
  - docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md
---

# Troubleshooting: Cache-Disabled Extract Path Still Computed Cache Key After Refactor

## Problem

During a service-layer refactor of `/extract`, cache-key generation moved into `src/invproc/services/extract_service.py`. The new implementation computed the cache key before checking `extract_cache_enabled`, so the cache-disabled path still performed signature hashing and depended on cache-signature config fields.

## Environment

- Module: Invoice Processing API
- Affected Component: Extract orchestration service (`run_extract_pipeline`)
- Date: 2026-02-26

## Symptoms

- `run_extract_pipeline()` built cache key/signature data even when `config.extract_cache_enabled` was `False`.
- Disabled-cache execution still touched cache-signature inputs such as `config.column_headers.model_dump(...)`.
- Existing tests did not catch the regression because the disabled-cache test used a complete config stub.

## What Didn't Work

**Attempted Solution 1:** Rely on existing cache enabled/disabled tests.
- **Why it failed:** Tests asserted returned cache status but did not verify lazy behavior (that cache-key builder is never called when cache is disabled).

**Direct solution:** The problem was identified in code review and fixed immediately.

## Solution

Moved cache-key generation into the `extract_cache_enabled` branch and added a regression test that fails if the cache-disabled path calls `build_extract_cache_key()`.

**Code changes**:
```python
# Before (regression):
cache_key = build_extract_cache_key(config, file_hash)
if config.extract_cache_enabled:
    ...

# After (fixed):
cache_key: str | None = None
if config.extract_cache_enabled:
    cache_key = build_extract_cache_key(config, file_hash)
    ...

if config.extract_cache_enabled:
    assert cache_key is not None
    cache.set(cache_key, validated_invoice.model_dump(mode="json"))
```

**Regression test**:
```python
with patch(
    "invproc.services.extract_service.build_extract_cache_key",
    side_effect=AssertionError("cache key builder should not run when cache is disabled"),
):
    result = run_extract_pipeline(... extract_cache_enabled=False ...)
```

**Commands run**:
```bash
python -m pytest -q --no-cov tests/test_extract_service.py tests/test_row_enrichment_service.py tests/test_upload_service.py tests/test_uom_kg_weight_candidate.py
```

## Why This Works

The root cause was not incorrect cache behavior for enabled mode, but a control-flow regression introduced by the refactor: cache-key generation happened before the feature flag branch. Moving cache-key construction into the cache-enabled branch restores the original semantics and removes unnecessary hashing/serialization work when caching is off.

The added test closes the gap by asserting behavior, not just output status. It prevents future refactors from reintroducing eager cache-key computation in disabled mode.

## Prevention

- When extracting route logic into services, preserve feature-flag boundaries exactly (compute gated values only inside the gated branch).
- Add regression tests for lazy behavior when refactoring orchestration code (for example, patch helper functions to fail if called unexpectedly).
- In code review, compare control-flow placement of expensive or dependency-heavy helper calls before vs after refactors.

## Related Issues

- Review todo: `todos/049-pending-p2-extract-cache-disabled-path-computes-cache-key.md`
- Related cache workflow documentation:
  - `docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md`
