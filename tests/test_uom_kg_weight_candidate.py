"""Tests for KG-mode weight candidate behavior."""

from invproc.models import InvoiceData, Product
from invproc.services.row_enrichment import add_row_metadata, normalize_kg_weighed_rows


def test_add_row_metadata_prefers_cant_weight_for_kg_uom() -> None:
    invoice = InvoiceData(
        supplier="X",
        invoice_number="1",
        date="24-02-2026",
        total_amount=150.04,
        currency="MDL",
        products=[
            Product(
                raw_code="2843670008789",
                name="SUNCA DE VITA ROGOB 1 KG",
                uom="KG",
                quantity=0.878,  # Cant. for KG rows is measured weight
                unit_price=149.92,
                total_price=150.04,
                confidence_score=0.9,
            )
        ],
    )

    normalize_kg_weighed_rows(invoice)
    add_row_metadata(invoice)

    product = invoice.products[0]
    assert product.weight_kg_candidate == 0.878
    assert product.quantity == 1.0
    assert product.unit_price == 150.04
    assert product.size_token is None
    assert product.parse_confidence is None
