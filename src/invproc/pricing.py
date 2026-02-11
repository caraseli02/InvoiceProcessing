"""Pricing formulas for invoice import parity."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PricingResult:
    """Computed pricing for a single invoice row."""

    base_price_eur: float
    transport_eur: float
    price_50: float
    price_70: float
    price_100: float


def _round4(value: float) -> float:
    return round(value, 4)


def compute_pricing(
    *,
    line_total_lei: float,
    quantity: float,
    weight_kg: float,
    fx_lei_to_eur: float,
    transport_rate_per_kg: float,
) -> PricingResult:
    """Compute Excel-parity pricing values with fixed constants."""
    for value, name in (
        (line_total_lei, "line_total_lei"),
        (quantity, "quantity"),
        (weight_kg, "weight_kg"),
        (fx_lei_to_eur, "fx_lei_to_eur"),
        (transport_rate_per_kg, "transport_rate_per_kg"),
    ):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if line_total_lei < 0:
        raise ValueError("line_total_lei cannot be negative")
    if weight_kg <= 0:
        raise ValueError("weight_kg must be positive")
    if fx_lei_to_eur <= 0:
        raise ValueError("fx_lei_to_eur must be positive")
    if transport_rate_per_kg <= 0:
        raise ValueError("transport_rate_per_kg must be positive")

    base = (line_total_lei / quantity) / fx_lei_to_eur
    transport = weight_kg * transport_rate_per_kg
    landed = base + transport

    return PricingResult(
        base_price_eur=_round4(base),
        transport_eur=_round4(transport),
        price_50=_round4(landed * 1.5),
        price_70=_round4(landed * 1.7),
        price_100=_round4(landed * 2.0),
    )
