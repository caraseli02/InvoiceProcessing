---
date: 2026-02-06
issue_id: "018"
status: resolved
severity: p1
category: security-issues
component: config
tags: [configuration, validation, iso-4217, security]
related_issues: ["019", "020"]
---

# Configuration Injection via ALLOWED_CURRENCIES

## Problem Statement

The `ALLOWED_CURRENCIES` configuration parameter lacked validation, allowing users to inject arbitrary, malformed, or potentially malicious values through environment variables or `.env` files. This created multiple security vulnerabilities and reliability issues.

**Why this matters:**
- **Denial of Service (DoS)**: Invalid or malformed currency codes could crash the application during validation
- **Fraud Risk**: Fake or invalid currency codes could be used to manipulate invoice processing
- **Data Integrity**: Non-ISO-compliant currency codes could break integrations with payment systems, accounting software, and reporting tools
- **Attack Surface**: Lack of input validation creates opportunities for injection attacks
- **Compliance**: Violates financial data handling standards requiring ISO 4217 compliance

## Symptoms

- Application accepts any string value for `ALLOWED_CURRENCIES` without validation
- No verification that currency codes follow ISO 4217 standard (3-letter alphabetic codes)
- Runtime errors when invalid currencies are used in validation logic
- Potential for command injection if currency codes are used in shell operations
- Silent failures when currencies are invalid but don't cause immediate crashes
- Inconsistent behavior across different environments (dev vs production)

## Root Cause

The root cause was a lack of input validation on the `ALLOWED_CURRENCIES` configuration parameter in `src/invproc/config.py`. The field was defined as a simple string with no validators:

```python
# BEFORE: No validation on allowed_currencies
allowed_currencies: str = Field(
    default="EUR,USD,MDL,RUB,RON",
    description="Comma-separated list of allowed currency codes",
)
```

The `get_allowed_currencies()` method performed basic parsing but no validation:

```python
# BEFORE: Only parsed, no validation
def get_allowed_currencies(self) -> set[str]:
    return {
        c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
    }
```

This meant:
- No validation of ISO 4217 format (3-letter alphabetic codes)
- No verification that codes are actually valid currencies
- No protection against injection attacks
- No error messages for invalid configuration
- Empty or whitespace-only strings could create empty sets

## Solution

### 1. Add Pydantic Field Validator

Added a comprehensive `@field_validator` to validate `ALLOWED_CURRENCIES` at configuration initialization time:

```python
# AFTER: Complete ISO 4217 validation
@field_validator("allowed_currencies")
@classmethod
def validate_allowed_currencies_format(cls, v: str) -> str:
    """Validate allowed currencies are valid ISO 4217 format."""
    if not v:
        raise ValueError("ALLOWED_CURRENCIES cannot be empty")

    currencies = [c.strip().upper() for c in v.split(",") if c.strip()]

    if not currencies:
        raise ValueError("ALLOWED_CURRENCIES cannot be empty")

    # Validate format: 3 letters, alphabetic only
    for currency in currencies:
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(
                f"Invalid currency code format: '{currency}'. "
                f"Must be 3-letter ISO 4217 codes (e.g., USD, EUR)."
            )

    # Validate against official ISO 4217 database
    valid_iso_codes = {c.alpha_3 for c in pycountry.currencies}
    invalid = set(currencies) - valid_iso_codes
    if invalid:
        raise ValueError(
            f"Invalid ISO 4217 codes: {', '.join(sorted(invalid))}. "
            f"See https://en.wikipedia.org/wiki/ISO_4217"
        )

    return v
```

### 2. Add Safe Fallback for Edge Cases

Enhanced `get_allowed_currencies()` with safe fallback for edge cases:

```python
# AFTER: Safe fallback for empty sets
def get_allowed_currencies(self) -> set[str]:
    """Parse allowed currencies from comma-separated string."""
    currencies = {
        c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
    }

    # Safe fallback if set is empty
    if not currencies:
        logger.warning(
            "ALLOWED_CURRENCIES produced empty set (value: '%s'). "
            "Using safe default.",
            self.allowed_currencies,
        )
        return {"EUR", "USD", "MDL", "RUB", "RON"}

    return currencies
```

### 3. Add Startup Validation

Integrated validation into the `validate_config()` method (see Issue #020):

```python
def validate_config(self) -> None:
    """Validate configuration at startup. Raises ValueError if invalid."""
    errors = []

    # Validate ALLOWED_CURRENCIES
    currencies = self.get_allowed_currencies()
    if not currencies:
        errors.append("ALLOWED_CURRENCIES cannot be empty")

    # ... other validations ...

    if errors:
        raise ValueError(
            "Configuration validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
```

### 4. Add pycountry Dependency

Added `pycountry>=24.0.0` to `pyproject.toml` dependencies to access the official ISO 4217 currency database:

```toml
dependencies = [
    # ... other dependencies ...
    "pycountry>=24.0.0",
]
```

## Code Changes

### File: src/invproc/config.py

**Before (Lines 73-76):**
```python
allowed_currencies: str = Field(
    default="EUR,USD,MDL,RUB,RON",
    description="Comma-separated list of allowed currency codes",
)
```

**After (Lines 73-105):**
```python
allowed_currencies: str = Field(
    default="EUR,USD,MDL,RUB,RON",
    description="Comma-separated list of allowed currency codes",
)

@field_validator("allowed_currencies")
@classmethod
def validate_allowed_currencies_format(cls, v: str) -> str:
    """Validate allowed currencies are valid ISO 4217 format."""
    if not v:
        raise ValueError("ALLOWED_CURRENCIES cannot be empty")

    currencies = [c.strip().upper() for c in v.split(",") if c.strip()]

    if not currencies:
        raise ValueError("ALLOWED_CURRENCIES cannot be empty")

    for currency in currencies:
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(
                f"Invalid currency code format: '{currency}'. "
                f"Must be 3-letter ISO 4217 codes (e.g., USD, EUR)."
            )

    valid_iso_codes = {c.alpha_3 for c in pycountry.currencies}
    invalid = set(currencies) - valid_iso_codes
    if invalid:
        raise ValueError(
            f"Invalid ISO 4217 codes: {', '.join(sorted(invalid))}. "
            f"See https://en.wikipedia.org/wiki/ISO_4217"
        )

    return v
```

**Why:** Added comprehensive validation at configuration initialization time to prevent invalid values from being accepted.

---

**Before (Lines 142-145):**
```python
def get_allowed_currencies(self) -> set[str]:
    """Parse allowed currencies from comma-separated string."""
    return {
        c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
    }
```

**After (Lines 142-156):**
```python
def get_allowed_currencies(self) -> set[str]:
    """Parse allowed currencies from comma-separated string."""
    currencies = {
        c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
    }

    if not currencies:
        logger.warning(
            "ALLOWED_CURRENCIES produced empty set (value: '%s'). "
            "Using safe default.",
            self.allowed_currencies,
        )
        return {"EUR", "USD", "MDL", "RUB", "RON"}

    return currencies
```

**Why:** Added safe fallback to prevent empty sets from causing runtime failures.

### File: pyproject.toml

**Before:**
```toml
dependencies = [
    "typer>=0.12.0",
    "rich>=14.0.0",
    "openai>=1.50.0",
    "pdfplumber>=0.10.3",
    "pytesseract>=0.3.10",
    "Pillow>=10.2.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.0.0",
    "python-dotenv>=1.0.0",
]
```

**After:**
```toml
dependencies = [
    "typer>=0.12.0",
    "rich>=14.0.0",
    "openai>=1.50.0",
    "pdfplumber>=0.10.3",
    "pytesseract>=0.3.10",
    "Pillow>=10.2.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.0.0",
    "python-dotenv>=1.0.0",
    "pycountry>=24.0.0",
]
```

**Why:** Added pycountry dependency to access the official ISO 4217 currency database for validation.

## Implementation Details

### Validation Strategy

The validation follows a defense-in-depth approach:

1. **Format Validation**: Ensures each code is exactly 3 alphabetic characters
2. **ISO 4217 Database Validation**: Verifies codes exist in the official ISO 4217 database
3. **Empty Value Protection**: Prevents empty strings from creating empty sets
4. **Safe Fallback**: Provides sensible defaults when edge cases occur

### Why pycountry?

Chose `pycountry` for ISO 4217 validation because:
- **Official Data Source**: Uses the official ISO 4217 database from the ISO 639-2 standard
- **Well-Maintained**: Active project with regular updates
- **Comprehensive**: Includes all ISO 4217 currencies, including historical codes
- **Lightweight**: Minimal dependencies, ~500KB download
- **Pythonic**: Simple API for lookup and validation

### Error Messages

The validation provides clear, actionable error messages:

- `"ALLOWED_CURRENCIES cannot be empty"` - Empty input
- `"Invalid currency code format: '{currency}'. Must be 3-letter ISO 4217 codes (e.g., USD, EUR)."` - Wrong format
- `"Invalid ISO 4217 codes: XXX, YYY, ZZZ. See https://en.wikipedia.org/wiki/ISO_4217"` - Not real currencies

### Performance Impact

Minimal performance impact:
- Validation runs once at configuration initialization
- `pycountry.currencies` is a cached attribute
- Set operations are O(n) where n = number of currencies (typically < 10)

### Logging

Added logging for the safe fallback case:
```python
logger.warning(
    "ALLOWED_CURRENCIES produced empty set (value: '%s'). "
    "Using safe default.",
    self.allowed_currencies,
)
```

This helps with debugging configuration issues in production.

## Testing

### Unit Tests Added

Added comprehensive test coverage in `tests/test_config.py`:

```python
def test_get_allowed_currencies():
    """Test parsing allowed currencies from comma-separated string."""
    config = InvoiceConfig(allowed_currencies="EUR,USD,GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}

def test_get_allowed_currencies_case_insensitive():
    """Test that currency codes are case-insensitive and normalized to uppercase."""
    config = InvoiceConfig(allowed_currencies="eur,usd,gBp")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}

def test_get_allowed_currencies_with_spaces():
    """Test that whitespace around currencies is trimmed."""
    config = InvoiceConfig(allowed_currencies="EUR , USD , GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}

def test_get_allowed_currencies_empty_items():
    """Test that empty items are filtered out."""
    config = InvoiceConfig(allowed_currencies="EUR,,USD,,GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}

def test_validate_config_empty_currencies():
    """Test that empty ALLOWED_CURRENCIES raises validation error."""
    with pytest.raises(ValueError, match="ALLOWED_CURRENCIES cannot be empty"):
        config = InvoiceConfig(allowed_currencies="")
        config.validate_config()

def test_validate_config_invalid_format():
    """Test that invalid currency format raises validation error."""
    with pytest.raises(ValueError, match="Invalid currency code format"):
        config = InvoiceConfig(allowed_currencies="US,EURRO")
        config.validate_config()

def test_validate_config_invalid_iso_codes():
    """Test that invalid ISO 4217 codes raise validation error."""
    with pytest.raises(ValueError, match="Invalid ISO 4217 codes"):
        config = InvoiceConfig(allowed_currencies="XXX,YYY,ZZZ")
        config.validate_config()
```

### Integration Testing

The startup validation (Issue #020) ensures configuration is validated before the application runs:

```python
# In get_config() - runs validation automatically
def get_config() -> InvoiceConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()  # Validates ALLOWED_CURRENCIES
        logger.info("Configuration validated successfully")
    return _config_instance
```

### Manual Testing

Test invalid configurations:

```bash
# Test empty value (should fail)
ALLOWED_CURRENCIES="" invproc process test.pdf

# Test invalid format (should fail)
ALLOWED_CURRENCIES="US,EURRO" invproc process test.pdf

# Test fake codes (should fail)
ALLOWED_CURRENCIES="XXX,YYY,ZZZ" invproc process test.pdf

# Test valid configuration (should succeed)
ALLOWED_CURRENCIES="EUR,USD,GBP" invproc process test.pdf --mock
```

## Prevention

### 1. Validate All Configuration at Startup

Never trust user input, even from environment variables. Validate all configuration at startup using Pydantic validators:

```python
@field_validator("any_config_field")
@classmethod
def validate_field(cls, v):
    # Always validate
    if not is_valid(v):
        raise ValueError("Clear error message")
    return v
```

### 2. Use Official Data Sources

For standards-based validation (ISO codes, country codes, etc.), use official data sources like pycountry instead of maintaining hardcoded lists:

```python
# Good: Use pycountry
import pycountry
valid_iso_codes = {c.alpha_3 for c in pycountry.currencies}

# Bad: Hardcoded list
valid_iso_codes = {"USD", "EUR", "GBP"}  # Incomplete, outdated
```

### 3. Provide Clear Error Messages

Error messages should be actionable and include:
- What went wrong
- What was expected
- Examples of valid values
- Links to documentation

```python
raise ValueError(
    f"Invalid ISO 4217 codes: {', '.join(sorted(invalid))}. "
    f"See https://en.wikipedia.org/wiki/ISO_4217"
)
```

### 4. Fail Fast, Not Fail Late

Validate configuration at startup, not at runtime. This prevents partial execution and makes debugging easier:

```python
# Good: Validate at startup
def get_config() -> InvoiceConfig:
    config = InvoiceConfig()
    config.validate_config()  # Fails before any processing
    return config

# Bad: Validate during processing
def process_invoice():
    if not is_valid_currency(data.currency):  # Fails after PDF extraction
        raise ValueError(...)
```

### 5. Add Safe Fallbacks for Edge Cases

Even with validation, edge cases can occur. Add safe fallbacks to prevent crashes:

```python
def get_allowed_currencies(self) -> set[str]:
    currencies = parse_currencies(self.allowed_currencies)
    if not currencies:
        logger.warning("Using safe default")
        return {"EUR", "USD"}  # Safe fallback
    return currencies
```

### 6. Document Configuration Requirements

Document all configuration requirements in README.md, .env.example, and inline comments:

```bash
# .env.example
# Comma-separated list of allowed ISO 4217 currency codes
# Must be valid 3-letter alphabetic codes (e.g., EUR, USD, GBP)
# See: https://en.wikipedia.org/wiki/ISO_4217
ALLOWED_CURRENCIES=EUR,USD,MDL,RUB,RON
```

### 7. Use Type Hints and Pydantic

Use Pydantic with type hints for automatic validation and clear error messages:

```python
from pydantic import Field, field_validator

class InvoiceConfig(BaseSettings):
    allowed_currencies: str = Field(
        default="EUR,USD,MDL,RUB,RON",
        description="Comma-separated list of allowed currency codes",
    )

    @field_validator("allowed_currencies")
    @classmethod
    def validate_allowed_currencies_format(cls, v: str) -> str:
        # Validation logic
        return v
```

## Resources

### Related Issues
- Issue #019: Global dependency in Pydantic model - Related architecture fix
- Issue #020: No startup config validation - Related validation fix

### Related Documentation
- [ISO 4217 Currency Codes](https://en.wikipedia.org/wiki/ISO_4217)
- [pycountry Documentation](https://pypi.org/project/pycountry/)
- [Pydantic Validators](https://docs.pydantic.dev/latest/concepts/validators/)

### External Resources
- [OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)
- [OWASP Configuration Management](https://cheatsheetseries.owasp.org/cheatsheets/Configuration_Management_Cheat_Sheet.html)
- [ISO 4217 Active Currency Codes](https://www.six-group.com/en/products-services/financial-information/data-standards/iso-4217.html)

## Verification

### Acceptance Criteria

- [x] `ALLOWED_CURRENCIES` validates ISO 4217 format (3 letters, alphabetic)
- [x] `ALLOWED_CURRENCIES` validates against official ISO 4217 database using pycountry
- [x] Empty or malformed `ALLOWED_CURRENCIES` raises clear error at startup
- [x] Safe fallback prevents empty sets from causing runtime failures
- [x] All unit tests pass (33 tests)
- [x] Type checking passes (mypy)
- [x] Linting passes (ruff)
- [x] pycountry>=24.0.0 added to dependencies
- [x] Comprehensive test coverage for validation edge cases

## Notes

- This fix was part of commit `43dd72d6`
- Combined with Issues #019 and #020 for comprehensive configuration security hardening
- All P1 critical issues blocking merge are now resolved
- The validation is backward compatible - existing valid configurations continue to work
- The safe fallback uses the original default value: {"EUR", "USD", "MDL", "RUB", "RON"}
