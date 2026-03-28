"""Sync workflow for backend-owned RAG catalog processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Optional

from invproc.catalog_sync import CATALOG_EMBEDDING_TEXT_VERSION
from invproc.rag.retrieval import EmbeddingClient
from invproc.repositories.base import (
    InvoiceImportRepository,
    ProductCatalogEmbeddingRecordInput,
    ProductSyncRecord,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_TEA_TOKENS = frozenset({"ceai", "tea"})
_FRUIT_TEA_TOKENS = frozenset(
    {
        "afine",
        "capsuni",
        "fructe",
        "fruits",
        "lamaie",
        "lamai",
        "portocala",
        "zmeura",
    }
)
_HERBAL_TEA_TOKENS = frozenset({"baby", "bebe", "ghimbir", "menta", "musetel", "plante"})
_PRODUCE_TOKENS = frozenset(
    {
        "ardei",
        "castraveti",
        "cherry",
        "legume",
        "morcov",
        "morcovi",
        "rosie",
        "rosii",
        "salata",
        "spanac",
    }
)


def _normalized_tokens(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        tokens.update(match.group(0).lower() for match in _TOKEN_RE.finditer(value))
    return tokens


def _append_unique(parts: list[str], *values: str) -> None:
    seen = {part.casefold() for part in parts if part}
    for value in values:
        normalized = " ".join(value.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        parts.append(normalized)
        seen.add(key)


def infer_catalog_embedding_context(record: ProductSyncRecord) -> dict[str, Any]:
    """Infer lightweight family/category hints for better retrieval recall."""
    tokens = _normalized_tokens(record.name, record.category)
    effective_category = record.category.strip() if record.category else None
    hints: list[str] = []
    family: str | None = None
    family_variants: list[str] = []

    if tokens & _TEA_TOKENS:
        family = "tea"
        _append_unique(hints, "tea", "ceai", "bauturi")
        if effective_category is None:
            effective_category = "Beverages"
        if tokens & _FRUIT_TEA_TOKENS:
            family_variants.append("fruit_tea")
            _append_unique(hints, "ceai de fructe", "fruit tea", "fructe", "fruit")
        elif tokens & _HERBAL_TEA_TOKENS:
            family_variants.append("herbal_tea")
            _append_unique(hints, "ceai de plante", "herbal tea", "plante")

    if effective_category is None and tokens & _PRODUCE_TOKENS:
        effective_category = "Produce"
        family = family or "produce"
        _append_unique(hints, "produce", "vegetables", "legume")

    return {
        "effective_category": effective_category,
        "family": family,
        "family_variant": family_variants[0] if family_variants else None,
        "family_variants": family_variants,
        "hint_terms": hints,
    }


def build_catalog_embedding_text(
    record: ProductSyncRecord,
    *,
    category_override: str | None = None,
) -> str:
    """Assemble the canonical catalog embedding text."""
    enrichment = infer_catalog_embedding_context(record)
    category_text = category_override.strip() if category_override else ""
    if not category_text:
        category_text = record.category.strip() if record.category else ""
    if not category_text:
        category_text = enrichment["effective_category"] or ""
    hint_terms = enrichment["hint_terms"] if enrichment["family"] == "tea" else []
    parts = [
        record.name.strip(),
        record.barcode.strip() if record.barcode else "",
        category_text,
        record.uom.strip() if record.uom else "",
    ]
    _append_unique(parts, *hint_terms)
    return " ".join(part for part in parts if part)


def compute_retry_delay(attempt_number: int) -> timedelta:
    """Return bounded exponential backoff for sync retries."""
    bounded_attempt = max(1, attempt_number)
    seconds = min(300, 30 * (2 ** (bounded_attempt - 1)))
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class CatalogSyncJobResult:
    """Result of processing a single sync row."""

    status: str
    sync_id: str | None = None
    product_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class CatalogSyncStatusSnapshot:
    """Operational summary of sync row state."""

    counts: dict[str, int]
    oldest_pending_age_sec: float | None
    oldest_processing_age_sec: float | None
    repeated_failures: list[dict[str, Any]]


class CatalogSyncWorker:
    """Claim/process loop for durable catalog sync rows."""

    def __init__(
        self,
        *,
        repository: InvoiceImportRepository,
        embedding_client: EmbeddingClient,
        worker_id: str,
        lease_timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.worker_id = worker_id
        self.lease_timeout = lease_timeout

    def process_one(self, *, now: Optional[datetime] = None) -> CatalogSyncJobResult:
        current_time = now or _utcnow()
        record = self.repository.claim_next_product_sync(
            worker_id=self.worker_id,
            now=current_time,
            lease_timeout=self.lease_timeout,
        )
        if record is None:
            return CatalogSyncJobResult(status="idle")

        enrichment = infer_catalog_embedding_context(record)
        canonical_category = record.category
        if canonical_category is None and enrichment["effective_category"] is not None:
            try:
                updated_product = self.repository.backfill_product_category(
                    product_id=record.product_id,
                    category=enrichment["effective_category"],
                )
            except KeyError:
                canonical_category = enrichment["effective_category"]
            else:
                canonical_category = updated_product.category
        embedding_text = build_catalog_embedding_text(
            record,
            category_override=canonical_category,
        )
        try:
            embedding = self.embedding_client.embed(
                model=record.embedding_model,
                text=embedding_text,
            )
            self.repository.upsert_product_catalog_embedding(
                ProductCatalogEmbeddingRecordInput(
                    product_id=record.product_id,
                    product_snapshot_hash=record.product_snapshot_hash,
                    embedding_model=record.embedding_model,
                    embedding_text=embedding_text,
                    embedding=embedding,
                    metadata={
                        "name": record.name,
                        "barcode": record.barcode,
                        "category": canonical_category,
                        "effective_category": canonical_category or enrichment["effective_category"],
                        "family": enrichment["family"],
                        "family_variant": enrichment["family_variant"],
                        "family_variants": list(enrichment["family_variants"]),
                        "hint_terms": list(enrichment["hint_terms"]),
                        "embedding_text_version": CATALOG_EMBEDDING_TEXT_VERSION,
                        "uom": record.uom,
                        "supplier": record.supplier,
                        "price_eur": record.price_eur,
                        "price_50": record.price_50,
                        "price_70": record.price_70,
                        "price_100": record.price_100,
                        "markup": record.markup,
                        "invoice_number": record.invoice_number,
                    },
                )
            )
            self.repository.mark_product_sync_synced(sync_id=record.id, synced_at=current_time)
            return CatalogSyncJobResult(
                status="synced",
                sync_id=record.id,
                product_id=record.product_id,
            )
        except Exception as exc:
            next_attempt = record.attempt_count + 1
            self.repository.mark_product_sync_failed(
                sync_id=record.id,
                failed_at=current_time,
                last_error=str(exc),
                next_retry_at=current_time + compute_retry_delay(next_attempt),
            )
            return CatalogSyncJobResult(
                status="failed",
                sync_id=record.id,
                product_id=record.product_id,
                error=str(exc),
            )

    def sync_pending(self, *, limit: int = 100) -> list[CatalogSyncJobResult]:
        results: list[CatalogSyncJobResult] = []
        for _ in range(limit):
            result = self.process_one()
            if result.status == "idle":
                break
            results.append(result)
        return results


def build_sync_status_snapshot(
    repository: InvoiceImportRepository,
    *,
    now: Optional[datetime] = None,
) -> CatalogSyncStatusSnapshot:
    """Summarize sync row state for operational inspection."""
    current_time = now or _utcnow()
    records = repository.list_product_sync_records()
    counts = {
        "pending": 0,
        "processing": 0,
        "failed": 0,
        "synced": 0,
    }
    pending_ages: list[float] = []
    processing_ages: list[float] = []
    repeated_failures: list[dict[str, Any]] = []

    for record in records:
        counts[record.sync_status] = counts.get(record.sync_status, 0) + 1
        if record.sync_status == "pending":
            pending_ages.append((current_time - record.created_at).total_seconds())
        if record.sync_status == "processing" and record.claimed_at is not None:
            processing_ages.append((current_time - record.claimed_at).total_seconds())
        if record.sync_status == "failed" and record.attempt_count > 1:
            repeated_failures.append(
                {
                    "sync_id": record.id,
                    "product_id": record.product_id,
                    "attempt_count": record.attempt_count,
                    "last_error": record.last_error,
                    "next_retry_at": (
                        record.next_retry_at.isoformat()
                        if record.next_retry_at is not None
                        else None
                    ),
                }
            )

    return CatalogSyncStatusSnapshot(
        counts=counts,
        oldest_pending_age_sec=max(pending_ages) if pending_ages else None,
        oldest_processing_age_sec=max(processing_ages) if processing_ages else None,
        repeated_failures=repeated_failures,
    )
