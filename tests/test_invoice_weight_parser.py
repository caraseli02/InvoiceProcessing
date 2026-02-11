"""Tests for invoice weight parser."""

from invproc.weight_parser import parse_weight_candidate


def test_parse_grams_token() -> None:
    parsed = parse_weight_candidate("200G UNT CIOCOLATA JLC")
    assert parsed.weight_kg == 0.2
    assert parsed.size_token == "200G"


def test_parse_liters_with_comma_decimal() -> None:
    parsed = parse_weight_candidate("Lapte 0,5L premium")
    assert parsed.weight_kg == 0.5
    assert parsed.size_token == "0,5L".upper()


def test_parse_ml() -> None:
    parsed = parse_weight_candidate("Suc 750ml")
    assert parsed.weight_kg == 0.75
    assert parsed.size_token == "750ML"


def test_parse_multipack_grams() -> None:
    parsed = parse_weight_candidate("24X2G CEAI LOVARE 1001 NOCI")
    assert parsed.weight_kg == 0.048
    assert parsed.size_token == "24X2G"


def test_parse_multipack_liters_with_comma() -> None:
    parsed = parse_weight_candidate("6x0,5L Apa minerala")
    assert parsed.weight_kg == 3.0
    assert parsed.size_token == "6X0,5L"


def test_parse_missing_token() -> None:
    parsed = parse_weight_candidate("Produs fara marime")
    assert parsed.weight_kg is None
    assert parsed.size_token is None
