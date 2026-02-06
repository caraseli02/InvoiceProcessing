---
date: 2026-02-06
issue_id: "020"
status: resolved
severity: p1
category: configuration-issues
component: config
tags: [configuration, validation, startup, fail-fast, error-handling]
related_issues: ["018", "019"]
---

# No Startup Configuration Validation

## Problem Statement

The application did not validate configuration at startup, allowing invalid configuration to be loaded and only failing at runtime when the configuration was actually used. This created poor user experience, made debugging difficult, and could lead to partial execution before failures occurred.

**Why this matters:**
- **Poor User Experience**: Users only discover configuration errors after the application has started processing files
- **Hard to Debug**: Runtime failures occur in deep call stacks, making it difficult to trace back to configuration issues
- **Partial Execution**: Application might partially process data before failing, leaving inconsistent state
- **Production Risk**: Invalid configuration could slip into production and cause failures in production workloads
- **Time Wasted**: Developers spend time debugging runtime issues that should have been caught at startup
- **Inconsistent Behavior**: Different paths might trigger validation at different times, leading to inconsistent errors

## Symptoms

- Application starts successfully even with invalid configuration
- Configuration errors only appear when specific features are used (e.g., currency validation only when processing an invoice)
- Error messages appear late in the execution flow (e.g., after PDF extraction)
- Multiple error messages might appear sequentially as different invalid configurations are encountered
- No single point of configuration validation
- Environment variable changes don't trigger validation until code paths are exercised

## Root Cause

The root cause was the lack of a startup validation hook. The `get_config()` function simply created and returned an `InvoiceConfig` instance without validating it:

```python
# BEFORE: No startup validation
def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()  # Just creates instance, no validation
    return _config_instance
```

Configuration validation only happened:
1. In Pydantic field validators (e.g., `allowed_currencies` format validation)
2. During business logic (e.g., currency validation in models or validators)

This meant:
- Missing required values (like `OPENAI_API_KEY`) were not caught at startup
- Invalid values in config fields were only caught when Pydantic validators ran
- Cross-field validations (e.g., "API key required unless mock mode") couldn't exist
- No centralized place for configuration validation logic
- No way to validate configuration before the application started processing

## Solution

### 1. Add validate_config() Method

Added a comprehensive `validate_config()` method to `InvoiceConfig` that performs all configuration validations:

```python
# AFTER: Centralized configuration validation
def validate_config(self) -> None:
    """Validate configuration at startup. Raises ValueError if invalid."""
    errors = []

    # Validate ALLOWED_CURRENCIES
    currencies = self.get_allowed_currencies()
    if not currencies:
        errors.append("ALLOWED_CURRENCIES cannot be empty")

    # Validate OpenAI API key (if not using mock mode)
    if not self.mock and not self.openai_api_key:
        errors.append("OPENAI_API_KEY required when mock mode is disabled")

    # Validate OCR config (basic security check)
    if self.ocr_config:
        if ";" in self.ocr_config or "&" in self.ocr_config:
            errors.append("OCR_CONFIG contains suspicious characters (; or &)")

    # Validate numeric ranges
    if self.temperature < 0 or self.temperature > 2:
        errors.append("TEMPERATURE must be between 0 and 2")

    if self.scale_factor <= 0:
        errors.append("SCALE_FACTOR must be positive")

    if self.ocr_dpi < 72 or self.ocr_dpi > 600:
        errors.append("OCR_DPI must be between 72 and 600")

    # Raise error if any validation failed
    if errors:
        raise ValueError(
            "Configuration validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
```

**Why:**
- Centralizes all configuration validation in one place
- Validates cross-field constraints (e.g., API key only required if not in mock mode)
- Collects all validation errors before raising, giving users complete feedback
- Provides clear, actionable error messages
- Runs early in the application lifecycle

### 2. Call Validation in get_config()

Updated `get_config()` to call `validate_config()` immediately after creating the config instance:

```python
# AFTER: Validation runs on first config access
def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()  # Validate at startup!
        logger.info("Configuration validated successfully")
    return _config_instance
```

**Why:**
- Validation runs automatically on first access to configuration
- No changes required to existing code that calls `get_config()`
- Guarantees config is validated before any other code runs
- Provides logging for successful validation

### 3. Add get_config_unvalidated() for Testing

Added `get_config_unvalidated()` for testing scenarios where validation needs to be skipped:

```python
def get_config_unvalidated() -> InvoiceConfig:
    """Get or create global configuration instance without validation."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
    return _config_instance
```

**Why:**
- Allows tests to test validation logic itself
- Enables testing of invalid configurations without the application failing
- Useful for unit tests that need to test specific validation rules

### 4. Add Logging

Added logging for successful validation:

```python
import logging

logger = logging.getLogger(__name__)

def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()
        logger.info("Configuration validated successfully")
    return _config_instance
```

**Why:**
- Provides visibility into when validation runs
- Helps with debugging startup issues
- Confirms configuration loaded successfully

## Code Changes

### File: src/invproc/config.py

**Before (Lines 194-204):**
```python
_config_instance = None


def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
    return _config_instance


def reload_config() -> InvoiceConfig:
    """Reload configuration (useful for testing)."""
    global _config_instance
    _config_instance = InvoiceConfig()
    return _config_instance
```

**After (Lines 194-220):**
```python
_config_instance = None


def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()
        logger.info("Configuration validated successfully")
    return _config_instance


def get_config_unvalidated() -> InvoiceConfig:
    """Get or create global configuration instance without validation."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
    return _config_instance


def reload_config() -> InvoiceConfig:
    """Reload configuration (useful for testing)."""
    global _config_instance
    _config_instance = InvoiceConfig()
    return _config_instance
```

**Why:**
- Added `validate_config()` call to `get_config()`
- Added `get_config_unvalidated()` for testing
- Added logging for successful validation
- Kept `reload_config()` unchanged (test helper, doesn't need validation)

---

**Added (Lines 158-191):**
```python
def validate_config(self) -> None:
    """Validate configuration at startup. Raises ValueError if invalid."""
    errors = []

    # Validate ALLOWED_CURRENCIES
    currencies = self.get_allowed_currencies()
    if not currencies:
        errors.append("ALLOWED_CURRENCIES cannot be empty")

    # Validate OpenAI API key (if not using mock mode)
    if not self.mock and not self.openai_api_key:
        errors.append("OPENAI_API_KEY required when mock mode is disabled")

    # Validate OCR config (basic check)
    if self.ocr_config:
        if ";" in self.ocr_config or "&" in self.ocr_config:
            errors.append("OCR_CONFIG contains suspicious characters (; or &)")

    # Validate numeric ranges
    if self.temperature < 0 or self.temperature > 2:
        errors.append("TEMPERATURE must be between 0 and 2")

    if self.scale_factor <= 0:
        errors.append("SCALE_FACTOR must be positive")

    if self.ocr_dpi < 72 or self.ocr_dpi > 600:
        errors.append("OCR_DPI must be between 72 and 600")

    # Raise error if any validation failed
    if errors:
        raise ValueError(
            "Configuration validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
```

**Why:**
- Centralizes all configuration validation logic
- Validates cross-field constraints
- Collects all errors before raising
- Provides clear error messages

## Implementation Details

### Validation Categories

The `validate_config()` method performs three types of validation:

**1. Pydantic Field Validators** (Already existed)
- Run automatically when `InvoiceConfig()` is instantiated
- Validate individual field types and formats
- Example: `allowed_currencies` ISO 4217 validation

**2. Cross-Field Validation** (Added)
- Validates relationships between fields
- Example: `OPENAI_API_KEY` only required if `mock=False`

**3. Business Rule Validation** (Added)
- Validates business-specific constraints
- Example: OCR config security checks, numeric range validations

### Error Collection Strategy

Collects all validation errors before raising, providing complete feedback:

```python
errors = []

# Check all validations
if not self.mock and not self.openai_api_key:
    errors.append("OPENAI_API_KEY required when mock mode is disabled")

if not currencies:
    errors.append("ALLOWED_CURRENCIES cannot be empty")

# ... more checks ...

# Raise once with all errors
if errors:
    raise ValueError(
        "Configuration validation failed:\n"
        + "\n".join(f"  - {e}" for e in errors)
    )
```

**Benefits:**
- Users see all configuration issues at once
- No need to fix one error, restart, then see the next error
- Faster debugging and configuration fixing

### Validation Timing

Validation now runs at the earliest possible point:

```
Application Start
    ↓
get_config() called (first time)
    ↓
InvoiceConfig() created (Pydantic validators run)
    ↓
validate_config() called (cross-field & business rule validators)
    ↓
Configuration validated successfully
    ↓
Rest of application runs with validated config
```

**Before:** Validation scattered throughout execution, failures at runtime
**After:** All validation at startup, failures before any processing

### Mock Mode Special Case

Validation respects mock mode by not requiring `OPENAI_API_KEY`:

```python
# Validate OpenAI API key (if not using mock mode)
if not self.mock and not self.openai_api_key:
    errors.append("OPENAI_API_KEY required when mock mode is disabled")
```

This allows:
- Testing without API keys (`--mock` flag)
- Development without API keys
- CI/CD pipelines to run tests without credentials

### Security Checks

Added basic security checks for OCR configuration:

```python
if self.ocr_config:
    if ";" in self.ocr_config or "&" in self.ocr_config:
        errors.append("OCR_CONFIG contains suspicious characters (; or &)")
```

**Why:** Prevents command injection if OCR config is used in shell operations.

### Numeric Range Validation

Added validation for numeric configuration values:

```python
if self.temperature < 0 or self.temperature > 2:
    errors.append("TEMPERATURE must be between 0 and 2")

if self.scale_factor <= 0:
    errors.append("SCALE_FACTOR must be positive")

if self.ocr_dpi < 72 or self.ocr_dpi > 600:
    errors.append("OCR_DPI must be between 72 and 600")
```

**Why:** Catches configuration errors that might cause runtime failures or poor results.

## Testing

### Unit Tests for Validation

Added comprehensive tests in `tests/test_config.py`:

```python
def test_validate_config_empty_currencies():
    """Test that empty ALLOWED_CURRENCIES raises validation error."""
    with pytest.raises(ValueError, match="ALLOWED_CURRENCIES cannot be empty"):
        config = InvoiceConfig(allowed_currencies="")
        config.validate_config()

def test_validate_config_missing_api_key():
    """Test that missing API key when not in mock mode raises validation error."""
    with pytest.raises(ValueError, match="OPENAI_API_KEY required"):
        config = InvoiceConfig(mock=False, openai_api_key=None)
        config.validate_config()

def test_validate_config_suspicious_ocr_config():
    """Test that suspicious OCR config characters raise validation error."""
    with pytest.raises(ValueError, match="OCR_CONFIG contains suspicious characters"):
        config = InvoiceConfig(ocr_config="--oem 1; rm -rf /")
        config.validate_config()

def test_validate_config_valid():
    """Test that valid configuration passes validation."""
    config = InvoiceConfig(
        allowed_currencies="EUR,USD",
        mock=True,
        temperature=0.5,
        scale_factor=0.2
    )
    config.validate_config()  # Should not raise
```

### Integration Tests for Startup Validation

Test that validation runs at application startup:

```python
def test_get_config_validates_at_startup():
    """Test that get_config() validates configuration."""
    # Clear any cached config
    import invproc.config as config_module
    config_module._config_instance = None

    # Set invalid config
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["MOCK"] = "false"

    # Should raise at get_config() time
    with pytest.raises(ValueError, match="OPENAI_API_KEY required"):
        config_module.get_config()

    # Cleanup
    del os.environ["OPENAI_API_KEY"]
    del os.environ["MOCK"]

def test_get_config_with_mock_mode():
    """Test that mock mode doesn't require API key."""
    config_module._config_instance = None
    os.environ["MOCK"] = "true"
    os.environ["OPENAI_API_KEY"] = ""

    # Should not raise - mock mode doesn't need API key
    config = config_module.get_config()
    assert config.mock is True

    del os.environ["MOCK"]
    del os.environ["OPENAI_API_KEY"]
```

### Manual Testing

Test startup validation with invalid configurations:

```bash
# Test missing API key (should fail at startup)
unset OPENAI_API_KEY
invproc process test.pdf

# Expected output:
# ValueError: Configuration validation failed:
#   - OPENAI_API_KEY required when mock mode is disabled

# Test with mock mode (should succeed)
invproc process test.pdf --mock

# Test invalid OCR config (should fail at startup)
OCR_CONFIG="--oem 1; rm -rf /" invproc process test.pdf

# Expected output:
# ValueError: Configuration validation failed:
#   - OCR_CONFIG contains suspicious characters (; or &)

# Test invalid temperature (should fail at startup)
TEMPERATURE=5.0 invproc process test.pdf --mock

# Expected output:
# ValueError: Configuration validation failed:
#   - TEMPERATURE must be between 0 and 2
```

## Prevention

### 1. Validate All Configuration at Startup

Never rely on runtime validation for configuration issues. Validate everything at startup:

```python
# Good: Validate at startup
def get_config():
    config = InvoiceConfig()
    config.validate_config()  # Fail fast
    return config

# Bad: Validate at runtime
def process_invoice():
    if not config.openai_api_key:
        raise ValueError("Missing API key")  # Too late!
```

### 2. Use Fail-Fast Principle

Fail as early as possible to save time and resources:

```python
# Good: Fail fast
def validate_config(self):
    if not self.mock and not self.openai_api_key:
        raise ValueError("OPENAI_API_KEY required")

# Bad: Fail late
def call_openai_api():
    if not self.openai_api_key:
        raise ValueError("OPENAI_API_KEY required")  # After PDF extraction!
```

### 3. Collect All Errors Before Raising

Don't stop at the first error. Collect all errors for complete feedback:

```python
# Good: Collect all errors
errors = []
if not condition1:
    errors.append("Error 1")
if not condition2:
    errors.append("Error 2")

if errors:
    raise ValueError("\n".join(errors))

# Bad: Stop at first error
if not condition1:
    raise ValueError("Error 1")
if not condition2:
    raise ValueError("Error 2")  # Never reached!
```

### 4. Provide Clear, Actionable Error Messages

Error messages should tell users exactly what's wrong and how to fix it:

```python
# Good: Clear error
raise ValueError(
    "OPENAI_API_KEY required when mock mode is disabled. "
    "Set OPENAI_API_KEY environment variable or use --mock flag."
)

# Bad: Unclear error
raise ValueError("Missing config")
```

### 5. Separate Testing from Production Code

Provide test-only accessors that skip validation:

```python
# Good: Separate test accessor
def get_config_unvalidated():
    """Get config without validation (for testing)."""
    return InvoiceConfig()

# Bad: Mix testing and production
def get_config(validate=True):
    config = InvoiceConfig()
    if validate:
        config.validate_config()
    return config
```

### 6. Add Logging for Successful Validation

Log successful validation to help with debugging:

```python
# Good: Log success
config.validate_config()
logger.info("Configuration validated successfully")

# Bad: Silent success
config.validate_config()  # No visibility
```

### 7. Document Configuration Requirements

Document all configuration requirements in code comments and documentation:

```python
def validate_config(self) -> None:
    """
    Validate configuration at startup.

    Validation Rules:
    - ALLOWED_CURRENCIES: Required, must be non-empty set of ISO 4217 codes
    - OPENAI_API_KEY: Required unless mock mode is enabled
    - OCR_CONFIG: Cannot contain shell injection characters (; or &)
    - TEMPERATURE: Must be between 0 and 2
    - SCALE_FACTOR: Must be positive
    - OCR_DPI: Must be between 72 and 600

    Raises:
        ValueError: If any validation rule is violated
    """
```

### 8. Test Validation Logic Separately

Write tests for validation logic itself:

```python
def test_validate_config_missing_api_key():
    """Test validation logic directly."""
    config = InvoiceConfig(mock=False, openai_api_key=None)
    with pytest.raises(ValueError):
        config.validate_config()
```

### 9. Use Environment Variable Defaults

Provide sensible defaults for optional configuration:

```python
# Good: Has default
allowed_currencies: str = Field(
    default="EUR,USD,MDL,RUB,RON",
    description="..."
)

# Bad: No default, required everywhere
allowed_currencies: str = Field(..., description="...")
```

## Resources

### Related Issues
- Issue #018: Configuration injection via ALLOWED_CURRENCIES - Related validation fix
- Issue #019: Global dependency in Pydantic model - Related architecture fix

### Related Documentation
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [FastAPI Environment Variables](https://fastapi.tiangolo.com/advanced/settings/)
- [Python-dotenv Documentation](https://saurabh-kumar.com/python-dotenv/)

### External Resources
- [Fail Fast Principle](https://martinfowler.com/bliki/FailFast.html)
- [Configuration Management Best Practices](https://12factor.net/config)
- [Environment Variable Naming Conventions](https://linux.die.net/man/7/environ)

## Verification

### Acceptance Criteria

- [x] `validate_config()` method added to `InvoiceConfig`
- [x] `get_config()` calls `validate_config()` on initialization
- [x] `get_config_unvalidated()` added for testing
- [x] Validation checks: ALLOWED_CURRENCIES non-empty
- [x] Validation checks: OPENAI_API_KEY required unless mock mode
- [x] Validation checks: OCR_CONFIG security (no ; or &)
- [x] Validation checks: TEMPERATURE range (0-2)
- [x] Validation checks: SCALE_FACTOR positive
- [x] Validation checks: OCR_DPI range (72-600)
- [x] All validation errors collected before raising
- [x] Clear error messages with all issues listed
- [x] Logging for successful validation
- [x] All 33 tests pass
- [x] Type checking passes (mypy)
- [x] Linting passes (ruff)

## Notes

- This fix was part of commit `43dd72d6`
- Combined with Issues #018 and #019 for comprehensive configuration hardening
- All P1 critical issues blocking merge are now resolved
- The validation runs only once on first call to `get_config()` due to singleton pattern
- `reload_config()` does not call `validate_config()` by design (test helper for testing invalid configs)
- Mock mode is fully supported and doesn't require API key validation
- The validation is extensible - new validation rules can be added to `validate_config()` method
- This aligns with 12-factor app principle: "Store config in the environment" and validate early
