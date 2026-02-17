---
date: 2026-02-17
topic: quality-gates-for-features-deploys-refactors
---

# Quality Gates For Features, Deploys, And Refactors

## What We're Building
We are defining a stricter and cleaner engineering quality policy for changes merged into `main`. The policy must improve confidence for new features, deployments, and refactors without adding unnecessary process overhead.

The chosen direction is a risk-based strict gate model. Every PR gets a strict baseline quality bar, then additional required checks are applied by change type. This keeps standards high while avoiding one-size-fits-all overkill.

## Why This Approach
We considered three options: uniform max strictness for all PRs, staged rollout, and risk-based strict gates. Uniform strictness is simple but creates avoidable friction on low-risk refactors. Staged rollout lowers short-term risk but delays real protection.

Risk-based strict gates provide immediate safety with better signal-to-friction balance. It aligns with YAGNI by enforcing only the checks needed for the type of change while still keeping a hard baseline for quality.

## Key Decisions
- Use strict merge gates on `main`: No merge unless required checks pass.
- Required baseline checks for all PRs: `pytest`, `mypy`, `ruff`, coverage threshold, and deploy smoke health check.
- Coverage threshold: `80%` minimum required in CI.
- Policy model: Risk-based strictness by change type (features, deploys, refactors) rather than one identical heavy policy for every change.
- Feature changes: Must include targeted tests for touched behavior.
- Refactor changes: Must provide regression confidence (existing behavior preserved via tests and evidence).
- Deploy-related changes: Must include explicit pre/post deploy verification steps with health and key endpoint checks.

## Open Questions
- Should branch protection also require at least one approving code review in addition to status checks?
- Should deploy smoke checks run on every PR or only PRs that affect runtime/deployment paths?
- What evidence format should refactor PRs use to prove no behavior change (test list, snapshot diff, checklist)?

## Next Steps
Proceed to `/workflows:plan` to convert this policy direction into concrete CI/workflow/file updates, enforcement rules, and rollout steps.
