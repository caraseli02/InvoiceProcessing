"""Category suggestion normalization tests."""

from invproc.config import InvoiceConfig
from invproc.llm_extractor import LLMExtractor


def test_normalize_preserves_valid_category_suggestion() -> None:
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
                "name": "LAPTE 1L",
                "category_suggestion": "Dairy",
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            }
        ],
    }

    normalized = extractor._normalize_invoice_payload(payload)
    assert normalized["products"][0]["category_suggestion"] == "Dairy"


def test_normalize_coerces_unknown_category_to_null() -> None:
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
                "category_suggestion": "general",  # wrong case
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            },
            {
                "raw_code": None,
                "name": "SOMETHING",
                "category_suggestion": "Unknown",
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            },
            {
                "raw_code": None,
                "name": "SOMETHING",
                "category_suggestion": 123,
                "quantity": 1,
                "unit_price": 10,
                "total_price": 10,
                "confidence_score": 0.9,
            },
        ],
    }

    normalized = extractor._normalize_invoice_payload(payload)

    assert normalized["products"][0]["category_suggestion"] is None
    assert normalized["products"][1]["category_suggestion"] is None
    assert normalized["products"][2]["category_suggestion"] is None
