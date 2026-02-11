"""Tests for pricing parity formulas."""

from invproc.pricing import compute_pricing


def test_compute_pricing_parity_values() -> None:
    pricing = compute_pricing(
        line_total_lei=200.0,
        quantity=10.0,
        weight_kg=0.2,
        fx_lei_to_eur=19.5,
        transport_rate_per_kg=1.5,
    )

    assert pricing.base_price_eur == 1.0256
    assert pricing.transport_eur == 0.3
    assert pricing.price_50 == 1.9885
    assert pricing.price_70 == 2.2536
    assert pricing.price_100 == 2.6513


def test_compute_pricing_uses_vat_inclusive_line_total() -> None:
    """Use line total including VAT and ensure transport is added."""
    pricing = compute_pricing(
        line_total_lei=359.70,
        quantity=3.0,
        weight_kg=0.5,
        fx_lei_to_eur=19.5,
        transport_rate_per_kg=1.5,
    )

    assert pricing.base_price_eur == 6.1487
    assert pricing.transport_eur == 0.75
    assert pricing.price_70 == 11.7278
