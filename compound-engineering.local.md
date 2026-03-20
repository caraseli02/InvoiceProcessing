---
review_agents: [kieran-python-reviewer, code-simplicity-reviewer, security-sentinel, performance-oracle]
plan_review_agents: [kieran-python-reviewer, code-simplicity-reviewer]
---

# Review Context

- This is a Python/FastAPI service with strict quality gates: `ruff`, `mypy`, and full `pytest` must pass before merge-ready work.
- Prefer review attention on dependency-injection boundaries, idempotent write paths, and configuration that claims runtime behavior.
- Treat `docs/brainstorms/`, `docs/plans/`, and `docs/solutions/` as protected workflow artifacts, not cleanup targets.
