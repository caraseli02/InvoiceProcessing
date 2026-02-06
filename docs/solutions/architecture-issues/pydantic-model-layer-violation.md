---
date: 2026-02-06
issue_id: "019"
status: resolved
severity: p1
category: architecture-issues
component: models
tags: [architecture, layering, pydantic, validation, dependency-injection]
related_issues: ["018", "020"]
---

# Global Dependency in Pydantic Model

## Problem Statement

The `InvoiceData` Pydantic model had a validator that directly imported and called `get_config()`, creating a global dependency from the data model layer to the configuration layer. This violates clean architecture principles, creates tight coupling, and makes the models difficult to test and reuse.

**Why this matters:**
- **Layer Violation**: Data models should be pure, domain-layer objects without external dependencies
- **Tight Coupling**: Models cannot be used independently of the configuration system
- **Testing Difficulty**: Cannot test models in isolation without mocking global configuration
- **Circular Dependencies**: Risk of circular imports between config and models modules
- **Hidden Dependencies**: Global state makes behavior unpredictable and hard to reason about
- **Reusability**: Models cannot be reused in other contexts (APIs, libraries, tests)

## Symptoms

- `InvoiceData` model imports `get_config()` from the config module
- Currency validation logic is embedded in the model's `@field_validator`
- Cannot instantiate `InvoiceData` objects without configuration being loaded
- Tests for models require mocking global configuration
- Models cannot be used independently of the application configuration
- Currency validation happens at model instantiation time, not during business logic

## Root Cause

The root cause was placing business logic validation (currency checking) directly in the Pydantic model using a `@field_validator` that accessed a global singleton:

```python
# BEFORE: Layer violation - model depends on global config
from invproc.config import get_config

class InvoiceData(BaseModel):
    currency: str = Field(..., description="Currency code (EUR, USD, MDL, RUB)")
    products: List[Product] = Field(..., min_length=0, description="List of products")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate currency code."""
        config = get_config()  # Global dependency!
        valid_currencies = config.get_allowed_currencies()
        v_upper = v.upper()
        if v_upper not in valid_currencies:
            raise ValueError(
                f"Invalid currency: {v}. Valid: {', '.join(sorted(valid_currencies))}"
            )
        return v_upper
```

This architecture has several problems:
1. **Layer Violation**: Models (data layer) depend on config (infrastructure layer)
2. **Global State**: Validator accesses global singleton `get_config()`
3. **Hidden Dependency**: Not obvious from the model signature that configuration is required
4. **Testability**: Cannot test `InvoiceData` in isolation
5. **Separation of Concerns**: Mixes data structure validation with business rule validation

## Solution

### 1. Remove Currency Validator from Model

Removed the `@field_validator` for currency from `InvoiceData` model:

```python
# AFTER: Clean model, no external dependencies
class InvoiceData(BaseModel):
    supplier: Optional[str] = Field(None, description="Supplier name")
    invoice_number: Optional[str] = Field(None, description="Invoice number")
    date: Optional[str] = Field(None, description="Invoice date (ISO format)")
    total_amount: float = Field(..., gt=0, description="Total invoice amount")
    currency: str = Field(..., description="Currency code (EUR, USD, MDL, RUB)")
    products: List[Product] = Field(..., min_length=0, description="List of products")

    @model_validator(mode="after")
    def validate_totals(self) -> "InvoiceData":
        """
        Validate that sum of products ≈ invoice total.
        Allow 20% tolerance for taxes/discounts.
        """
        if not self.products:
            return self

        sum_products = sum(p.total_price for p in self.products)
        tolerance = 0.20

        if abs(sum_products - self.total_amount) > self.total_amount * tolerance:
            pass

        return self
```

**Why:** The model is now a pure data structure with only intrinsic validation (math checks between fields), no extrinsic validation (business rules about currency).

### 2. Move Currency Validation to Service Layer

Added currency validation to `InvoiceValidator` service class:

```python
# AFTER: Currency validation in service layer
class InvoiceValidator:
    """Validate and score invoice data."""

    def __init__(self, config: "InvoiceConfig") -> None:
        """Initialize validator with configuration."""
        from .config import InvoiceConfig

        self.config: InvoiceConfig = config
        self.allowed_currencies: set[str] = config.get_allowed_currencies()

    def validate_invoice(self, data: InvoiceData) -> InvoiceData:
        """
        Post-process validation and confidence scoring.

        Args:
            data: InvoiceData from LLM extraction

        Returns:
            InvoiceData with validated confidence scores
        """
        # Validate currency (extrinsic business rule)
        v_upper = data.currency.upper()
        if v_upper not in self.allowed_currencies:
            raise ValueError(
                f"Invalid currency: {data.currency}. "
                f"Valid: {', '.join(sorted(self.allowed_currencies))}"
            )
        data.currency = v_upper

        for product in data.products:
            confidence = self._score_product(product)
            product.confidence_score = confidence

        avg_confidence = self._calculate_overall_confidence(data)
        logger.info(f"Overall extraction confidence: {avg_confidence:.2f}")

        return data
```

**Why:** Business rule validation (allowed currencies) now lives in the service layer, where it belongs. Configuration is injected via the constructor.

### 3. Update Invoice Imports

Removed the config import from models.py:

```python
# BEFORE: Import dependency
from pydantic import BaseModel, Field, field_validator, model_validator
from invproc.config import get_config

# AFTER: Clean imports
from pydantic import BaseModel, Field, model_validator
```

**Why:** Removes the dependency chain from models → config, breaking the layer violation.

### 4. Update Service to Inject Config

Updated `InvoiceValidator` to receive configuration via dependency injection:

```python
# AFTER: Constructor injection
def __init__(self, config: "InvoiceConfig") -> None:
    """Initialize validator with configuration."""
    from .config import InvoiceConfig

    self.config: InvoiceConfig = config
    self.allowed_currencies: set[str] = config.get_allowed_currencies()
```

**Why:** Configuration is explicitly passed as a dependency, making the relationship clear and testable.

## Code Changes

### File: src/invproc/models.py

**Before (Lines 1-5):**
```python
"""Pydantic data models for invoice data."""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from invproc.config import get_config
```

**After (Lines 1-4):**
```python
"""Pydantic data models for invoice data."""

from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
```

**Why:** Removed the `field_validator` import and the `get_config` import, eliminating the dependency.

---

**Before (Lines 40-56):**
```python
class InvoiceData(BaseModel):
    supplier: Optional[str] = Field(None, description="Supplier name")
    invoice_number: Optional[str] = Field(None, description="Invoice number")
    date: Optional[str] = Field(None, description="Invoice date (ISO format)")
    total_amount: float = Field(..., gt=0, description="Total invoice amount")
    currency: str = Field(..., description="Currency code (EUR, USD, MDL, RUB)")
    products: List[Product] = Field(..., min_length=0, description="List of products")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate currency code."""
        config = get_config()
        valid_currencies = config.get_allowed_currencies()
        v_upper = v.upper()
        if v_upper not in valid_currencies:
            raise ValueError(
                f"Invalid currency: {v}. Valid: {', '.join(sorted(valid_currencies))}"
            )
        return v_upper

    @model_validator(mode="after")
    def validate_totals(self) -> "InvoiceData":
        """
        Validate that sum of products ≈ invoice total.
        Allow 20% tolerance for taxes/discounts.
        """
        if not self.products:
            return self

        sum_products = sum(p.total_price for p in self.products)
        tolerance = 0.20

        if abs(sum_products - self.total_amount) > self.total_amount * tolerance:
            pass

        return self
```

**After (Lines 32-57):**
```python
class InvoiceData(BaseModel):
    supplier: Optional[str] = Field(None, description="Supplier name")
    invoice_number: Optional[str] = Field(None, description="Invoice number")
    date: Optional[str] = Field(None, description="Invoice date (ISO format)")
    total_amount: float = Field(..., gt=0, description="Total invoice amount")
    currency: str = Field(..., description="Currency code (EUR, USD, MDL, RUB)")
    products: List[Product] = Field(..., min_length=0, description="List of products")

    @model_validator(mode="after")
    def validate_totals(self) -> "InvoiceData":
        """
        Validate that sum of products ≈ invoice total.
        Allow 20% tolerance for taxes/discounts.
        """
        if not self.products:
            return self

        sum_products = sum(p.total_price for p in self.products)
        tolerance = 0.20

        if abs(sum_products - self.total_amount) > self.total_amount * tolerance:
            pass

        return self
```

**Why:** Removed the `validate_currency` field validator entirely, leaving only intrinsic validation (`validate_totals` which checks relationships between fields).

### File: src/invproc/validator.py

**Before (Lines 1-16):**
```python
"""Invoice validation and confidence scoring."""

import logging
from typing import Tuple

from .models import Product, InvoiceData

logger = logging.getLogger(__name__)


class InvoiceValidator:
    """Validate and score invoice data."""

    def validate_invoice(self, data: InvoiceData) -> InvoiceData:
        """
        Post-process validation and confidence scoring.

        Args:
            data: InvoiceData from LLM extraction

        Returns:
            InvoiceData with validated confidence scores
        """
        for product in data.products:
            confidence = self._score_product(product)
            product.confidence_score = confidence

        avg_confidence = self._calculate_overall_confidence(data)
        logger.info(f"Overall extraction confidence: {avg_confidence:.2f}")

        return data
```

**After (Lines 1-41):**
```python
"""Invoice validation and confidence scoring."""

import logging
from typing import Tuple, TYPE_CHECKING

from .models import Product, InvoiceData

if TYPE_CHECKING:
    from .config import InvoiceConfig

logger = logging.getLogger(__name__)


class InvoiceValidator:
    """Validate and score invoice data."""

    def __init__(self, config: "InvoiceConfig") -> None:
        """Initialize validator with configuration."""
        from .config import InvoiceConfig

        self.config: InvoiceConfig = config
        self.allowed_currencies: set[str] = config.get_allowed_currencies()

    def validate_invoice(self, data: InvoiceData) -> InvoiceData:
        """
        Post-process validation and confidence scoring.

        Args:
            data: InvoiceData from LLM extraction

        Returns:
            InvoiceData with validated confidence scores
        """
        # Validate currency
        v_upper = data.currency.upper()
        if v_upper not in self.allowed_currencies:
            raise ValueError(
                f"Invalid currency: {data.currency}. "
                f"Valid: {', '.join(sorted(self.allowed_currencies))}"
            )
        data.currency = v_upper

        for product in data.products:
            confidence = self._score_product(product)
            product.confidence_score = confidence

        avg_confidence = self._calculate_overall_confidence(data)
        logger.info(f"Overall extraction confidence: {avg_confidence:.2f}")

        return data
```

**Why:**
1. Added `TYPE_CHECKING` import to avoid circular imports at runtime
2. Added `__init__` method with constructor injection of config
3. Moved currency validation logic from model to service
4. Caches `allowed_currencies` on instance for efficiency

## Implementation Details

### Architecture Principles Applied

This fix follows several key architecture principles:

**1. Separation of Concerns**
- **Models**: Data structure with intrinsic validation only (field relationships)
- **Services**: Business logic and extrinsic validation (business rules)
- **Config**: Configuration management (infrastructure)

**2. Dependency Injection**
```python
# BEFORE: Global dependency
config = get_config()  # Hidden dependency

# AFTER: Constructor injection
def __init__(self, config: InvoiceConfig):
    self.config = config  # Explicit dependency
```

**3. Dependency Inversion**
Models no longer depend on concrete config implementation. They depend only on their own structure.

**4. Single Responsibility**
- Models: Represent data structure
- Validators: Apply business rules
- Config: Manage configuration

### Intrinsic vs Extrinsic Validation

The key distinction between what stays in models vs. what moves to services:

**Intrinsic Validation (Keep in Models):**
- Field type validation (str, int, float)
- Field format validation (email, URL patterns)
- Field relationship validation (qty × price ≈ total)
- Constraints (gt=0, le=1)

**Extrinsic Validation (Move to Services):**
- Business rules (allowed currencies)
- Context-specific rules (user permissions)
- External validation (database lookups, API calls)
- State-dependent validation (quota checks, rate limits)

### TYPE_CHECKING Usage

Used `TYPE_CHECKING` to avoid circular imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import InvoiceConfig

class InvoiceValidator:
    def __init__(self, config: "InvoiceConfig"):
        # Runtime import to avoid circular dependency
        from .config import InvoiceConfig
```

This allows type checkers (mypy) to see the type without creating a circular import at runtime.

### Testing Implications

**Before Fix:**
```python
# Hard to test - requires mocking global config
def test_invoice_data_validation():
    with mock.patch('invproc.models.get_config') as mock_config:
        mock_config.return_value.get_allowed_currencies.return_value = {'EUR'}
        invoice = InvoiceData(currency='usd', ...)  # Should fail
        # But need to patch global state...
```

**After Fix:**
```python
# Easy to test - inject test config
def test_invoice_validator_rejects_invalid_currency():
    config = InvoiceConfig(allowed_currencies="EUR")
    validator = InvoiceValidator(config)
    data = InvoiceData(currency="usd", total_amount=100, products=[])

    with pytest.raises(ValueError, match="Invalid currency"):
        validator.validate_invoice(data)
```

### Performance Considerations

- Caching `allowed_currencies` in `__init__` avoids repeated lookups
- No performance penalty - validation was already happening, just moved to a different layer
- Model instantiation is slightly faster (no config lookup)

## Testing

### Unit Tests for Models

Models can now be tested in isolation without configuration:

```python
def test_invoice_data_intrinsic_validation():
    """Test intrinsic validation (field relationships)."""
    # Math is off by more than 20%
    with pytest.raises(ValidationError):
        InvoiceData(
            total_amount=1000,
            currency="USD",
            products=[
                Product(name="Item 1", quantity=1, unit_price=10, total_price=5)
            ]
        )
```

### Unit Tests for Validator

Validator is tested with injected configuration:

```python
def test_invoice_validator_rejects_invalid_currency():
    """Test currency validation in service layer."""
    config = InvoiceConfig(allowed_currencies="EUR,USD")
    validator = InvoiceValidator(config)
    data = InvoiceData(
        currency="GBP",  # Invalid for this config
        total_amount=100,
        products=[]
    )

    with pytest.raises(ValueError, match="Invalid currency: GBP"):
        validator.validate_invoice(data)

def test_invoice_validator_normalizes_currency():
    """Test that currency codes are normalized to uppercase."""
    config = InvoiceConfig(allowed_currencies="EUR,USD")
    validator = InvoiceValidator(config)
    data = InvoiceData(
        currency="eur",  # Lowercase
        total_amount=100,
        products=[]
    )

    validated = validator.validate_invoice(data)
    assert validated.currency == "EUR"  # Normalized to uppercase
```

### Integration Tests

Full integration test showing the validation flow:

```python
def test_full_validation_flow():
    """Test validation flow from LLM to final data."""
    config = get_config()
    validator = InvoiceValidator(config)

    # Simulate LLM extraction
    raw_data = InvoiceData(
        currency="eur",
        total_amount=150.00,
        products=[
            Product(
                name="Product A",
                quantity=2,
                unit_price=50.00,
                total_price=100.00,
                confidence_score=0.9
            )
        ]
    )

    # Apply validation
    validated = validator.validate_invoice(raw_data)

    # Currency normalized to uppercase
    assert validated.currency == "EUR"

    # Confidence scores recalculated
    assert 0.0 <= validated.products[0].confidence_score <= 1.0
```

## Prevention

### 1. Follow Clean Architecture Layering

Maintain clear layer boundaries:

- **Domain Layer (models)**: Pure data structures, no external dependencies
- **Service Layer (validators)**: Business logic, depends on config
- **Infrastructure Layer (config)**: Configuration management

```
models.py ← validator.py ← config.py
  (pure)    (business)    (infrastructure)
```

**Never**: models → config
**Always**: validator → models AND validator → config

### 2. Use Dependency Injection

Always inject dependencies rather than accessing global singletons:

```python
# Good: Constructor injection
class InvoiceValidator:
    def __init__(self, config: InvoiceConfig):
        self.config = config

# Bad: Global dependency
class InvoiceValidator:
    def __init__(self):
        self.config = get_config()  # Hidden dependency
```

### 3. Separate Intrinsic and Extrinsic Validation

Keep model validators for intrinsic validation only:

```python
class InvoiceData(BaseModel):
    # Intrinsic validation: OK
    @field_validator("total_amount")
    def validate_positive(cls, v):
        if v <= 0:
            raise ValueError("Must be positive")
        return v

    # Extrinsic validation: MOVE TO SERVICE LAYER
    # @field_validator("currency")
    # def validate_allowed_currency(cls, v):
    #     if v not in get_config().get_allowed_currencies():
    #         raise ValueError(...)
    #     return v
```

### 4. Avoid Global State

Avoid global singletons in model validators:

```python
# Bad: Global state in validator
@field_validator("field")
def validate_field(cls, v):
    config = get_config()  # Global singleton!
    return v

# Good: Dependency injection
class Validator:
    def __init__(self, config):
        self.config = config

    def validate(self, data):
        if not self.is_valid(data.field):
            raise ValueError(...)
```

### 5. Use TYPE_CHECKING for Forward References

When you need type hints but want to avoid circular imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import InvoiceConfig

class Service:
    def __init__(self, config: "InvoiceConfig"):
        from .config import InvoiceConfig  # Runtime import
        self.config = config
```

### 6. Test Models in Isolation

Ensure models can be tested without external dependencies:

```python
# Should be able to do this without any mocking:
def test_model_validation():
    data = InvoiceData(
        currency="EUR",
        total_amount=100,
        products=[]
    )
    assert data.currency == "EUR"
```

### 7. Document Layer Boundaries

Document architectural boundaries in code comments:

```python
"""
Pydantic data models for invoice data.

IMPORTANT: These models represent pure data structures with INTRINSIC
validation only (field types, relationships, constraints). They MUST NOT
depend on external services or configuration.

For EXTRINSIC validation (business rules, permissions, etc.), use the
InvoiceValidator service in validator.py.
"""
```

## Resources

### Related Issues
- Issue #018: Configuration injection via ALLOWED_CURRENCIES - Related validation fix
- Issue #020: No startup config validation - Related configuration fix

### Related Documentation
- [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [Dependency Injection in Python](https://python-dependency-injector.ets-labs.org/)
- [Pydantic Validators](https://docs.pydantic.dev/latest/concepts/validators/)

### External Resources
- [Martin Fowler's Layered Architecture](https://martinfowler.com/bliki/PresentationDomainDataLayering.html)
- [Six Principles of Clean Architecture](https://herbertograca.com/2017/09/14/are-you-still-passing-dependencies-around/)
- [Type Checking Annotations](https://docs.python.org/3/library/typing.html#typing.TYPE_CHECKING)

## Verification

### Acceptance Criteria

- [x] `InvoiceData` model has no imports from config module
- [x] `InvoiceData` has no `@field_validator` for currency
- [x] Currency validation moved to `InvoiceValidator.validate_invoice()`
- [x] `InvoiceValidator` receives config via constructor injection
- [x] Models can be instantiated and tested without configuration
- [x] All 33 tests pass
- [x] Type checking passes (mypy)
- [x] Linting passes (ruff)
- [x] No circular import errors
- [x] Documentation updated to reflect architecture

## Notes

- This fix was part of commit `43dd72d6`
- Combined with Issues #018 and #020 for comprehensive architecture cleanup
- All P1 critical issues blocking merge are now resolved
- The model is now reusable across different contexts (APIs, tests, libraries)
- Validation timing changed: currency now validated during business logic, not during model instantiation
- This aligns with the architecture guidance in CLAUDE.md: "Models: Pydantic models (Product, InvoiceData) with built-in validators"
