---
title: "feat: Extract OpenAI cache by file hash"
type: "feat"
date: "2026-02-11"
source_brainstorm: "docs/brainstorms/2026-02-11-extract-openai-cache-by-file-hash-brainstorm.md"
---

# feat: Extract OpenAI cache by file hash

## Overview

Add an in-memory cache for `POST /extract` so re-uploading the exact same PDF bytes returns the previous extraction result without calling OpenAI again.

Cache key will be `sha256` of raw uploaded PDF bytes. Scope is per-process memory (single instance).

## Problem Statement / Motivation

Current extraction flow always performs full processing and LLM call:
- Save upload
- Extract text grid
- Call LLM
- Validate
- Return payload

For repeated uploads of the same invoice, this increases:
- latency
- OpenAI cost
- failure surface (timeouts/rate limits)

The team already aligned on MVP scope in brainstorm: deterministic exact-file matching with no infra dependencies.

## Local Research Summary

### Internal references
- `POST /extract` request flow: `src/invproc/api.py:178`
- LLM call entrypoint: `src/invproc/api.py:225`
- Existing request hashing pattern (idempotency): `src/invproc/import_service.py:194`
- Config singleton usage: `src/invproc/config.py:254`
- API tests for extract auth/behavior: `tests/test_api.py:48`

### Institutional learnings applied
- Keep request-safe state and avoid brittle globals:
  `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md`
- Blocking operations in async route should stay in threadpool:
  `docs/solutions/performance-issues/blocking-io-async-prevents-concurrency.md`
- Global middleware/cache-like state must be reset in tests:
  `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`

## Research Decision

External research skipped.

Reason: this is a low-risk local optimization with clear existing repo patterns (hashing + FastAPI endpoint orchestration + in-memory test reset practices).

## SpecFlow Analysis

### Primary flow: cache miss
1. Client uploads PDF to `POST /extract`.
2. Backend computes file hash from uploaded bytes.
3. Cache miss -> run normal extraction pipeline.
4. Backend stores validated response payload under hash.
5. Backend returns extraction response.

### Primary flow: cache hit
1. Client uploads same PDF bytes.
2. Backend computes same file hash.
3. Cache hit -> return cached payload.
4. LLM call is skipped.

### Edge cases
- Different filename, same bytes -> same cache key (hit expected).
- Same semantic invoice but different bytes/metadata -> cache miss (expected for MVP exact-match).
- Very high unique-file churn -> memory growth risk.
- Multi-instance deployment -> cache only per instance.

## Proposed Solution

### Design
- Add a small in-memory extraction cache module/state.
- Key: `sha256(pdf_bytes)`.
- Value: serialized `InvoiceData` payload (JSON-compatible dict).
- Endpoint behavior remains identical to clients.

### API behavior
- No contract change required.
- Optional debug response header may be added later (`X-Extract-Cache: hit|miss`) if needed.

### Scope boundaries
- In scope: `/extract` cache only.
- Out of scope: Redis/shared cache, dedupe across instances, cache invalidation APIs.

## Technical Approach

### Files to update
- `src/invproc/api.py`
  - Compute hash of uploaded file bytes for cache key.
  - Read/write in-memory cache in extract flow.
  - Keep existing error mapping behavior.
- `src/invproc/config.py`
  - Optional flags for cache enablement/size (if needed in MVP).
- `tests/test_api.py`
  - Add cache hit/miss behavior tests using mocked LLM call count.
- `tests/test_error_paths.py`
  - Ensure cache state does not leak between tests.

### Implementation notes
- Keep PDF upload size enforcement unchanged.
- Avoid adding mutable request state outside controlled cache structure.
- Use lock-safe access if cache may be touched concurrently.
- Store a copy/serialized payload to avoid accidental mutation by callers.

## Acceptance Criteria

### Functional
- [ ] First upload of a PDF hash performs full extraction and caches result.
- [ ] Second upload of identical PDF bytes returns same payload without LLM call.
- [ ] Different file bytes still execute normal extraction path.
- [ ] Endpoint response schema remains unchanged.

### Non-functional
- [ ] No regression in auth, rate limiting, upload-size enforcement.
- [ ] Concurrent requests do not corrupt cache state.
- [ ] Tests deterministic and isolated (cache reset between tests).

### Testing
- [ ] Unit/API test for miss then hit sequence.
- [ ] Test that same bytes with different filename still hit.
- [ ] Test that changed bytes miss.
- [ ] Full suite remains green.

## Success Metrics

- Reduced repeated `/extract` latency for identical files.
- Reduced OpenAI calls for repeated uploads in local/single-instance workflows.
- No new 5xx regressions introduced by cache.

## Dependencies & Risks

### Dependencies
- None external for MVP in-memory cache.

### Risks
- Memory growth from unbounded unique uploads.
- Cache effectiveness limited in multi-instance deployments.
- Hidden coupling if cache object is globally mutable without protection.

### Mitigations
- Add bounded size (LRU) or explicit max entries soon after MVP.
- Document per-instance behavior clearly.
- Reset cache in test fixtures.

## Rollout / Follow-ups

### MVP now
- In-memory exact-file hash cache.

### Later (if needed)
- Configurable TTL/LRU bounds.
- Shared Redis cache for multi-instance environments.
- Lightweight observability (hit/miss counters or debug header).

## References & Research

- Brainstorm: `docs/brainstorms/2026-02-11-extract-openai-cache-by-file-hash-brainstorm.md`
- Endpoint flow: `src/invproc/api.py:178`
- Idempotent hashing pattern: `src/invproc/import_service.py:194`
- Thread-safety learning: `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md`
- Async blocking guidance: `docs/solutions/performance-issues/blocking-io-async-prevents-concurrency.md`
- Test state reset learning: `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`
