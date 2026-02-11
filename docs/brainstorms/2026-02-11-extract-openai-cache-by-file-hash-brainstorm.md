---
date: 2026-02-11
topic: extract-openai-cache-by-file-hash
---

# Extract OpenAI Cache by File Hash

## What We're Building
Add a cache for `POST /extract` so repeated uploads of the exact same PDF bytes do not call OpenAI again. The endpoint should return the same extracted `InvoiceData` payload from cache.

The goal is lower cost, lower latency, and more stable repeated testing for the same invoice while keeping current API contract unchanged.

## Why This Approach
We considered three cache scopes: in-memory process cache, file-based disk cache, and shared Redis cache.

Recommendation was in-memory process cache for MVP (YAGNI): smallest change surface, no extra infra, no schema migrations, and immediate value for local/dev/single-instance use.

For cache key strategy, we considered exact file hash, normalized text-grid hash, and extracted invoice identity. We selected exact PDF-byte hash (`sha256`) as safest and deterministic for MVP.

## Key Decisions
- Cache scope: in-memory per backend process. Rationale: fastest delivery, no operational dependencies.
- Cache key: exact uploaded PDF bytes hash (`sha256`). Rationale: deterministic, minimal false positives.
- Behavior: cache hit must bypass OpenAI call and return previous extraction payload unchanged.
- API contract: no response shape changes required for MVP.

## Open Questions
- TTL policy: no expiration vs finite TTL (for example 24h).
- Memory cap/eviction: unbounded dict vs bounded LRU.
- Optional observability: whether to expose hit/miss metric or debug header.
- Multi-instance production behavior: accept per-instance misses for now, or plan later Redis upgrade.

## Next Steps
-> `/workflows:plan` for implementation details
