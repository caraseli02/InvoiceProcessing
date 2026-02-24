"""UOM normalization tests."""

from invproc.config import InvoiceConfig
from invproc.llm_extractor import LLMExtractor


def test_normalize_coerces_uom_to_uppercase_or_null() -> None:
    extractor = LLMExtractor(InvoiceConfig(mock=True))

    payload = {
        "supplier": None,
        "invoice_number": None,
        "date": None,
        "total_amount": 10.0,
        "currency": "MDL",
        "products": [
            {
                "raw_code": None,
                "name": "SOMETHING",
                "uom": "kg",
                "category_suggestion": None,
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            },
            {
                "raw_code": None,
                "name": "SOMETHING",
                "uom": "   ",
                "category_suggestion": None,
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            },
        ],
    }

    normalized = extractor._normalize_invoice_payload(payload)

    assert normalized["products"][0]["uom"] == "KG"
    assert normalized["products"][1]["uom"] is None

