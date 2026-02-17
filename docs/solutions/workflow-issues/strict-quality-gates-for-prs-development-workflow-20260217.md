---
module: Development Workflow
date: 2026-02-17
problem_type: workflow_issue
component: development_workflow
symptoms:
  - "PRs could merge without lint checks, coverage threshold, health smoke verification, or deploy/refactor evidence"
  - "Coverage gate activation initially failed at 78.46% and blocked the pipeline"
  - "Config defaults test was environment-sensitive because .env values leaked into `InvoiceConfig()`"
root_cause: missing_tooling
resolution_type: tooling_addition
severity: medium
tags: [ci, github-actions, coverage, branch-protection, workflow, quality-gates]
related:
  - docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md
  - docs/solutions/workflow-issues/fastapi-server-startup-fails-supabase-dependency-missing-20260217.md
---

# Troubleshooting: Strict PR Quality Gates Were Missing and Coverage Enforcement Exposed Gaps

## Problem
`main` did not enforce a strict merge policy for feature, deploy, and refactor changes. CI only covered part of the quality surface, so low-signal PRs could still merge.

## Environment
- Module: Development Workflow
- Affected Component: GitHub Actions CI, PR policy metadata, deployment workflow docs
- Date: 2026-02-17

## Symptoms
- PRs were not blocked on lint, coverage threshold, or runtime smoke checks.
- No enforceable PR metadata existed for change type (`feature`, `refactor`, `deploy`) or required evidence.
- After enabling coverage gate, suite failed at `78.46%` and was below required `80%`.
- `tests/test_config.py::test_supabase_settings_default` failed locally because `.env` injected `SUPABASE_URL`.

## What Didn't Work

**Attempted Solution 1:** Enable coverage gate without adjusting tests.
- **Why it failed:** Existing test coverage sat below threshold and immediately failed CI intent (`<80%`).

**Attempted Solution 2:** Assume config defaults test was environment-independent.
- **Why it failed:** `InvoiceConfig()` reads env/.env; default assertion was not isolated and became machine-dependent.

## Solution
Implemented strict quality tooling and made tests deterministic.

1. Reworked CI into explicit required jobs:
- `lint` (`ruff`)
- `typecheck` (`mypy`)
- `tests` (`pytest` + coverage gate)
- `health-smoke` (`GET /health`)
- `pr-policy` (exactly one change-type label + required PR evidence section)
- aggregate gates: `quality-gate-pr`, `quality-gate-push`

2. Enforced project-wide coverage floor:
- Added pytest options in `pyproject.toml`:
  - `--cov=src/invproc --cov-report=term-missing --cov-fail-under=80`

3. Added policy docs/templates:
- `.github/pull_request_template.md` with evidence sections:
  - `Feature Test Evidence`
  - `Refactor Regression Evidence`
  - `Deploy Verification Plan`
- `docs/quality-gates.md` with required status checks and triage guidance.
- `DEPLOYMENT.md` updated to align deploy flow with PR evidence policy.

4. Fixed deterministic and coverage blockers:
- Updated config tests to isolate env via `monkeypatch` and `_env_file=None`.
- Added focused tests for entrypoint and pricing/exception validation.

## Why This Works
- The CI workflow now enforces both code correctness and operational readiness, not just unit test success.
- Label + evidence policy makes change intent explicit and reviewable.
- Coverage floor (`80%`) blocks under-tested changes early.
- Deterministic tests remove local env leakage and reduce false failures.

## Verification
Commands used:

```bash
python -m pytest -q
python -m mypy src/
python -m ruff check src/ tests/
```

Result at fix time:
- `98 passed`
- Coverage: `80.84%`
- `mypy`: success
- `ruff`: all checks passed

## Prevention
- Keep `.github/pull_request_template.md` aligned with `pr-policy` CI validation logic.
- Treat coverage threshold changes as rollout events: verify current baseline before raising floor.
- For config tests, use explicit env isolation (`monkeypatch`, `_env_file=None`) when asserting defaults.
- In branch protection for `main`, require: `lint`, `typecheck`, `tests`, `health-smoke`, `pr-policy`, `quality-gate-pr`.

## Related Issues
- See also: `docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md`
- See also: `docs/solutions/workflow-issues/fastapi-server-startup-fails-supabase-dependency-missing-20260217.md`
