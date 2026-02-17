# AGENTS.md

## Purpose
Repository-specific instructions for coding agents working in this project.

## Quality Gate Rules (Required)
- Treat `main` as strict-gated.
- Run before proposing merge-ready changes:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`
- Coverage policy: pytest fail-under is `80%`.

## PR Policy Rules (Required)
- PR must include exactly one change-type label:
  - `change:feature`
  - `change:refactor`
  - `change:deploy`
- PR body must include matching section:
  - `### Feature Test Evidence`
  - `### Refactor Regression Evidence`
  - `### Deploy Verification Plan`
- Avoid placeholder evidence (`TBD`, `TODO`, `N/A`).

## Required Branch Protection Checks (`main`)
- `lint`
- `typecheck`
- `tests`
- `health-smoke`
- `pr-policy`
- `quality-gate-pr`

## Canonical References
- CI workflow: `.github/workflows/ci.yml`
- Quality gate policy: `docs/quality-gates.md`
- PR template: `.github/pull_request_template.md`
