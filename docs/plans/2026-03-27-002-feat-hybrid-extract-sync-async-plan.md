---
title: feat: Add hybrid sync-async extract contract
type: feat
status: active
date: 2026-03-27
origin: docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md
---

# feat: Add hybrid sync-async extract contract

## Overview

Add a backend-owned hybrid extraction contract for `POST /extract` so fast invoices still return the current inline extraction payload, while invoices predicted to be slow return `202 Accepted` with a minimal polling envelope and complete asynchronously through a separate job endpoint.

This plan is grounded in the reviewed requirements doc at [docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md). It carries forward the key decisions that the backend owns routing, the async response stays minimal, polling uses a separate endpoint, no mid-request sync-to-async upgrade is allowed, and duplicate submissions collapse onto the canonical existing job (see origin: `docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md`).

## Problem Statement / Motivation

`POST /extract` currently holds a single request open for the full upload -> PDF processing -> LLM extraction -> validation flow in [src/invproc/api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py). Recent validation in this repo showed representative invoices completing in about 81 seconds and 152 seconds, which is too slow for a reliable frontend request/response experience.

The synchronous path has already been improved:

- exact-file extract caching in [src/invproc/services/extract_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/extract_service.py)
- chunked large-invoice extraction in [src/invproc/llm_extractor.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/llm_extractor.py)
- discount/noise pruning in [src/invproc/pdf_processor.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/pdf_processor.py)

Those fixes improved correctness and repeated-request behavior, but they did not change the product contract. Some invoices are still slow enough that the backend should stop making the frontend wait on a single open request. The repo already has strong precedents for durable queue semantics and explicit status endpoints in the RAG subsystem, so the async ownership should live in the backend rather than being improvised in the frontend (see origin: `docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md`).

## Research Summary

### Repo context

- The current extract route streams uploads to disk with size enforcement via [src/invproc/services/upload_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/upload_service.py), authenticates via `verify_supabase_jwt`, and then runs the full extraction pipeline inline in [src/invproc/api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py).
- The current extract cache identity already exists as `file_hash + extract_config_signature` in [src/invproc/services/extract_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/extract_service.py), which is the strongest starting point for async dedupe.
- There is already a queue-focused todo for `/extract` in [todos/013-pending-p1-request-queue-concurrency.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/todos/013-pending-p1-request-queue-concurrency.md), which confirms this direction is repo-grounded rather than speculative.
- Existing tests already cover auth, upload streaming, extract cache behavior, and large-invoice chunk merging in [tests/test_api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_api.py), [tests/test_extract_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_extract_service.py), [tests/test_upload_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_upload_service.py), and [tests/test_error_paths.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_error_paths.py).

### Institutional learnings

- [docs/solutions/runtime-errors/large-invoice-llm-json-truncation-and-discount-page-pruning-20260326.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/runtime-errors/large-invoice-llm-json-truncation-and-discount-page-pruning-20260326.md): large invoices already required chunking and selective page pruning, so there is real long-tail complexity in the extraction path.
- [docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md): extract cache behavior needs explicit observability and tests, not timing-only validation.
- [docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/best-practices/fastapi-app-factory-lifespan-di-resource-lifecycle-invoice-api-20260226.md): app-scoped resources and dependency injection matter here; new extract-job infrastructure should follow the current app-resource pattern instead of reintroducing globals.
- [docs/solutions/security-issues/multipart-upload-size-enforcement-system-20260210.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/security-issues/multipart-upload-size-enforcement-system-20260210.md): upload-size enforcement must remain stream-safe even as async job creation is added.

### External research

Skipped. The repo already has strong local patterns, a fresh requirements document, and a well-scoped contract. External research would add little value relative to local planning needs.

## Proposed Solution

Introduce a small extraction-job subsystem that allows `POST /extract` to choose between:

1. **Fast path:** preserve the current `200 OK` response with the existing `InvoiceData` payload when the invoice is predicted to complete inline.
2. **Async path:** return `202 Accepted` immediately with:

```json
{
  "job_id": "ext_123",
  "status": "queued",
  "status_url": "/invoice/extraction-jobs/ext_123"
}
```

Clients poll:

- `GET /invoice/extraction-jobs/{job_id}`

Non-terminal states:

```json
{
  "job_id": "ext_123",
  "status": "queued"
}
```

or

```json
{
  "job_id": "ext_123",
  "status": "processing"
}
```

Terminal success:

```json
{
  "job_id": "ext_123",
  "status": "succeeded",
  "result": { "...current extract payload..." }
}
```

Terminal failure:

```json
{
  "job_id": "ext_123",
  "status": "failed",
  "error": {
    "code": "EXTRACTION_FAILED",
    "message": "Unable to extract invoice"
  }
}
```

Recommended v1 planning decisions for deferred questions:

- **Canonical dedupe key:** `file_hash + extract_config_signature` from the existing extract service logic
- **Additional 202 headers:** include `Location` and `Retry-After` in addition to `status_url`
- **Auth on job endpoint:** same auth class as `POST /extract`, limited to the submitting principal
- **Initial error code set:** `EXTRACTION_FAILED`, `EXTRACTION_TIMEOUT`, `INVALID_PDF`, `PDF_TOO_LARGE`, `AUTH_REQUIRED`, `JOB_NOT_FOUND`, `JOB_EXPIRED`
- **Retention/expiry:** 24h retention for terminal jobs; expired jobs return `404` with `JOB_EXPIRED`
- **V1 persistence scope:** app-scoped in-memory job store, explicitly bounded to local/single-process behavior for this slice
- **Async execution model:** background thread execution from the API process for v1, reusing the existing extraction service rather than introducing Redis/Celery in this change
- **Preflight trigger for v1:** route async when predicted chunk count is greater than 1, or when cheap input signals indicate likely OCR/large-document cost (page count and text-grid size threshold)

## Technical Considerations

- Preserve the current `InvoiceData` success payload for the synchronous path and for async terminal `result` (see origin: `docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md`).
- Keep upload validation and streaming semantics in the request path even for async jobs. The server still needs to authenticate the caller, enforce size limits, and compute the dedupe identity before routing.
- Reuse [src/invproc/services/extract_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/extract_service.py) as the extraction execution core so sync and async paths do not drift.
- Prefer app-scoped job infrastructure injected through the existing `AppResources` pattern in [src/invproc/dependencies.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/dependencies.py).
- For this implementation slice, start with an app-scoped in-memory job registry plus background execution worker. This keeps the change set bounded and fits the current local frontend-over-local-backend validation goal. Document the single-process limitation explicitly in the plan and code comments so future durable persistence work is not obscured.
- Any preflight heuristic must remain cheap. It should avoid entering the full LLM path before deciding async.

## System-Wide Impact

- **API contract:** [src/invproc/api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) will gain mixed `200`/`202` behavior on `POST /extract` plus a new `GET /invoice/extraction-jobs/{job_id}` endpoint.
- **Auth surface:** the new job endpoint must align with the existing `verify_supabase_jwt` semantics so frontend access remains consistent with the submission endpoint.
- **Persistence boundary:** this feature introduces a new durable job/result lifecycle that must coexist with existing extract cache behavior without confusing “cached result” and “job status”.
- **Process boundary limitation:** because the v1 slice is planned as app-scoped in-memory state, jobs will not survive process restarts and will not dedupe across multiple API instances.
- **Observability:** stage timings and route decisions should be visible enough to support manual validation, similar to how `X-Extract-Cache` made extract-cache verification explicit.
- **Failure model:** job failure must produce stable error codes and not leak raw model/library exception details to the frontend.
- **Deduplication safety:** same-file resubmissions must return the same canonical job instead of creating multiple expensive extraction executions.

## SpecFlow / User Flow Analysis

- User uploads invoice through existing frontend flow.
- Backend authenticates, streams upload, computes file hash, and runs cheap preflight/routing.
- If fast:
  - request completes inline
  - frontend gets the current extract payload and proceeds unchanged
- If slow:
  - backend returns `202` with `job_id`, `status`, and `status_url`
  - frontend polls the job endpoint
  - frontend receives `queued` or `processing` until terminal state
  - on success, frontend receives the same extract payload shape under `result`
  - on failure, frontend receives stable error metadata
- If the same invoice is submitted again before completion:
  - backend returns the canonical existing job rather than duplicating work

Key edge cases to cover:

- duplicate submits while job is `queued`
- duplicate submits while job is `processing`
- duplicate submits after success
- expired job lookup
- auth mismatch between submitter and job reader
- preflight says async but downstream execution fails immediately
- sync path still returns the same success shape as today

## Implementation Phases

### Phase 1: Freeze contracts and domain models

- Add Pydantic response/request models for:
  - async submit envelope
  - extraction job status payload
  - extraction job error payload
- Update OpenAPI metadata in [src/invproc/api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py) so `POST /extract` documents both `200` and `202`.
- Define stable enum/status constants and error-code constants in one backend-owned location.

### Phase 2: Add extraction job persistence and service layer

- Introduce a repository/service abstraction for extraction jobs and results.
- Persist:
  - `job_id`
  - dedupe key
  - owner/auth context
  - status
  - timestamps
  - terminal result payload or terminal error payload
- Implement the canonical dedupe lookup using `file_hash + extract_config_signature`.
- Define retention fields and expiry behavior.
- For v1, implement the repository as an app-scoped in-memory store with:
  - per-job owner/auth identity
  - dedupe-key index
  - terminal result/error payload storage
  - timestamp-based expiry pruning
- Keep the abstraction narrow so a future durable backend can replace the in-memory implementation without changing route/service contracts.

### Phase 3: Add preflight routing and async execution orchestration

- Add a cheap preflight stage that inspects submission-safe signals before entering the full expensive extraction flow.
- Reuse PDF inspection data already available before LLM execution whenever possible. Prefer a helper that returns:
  - page count
  - estimated chunk count from sanitized text grid
  - whether OCR was used or likely required
  - text-grid character length
- Decide whether to:
  - execute inline and return `200`
  - create/reuse a job and return `202`
- For async work, dispatch extraction execution through a backend-owned worker path that updates job state from `queued` -> `processing` -> terminal state.
- Ensure async execution reuses the same extraction service logic as the sync path.
- For v1, run async work in a background thread from the API process after the `202` response path has created the canonical job.

### Phase 4: Add polling endpoint and auth/headers

- Implement `GET /invoice/extraction-jobs/{job_id}`.
- Enforce the same auth class as `POST /extract` plus ownership checks.
- Return `404` plus `JOB_NOT_FOUND` or `JOB_EXPIRED` where appropriate.
- Add `Location` and `Retry-After` headers on `202` responses from `POST /extract`.

### Phase 5: Add observability and regression coverage

- Emit route-decision/stage timing logs or headers sufficient to validate:
  - why a request became async
  - whether dedupe reused an existing job
  - how job status progressed
- Add tests for:
  - fast sync success
  - async submit response shape
  - polling non-terminal states
  - terminal success shape
  - terminal failure shape
  - duplicate submit reuse
  - job auth enforcement
  - expiry behavior

## Alternatives Considered

- **Keep everything synchronous and only tune threadpools/timeouts**
  Rejected because it does not solve the core frontend contract problem for invoices that already take 80-150 seconds.
- **Make every extraction async**
  Rejected because fast invoices still benefit from the current simple request/response path (see origin: `docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md`).
- **Let clients choose sync vs async**
  Rejected for v1 because routing ownership was explicitly decided to live in the backend.
- **Use the same `/extract` endpoint for polling**
  Rejected because the reviewed contract explicitly chose a dedicated job endpoint.
- **Use webhooks first**
  Rejected because the brainstorm explicitly scoped webhooks out of v1 in favor of simpler polling.

## Risks / Unknowns

- Preflight heuristics may misclassify borderline invoices if the signals are too weak.
- In-memory job storage adds state-management complexity to a path that is currently stateless apart from cache.
- In-process execution means multi-instance behavior and process restarts are explicitly not solved by this slice.
- Auth/ownership checks on job endpoints could become a security footgun if they are treated more loosely than the submit endpoint.
- Reusing the current extract cache semantics for dedupe is strong, but the boundary between “job record” and “cached result” must stay explicit.

## Acceptance Criteria

- [ ] `POST /extract` can return either the current `InvoiceData` payload with `200` or a minimal async envelope with `202`.
- [ ] No request ever upgrades from sync to async after submission handling has completed.
- [ ] `GET /invoice/extraction-jobs/{job_id}` returns only `queued`, `processing`, `succeeded`, or `failed`.
- [ ] Terminal async success returns the current extract payload shape unchanged under `result`.
- [ ] Duplicate submissions for the same file/config pair reuse the canonical job instead of duplicating extraction work.
- [ ] Auth for job polling matches the intended submitter-only access model.
- [ ] Job expiry behavior is explicit and tested.
- [ ] The implementation documents that v1 job state is app-scoped and not durable across process restarts.
- [ ] Quality gates pass:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`

## Verification Plan

- Unit-test preflight routing with fixed signals to prove deterministic sync vs async decisions.
- Integration-test `POST /extract` for both `200` and `202` cases.
- Integration-test polling endpoint states and terminal payload shapes.
- Add duplicate-submit tests proving the same canonical job is returned.
- Add auth tests for job ownership and unauthorized access.
- Add expiry tests for `JOB_EXPIRED`.
- Manual browser validation in Swagger or frontend once implemented:
  - small invoice returns `200`
  - large invoice returns `202`
  - polling reaches terminal success or failure cleanly

## Sources

- **Origin document:** [docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/brainstorms/2026-03-27-hybrid-extract-sync-async-requirements.md)
- [src/invproc/api.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/api.py)
- [src/invproc/services/extract_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/extract_service.py)
- [src/invproc/services/upload_service.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/services/upload_service.py)
- [src/invproc/llm_extractor.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/llm_extractor.py)
- [src/invproc/pdf_processor.py](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/pdf_processor.py)
- [todos/013-pending-p1-request-queue-concurrency.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/todos/013-pending-p1-request-queue-concurrency.md)
- [docs/solutions/runtime-errors/large-invoice-llm-json-truncation-and-discount-page-pruning-20260326.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/runtime-errors/large-invoice-llm-json-truncation-and-discount-page-pruning-20260326.md)
- [docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md)
