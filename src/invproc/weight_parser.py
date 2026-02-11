"""Parse product size tokens into weight in kilograms."""

import math
import re
from dataclasses import dataclass
from typing import Optional


_WEIGHT_PATTERN = re.compile(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)\b", re.IGNORECASE)
_MULTIPACK_PATTERN = re.compile(
    r"(?<!\w)(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WeightParseResult:
    """Parsed weight details from a product name."""

    weight_kg: Optional[float]
    size_token: Optional[str]
    parse_confidence: Optional[float]


def parse_weight_candidate(product_name: str) -> WeightParseResult:
    """Parse the first supported size token and convert to kilograms."""
    multipack_match = _MULTIPACK_PATTERN.search(product_name)
    if multipack_match:
        packs_raw = multipack_match.group(1).replace(",", ".")
        unit_raw = multipack_match.group(2).replace(",", ".")
        unit = multipack_match.group(3).lower()

        try:
            packs = float(packs_raw)
            unit_value = float(unit_raw)
        except ValueError:
            return WeightParseResult(
                weight_kg=None, size_token=None, parse_confidence=None
            )

        total_value = packs * unit_value
        if not math.isfinite(total_value) or total_value <= 0:
            return WeightParseResult(
                weight_kg=None, size_token=None, parse_confidence=None
            )

        if unit == "kg":
            weight_kg = total_value
        elif unit == "g":
            weight_kg = total_value / 1000.0
        elif unit == "l":
            weight_kg = total_value
        else:  # ml
            weight_kg = total_value / 1000.0

        return WeightParseResult(
            weight_kg=weight_kg,
            size_token=multipack_match.group(0).upper().replace(" ", ""),
            parse_confidence=0.98,
        )

    match = _WEIGHT_PATTERN.search(product_name)
    if not match:
        return WeightParseResult(weight_kg=None, size_token=None, parse_confidence=None)

    raw_value = match.group(1).replace(",", ".")
    unit = match.group(2).lower()

    try:
        value = float(raw_value)
    except ValueError:
        return WeightParseResult(weight_kg=None, size_token=None, parse_confidence=None)

    if not math.isfinite(value) or value <= 0:
        return WeightParseResult(weight_kg=None, size_token=None, parse_confidence=None)

    if unit == "kg":
        weight_kg = value
    elif unit == "g":
        weight_kg = value / 1000.0
    elif unit == "l":
        weight_kg = value
    else:  # ml
        weight_kg = value / 1000.0

    return WeightParseResult(
        weight_kg=weight_kg,
        size_token=match.group(0).upper().replace(" ", ""),
        parse_confidence=0.98,
    )
