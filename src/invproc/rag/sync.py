"""Sync workflow for backend-owned RAG catalog processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from invproc.rag.retrieval import EmbeddingClient
from invproc.repositories.base import (
    InvoiceImportRepository,
    ProductCatalogEmbeddingRecordInput,
    ProductSyncRecord,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_catalog_embedding_text(record: ProductSyncRecord) -> str:
    """Assemble the canonical V1 catalog embedding text."""
    parts = [
        record.name.strip(),
        record.barcode.strip() if record.barcode else "",
        record.category.strip() if record.category else "",
        record.uom.strip() if record.uom else "",
    ]
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

        embedding_text = build_catalog_embedding_text(record)
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
                        "category": record.category,
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
