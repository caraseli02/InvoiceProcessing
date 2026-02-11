---
title: "feat: OpenAI Extraction Cache by File Hash"
type: "feat"
date: "2026-02-11"
source: "docs/brainstorms/2026-02-11-extract-openai-cache-by-file-hash-brainstorm.md"
---

# feat: OpenAI Extraction Cache by File Hash

## Overview

Add a backend cache for `POST /extract` keyed by uploaded PDF content hash so repeated uploads of the same file can reuse a prior extraction result and skip new OpenAI calls.

Primary goals:
- Reduce OpenAI cost for duplicate files.
- Improve response latency for repeated requests.
- Preserve existing API contract (`InvoiceData`) and validation flow.

## Problem Statement / Motivation

`/extract` currently always runs full pipeline: save upload -> PDF processing -> OpenAI extraction -> validation -> row metadata.

Current orchestration is in `src/invproc/api.py:193`. LLM call happens in `src/invproc/api.py:225` via `LLMExtractor.parse_with_llm` (`src/invproc/llm_extractor.py:31`). There is no extraction-result cache.

For repeated uploads of the same invoice (manual retries, frontend re-uploads, QA runs), we pay the same OpenAI cost and latency each time.

## Idea Refinement Input

No matching brainstorm file was present at `docs/brainstorms/2026-02-11-extract-openai-cache-by-file-hash-brainstorm.md` in this worktree. Planning proceeded from the feature intent in the filename and current codebase patterns.

## Local Research Summary

### Internal References

- `/extract` flow and error mapping: `src/invproc/api.py:178`
- LLM call boundary: `src/invproc/llm_extractor.py:31`
- Config singleton and validation style: `src/invproc/config.py:28`
- Existing hash/idempotency pattern: `src/invproc/import_service.py:194`
- Existing API test style and fixture reset patterns: `tests/test_api.py:14`

### Institutional Learnings Applied

- Keep blocking work out of event loop with `run_in_threadpool`: `docs/solutions/performance-issues/blocking-io-async-prevents-concurrency.md`
- Avoid unsafe shared mutable globals for request logic: `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md`
- Reset shared state in tests to avoid flakiness (especially limiters/caches): `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`

## External Research Decision

External research included because this touches OpenAI API behavior and caching strategy.

Key references:
- OpenAI Prompt Caching overview: [OpenAI Prompt Caching](https://platform.openai.com/docs/guides/prompt-caching)
- OpenAI API reference: [Responses API](https://platform.openai.com/docs/api-reference/responses)

Decision: keep this feature as an application-level file hash cache. Do not rely on prompt caching alone, since prompt caching does not eliminate request execution and does not provide deterministic per-file replay semantics.

## Proposed Solution

Implement an in-memory extraction cache keyed by PDF content hash (`sha256`) and validated against relevant config dimensions.

### Scope (MVP)

- Cache key includes:
  - `file_hash` (hash of uploaded bytes)
  - extraction-affecting config snapshot (`model`, `temperature`, `max_tokens`, invoice parsing headers)
- Cache value stores:
  - validated `InvoiceData` response payload (`model_dump(mode="json")`)
  - metadata (`created_at`, `hit_count`)
- Cache location:
  - process-local in-memory component (new module)
- Endpoint behavior:
  - On cache hit, return cached payload after model re-hydration.
  - On cache miss, execute existing pipeline and persist response in cache.

### Explicit Non-Goals (MVP)

- Distributed/shared cache (Redis, DB) across workers.
- Long-term persistence across process restarts.
- Automatic invalidation based on model quality changes beyond key dimensions.

## Technical Considerations

- Concurrency/thread safety:
  - Cache must be lock-protected, matching existing thread-safe patterns (`src/invproc/repositories/memory.py:11`).
- Event loop safety:
  - Hashing large files should not block async path excessively; keep heavy operations in threadpool where needed.
- Memory limits:
  - Add bounded size and TTL to avoid unbounded growth.
- Correctness:
  - Must never return stale payload for incompatible config/model values.

## SpecFlow Analysis

### Primary Flow

1. Client uploads PDF to `POST /extract`.
2. API streams to temp file and computes file hash.
3. API checks extraction cache with key (`file_hash` + config signature).
4. If hit: return cached `InvoiceData`.
5. If miss: run extract -> LLM -> validate -> metadata enrichment.
6. Store successful response in cache and return payload.

### Edge Cases

- Same file bytes, different `model` or `column_headers` config: must miss.
- OpenAI timeout/error responses: must not cache failures.
- Malformed LLM output (`422`): must not cache.
- Rate limited (`429`) or auth failures (`401`): unaffected by cache.
- Multi-worker deployment: per-process cache inconsistency is acceptable in MVP; document it.

### Gaps Closed by Plan

- No duplicate OpenAI calls for same file in one process.
- Deterministic cache invalidation via key signature, not manual flush.

## Implementation Plan

### Phase 1: Cache primitive and config

- [x] `src/invproc/config.py`: add cache settings (`extract_cache_enabled`, `extract_cache_ttl_sec`, `extract_cache_max_entries`).
- [x] `src/invproc/extract_cache.py`: add thread-safe in-memory cache with TTL + max entries.
- [x] `tests/test_config.py`: add config validation/default tests for new cache settings.

### Phase 2: API integration

- [x] `src/invproc/api.py`: add helper to compute `file_hash` (`sha256`) from uploaded temp file.
- [x] `src/invproc/api.py`: build config signature helper for cache key.
- [x] `src/invproc/api.py`: check cache before `pdf_processor.extract_content` and write cache on successful response.
- [x] `src/invproc/api.py`: ensure exception paths do not populate cache.

### Phase 3: Tests and observability

- [x] `tests/test_api.py`: add hit/miss behavior tests (same file twice -> second call bypasses LLM mock invocation).
- [x] `tests/test_api.py`: add tests proving config signature change forces miss.
- [x] `tests/test_api.py`: add tests proving error responses are not cached.
- [x] `src/invproc/api.py`: add low-noise logs/headers for diagnostics (e.g., cache hit/miss log line).

## Acceptance Criteria

- [x] Re-uploading identical PDF with same cache key dimensions returns same `InvoiceData` without new OpenAI call.
- [x] Cache miss occurs when extraction-affecting config signature changes.
- [x] `401`, `422`, `429`, `500`, `504` responses are never cached.
- [x] Cache has bounded growth (TTL + max entries enforced).
- [x] All existing API tests pass; new cache tests pass.

## Success Metrics

- Cache hit ratio for `/extract` duplicate-file traffic is measurable.
- OpenAI request count decreases for duplicate-file workloads.
- Median response time improves on repeated uploads.

## Dependencies & Risks

### Dependencies

- Existing `POST /extract` flow in `src/invproc/api.py` remains source of truth.
- Config management through singleton `get_config()` in `src/invproc/config.py:254`.

### Risks

- Process-local cache may have low hit rates under multi-worker deployment.
- Memory pressure if cache bounds are misconfigured.
- Incorrect cache key dimensions could serve stale/incorrect payloads.

### Mitigations

- Keep key signature explicit and versioned.
- Use conservative defaults for TTL and max entries.
- Add targeted tests for invalidation dimensions.

## References & Research

### Internal

- `src/invproc/api.py:193`
- `src/invproc/llm_extractor.py:31`
- `src/invproc/import_service.py:194`
- `docs/solutions/performance-issues/blocking-io-async-prevents-concurrency.md`
- `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md`
- `docs/solutions/runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md`

### External

- [OpenAI Prompt Caching](https://platform.openai.com/docs/guides/prompt-caching)
- [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses)
