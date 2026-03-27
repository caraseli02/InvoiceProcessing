---
date: 2026-03-26
topic: open
focus: extraction latency, hybrid sync-async flow, backend protections
---

# Ideation: Open Project Improvements

## Codebase Context

This repository is a Python invoice-processing service with two primary delivery surfaces: a CLI and a FastAPI backend. The current `POST /extract` path remains a single synchronous request that:

- streams the upload to a temp file
- computes a file-hash cache key
- runs native PDF text extraction with OCR fallback
- prunes discount-only rows/pages before LLM use
- chunks large invoices into multiple LLM requests when needed
- validates and normalizes the final merged payload before returning

Recent extraction validation showed:

- `invoice-test.pdf` completed in about `81s`
- `invoice-test5.pdf` completed in about `152s`
- the upload reached disk quickly
- no OCR subprocess dominated the request
- the API held a live outbound HTTPS connection during most of the wait, indicating the current latency is primarily model-call dominated

Relevant repo learnings and signals:

- Large-invoice chunking and page pruning already exist, so the synchronous path has been optimized once and still leaves long-tail latency for larger invoices.
- File-hash extract caching already exists, but it is in-memory per process, which helps retries in one instance but does not create a durable job/result contract for frontend polling.
- There is already a pending queue/concurrency todo for `/extract` in `todos/013-pending-p1-request-queue-concurrency.md`.
- Earlier performance notes confirm the endpoint is effectively a blocking workflow even though it is exposed through FastAPI.
- The repo already uses durable queue semantics and internal API boundaries in the RAG subsystem, which is a strong local precedent for backend-owned async work rather than pushing complexity into the frontend alone.

## Ranked Ideas

### 1. Design `/extract` as a hybrid sync/async contract with backend-owned fallback
**Description:** Keep the current synchronous response for clearly small invoices, but add a backend-owned async path that returns `202 Accepted` plus a job/status reference when the invoice is predicted to be slow. The backend, not the frontend, decides which path to use based on preflight signals such as page count, text-grid size, OCR usage, or predicted chunk count.
**Rationale:** This directly addresses the frontend pain without forcing every caller into polling. It matches the repo’s current reality: some invoices are fine synchronously, while others take 80-150 seconds. It also aligns with the existing queue-oriented todo for `/extract`.
**Downsides:** Changes the HTTP contract for some requests and introduces job lifecycle/state management that must be documented and tested carefully.
**Confidence:** 95%
**Complexity:** High
**Status:** Explored

### 2. Add durable extract job + result persistence keyed by file hash and config signature
**Description:** Promote the existing in-memory extract cache key (`file_hash + extraction config signature`) into a persistent job/result record. If the same file is uploaded while extraction is already running, return the existing in-progress job instead of starting duplicate model work. If it already completed, return or hydrate the stored result immediately.
**Rationale:** The repo already has the right deterministic cache identity. Making that durable turns retries, repeated frontend uploads, and multi-instance behavior into a product feature instead of a best-effort optimization.
**Downsides:** Needs storage design, retention rules, and careful separation between “cached completed result” and “actively running job.”
**Confidence:** 93%
**Complexity:** Medium
**Status:** Unexplored

### 3. Add a cheap extraction preflight router before full model execution
**Description:** Introduce a fast preflight stage that computes page count, native-text density, likely OCR usage, and estimated chunk count before the expensive LLM phase. Use that to choose sync vs async, reject pathological workloads early, or set realistic client expectations.
**Rationale:** Right now the backend learns a document is expensive only after entering the full synchronous path. A preflight router gives the frontend and backend a shared truth about whether this request should stay inline.
**Downsides:** Prediction can be wrong, and a weak heuristic layer can become another source of edge-case bugs if it is not tested against real invoices.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 4. Parallelize chunk-level LLM extraction with deterministic merge semantics
**Description:** For invoices that are already chunked, execute chunk requests with bounded concurrency instead of the current sequential list-comprehension flow, then merge in original chunk order with stricter metadata resolution rules.
**Rationale:** The current large-invoice fix improved correctness, but chunked requests are still sent serially. On model-bound invoices like `invoice-test5.pdf`, this is one of the clearest backend levers for reducing wall-clock time without requiring a full async contract change.
**Downsides:** There is already a follow-up note that chunk merging is order-dependent. Parallelizing before hardening merge semantics could introduce subtle metadata or duplication regressions.
**Confidence:** 84%
**Complexity:** Medium
**Status:** Unexplored

### 5. Add stage-level observability and SLA-aware backend behavior for extraction
**Description:** Record per-stage timings for upload, PDF text extraction, OCR, chunk splitting, each LLM chunk request, merge, and validation, then use those timings for operational visibility and policy decisions such as async fallback thresholds and timeout messaging.
**Rationale:** Today we can infer that the request waits on the model, but not precisely how much each stage costs. Better observability would let the backend make smarter routing decisions and would prevent frontend debates from being driven by guesswork.
**Downsides:** Observability alone does not fix user pain; it is only valuable if it feeds routing, budgeting, or retry strategy decisions.
**Confidence:** 91%
**Complexity:** Low
**Status:** Unexplored

### 6. Split extraction into “fast preview” and “finalized result” phases
**Description:** Return a first-pass preview quickly using header metadata plus early product rows or an incomplete-but-usable draft, then finalize the full validated extraction asynchronously. The frontend can render a progressive review flow instead of waiting for the entire invoice to settle before showing anything.
**Rationale:** This is the strongest UX idea when some invoices are inherently slow. It keeps the user moving even if the backend still needs a long model call for full fidelity.
**Downsides:** It introduces a more complex product contract than sync-or-polling alone because the draft payload and final payload can differ. That makes correctness, user trust, and idempotency harder.
**Confidence:** 72%
**Complexity:** High
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Only increase threadpool size and keep everything synchronous | Too weak relative to the observed 81-152 second model-bound latency; it may help throughput but does not protect the frontend from long waits. |
| 2 | Focus mainly on OCR optimization | Not well matched to the observed runtime signal; these invoices spent most of their time in outbound model calls, not OCR. |
| 3 | Add more prompt tuning alone | Worth doing eventually, but too speculative as the primary answer when the larger problem is contract shape and execution mode. |
| 4 | Push all async handling into the frontend only | Wrong ownership boundary. The backend already has file-hash identity, cache semantics, and queue precedents, so the job contract should live server-side. |
| 5 | Force every extraction to become async immediately | Over-corrects. The repo still benefits from fast synchronous responses for small invoices, so a hybrid contract is stronger than a blanket switch. |
| 6 | Add webhooks as the first async interface | Useful later, but polling/status is a simpler and better-grounded first backend contract for the current frontend/backend shape. |

## Session Log

- 2026-03-26: Initial ideation - 16 candidate directions considered, 7 survivors kept.
- 2026-03-26: Idea 2 selected for brainstorming; marked as Explored.
- 2026-03-27: Latency-focused refinement - extraction path reviewed against `invoice-test.pdf` (~81s) and `invoice-test5.pdf` (~152s); 6 backend-facing survivors kept, with hybrid sync/async extraction promoted to the top direction.
- 2026-03-27: Idea 1 selected for brainstorming; marked as Explored.
