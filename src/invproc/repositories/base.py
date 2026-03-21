"""Repository interfaces for invoice import persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class ProductRecord:
    """Persisted product shape used by import service."""

    product_id: str
    barcode: Optional[str]
    name: str
    normalized_name: str
    supplier: Optional[str]


@dataclass(frozen=True)
class UpsertProductInput:
    """Input payload for create/update product operations."""

    name: str
    barcode: Optional[str]
    supplier: Optional[str]
    price: float
    price_50: float
    price_70: float
    price_100: float
    markup: int


@dataclass(frozen=True)
class ProductSyncRecordInput:
    """Input payload for durable catalog sync rows."""

    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    name: str
    barcode: Optional[str]
    category: Optional[str]
    uom: Optional[str]
    supplier: Optional[str]
    price_eur: Optional[float]
    price_50: Optional[float]
    price_70: Optional[float]
    price_100: Optional[float]
    markup: Optional[int]
    source_import_id: str
    source_row_id: str
    invoice_number: Optional[str]
    sync_status: str
    attempt_count: int
    last_error: Optional[str] = None
    claimed_at: Optional[datetime] = None
    claimed_by: Optional[str] = None
    next_retry_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None


@dataclass(frozen=True)
class ProductSyncRecord:
    """Persisted catalog sync row."""

    id: str
    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    name: str
    barcode: Optional[str]
    category: Optional[str]
    uom: Optional[str]
    supplier: Optional[str]
    price_eur: Optional[float]
    price_50: Optional[float]
    price_70: Optional[float]
    price_100: Optional[float]
    markup: Optional[int]
    source_import_id: str
    source_row_id: str
    invoice_number: Optional[str]
    sync_status: str
    attempt_count: int
    last_error: Optional[str]
    claimed_at: Optional[datetime]
    claimed_by: Optional[str]
    next_retry_at: Optional[datetime]
    last_synced_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProductCatalogEmbeddingRecordInput:
    """Input payload for durable product catalog embeddings."""

    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    embedding_text: str
    embedding: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProductCatalogEmbeddingRecord:
    """Persisted product catalog embedding row."""

    id: str
    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    embedding_text: str
    embedding: list[float]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProductCatalogEmbeddingMatch:
    """Search result returned by repository-native vector queries."""

    product_id: str
    product_snapshot_hash: str
    embedding_model: str
    embedding_text: str
    metadata: dict[str, Any]
    score: float


class InvoiceImportRepository(Protocol):
    """Persistence operations required for invoice import."""

    def find_product_by_barcode(self, barcode: str) -> Optional[ProductRecord]:
        ...

    def find_products_by_normalized_name(self, normalized_name: str) -> list[ProductRecord]:
        ...

    def create_product(self, data: UpsertProductInput) -> ProductRecord:
        ...

    def update_product(self, product_id: str, data: UpsertProductInput) -> ProductRecord:
        ...

    def add_stock_movement_in(
        self,
        *,
        product_id: str,
        quantity: float,
        source: str,
        invoice_number: Optional[str],
    ) -> str:
        ...

    def get_idempotent_result(self, idempotency_key: str) -> Optional[tuple[str, dict]]:
        ...

    def save_idempotent_result(
        self, *, idempotency_key: str, request_hash: str, response_payload: dict
    ) -> None:
        ...

    def create_or_reuse_product_sync(
        self, data: ProductSyncRecordInput
    ) -> tuple[ProductSyncRecord, bool]:
        ...

    def claim_next_product_sync(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_timeout: timedelta,
    ) -> Optional[ProductSyncRecord]:
        ...

    def mark_product_sync_synced(
        self,
        *,
        sync_id: str,
        synced_at: datetime,
    ) -> ProductSyncRecord:
        ...

    def mark_product_sync_failed(
        self,
        *,
        sync_id: str,
        failed_at: datetime,
        last_error: str,
        next_retry_at: datetime,
    ) -> ProductSyncRecord:
        ...

    def get_product_sync(self, sync_id: str) -> Optional[ProductSyncRecord]:
        ...

    def list_product_sync_records(self) -> list[ProductSyncRecord]:
        ...

    def upsert_product_catalog_embedding(
        self, data: ProductCatalogEmbeddingRecordInput
    ) -> ProductCatalogEmbeddingRecord:
        ...

    def list_product_catalog_embeddings(
        self,
        *,
        embedding_model: Optional[str] = None,
    ) -> list[ProductCatalogEmbeddingRecord]:
        ...

    def search_product_catalog_embeddings(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        ...

    def search_product_catalog_embeddings_lexical(
        self,
        *,
        query_text: str,
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        ...
