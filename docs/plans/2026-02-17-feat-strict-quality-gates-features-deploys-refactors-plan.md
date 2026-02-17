---
title: feat: Enforce strict quality gates for features, deploys, and refactors
type: feat
date: 2026-02-17
---

# feat: Enforce strict quality gates for features, deploys, and refactors

## Overview
Establish strict, merge-blocking quality gates on `main` with a risk-based policy by change type. Every PR must pass a baseline gate (`pytest`, `mypy`, `ruff`, coverage >= 80%, health smoke check). Additional evidence is required for feature, deploy, and refactor changes so confidence increases without forcing one heavy workflow on every PR.

## Problem Statement
Current CI runs tests and mypy only, which leaves gaps for lint, coverage enforcement, deploy verification, and refactor safety evidence. Deployment verification is mostly manual. This can allow regressions or incomplete deploy readiness to slip through.

## Proposed Solution
Implement policy in three layers:

1. Baseline strict CI checks for all PRs.
2. Risk-based required evidence by change type (feature/deploy/refactor).
3. Branch protection configured to require all quality-gate checks before merge.

Use GitHub Actions + PR metadata (labels/checklists) for enforcement. Keep checks deterministic and fast.

## SpecFlow Analysis (Flows and Gaps)
Primary flows covered:
- Feature flow: code change -> targeted tests added -> baseline checks pass -> merge.
- Refactor flow: internal structure change -> regression evidence attached -> baseline checks pass -> merge.
- Deploy flow: runtime/deploy config change -> smoke + deploy checklist evidence -> merge.

Edge cases to address:
- Docs-only PRs should still satisfy baseline quickly (no manual deploy burden).
- Hotfix PRs need strict gates without process deadlock.
- Flaky tests must be isolated (`@pytest.mark.e2e`) so strict mode remains reliable.
- Missing change-type metadata must fail fast with clear remediation.

## Technical Approach

### Architecture
- Keep one CI entry workflow in `.github/workflows/ci.yml`.
- Split checks into explicit jobs: `lint`, `typecheck`, `tests`, `coverage`, `health-smoke`, `quality-gate`.
- Add change-classification step (labels and/or paths filter) to drive conditional evidence checks.
- Add PR template/checklist sections so evidence is auditable and reviewable.

### Implementation Phases

#### Phase 1: Baseline Strict Gates
Deliverables:
- Add `ruff` check to CI.
- Enforce coverage threshold `>=80%` in pytest/coverage config.
- Add API health smoke job (start app in CI, `curl /health`, fail on non-200).
- Add a final `quality-gate` job that depends on all required baseline jobs.

Success criteria:
- Any failing baseline check blocks merge.
- CI output names are stable for branch protection.
- Coverage failure message is explicit.

Estimated effort: Small.

#### Phase 2: Risk-Based Policy Enforcement
Deliverables:
- Add PR metadata policy for change type:
  - `change:feature`
  - `change:refactor`
  - `change:deploy`
- Add CI validation job that fails if no valid change-type label is present.
- Add required evidence checks by label:
  - Feature: targeted test evidence section non-empty.
  - Refactor: regression proof section non-empty (test list and behavior parity note).
  - Deploy: pre/post deploy checklist section completed.

Success criteria:
- PR without change type cannot merge.
- Missing required evidence for selected type fails CI.
- Reviewers can audit evidence directly in PR template sections.

Estimated effort: Medium.

#### Phase 3: Merge Protection and Operational Hardening
Deliverables:
- Configure branch protection on `main` to require quality-gate statuses.
- Document policy and examples in `DEPLOYMENT.md` and/or a dedicated `docs/quality-gates.md`.
- Add troubleshooting section for common failures (coverage misses, health smoke startup failures, label mismatch).

Success criteria:
- Branch protection requires quality-gate jobs.
- Team has one canonical reference for fixing failed gates.
- First week after rollout shows no ambiguous failure states.

Estimated effort: Medium.

## Alternative Approaches Considered

### A. Uniform maximum strictness for every PR
Rejected: simpler policy, but too much friction for low-risk refactors/docs changes.

### B. Staged gradual rollout
Rejected for now: lower short-term disruption, but delays safety guarantees requested for `main`.

### C. Risk-based strict policy (chosen)
Chosen: preserves strict baseline while matching extra checks to actual risk.

## Acceptance Criteria

### Functional Requirements
- [ ] CI enforces baseline checks on every PR: `pytest`, `mypy`, `ruff`, coverage `>=80%`, health smoke check.
- [ ] PRs must declare one change type (`feature`, `refactor`, `deploy`) for risk-based enforcement.
- [ ] CI fails when required evidence for selected change type is missing.
- [ ] Branch protection on `main` requires all quality gate statuses.

### Non-Functional Requirements
- [ ] Added checks keep median CI runtime within acceptable team target (define target during implementation, e.g. <= 12 minutes).
- [ ] Failures are actionable (clear error messages, no opaque script exits).
- [ ] Policy remains deterministic (no timing-only validation as correctness proof).

### Quality Gates
- [ ] Coverage threshold fixed at 80% and enforced in CI.
- [ ] Smoke check validates `/health` returns success in CI runtime.
- [ ] Docs updated with remediation steps for each gate.

## Success Metrics
- Merge-blocking gate pass rate trends upward after first week.
- Number of post-merge regressions from feature/refactor/deploy paths decreases.
- Time to triage CI failures decreases due to clearer failure categories.

## Dependencies & Prerequisites
- GitHub repo admin access for branch protection updates.
- Stable health endpoint behavior in CI environment.
- Team agreement on change-type label taxonomy.

## Risk Analysis & Mitigation
- Risk: Label misuse or omission.
  - Mitigation: fail-fast label validator + PR template guidance.
- Risk: CI flakiness from startup/smoke checks.
  - Mitigation: deterministic startup command, explicit timeout, artifact logs.
- Risk: Coverage gate causes short-term friction.
  - Mitigation: introduce clear per-module test ownership and failure guidance.

## Documentation Plan
Update:
- `.github/workflows/ci.yml` with explicit gate jobs.
- `pyproject.toml` to enforce coverage target.
- `DEPLOYMENT.md` to add deploy verification checklist integration.
- Add `docs/quality-gates.md` (or extend existing docs) with label rules and troubleshooting.

## References & Research

### Internal References
- Current CI baseline: `.github/workflows/ci.yml:1`
- Current pytest/mypy config: `pyproject.toml:48`
- Brainstorm decisions (source of truth for WHAT): `docs/brainstorms/2026-02-17-quality-gates-for-features-deploys-refactors-brainstorm.md:18`
- Existing deploy verification context: `DEPLOYMENT.md:65`
- Existing workflow learning on deterministic cache verification: `docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md:1`
- Existing workflow learning on startup dependency drift: `docs/solutions/workflow-issues/fastapi-server-startup-fails-supabase-dependency-missing-20260217.md:1`

### Conventions
- Keep commands concise and interpreter-scoped (`python -m ...`) per repo guidance: `CLAUDE.md:13`

## Execution Checklist
- [ ] Finalize CI job names and branch protection required statuses.
- [ ] Implement baseline strict jobs.
- [ ] Implement change-type and evidence enforcement.
- [ ] Update docs and examples.
- [ ] Run dry-run PR to validate developer UX and failure messages.
