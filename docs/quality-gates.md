# Quality Gates

Strict merge gates are required for `main`.

## Baseline Required Checks
Every PR must pass:
- `ruff check src/ tests/`
- `mypy src/`
- `pytest` with coverage gate `>= 80%`
- API smoke check: `GET /health` returns 200 in CI

## Change-Type Policy
Each PR must include exactly one label:
- `change:feature`
- `change:refactor`
- `change:deploy`

CI enforces this label requirement.

## Required PR Evidence By Type
- `change:feature`: fill `Feature Test Evidence` with test proof and behavior verification.
- `change:refactor`: fill `Refactor Regression Evidence` with parity proof and regression tests.
- `change:deploy`: fill `Deploy Verification Plan` with pre/post deploy checks.

CI fails if the required section is missing or placeholder-only.

## Branch Protection Setup
In GitHub branch protection for `main`, require these status checks:
- `lint`
- `typecheck`
- `tests`
- `health-smoke`
- `pr-policy`
- `quality-gate-pr`

## Failure Triage
- Coverage failure: add tests for touched paths or reduce untested branches.
- Smoke failure: inspect `/tmp/invproc-api.log` from CI logs and verify app startup command.
- PR policy failure: fix label selection and fill required PR template section with concrete evidence.
