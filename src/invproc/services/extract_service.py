"""Extraction orchestration service."""

import hashlib
import json
from dataclasses import dataclass
import logging
from pathlib import Path

from typing import TYPE_CHECKING

from invproc.extract_cache import InMemoryExtractCache
from invproc.models import InvoiceData
from invproc.services.row_enrichment import add_row_metadata, normalize_kg_weighed_rows

if TYPE_CHECKING:
    from invproc.config import InvoiceConfig
    from invproc.llm_extractor import LLMExtractor
    from invproc.pdf_processor import PDFProcessor
    from invproc.validator import InvoiceValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractResult:
    """Container for extraction output and cache observability state."""

    invoice_data: InvoiceData
    cache_status: str


def build_extract_cache_signature(config: "InvoiceConfig") -> str:
    """Build a stable signature for extraction-affecting config fields."""
    payload = {
        "schema_version": 1,
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "scale_factor": config.scale_factor,
        "tolerance": config.tolerance,
        "ocr_dpi": config.ocr_dpi,
        "ocr_languages": config.ocr_languages,
        "ocr_config": config.ocr_config,
        "column_headers": config.column_headers.model_dump(mode="json"),
        "mock": config.mock,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_extract_cache_key(config: "InvoiceConfig", file_hash: str) -> str:
    """Build extract cache key for file bytes + effective extraction config."""
    return f"{file_hash}:{build_extract_cache_signature(config)}"


def run_extract_pipeline(
    *,
    config: "InvoiceConfig",
    pdf_path: Path,
    file_hash: str,
    pdf_processor: "PDFProcessor",
    llm_extractor: "LLMExtractor",
    validator: "InvoiceValidator",
    cache: InMemoryExtractCache,
) -> ExtractResult:
    """Execute extraction pipeline with optional cache lookup + write-through."""
    cache_key: str | None = None
    if config.extract_cache_enabled:
        cache_key = build_extract_cache_key(config, file_hash)
        cache.configure(
            ttl_sec=config.extract_cache_ttl_sec,
            max_entries=config.extract_cache_max_entries,
        )
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            logger.info("extract cache hit: file_hash=%s", file_hash[:12])
            return ExtractResult(
                invoice_data=InvoiceData(**cached_payload),
                cache_status="hit",
            )
        logger.info("extract cache miss: file_hash=%s", file_hash[:12])
        cache_status = "miss"
    else:
        cache_status = "off"

    text_grid, _metadata = pdf_processor.extract_content(pdf_path)
    invoice_data = llm_extractor.parse_with_llm(text_grid)
    normalize_kg_weighed_rows(invoice_data)
    validated_invoice = validator.validate_invoice(invoice_data)
    add_row_metadata(validated_invoice)

    if config.extract_cache_enabled:
        assert cache_key is not None
        cache.set(cache_key, validated_invoice.model_dump(mode="json"))

    return ExtractResult(invoice_data=validated_invoice, cache_status=cache_status)
