---
date: 2026-02-06
layer: 3
focus: code-quality
status: planning
---

# Layer 3: Code Quality Improvements

## Overview

Improve codebase maintainability and type safety through three focused code quality improvements: moving currency validation to configuration, removing duplicate field definitions, and adding mypy type checking to CI pipeline.

## Objectives

1. Make currency list configurable instead of hardcoded
2. Eliminate duplicate code (api_keys field)
3. Add static type checking to catch bugs early
4. Improve developer experience with better IDE support

## Implementation Plan

### Improvement 1: Move Currency List to Configuration

**Files to modify:**
- `src/invproc/config.py` - Add `allowed_currencies` field
- `src/invproc/models.py` - Import from config instead of hardcoded set
- `.env.example` - Add ALLOWED_CURRENCIES example

**Changes:**

#### 1.1 Update `src/invproc/config.py`

Add new field after `ocr_config` (line 67):

```python
allowed_currencies: str = Field(
    default="EUR,USD,MDL,RUB,RON",
    description="Comma-separated list of allowed currency codes",
)
```

Add helper method after `create_output_dirs` (line 107):

```python
def get_allowed_currencies(self) -> set[str]:
    """Parse allowed currencies from comma-separated string."""
    return {c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()}
```

#### 1.2 Update `src/invproc/models.py`

Remove hardcoded currency list (line 46):

**Before:**
```python
valid_currencies = {"EUR", "USD", "MDL", "RUB", "RON"}
v_upper = v.upper()
if v_upper not in valid_currencies:
    raise ValueError(f"Invalid currency: {v}. Valid: {valid_currencies}")
return v_upper
```

**After:**
```python
from invproc.config import get_config

config = get_config()
valid_currencies = config.get_allowed_currencies()
v_upper = v.upper()
if v_upper not in valid_currencies:
    raise ValueError(f"Invalid currency: {v}. Valid: {', '.join(sorted(valid_currencies))}")
return v_upper
```

**Import addition at top of file:**
```python
from invproc.config import get_config
```

#### 1.3 Update `.env.example`

Add after `ALLOWED_ORIGINS`:

```bash
# Currency settings
ALLOWED_CURRENCIES=EUR,USD,MDL,RUB,RON
```

#### 1.4 Update `render.yaml`

Add after `ALLOWED_ORIGINS`:

```yaml
- key: ALLOWED_CURRENCIES
  value: EUR,USD,MDL,RUB,RON
```

**Benefits:**
- Easy to add new currencies without code changes
- Different environments can have different allowed currencies
- More flexible for multi-region deployments

**Tests to add:**
```python
# tests/test_config.py
def test_get_allowed_currencies():
    config = InvoiceConfig(allowed_currencies="EUR,USD,GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}

def test_get_allowed_currencies_case_insensitive():
    config = InvoiceConfig(allowed_currencies="eur,usd")
    assert config.get_allowed_currencies() == {"EUR", "USD"}
```

---

### Improvement 2: Remove Duplicate api_keys Field

**Files to modify:**
- `src/invproc/config.py` - Remove duplicate definition (lines 96-99)

**Changes:**

Delete lines 96-99 (duplicate api_keys field):

```python
# DELETE THESE LINES:
api_keys: str = Field(
    default="",
    description="Comma-separated API keys for authentication",
)
```

**Benefits:**
- Cleaner code, no duplication
- Single source of truth for API keys configuration
- Prevents confusion when maintaining code

**Tests to add:**
```python
# tests/test_config.py
def test_api_keys_default():
    config = InvoiceConfig()
    assert config.api_keys == ""

def test_api_keys_from_env():
    import os
    os.environ["API_KEYS"] = "key1,key2"
    config = InvoiceConfig()
    assert "key1" in config.api_keys
    assert "key2" in config.api_keys
```

---

### Improvement 3: Add Mypy Type Checking to CI

**Files to modify:**
- `.github/workflows/ci.yml` - Add mypy step
- `pyproject.toml` - Add mypy configuration
- `requirements.txt` or `pyproject.toml` - Add mypy dependency (check if already in dev extras)

**Changes:**

#### 3.1 Update `.github/workflows/ci.yml`

Add mypy step after "Run tests" (after line 38):

```yaml
      - name: Type check with mypy
        run: mypy src/
```

#### 3.2 Add mypy configuration to `pyproject.toml`

Add new section at root (after [tool.ruff]):

```toml
[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
ignore_missing_imports = true
strict_optional = true
check_untyped_defs = true
```

#### 3.3 Update dependencies

Check if mypy is already in dev extras:

```bash
grep -A 10 "\[project.optional-dependencies\]" pyproject.toml
```

If not present, add to dev dependencies.

**Benefits:**
- Catch type errors before runtime
- Better IDE autocomplete and refactoring support
- Enforce type discipline across codebase
- Prevents bugs like returning wrong types

**Initial type errors to fix:**
Based on code analysis, expect mypy to flag:
- `api.py:91` - Missing type hint for `get_allowed_origins()` return type (already fixed)
- `api.py:60` - `get_remote_address` from slowapi may need type ignore
- Config singleton pattern may need `-> None` or proper type annotation

---

## Testing Strategy

### Unit Tests
Add `tests/test_config.py` with:
- Currency list parsing tests
- API keys default and env-based tests
- Config reload tests

### Type Checking
Run mypy locally first:
```bash
pip install mypy
mypy src/ --ignore-missing-imports
```

Fix all type errors before committing.

### Integration Tests
- Test currency validation with different env configs
- Test API authentication with multiple keys
- Test CLI with custom currency config

---

## Rollback Plan

If issues arise:
1. **Currency config**: Revert to hardcoded set, remove config field
2. **Duplicate api_keys**: Restore duplicate definition (unlikely to cause issues)
3. **Mypy**: Remove mypy step from CI, comment out pyproject.toml config

Each improvement is independent, can be rolled back individually.

---

## Success Criteria

- [x] Currency list configurable via environment variable
- [x] Duplicate api_keys field removed
- [x] Mypy added to CI and passes without errors
- [x] All existing tests still pass
- [x] New tests added for config changes
- [x] Documentation updated (.env.example, render.yaml)
- [x] No mypy errors in src/ directory

---

## Estimated Effort

| Improvement | Implementation | Testing | Total |
|-------------|---------------|----------|-------|
| Move currency to config | 2h | 1h | 3h |
| Remove duplicate api_keys | 15m | 30m | 45m |
| Add mypy to CI | 2h | 2h | 4h |
| **Total** | **4.25h** | **3.5h** | **7.75h** |

**Timeline:** 1 day if working full-time, or 2-3 sessions if part-time.

---

## Dependencies

None - these improvements are independent and can be done in any order.

Recommended order:
1. Remove duplicate api_keys (quick win)
2. Move currency to config (requires testing)
3. Add mypy to CI (may reveal type errors to fix)

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing deployments (currency config) | Low | Medium | Keep same default currencies in config |
| Mypy reveals many type errors | Medium | Medium | Fix incrementally, use type: ignore if necessary |
| Regression in currency validation | Low | Low | Add comprehensive tests for edge cases |

---

## Open Questions

- [ ] Should mypy enforce `disallow_untyped_defs` immediately or phase in?
- [ ] Any other type checkers to consider? (pyright, pyre)
- [ ] Should we add `strict` mode for mypy eventually?

---

## Next Steps

1. Create PR for Improvement 2 (duplicate api_keys) - quick win ✅
2. Create PR for Improvement 1 (currency config) - requires tests ✅
3. Create PR for Improvement 3 (mypy) - may require type fixes ✅
4. Merge PRs incrementally after review
5. Update documentation as each PR is deployed

✅ All improvements completed in a single PR as requested
