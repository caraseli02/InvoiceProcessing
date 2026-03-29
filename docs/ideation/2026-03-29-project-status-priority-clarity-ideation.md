---
date: 2026-03-29
topic: project-status-priority-clarity
focus: roadmap clarity, current status, priorities, next work
---

# Ideation: Project Status and Priority Clarity

## Codebase Context

This repository already has substantial planning and historical documentation, but it is distributed across several layers with different purposes:

- [`README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/README.md) gives operator and developer usage guidance
- [`docs/contracts/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts) locks feature boundaries and runtime semantics
- [`docs/plans/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans) contains feature plans, some completed and some still marked active
- [`docs/solutions/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions) captures solved problems and architectural learnings
- [`docs/ideation/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/ideation) stores idea-generation artifacts

Grounded signals from the current repo state:

- There is no single roadmap or status-control document that answers “what is done, what is active, what is next, and why”.
- Current next-work reasoning requires reading multiple plans, contract notes, and recent solution docs, then reconciling them manually.
- At least two plans are still marked `active` today, including the compare-first RAG eval harness and the hybrid sync/async extract contract.
- The main RAG phase plan is marked `completed`, but still contains an in-progress Phase 5 section, which creates ambiguity about whether the feature track is done or still open.
- Recent PM/doc follow-up was necessary just to align the README, contract, and March 28 plan status with what was actually shipped.

Relevant institutional learnings:

- Contract and CLI/API parity drift has already caused repeated confusion in the RAG subsystem.
- Several workflow learnings in [`docs/solutions/workflow-issues/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues) point to process drift, missing verification steps, and stale context as recurring problems.
- The repo has strong quality gates for code changes, but no equally strong workflow for keeping project-control docs synchronized after merges.

## Ranked Ideas

### 1. Add a single `docs/project-status.md` as the canonical “control tower”
**Description:** Create one manually maintained status document that answers only four things: `Current priorities`, `Active work`, `Recently completed`, and `Next up`. It should link out to plans and contracts instead of duplicating them. The document becomes the first place to check before asking “what next?” or opening a new plan.
**Rationale:** This is the cleanest fix for the exact pain you hit. It does not fight the existing docs structure; it adds one control layer above it. It also solves the repo’s current gap more directly than trying to force README or plans to act like a roadmap.
**Downsides:** It adds one more file that can go stale unless there is a maintenance rule tied to merges.
**Confidence:** 96%
**Complexity:** Low
**Status:** Unexplored

### 2. Add a strict plan lifecycle workflow with required status transitions
**Description:** Define and enforce a small lifecycle for plan docs: `planned -> active -> completed -> superseded`. Require every merge that finishes a scoped plan to update that plan’s frontmatter and acceptance checklist, and require any follow-up plan to explicitly reference which earlier plan it continues or supersedes.
**Rationale:** The current ambiguity is partly structural: some plans are “completed” while containing open in-progress sub-phases, and some active plans may already be partly shipped. Tightening lifecycle semantics would make the planning layer much easier to trust.
**Downsides:** This fixes consistency, but by itself it still leaves the user scanning many files to understand current priorities.
**Confidence:** 93%
**Complexity:** Low
**Status:** Unexplored

### 3. Generate a machine-readable project inventory from plan frontmatter
**Description:** Standardize plan frontmatter enough to support a small script that lists all plans by `status`, `type`, `date`, and `topic`, then generates a compact dashboard or markdown summary. This could also flag contradictions such as “completed file with unchecked acceptance criteria” or multiple active plans in the same feature lane.
**Rationale:** The repo already encodes most of the needed information in docs; the problem is retrieval and consistency. A generated inventory would turn scattered plan metadata into a usable program view.
**Downsides:** It requires frontmatter normalization across older plans before the output becomes trustworthy, so it is not the fastest first fix.
**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

### 4. Split project control into “Product Now/Next/Later” and “Engineering Workstreams”
**Description:** Maintain two lightweight project-facing docs instead of one overloaded roadmap:
- a product-facing `Now / Next / Later` summary
- an engineering workstream map that lists active tracks like `RAG quality`, `extract async`, `quality-gate policy`, `import flow`

Each workstream would point to its controlling plan and recent solution docs.
**Rationale:** The repo currently mixes delivery sequencing with architecture history. Separating product priority from technical track state makes it easier to answer both “what should we work on next?” and “what has already been done on this subsystem?” without overloading one file.
**Downsides:** More structure than a single status doc, so it risks becoming ceremony if not kept concise.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 5. Add a merge checklist item for project-status updates
**Description:** Extend the repo workflow so feature/refactor PRs must answer: “Does this change require updates to project control docs?” If yes, the PR must update the control doc, affected plan statuses, and any contract/README drift. This can live in the PR template or AGENTS guidance.
**Rationale:** The repo already enforces code quality and PR evidence strongly. This idea adds a small governance step so roadmap/status clarity becomes part of done-ness, not a later cleanup.
**Downsides:** It improves maintenance but does not define the status surface itself; it is strongest only in combination with Idea 1 or 4.
**Confidence:** 91%
**Complexity:** Low
**Status:** Unexplored

### 6. Create a “current priorities” CLI command or generated note
**Description:** Add a simple command or script such as `python scripts/project_status.py` that prints the active plans, latest completed work, and recommended next step based on a curated file or plan metadata. This can support terminal workflows without forcing people to scan markdown manually.
**Rationale:** The repo is CLI-heavy, and a command-line status view matches how work is already being done. It also lowers the friction of checking priorities before starting new changes.
**Downsides:** If the underlying source of truth is fuzzy, the command only automates confusion. This should come after the control model is clarified.
**Confidence:** 80%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Put the full roadmap directly into `README.md` | Too overloaded; README should stay operator/developer-oriented, not become the project control surface. |
| 2 | Keep using plans only and just read them more carefully | This is the current failure mode; the repo already proves that scattered plans are not enough for quick status clarity. |
| 3 | Use GitHub Projects as the only source of truth | Not grounded in the current repo workflow; the codebase already uses durable markdown docs as the planning record. |
| 4 | Replace all existing docs with one big master roadmap | Too destructive and too expensive relative to the problem. The current docs should be organized, not flattened. |
| 5 | Solve it purely with automation first | Premature. Without clearer status semantics and ownership, automation will just generate low-trust output. |
| 6 | Track everything only through solution docs | Solution docs explain what happened and why; they are historical and architectural, not a priority queue. |
| 7 | Open more issue tickets for every next-step question | Adds fragmentation instead of reducing it. The missing layer is synthesis, not more containers. |

## Session Log

- 2026-03-29: Initial ideation — 13 candidate directions considered, 6 survivors kept.
