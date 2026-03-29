---
title: "Scattered plans and docs hid what was active, done, and next"
category: "workflow-issues"
date: "2026-03-29"
tags: ["workflow", "project-status", "planning", "documentation", "roadmap", "pr-hygiene"]
components: ["docs/project-status.md", "AGENTS.md", ".github/pull_request_template.md", "CLAUDE.md", "/Users/vladislavcaraseli/.codex/skills/yeet/SKILL.md", "docs/plans/"]
symptoms:
  - "Answering 'what should we work on next?' required manually reconciling README, plans, contracts, and recent solution docs"
  - "Completed work and active work were both visible in the repo, but there was no single control surface for current priorities"
  - "A completed top-level plan could still contain an in-progress sub-phase, which made feature-track status ambiguous"
---

## Problem

The repo had plenty of documentation, but not enough project control.

By late March 2026, the planning and knowledge layers were all individually useful:

- [`README.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/README.md) for operator and developer usage
- [`docs/plans/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans) for implementation intent
- [`docs/contracts/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts) for behavior boundaries
- [`docs/solutions/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions) for solved-problem memory

But there was no single file that answered the operational PM question:

> what is active, what was just completed, and what should happen next?

That forced manual reconciliation across multiple documents every time someone asked for priorities.

## Root Cause

This was not a lack-of-docs problem. It was a missing-control-surface problem.

- The repo had historical memory and implementation plans, but no canonical status dashboard above them.
- Plan status semantics were not strong enough on their own to answer priority questions quickly.
- Merge/package workflows enforced code quality and PR evidence, but they did not enforce project-status hygiene.
- `README.md` risked becoming a de facto roadmap, while `docs/solutions/` risked being read like an active priority queue even though neither file set was designed for that role.

## Investigation Steps

- Reviewed the current planning layer and found at least two active plans that still represented meaningful future work:
  - [`docs/plans/2026-03-27-001-feat-rag-eval-harness-regression-reporting-plan.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-27-001-feat-rag-eval-harness-regression-reporting-plan.md)
  - [`docs/plans/2026-03-27-002-feat-hybrid-extract-sync-async-plan.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-27-002-feat-hybrid-extract-sync-async-plan.md)
- Re-read the main RAG phase plan and confirmed that the file was marked completed overall while still containing an in-progress Phase 5 retrieval-quality section:
  - [`docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-20-001-feat-rag-whatsapp-catalog-sync-plan.md)
- Verified that recent March 28 work had required explicit PM/documentation cleanup after implementation, which was a strong signal that the repo needed a durable status-maintenance rule rather than another one-off refresh.
- Compared this workflow problem to earlier repo learnings and found strong precedent:
  - [`docs/solutions/workflow-issues/stale-frontend-branch-masked-pricing-fixes-development-workflow-20260211.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/stale-frontend-branch-masked-pricing-fixes-development-workflow-20260211.md)
  - [`docs/solutions/workflow-issues/strict-quality-gates-for-prs-development-workflow-20260217.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/strict-quality-gates-for-prs-development-workflow-20260217.md)

## Fix

### 1. Add one control-tower file

Created [`docs/project-status.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/project-status.md) as the canonical high-level project status view.

It intentionally answers only:

- current priorities
- active work
- recently completed work
- next up

It links outward to controlling plans and contracts instead of duplicating their detail.

### 2. Define repo-level project-status hygiene

Updated [`AGENTS.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/AGENTS.md) to define when project-control docs must be updated:

- when a PR starts tracked work
- when a PR completes tracked work
- when a PR supersedes tracked work
- when a PR changes work sequencing or priorities

The rule is to update both:

- the controlling file in `docs/plans/`
- [`docs/project-status.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/project-status.md)

### 3. Add a PR-time reminder

Updated [`.github/pull_request_template.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/.github/pull_request_template.md) with checklist items for:

- project-status updates
- plan-status/frontmatter updates

This made project-control maintenance part of merge packaging instead of a best-effort follow-up.

### 4. Align agent packaging behavior

Updated [`CLAUDE.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/CLAUDE.md) and the local [`yeet` skill](/Users/vladislavcaraseli/.codex/skills/yeet/SKILL.md) so the same rule is visible in both agent instruction surfaces.

The `yeet` workflow now checks for project-control impact before staging and PR creation. If the diff appears to change tracked work state or sequencing, it must verify:

- `docs/project-status.md`
- the controlling `docs/plans/` file

or explicitly note that no project-control update was needed.

## Result

The repo now has a layered documentation model with a clear control boundary:

- `README.md` remains operator/developer-facing
- `docs/plans/` remain the implementation record
- `docs/contracts/` remain the behavior record
- `docs/solutions/` remain the historical learning record
- `docs/project-status.md` is now the first place to answer “what now, what next, and why”

That change does not automate prioritization. It does something more important first: it makes the status source of truth explicit and gives merge workflows a way to keep it current.

## Verification

The fix was verified by inspecting the final repo surfaces after the changes:

- [`docs/project-status.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/project-status.md) existed and contained current priorities, active work, recent completions, and next-step guidance grounded in existing plans
- [`AGENTS.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/AGENTS.md) contained explicit project-status hygiene rules
- [`.github/pull_request_template.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/.github/pull_request_template.md) contained checklist items for status and plan updates
- [`CLAUDE.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/CLAUDE.md) reflected the same policy
- the local [`yeet` skill](/Users/vladislavcaraseli/.codex/skills/yeet/SKILL.md) was updated to enforce the rule during PR packaging

## Prevention

- Keep `docs/project-status.md` narrow. If it grows into another long narrative doc, people will stop using it.
- Never use `README.md` as the roadmap. It will drift because its job is usage, not prioritization.
- Never use `docs/solutions/` as the active priority queue. It records what was learned, not what should happen next.
- Any PR that changes tracked work state should update the controlling plan and the control-tower file in the same change set.
- Automation should come after status semantics are clear, not before. A script can later validate active/completed mismatches, but only after the human-owned status model is stable.

Useful follow-up checks:

- compare active plans in `docs/plans/` against `docs/project-status.md`
- flag completed plans that still have unchecked acceptance items
- flag project-status entries that point to plans no longer marked active

## See Also

- [Strict quality gates for PRs and development workflow](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/strict-quality-gates-for-prs-development-workflow-20260217.md)
- [Stale frontend branch masked pricing fixes and created false regression signals](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/workflow-issues/stale-frontend-branch-masked-pricing-fixes-development-workflow-20260211.md)
- [Feature propagation gaps across SQL, dataclasses, and CLI/API parity](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md)
- [RAG catalog sync contract](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/contracts/2026-03-20-rag-catalog-sync-contract.md)
- [Project-status clarity ideation](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/ideation/2026-03-29-project-status-priority-clarity-ideation.md)
