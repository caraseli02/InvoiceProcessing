---
title: "feat: Fast feedback dev loop (enforced releasability)"
type: feat
date: 2026-02-28
---

# feat: Fast feedback dev loop (enforced releasability)

## Overview

Make “is my change releasable?” fast to answer locally, and impossible to bypass on `main`.

This plan operationalizes the repo’s stated principles:

- Speed & quality of feedback is first-class.
- Tests are the definitive release gate.
- Prefer continuous integration (small, frequent integration) over long-lived feature branches.
- Invest in a fast, reliable, repeatable pipeline to production.

## Context (what exists today)

Found brainstorm from 2026-02-17: `quality-gates-for-features-deploys-refactors`. Using as context for planning.

Already present in repo:

- CI checks: `ruff`, `mypy`, `pytest` (coverage fail-under 80%), and runtime `/health` smoke (`.github/workflows/ci.yml`).
- PR policy enforcement: exactly one change-type label + matching evidence section (`pr-policy` job + `.github/pull_request_template.md`).
- Local guidance: run `ruff`/`mypy`/`pytest` before PR (`CLAUDE.md`, `docs/quality-gates.md`).
- Git hooks: secret-file blocking pre-commit hook (`.githooks/pre-commit`).

Key gap discovered (2026-02-28):

- `main` is not protected in GitHub (required checks can be bypassed by merging/pushing without enforcement).

## Problem Statement / Motivation

The project has the right checks and docs, but the development loop still risks:

- **Slow feedback**: CI repeats expensive setup in every job; local “one command” gate is missing.
- **Low trust feedback**: environment leakage/regressions can waste cycles (see past learning on config/env isolation).
- **Hidden changes**: lack of enforced `main` protections invites drift from “always releasable”.
- **Pipeline gaps**: Docker/deploy-path breakage may only be caught at deploy time.

## Proposed Solution (high level)

1. **Enforce “releasable” on `main`** with GitHub branch protection requiring the existing CI checks.
2. **Make local feedback trivial and fast** via a canonical `./bin/check` (and optional `./bin/smoke`) that mirrors CI gates.
3. **Reduce CI runtime without reducing accuracy** by removing unnecessary system setup from jobs that don’t need it.
4. **Shift deployment feedback left** by validating Docker build (and optionally container `/health`) in CI.
5. **Document an explicit integration workflow** that discourages long-lived branches and encourages small PRs with evidence.

## Technical Considerations

- Keep git hooks fast. Current hook blocks secrets; do not add slow checks to pre-commit by default.
- Prefer checks that validate **behavior**, not only syntax:
  - keep `health-smoke` as a runtime behavior gate.
  - ensure tests run in deterministic/offline mode where possible (use existing `MOCK=true` patterns).
- CI speed improvements must not reduce reproducibility:
  - avoid “it worked locally” by aligning local commands with CI commands.
  - consider locking dependencies once workflow is stable.

## Implementation Plan

### Phase 1: Enforcement (make bypass impossible)

- [ ] Enable GitHub branch protection on `main` to require:
  - [ ] `lint`
  - [ ] `typecheck`
  - [ ] `tests`
  - [ ] `health-smoke`
  - [ ] `pr-policy`
  - [ ] `quality-gate-pr`
- [ ] Disable direct pushes to `main` (admins optional, based on team preference).
- [ ] Decide whether to require at least one approving review (open question from brainstorm).

Suggested verification:

- [ ] Attempt to merge a PR with one check failing → merge is blocked.
- [ ] Attempt to merge a PR missing label/evidence → merge is blocked by `pr-policy`.

### Phase 2: Local “fast feedback” entrypoints (answer releasability quickly)

- [ ] Add `bin/check`:
  - runs `python -m ruff check src/ tests/`
  - runs `python -m mypy src/`
  - runs `python -m pytest -q`
  - prints a short summary and exits non-zero on failure
- [ ] Add `bin/smoke` (optional, fast runtime validation):
  - starts API in `MOCK=true` mode
  - curls `/health`
  - shuts down cleanly
- [ ] Update `README.md` and `CLAUDE.md` to treat `./bin/check` as the canonical local gate.

Why this helps:

- Encourages small steps with immediate feedback (run after each change).
- Makes “is my change releasable?” a single command.

### Phase 3: CI speed without losing accuracy

Current CI repeats `apt-get install tesseract-ocr` in every job. Many steps don’t require it.

- [ ] Remove system dependency install from jobs that don’t need OCR runtime:
  - [ ] `lint`: no `tesseract-ocr`
  - [ ] `typecheck`: no `tesseract-ocr`
  - [ ] Re-evaluate `tests`: keep only if tests genuinely exercise OCR paths
  - [ ] Keep for `health-smoke` only if API startup path needs it
- [ ] Consider splitting install extras for CI:
  - [ ] `.[dev]` for lint/typecheck
  - [ ] `.[dev,api]` only where API imports are needed
- [ ] Add a small “CI runtime baseline” note in docs (capture p50/p95 job times before/after).

### Phase 4: Pipeline-to-production confidence (behavior gates for deploy paths)

- [ ] Add a CI job to validate Docker build:
  - [ ] `docker build .` succeeds
- [ ] Optional: run container and verify `/health`:
  - [ ] `docker run ...` then `curl /health`
- [ ] Ensure deployment docs remain aligned with enforced CI policy (`DEPLOYMENT.md`, `docs/quality-gates.md`).

### Phase 5: Integration workflow guidance (avoid “hidden” work)

- [ ] Document a “small-step” workflow in `docs/newcomer-guide.md`:
  - [ ] prefer PRs that are small and verifiable
  - [ ] run `./bin/check` before push
  - [ ] integrate frequently (rebase/merge main often)
- [ ] Revisit CI triggers that encourage long-lived branches:
  - [ ] consider removing push trigger for `feat/**` and standardize on PR-based CI only
  - [ ] keep push-to-main CI as the post-merge safety net (`quality-gate-push`)
- [ ] Cross-repo parity rule for integration debugging (learning from 2026-02-11):
  - [ ] “sync frontend + backend to latest `main`” before concluding regressions
  - [ ] record commit SHAs in bug reports

## Acceptance Criteria

- [ ] `main` enforces required checks (cannot merge with failing `lint/typecheck/tests/health-smoke/pr-policy/quality-gate-pr`).
- [ ] A developer can answer “is my change releasable?” by running `./bin/check`.
- [ ] CI no longer installs OCR system deps in jobs that don’t need them (measurable runtime reduction).
- [ ] Docker build is validated in CI (deploy-path breakage caught before deployment).
- [ ] PR policy (label + evidence) remains enforced and documented.

## Success Metrics

- CI: reduced median PR wall-clock time (measure before/after from GitHub Actions).
- Local: `./bin/check` runtime is short enough to run repeatedly during development (target: “minutes, not tens of minutes”).
- Reliability: fewer “false regression” investigations caused by stale branch parity or env leakage.

## Dependencies & Risks

- Requires GitHub repo admin access to enable branch protection.
- Risk: removing `tesseract-ocr` from CI jobs could uncover hidden runtime dependencies.
  - Mitigation: only remove per-job after confirming tests/import paths don’t require it.
- Risk: adding slow checks to git hooks would harm feedback speed.
  - Mitigation: keep hooks fast; use `./bin/check` + CI as the gate.

## References & Research (internal)

- Quality gate policy: `docs/quality-gates.md`
- CI workflow: `.github/workflows/ci.yml`
- PR template: `.github/pull_request_template.md`
- Local policy: `CLAUDE.md`
- Learning: strict gates + env isolation: `docs/solutions/workflow-issues/strict-quality-gates-for-prs-development-workflow-20260217.md`
- Learning: stale branch parity masking behavior: `docs/solutions/workflow-issues/stale-frontend-branch-masked-pricing-fixes-development-workflow-20260211.md`

## Open Questions

- Should `main` require at least 1 approving review in addition to status checks?
- Should Docker `/health` run in CI for every PR, or only for deploy-related changes?
- Do we want dependency locking now (repeatability), or after the workflow stabilizes?

