"""Row-level normalization and enrichment helpers."""

import hashlib

from invproc.models import InvoiceData
from invproc.weight_parser import parse_weight_candidate


def add_row_metadata(invoice_data: InvoiceData) -> None:
    """Populate extracted rows with stable IDs and weight candidates."""
    for idx, product in enumerate(invoice_data.products):
        raw = (
            f"{idx}|{product.raw_code or ''}|{product.name}|"
            f"{product.quantity}|{product.total_price}"
        )
        row_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        product.row_id = f"r_{row_hash}"

        if (product.uom or "").strip().upper() == "KG" and product.weight_kg_candidate:
            product.size_token = None
            product.parse_confidence = None
            continue

        parsed = parse_weight_candidate(product.name)
        product.weight_kg_candidate = parsed.weight_kg
        product.size_token = parsed.size_token
        product.parse_confidence = parsed.parse_confidence


def normalize_kg_weighed_rows(invoice_data: InvoiceData) -> None:
    """
    Normalize weighed KG rows to match invoice-import semantics.

    For rows where `uom == "KG"`:
    - Treat the extracted `quantity` as measured weight in kilograms (from "Cant.")
    - Store that weight in `weight_kg_candidate`
    - Rewrite `quantity` to 1 (one weighed item / line)
    - Rewrite `unit_price` to `total_price` (VAT-inclusive end price per weighed item)
    """
    for product in invoice_data.products:
        if (product.uom or "").strip().upper() != "KG":
            continue

        measured_weight_kg = product.quantity
        product.weight_kg_candidate = measured_weight_kg
        product.quantity = 1.0
        product.unit_price = product.total_price
        product.size_token = None
        product.parse_confidence = None
