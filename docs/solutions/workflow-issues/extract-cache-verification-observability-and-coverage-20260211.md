---
module: Invoice Processing API
date: 2026-02-11
problem_type: workflow_issue
component: development_workflow
symptoms:
  - "Manual cache verification for POST /extract was ambiguous because runtime logs did not show cache hit/miss signals"
  - "Timing-only checks were noisy and could not reliably prove cache behavior"
  - "New cache primitive had no direct TTL/LRU unit tests, leaving eviction/expiry guarantees under-specified"
root_cause: missing_workflow_step
resolution_type: workflow_improvement
severity: medium
tags: [extract-cache, observability, testing, lru, ttl, fastapi]
related:
  - docs/plans/2026-02-11-feat-openai-extraction-cache-file-hash-plan.md
  - todos/001-complete-p2-cache-observability-hit-miss-signals.md
  - todos/002-complete-p2-extract-cache-ttl-lru-tests.md
---

# Troubleshooting: Extract Cache Verification Needed Explicit Signals and Invariant Tests

## Problem

After implementing file-hash caching for `POST /extract`, manual validation remained uncertain. Server logs did not reliably surface cache hit/miss status in common `uvicorn` runs, and timing-based checks alone were insufficient to prove behavior. In parallel, cache internals (TTL expiry and LRU eviction) were not directly tested.

## Environment

- Module: Invoice Processing API
- Affected Component: `/extract` cache verification workflow and test coverage
- Date: 2026-02-11

## Symptoms

- Two identical `/extract` calls returned `200`, but no clear runtime signal showed whether second call was a cache hit.
- Teams relied on elapsed time as proxy, which is unstable with small files, local variance, and network jitter.
- Review identified missing direct tests for `InMemoryExtractCache` TTL and LRU behavior.

## What Didn't Work

**Attempted Solution 1:** Validate cache solely by response timing.
- **Why it failed:** Timing deltas are not deterministic and can mislead under variable runtime conditions.

**Attempted Solution 2:** Depend on module logger messages for cache status.
- **Why it failed:** Logger output was not consistently visible in runtime logs in the current launch path, creating an observability gap.

## Solution

Implemented two complementary fixes:

1. **Deterministic runtime signal in API responses**
- Added `X-Extract-Cache` response header on `/extract` when cache is enabled.
  - `miss` for cache miss path
  - `hit` for cache hit path
- File: `src/invproc/api.py`

2. **Direct unit tests for cache invariants**
- Added dedicated tests for `InMemoryExtractCache`:
  - basic hit/miss
  - TTL expiry behavior
  - LRU eviction behavior
  - `configure()` capacity prune behavior
- File: `tests/test_extract_cache.py`

3. **Strengthened existing API tests**
- Asserted `X-Extract-Cache` transitions (`miss` then `hit`) for identical requests.
- Asserted miss after extraction-affecting config change.
- File: `tests/test_api.py`

## Why This Works

- Header-based signaling makes cache behavior explicit and environment-independent for manual verification.
- Invariant unit tests verify eviction/expiry guarantees directly instead of inferring them from endpoint behavior.
- Combined API + unit coverage reduces false confidence and catches regressions at both integration and primitive levels.

## Verification

Commands used:

```bash
PYTHONPATH=src python3 -m pytest tests/test_extract_cache.py tests/test_api.py tests/test_config.py -q
python3 -m ruff check src/invproc/api.py tests/test_api.py tests/test_extract_cache.py tests/test_config.py
```

Result at fix time:
- `37 passed`
- `All checks passed!`

## Prevention

- Treat cache behavior as a first-class contract with deterministic observability (`X-Extract-Cache`) for manual debugging.
- Always add direct unit tests for new cache primitives (TTL, eviction, reconfiguration), not only endpoint-level tests.
- Avoid using timing as primary correctness proof for cache behavior.
- In PR reviews, require both:
  - one integration test for hit/miss behavior
  - one primitive-level test suite for cache invariants

## Related Issues

- Plan: `docs/plans/2026-02-11-feat-openai-extraction-cache-file-hash-plan.md`
- Todo closure records:
  - `todos/001-complete-p2-cache-observability-hit-miss-signals.md`
  - `todos/002-complete-p2-extract-cache-ttl-lru-tests.md`
