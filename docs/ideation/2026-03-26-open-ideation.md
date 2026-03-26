---
date: 2026-03-26
topic: open
---

# Ideation: Open Project Improvements

## Codebase Context

This repository is a Python invoice-processing service with two primary delivery surfaces: a CLI and a FastAPI backend. The core system combines PDF text extraction, OCR fallback, LLM-based structured extraction, validation, import flows, and a growing RAG/catalog-sync subsystem backed by Supabase.

The strongest observed patterns from the codebase and recent solution docs are:

- API and CLI parity gaps recur as the system grows new features and threshold/config knobs.
- RAG and ingest behavior is powerful but concentrated in a few large modules, especially `src/invproc/rag.py`, `src/invproc/api.py`, and `src/invproc/cli.py`.
- Operational hardening is already a priority: quality gates, strict PR policy, fail-fast production config, auth hardening, and persistence safety all appear repeatedly in recent fixes.
- The project has strong troubleshooting discipline via `docs/solutions/`, but many fixes are still preserved as point solutions rather than lifted into shared regression/eval infrastructure.
- Pending todos suggest open leverage around persistence hardening, internal endpoint security, hybrid search performance, and reducing low-value complexity.

## Ranked Ideas

### 1. Build a parity contract layer for API + CLI
**Description:** Define shared contracts and propagation tests so new parameters, thresholds, workflow options, and result shapes cannot ship in one surface without the other.
**Rationale:** API/CLI parity drift is one of the most repeated failure patterns in the repo's recent solutions and pending todos. Fixing it would reduce recurring defects across RAG, import, sync, and eval work.
**Downsides:** Much of the payoff is indirect at first, so it may feel like infrastructure work rather than visible feature progress.
**Confidence:** 92%
**Complexity:** Medium
**Status:** Unexplored

### 2. Create a first-class eval harness for extraction and retrieval
**Description:** Turn representative invoices, retrieval fixtures, and past bug cases into one reusable evaluation flow that scores extraction quality, retrieval quality, and regressions over time.
**Rationale:** The repo already has fixtures, baselines, and a strong troubleshooting archive. A unified harness would make prompt, OCR, threshold, and normalization changes much safer and easier to assess.
**Downsides:** Requires curating datasets and agreeing on metrics before it delivers full value.
**Confidence:** 88%
**Complexity:** Medium
**Status:** Explored

### 3. Refactor the RAG subsystem into smaller, explicit modules
**Description:** Split RAG concerns into narrower units such as ingest, retrieval, ranking, sync orchestration, and eval so the codebase becomes easier to change safely.
**Rationale:** The current RAG-related logic is concentrated in a small set of large files. That is where future change risk and coupling are accumulating.
**Downsides:** Refactors can expand in scope and create temporary drag if not tightly bounded.
**Confidence:** 85%
**Complexity:** High
**Status:** Unexplored

### 4. Add an extraction trace and replay workflow
**Description:** Persist sanitized extraction artifacts such as text grids, prompt inputs, model outputs, validation failures, and normalized rows so tricky invoices can be replayed and inspected quickly.
**Rationale:** Edge-case debugging is central to this project. A replay workflow would speed diagnosis and feed directly into evaluation and prompt iteration.
**Downsides:** Needs careful data-sensitivity handling and disciplined storage boundaries.
**Confidence:** 82%
**Complexity:** Medium
**Status:** Unexplored

### 5. Finish internal-surface hardening as a productized admin boundary
**Description:** Treat internal endpoints, sync operations, and ingestion tools as a real privileged surface with explicit roles, audit-safe logging, and clearer operator contracts.
**Rationale:** Recent learnings and todos show the system is moving beyond local tooling into shared persistent infrastructure, where internal paths need stronger boundaries.
**Downsides:** Much of the work is preventive and may feel less exciting than new capabilities.
**Confidence:** 84%
**Complexity:** Medium
**Status:** Unexplored

### 6. Ship a performance pack for hybrid retrieval
**Description:** Improve hybrid search performance with indexing, parallelized DB work, timing instrumentation, and a benchmark loop that shows before/after latency and quality tradeoffs.
**Rationale:** The repo already has open signals around missing pgvector indexing and non-parallel hybrid retrieval work. This is a concrete way to prepare for larger catalogs.
**Downsides:** The urgency depends on real catalog size and traffic patterns; there is some risk of tuning too early.
**Confidence:** 79%
**Complexity:** Medium
**Status:** Unexplored

### 7. Reduce configuration and CLI complexity
**Description:** Prune low-value flags and ambiguous config paths, and define a smaller golden path for common operations while preserving advanced workflows where they truly matter.
**Rationale:** Several past fixes and open todos point to complexity creep from debug-era ergonomics and hidden defaults.
**Downsides:** Simplification can frustrate power users if changes remove flexibility carelessly.
**Confidence:** 77%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Build a human review queue or UI | Interesting, but not strongly grounded in the current backend-heavy repo shape. |
| 2 | Add more model providers | Not the dominant leverage point compared with parity, eval, and hardening work. |
| 3 | Expand OCR heuristics only | Too narrow on its own; better captured inside a stronger eval/replay strategy. |
| 4 | Create a frontend/admin app | Too expensive relative to the current repo's main leverage points. |
| 5 | Focus only on repository typing cleanup | Real issue, but too small and subsumed by broader architecture work. |
| 6 | Add more branch protection and PR policy rules | The repo already has strong workflow gates; returns appear diminished. |
| 7 | Invest primarily in docs improvements | Helpful but not the core bottleneck limiting correctness or iteration speed. |
| 8 | Add a background worker system now | Potentially useful, but weaker than first clarifying parity, hardening, and eval boundaries. |
| 9 | Build invoice analytics/reporting features | Not well grounded in the current repository direction. |

## Session Log

- 2026-03-26: Initial ideation - 16 candidate directions considered, 7 survivors kept.
- 2026-03-26: Idea 2 selected for brainstorming; marked as Explored.
