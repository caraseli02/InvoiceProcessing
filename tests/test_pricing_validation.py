"""Extra pricing and contract error tests for strict quality-gate coverage."""

import math

import pytest

from invproc.exceptions import ContractError
from invproc.pricing import compute_pricing


def test_contract_error_defaults():
    """ContractError should retain structured API payload fields."""
    err = ContractError(code="bad_input", message="Invalid payload")
    assert err.code == "bad_input"
    assert err.message == "Invalid payload"
    assert err.status_code == 400
    assert err.details == {}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"line_total_lei": math.inf}, "line_total_lei must be finite"),
        ({"quantity": 0}, "quantity must be positive"),
        ({"line_total_lei": -1}, "line_total_lei cannot be negative"),
        ({"weight_kg": 0}, "weight_kg must be positive"),
        ({"fx_lei_to_eur": 0}, "fx_lei_to_eur must be positive"),
        ({"transport_rate_per_kg": 0}, "transport_rate_per_kg must be positive"),
    ],
)
def test_compute_pricing_validation_errors(kwargs, message):
    """compute_pricing should reject invalid numeric inputs with clear errors."""
    base_kwargs = {
        "line_total_lei": 100.0,
        "quantity": 2.0,
        "weight_kg": 1.0,
        "fx_lei_to_eur": 5.0,
        "transport_rate_per_kg": 1.5,
    }
    base_kwargs.update(kwargs)

    with pytest.raises(ValueError, match=message):
        compute_pricing(**base_kwargs)
