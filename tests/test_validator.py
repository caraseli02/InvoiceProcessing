"""Tests for InvoiceValidator."""

import pytest
from invproc.config import InvoiceConfig
from invproc.validator import InvoiceValidator
from invproc.models import InvoiceData, Product

def test_validator_valid_currency():
    """Test that valid currency is accepted."""
    config = InvoiceConfig(allowed_currencies="EUR,USD")
    validator = InvoiceValidator(config)

    data = InvoiceData(
        currency="eur",  # Lowercase input
        total_amount=100.0,
        products=[Product(
            name="Product 1",
            quantity=1.0,
            unit_price=100.0,
            total_price=100.0,
            confidence_score=1.0
        )]
    )

    validated = validator.validate_invoice(data)
    assert validated.currency == "EUR"  # Normalized to uppercase

def test_validator_invalid_currency():
    """Test that invalid currency raises ValueError."""
    config = InvoiceConfig(allowed_currencies="EUR,USD")
    validator = InvoiceValidator(config)

    data = InvoiceData(
        currency="GBP",  # Not in allowed list
        total_amount=100.0,
        products=[Product(
            name="Product 1",
            quantity=1.0,
            unit_price=100.0,
            total_price=100.0,
            confidence_score=1.0
        )]
    )

    with pytest.raises(ValueError, match="Invalid currency: GBP"):
        validator.validate_invoice(data)

def test_validator_currency_case_insensitive():
    """Test that currency validation is case-insensitive."""
    config = InvoiceConfig(allowed_currencies="eur,usd")
    validator = InvoiceValidator(config)

    # Test lowercase
    data1 = InvoiceData(
        currency="eur",
        total_amount=100.0,
        products=[Product(
            name="Product 1",
            quantity=1.0,
            unit_price=100.0,
            total_price=100.0,
            confidence_score=1.0
        )]
    )
    validated1 = validator.validate_invoice(data1)
    assert validated1.currency == "EUR"

    # Test uppercase
    data2 = InvoiceData(
        currency="EUR",
        total_amount=100.0,
        products=[Product(
            name="Product 2",
            quantity=1.0,
            unit_price=100.0,
            total_price=100.0,
            confidence_score=1.0
        )]
    )
    validated2 = validator.validate_invoice(data2)
    assert validated2.currency == "EUR"

    # Test mixed case
    data3 = InvoiceData(
        currency="EuR",
        total_amount=100.0,
        products=[Product(
            name="Product 3",
            quantity=1.0,
            unit_price=100.0,
            total_price=100.0,
            confidence_score=1.0
        )]
    )
    validated3 = validator.validate_invoice(data3)
    assert validated3.currency == "EUR"


def test_validator_accepts_vat_inclusive_total_price() -> None:
    """Quantity * unit may be ex-VAT while total_price can be VAT-inclusive."""
    validator = InvoiceValidator(InvoiceConfig(allowed_currencies="EUR,MDL"))
    product = Product(
        raw_code=None,
        name="50G CEAI CATINA GHIMBIR RIOBA",
        quantity=24.0,
        unit_price=6.58,
        total_price=189.6,  # ~= 24 * 6.58 * 1.20
        confidence_score=1.0,
    )

    score = validator._score_product(product)
    assert score > 0.9


def test_validator_still_penalizes_large_mismatch() -> None:
    """Large math mismatch should still reduce confidence substantially."""
    validator = InvoiceValidator(InvoiceConfig(allowed_currencies="EUR,MDL"))
    product = Product(
        raw_code="123",
        name="Mismatch product",
        quantity=10.0,
        unit_price=10.0,
        total_price=220.0,
        confidence_score=1.0,
    )

    score = validator._score_product(product)
    assert score < 0.5
