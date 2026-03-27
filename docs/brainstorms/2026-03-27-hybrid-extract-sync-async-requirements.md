---
date: 2026-03-27
topic: hybrid-extract-sync-async
---

# Hybrid Sync/Async Extract Contract

## Problem Frame

`POST /extract` currently behaves as a single synchronous request even for invoices that take a very long time to finish. Recent validation in this repo showed representative invoices completing in about 81 seconds and 152 seconds. That creates frontend pain, unclear waiting behavior, and a poor fit for local and production user flows where some invoices are fast and others are outliers.

The repo has already improved the synchronous path with file-hash caching, chunked LLM extraction, and discount-page pruning. Those improvements help correctness and repeated requests, but they do not change the core product problem: some invoices are still slow enough that the backend should not make the frontend block on one long request.

The goal of this brainstorm is to define a backend-owned contract that keeps fast invoices simple while moving slow invoices into an explicit async workflow with a clean polling model.

## Requirements

- R1. `POST /extract` must remain available as the submission entrypoint for invoice extraction.
- R2. The backend must decide whether a request stays synchronous or is routed to async; clients do not choose the mode in v1.
- R3. The backend must decide between `200` and `202` immediately after submission handling; v1 must not support a mid-request upgrade from synchronous execution to async execution.
- R4. When the backend predicts a slow invoice, `POST /extract` must return `202 Accepted` instead of holding the request open.
- R5. The v1 `202` response must be minimal and use one stable envelope shape:
  - `job_id`
  - `status`
  - `status_url`
- R6. Clients must fetch async progress and final results through `GET /invoice/extraction-jobs/{job_id}` rather than by reusing the upload endpoint.
- R7. The v1 job status enum must be exactly: `queued | processing | succeeded | failed`.
- R8. The async job endpoint must expose non-terminal states using `queued` and `processing` only.
- R9. The async job endpoint must expose a terminal success state where the completed extraction payload is returned under `result`, and that nested payload must keep the current `/extract` success payload shape unchanged.
- R10. The async job endpoint must expose a terminal failure state where a stable `error` object is returned with a machine-readable `code` and user-facing `message`.
- R11. Slow-invoice routing in v1 must happen automatically with no public sync override.
- R12. The routing decision must be based on cheap backend-observed preflight signals rather than waiting for the full long-running extraction to complete.
- R13. Repeated uploads of the same file under the same effective extraction configuration must return the canonical existing job instead of creating duplicate expensive work when a matching job is already running or already completed.

## Success Criteria

- A frontend can submit an invoice once and handle both fast and slow invoices through one predictable contract.
- Fast invoices still feel simple and immediate.
- Slow invoices no longer force the frontend to sit on a long open request when the backend already knows they are likely expensive.
- Retrying or re-uploading the same slow invoice does not unnecessarily duplicate model work.
- Planning can proceed without needing to invent the product contract for async extraction.

## Scope Boundaries

- Out of scope for v1: client-selected sync vs async mode.
- Out of scope for v1: webhook delivery as the primary async completion mechanism.
- Out of scope for v1: ETA promises or progress percentages.
- Out of scope for v1: draft/preview extraction payloads before final completion.
- Out of scope for v1: redesigning the extraction payload itself.

## Key Decisions

- Backend-owned routing: the server, not the client, decides when an invoice is too slow for inline completion.
- Minimal `202` envelope: the async response stays intentionally small in v1 to reduce product and implementation complexity.
- Separate polling endpoint: job submission and job inspection are different concerns and should have different endpoints.
- No public sync override in v1: this keeps the initial contract safer, simpler, and easier to explain.
- No mid-request mode switch: requests are classified as sync or async up front after submission handling.
- Terminal async success reuses the current extract result shape unchanged under `result`.
- Duplicate submits should collapse onto the canonical existing job rather than fan out into duplicate model work.

## Dependencies / Assumptions

- A cheap enough preflight signal exists or can be added so the backend can route before starting the full expensive path.
- The current file-hash extract identity can be extended into durable async job/result deduplication.
- Existing frontend consumers can handle a mixed `200` or `202` response from `POST /extract`.

## Outstanding Questions

### Deferred to Planning

- [Affects R12][Technical] What exact preflight signals should trigger async routing: page count, OCR usage, text-grid size, predicted chunk count, or a combination?
- [Affects R13][Technical] What is the canonical dedupe key for extraction jobs?
- [Affects R13][Technical] Where should async job state and completed extraction results be persisted?
- [Affects R10][Technical] What stable error codes should exist beyond `EXTRACTION_FAILED`?
- [Affects R5][Technical] Should `202 Accepted` also return `Location` and `Retry-After` headers in addition to `status_url`?
- [Affects R6][Technical] What auth behavior should `GET /invoice/extraction-jobs/{job_id}` enforce?
- [Affects R13][Technical] What retention and expiry policy should job records use, and what should expired jobs return?

## Next Steps

-> /prompts:ce-plan for structured implementation planning
