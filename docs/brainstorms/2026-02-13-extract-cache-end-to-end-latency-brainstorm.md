---
date: 2026-02-13
topic: extract-cache-end-to-end-latency
---

# Extract Cache End-to-End Latency

## What We're Building
Make repeated invoice extraction feel materially faster from the frontend, not just cheaper on OpenAI spend.

The current hypothesis is: an LLM-call cache exists, but the overall `/extract` request still runs enough other work (upload, PDF processing, validation, follow-up endpoints, multi-worker routing) that perceived latency does not change much.

## Current Reality (Repo Findings)
- Upstream `origin/main` includes the `/extract` cache via PR #9 (merge commit `f376ea7`).
  - `src/invproc/api.py` adds an in-memory cache keyed by `sha256(pdf_bytes)` plus an extraction config signature.
  - On cache hit, it returns cached `InvoiceData` and skips PDF extraction + LLM + validation.
  - It adds `X-Extract-Cache: miss|hit` header when cache is enabled.
- A local checkout (or deployment) can still be missing the cache if it is pinned to an older commit and not redeployed.
- Even on cache hit, the server still must receive the file upload and stream it to disk while hashing (to know the key).
- The cache is off by default unless `EXTRACT_CACHE_ENABLED=true` is set in the backend environment. The provided `docker-compose.yml` does not set it currently.

## Why This Approach (2-3 Options)

### Approach A: Make Sure We’re Actually Hitting the Existing Cache (Recommended)
Treat this as a verification and deployment alignment issue first.

Pros:
- Fastest path to a real win if the cache isn’t currently deployed/enabled.
- Avoids premature caching of the wrong layer.

Cons:
- Helps only for repeated *byte-identical* uploads and only per instance/worker.

Best when:
- The second call is still showing full LLM latency and/or `X-Extract-Cache` is missing.

### Approach B: Optimize “Everything But the LLM” (Upload + PDF Processing + Follow-Up Calls)
Assume LLM is not the dominant cost (or cache isn’t the dominant lever) and target the other parts of perceived latency.

Pros:
- Improves first-time extraction too (not just repeats).

Cons:
- Risk of spending time optimizing the wrong thing without timing breakdowns.

Best when:
- Cache hits are confirmed, but end-to-end frontend latency is still high.

### Approach C: Make Cache Effective Across Workers/Instances and Across Sessions
Assume cache hits are rare because requests are routed to different workers/instances, or the process restarts frequently.

Pros:
- Converts “works locally” into “works in production”.

Cons:
- More operational surface area and invalidation concerns.

Best when:
- You run multiple workers/replicas and see misses even on repeat tests.

## Key Decisions (To Make Explicit)
- Success criteria: what is the target “repeat extraction” latency improvement from the frontend?
- Scope: do we care about repeated extraction of the same file, first-time extraction, or both?
- Deployment model: single instance vs multi-instance; single worker vs multiple workers.
- Verification contract: do we require `X-Extract-Cache` (or similar) to be present in all environments?

## Open Questions
- When you repeat an identical upload, what does the response header `X-Extract-Cache` show (or is it missing)?
- Is the backend you’re testing running `main` or `codex/extract-cache-file-hash` (or a deployed image built from one of them)?
- Are you running with multiple workers/replicas (which would reduce per-process cache hit rate)?
- Is your frontend “one call” actually multiple backend calls (for example `/extract` then `/invoice/preview-pricing`) and which one dominates?

## Next Steps
Answer the single verification question first:
What is `X-Extract-Cache` on the *second* identical `/extract` request?

Then proceed to `/workflows:plan` once we pick Approach A, B, or C.
