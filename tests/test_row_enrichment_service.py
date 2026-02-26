"""Unit tests for row enrichment service helpers."""

from invproc.models import InvoiceData, Product
from invproc.services.row_enrichment import add_row_metadata, normalize_kg_weighed_rows


def test_normalize_kg_weighed_rows_rewrites_kg_rows() -> None:
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
                quantity=0.878,
                unit_price=149.92,
                total_price=150.04,
                confidence_score=0.9,
            )
        ],
    )

    normalize_kg_weighed_rows(invoice)

    product = invoice.products[0]
    assert product.weight_kg_candidate == 0.878
    assert product.quantity == 1.0
    assert product.unit_price == 150.04
    assert product.size_token is None
    assert product.parse_confidence is None


def test_add_row_metadata_prefers_existing_kg_weight_candidate() -> None:
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
                quantity=1.0,
                unit_price=150.04,
                total_price=150.04,
                confidence_score=0.9,
                weight_kg_candidate=0.878,
            )
        ],
    )

    add_row_metadata(invoice)

    product = invoice.products[0]
    assert product.row_id is not None
    assert product.weight_kg_candidate == 0.878
    assert product.size_token is None
    assert product.parse_confidence is None


def test_add_row_metadata_parses_non_kg_weight_candidate() -> None:
    invoice = InvoiceData(
        supplier="Y",
        invoice_number="2",
        date="24-02-2026",
        total_amount=12.0,
        currency="MDL",
        products=[
            Product(
                raw_code=None,
                name="Branza 500g",
                uom="BU",
                quantity=2.0,
                unit_price=6.0,
                total_price=12.0,
                confidence_score=0.9,
            )
        ],
    )

    add_row_metadata(invoice)

    product = invoice.products[0]
    assert product.row_id is not None
    assert product.weight_kg_candidate == 0.5
    assert product.size_token == "500G"
