## Summary
- Describe the change briefly.

## Change Type Label
Apply exactly one label to this PR:
- `change:feature`
- `change:refactor`
- `change:deploy`

## Verification
- [ ] `pytest` passes locally
- [ ] `mypy src/` passes locally
- [ ] `ruff check src/ tests/` passes locally
- [ ] Coverage is at least 80%
- [ ] `/health` smoke check works

### Feature Test Evidence
Provide concrete evidence for feature changes (tests added/updated, commands run, behavior proven).

### Refactor Regression Evidence
Provide concrete evidence for refactors (behavior parity proof, unchanged contracts, tests proving no regressions).

### Deploy Verification Plan
Provide deploy pre/post checks (health, key endpoint validation, rollback trigger).
