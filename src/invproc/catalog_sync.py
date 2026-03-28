"""Catalog sync producer for durable product snapshot intents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from invproc.repositories.base import (
    InvoiceImportRepository,
    ProductRecord,
    ProductSyncRecord,
    ProductSyncRecordInput,
    UpsertProductInput,
)

CATALOG_EMBEDDING_TEXT_VERSION = "v6"


@dataclass(frozen=True)
class CatalogSyncContext:
    """Import context needed to create a product sync row."""

    import_id: str
    source_row_id: str
    invoice_number: str | None


@dataclass(frozen=True)
class CatalogSyncResult:
    """Result of a sync emission attempt."""

    record: ProductSyncRecord | None
    created: bool


class CatalogSyncProducer(Protocol):
    """Producer contract for durable catalog sync rows."""

    def emit_product_sync(
        self,
        *,
        product: ProductRecord,
        upsert_input: UpsertProductInput,
        context: CatalogSyncContext,
    ) -> CatalogSyncResult:
        ...


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def build_product_snapshot_hash(
    *,
    product: ProductRecord,
    upsert_input: UpsertProductInput,
    embedding_model: str,
    category: str | None,
    uom: str | None,
) -> str:
    """Build deterministic product snapshot hash from Phase 1 contract fields."""
    payload = {
        "product_id": product.product_id,
        "name": _normalize_text(product.name),
        "barcode": _normalize_text(product.barcode),
        "category": _normalize_text(category),
        "uom": _normalize_text(uom),
        "supplier": _normalize_text(product.supplier),
        "price_eur": upsert_input.price,
        "price_50": upsert_input.price_50,
        "price_70": upsert_input.price_70,
        "price_100": upsert_input.price_100,
        "markup": upsert_input.markup,
        "embedding_model": embedding_model,
        "embedding_text_version": CATALOG_EMBEDDING_TEXT_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NoopCatalogSyncProducer:
    """Producer used when catalog sync is disabled."""

    def emit_product_sync(
        self,
        *,
        product: ProductRecord,
        upsert_input: UpsertProductInput,
        context: CatalogSyncContext,
    ) -> CatalogSyncResult:
        _ = product
        _ = upsert_input
        _ = context
        return CatalogSyncResult(record=None, created=False)


class RepositoryCatalogSyncProducer:
    """Repository-backed producer for durable catalog sync rows."""

    def __init__(
        self,
        repository: InvoiceImportRepository,
        *,
        embedding_model: str,
    ) -> None:
        self.repository = repository
        self.embedding_model = embedding_model

    def emit_product_sync(
        self,
        *,
        product: ProductRecord,
        upsert_input: UpsertProductInput,
        context: CatalogSyncContext,
    ) -> CatalogSyncResult:
        snapshot_hash = build_product_snapshot_hash(
            product=product,
            upsert_input=upsert_input,
            embedding_model=self.embedding_model,
            category=product.category,
            uom=product.uom,
        )
        record, created = self.repository.create_or_reuse_product_sync(
            ProductSyncRecordInput(
                product_id=product.product_id,
                product_snapshot_hash=snapshot_hash,
                embedding_model=self.embedding_model,
                name=product.name,
                barcode=product.barcode,
                category=product.category,
                uom=product.uom,
                supplier=product.supplier,
                price_eur=upsert_input.price,
                price_50=upsert_input.price_50,
                price_70=upsert_input.price_70,
                price_100=upsert_input.price_100,
                markup=upsert_input.markup,
                source_import_id=context.import_id,
                source_row_id=context.source_row_id,
                invoice_number=context.invoice_number,
                sync_status="pending",
                attempt_count=0,
            )
        )
        return CatalogSyncResult(record=record, created=created)
