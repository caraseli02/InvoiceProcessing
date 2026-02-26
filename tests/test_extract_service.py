"""Unit tests for extract service orchestration."""

from pathlib import Path

from types import SimpleNamespace
from unittest.mock import patch
from invproc.extract_cache import InMemoryExtractCache
from invproc.models import InvoiceData, Product
from invproc.services.extract_service import (
    build_extract_cache_key,
    build_extract_cache_signature,
    run_extract_pipeline,
)


class StubPDFProcessor:
    def extract_content(self, _pdf_path: Path):
        return "grid", {"source": "stub"}


class StubLLMExtractor:
    def __init__(self, invoice: InvoiceData):
        self.invoice = invoice
        self.calls = 0

    def parse_with_llm(self, _text_grid: str) -> InvoiceData:
        self.calls += 1
        return self.invoice.model_copy(deep=True)


class StubValidator:
    def validate_invoice(self, invoice: InvoiceData) -> InvoiceData:
        return invoice


def _sample_invoice() -> InvoiceData:
    return InvoiceData(
        supplier="X",
        invoice_number="1",
        date="24-02-2026",
        total_amount=10,
        currency="MDL",
        products=[
            Product(
                raw_code=None,
                name="Paine alba 500g",
                uom="BU",
                quantity=1,
                unit_price=10,
                total_price=10,
                confidence_score=0.9,
            )
        ],
    )


def test_extract_pipeline_returns_cache_off_when_disabled() -> None:
    config = SimpleNamespace(
        model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=4096,
        scale_factor=0.2,
        tolerance=3,
        ocr_dpi=150,
        ocr_languages="ron+eng+rus",
        ocr_config="--oem 1 --psm 6",
        column_headers=SimpleNamespace(model_dump=lambda mode: {}),
        mock=True,
        extract_cache_enabled=False,
        extract_cache_ttl_sec=60,
        extract_cache_max_entries=10,
    )
    llm = StubLLMExtractor(_sample_invoice())

    result = run_extract_pipeline(
        config=config,
        pdf_path=Path("invoice.pdf"),
        file_hash="hash",
        pdf_processor=StubPDFProcessor(),
        llm_extractor=llm,
        validator=StubValidator(),
        cache=InMemoryExtractCache(ttl_sec=60, max_entries=10),
    )

    assert result.cache_status == "off"
    assert llm.calls == 1
    assert result.invoice_data.products[0].row_id is not None


def test_extract_pipeline_skips_cache_key_build_when_cache_disabled() -> None:
    config = SimpleNamespace(
        model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=4096,
        scale_factor=0.2,
        tolerance=3,
        ocr_dpi=150,
        ocr_languages="ron+eng+rus",
        ocr_config="--oem 1 --psm 6",
        column_headers=SimpleNamespace(model_dump=lambda mode: {}),
        mock=True,
        extract_cache_enabled=False,
        extract_cache_ttl_sec=60,
        extract_cache_max_entries=10,
    )
    llm = StubLLMExtractor(_sample_invoice())

    with patch(
        "invproc.services.extract_service.build_extract_cache_key",
        side_effect=AssertionError("cache key builder should not run when cache is disabled"),
    ):
        result = run_extract_pipeline(
            config=config,
            pdf_path=Path("invoice.pdf"),
            file_hash="hash",
            pdf_processor=StubPDFProcessor(),
            llm_extractor=llm,
            validator=StubValidator(),
            cache=InMemoryExtractCache(ttl_sec=60, max_entries=10),
        )

    assert result.cache_status == "off"
    assert llm.calls == 1


def test_extract_pipeline_uses_cache_on_second_call() -> None:
    config = SimpleNamespace(
        model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=4096,
        scale_factor=0.2,
        tolerance=3,
        ocr_dpi=150,
        ocr_languages="ron+eng+rus",
        ocr_config="--oem 1 --psm 6",
        column_headers=SimpleNamespace(model_dump=lambda mode: {}),
        mock=True,
        extract_cache_enabled=True,
        extract_cache_ttl_sec=60,
        extract_cache_max_entries=10,
    )
    cache = InMemoryExtractCache(ttl_sec=60, max_entries=10)
    llm = StubLLMExtractor(_sample_invoice())
    kwargs = {
        "config": config,
        "pdf_path": Path("invoice.pdf"),
        "file_hash": "same-file-hash",
        "pdf_processor": StubPDFProcessor(),
        "llm_extractor": llm,
        "validator": StubValidator(),
        "cache": cache,
    }

    first = run_extract_pipeline(**kwargs)
    second = run_extract_pipeline(**kwargs)

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert llm.calls == 1


def test_extract_cache_signature_changes_with_config() -> None:
    base = dict(
        temperature=0.0,
        max_tokens=4096,
        scale_factor=0.2,
        tolerance=3,
        ocr_dpi=150,
        ocr_languages="ron+eng+rus",
        ocr_config="--oem 1 --psm 6",
        column_headers=SimpleNamespace(model_dump=lambda mode: {}),
        mock=True,
        extract_cache_enabled=True,
        extract_cache_ttl_sec=60,
        extract_cache_max_entries=10,
    )
    config_a = SimpleNamespace(model="gpt-4o-mini", **base)
    config_b = SimpleNamespace(model="gpt-4o", **base)

    assert build_extract_cache_signature(config_a) != build_extract_cache_signature(config_b)
    assert build_extract_cache_key(config_a, "abc").startswith("abc:")
