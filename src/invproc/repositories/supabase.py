"""Supabase-backed repository for invoice import and backend RAG persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast

from supabase import Client

from invproc.import_service import normalize_name
from invproc.repositories.base import (
    InvoiceImportRepository,
    ProductCatalogEmbeddingMatch,
    ProductCatalogEmbeddingRecord,
    ProductCatalogEmbeddingRecordInput,
    ProductRecord,
    ProductSyncRecord,
    ProductSyncRecordInput,
    UpsertProductInput,
)


def _parse_embedding(value: Any) -> list[float]:
    """Parse a pgvector embedding returned as a string '[0.1, 0.2, ...]' or already a list."""
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, str):
        import json
        return [float(x) for x in json.loads(value)]
    raise TypeError(f"Unsupported embedding type: {type(value)}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError(f"Unsupported datetime value: {value!r}")


class SupabaseInvoiceImportRepository(InvoiceImportRepository):
    """Repository implementation backed by Supabase tables."""

    def __init__(
        self,
        client: Client,
        *,
        products_table: str = "products",
        stock_movements_table: str = "stock_movements",
        import_runs_table: str = "invoice_import_runs",
        product_sync_table: str = "product_embedding_sync",
        embeddings_table: str = "product_catalog_embeddings",
    ) -> None:
        self.client = client
        self.products_table = products_table
        self.stock_movements_table = stock_movements_table
        self.import_runs_table = import_runs_table
        self.product_sync_table = product_sync_table
        self.embeddings_table = embeddings_table

    def find_product_by_barcode(self, barcode: str) -> Optional[ProductRecord]:
        rows = self._select(
            self.products_table,
            filters=[("barcode", barcode)],
            limit=1,
        )
        if not rows:
            return None
        return self._map_product(rows[0])

    def find_products_by_normalized_name(self, normalized_name: str) -> list[ProductRecord]:
        rows = self._select(
            self.products_table,
            filters=[("normalized_name", normalized_name)],
        )
        return [self._map_product(row) for row in rows]

    def create_product(self, data: UpsertProductInput) -> ProductRecord:
        payload = {
            "name": data.name,
            "barcode": data.barcode,
            "normalized_name": normalize_name(data.name),
            "supplier": data.supplier,
            "price": data.price,
            "price_50": data.price_50,
            "price_70": data.price_70,
            "price_100": data.price_100,
            "markup": data.markup,
        }
        row = self._insert_one(self.products_table, payload)
        return self._map_product(row)

    def update_product(self, product_id: str, data: UpsertProductInput) -> ProductRecord:
        payload = {
            "name": data.name,
            "barcode": data.barcode,
            "normalized_name": normalize_name(data.name),
            "supplier": data.supplier,
            "price": data.price,
            "price_50": data.price_50,
            "price_70": data.price_70,
            "price_100": data.price_100,
            "markup": data.markup,
            "updated_at": _utcnow().isoformat(),
        }
        row = self._update_one(self.products_table, payload, filters=[("id", product_id)])
        return self._map_product(row)

    def add_stock_movement_in(
        self,
        *,
        product_id: str,
        quantity: float,
        source: str,
        invoice_number: Optional[str],
    ) -> str:
        row = self._insert_one(
            self.stock_movements_table,
            {
                "product_id": product_id,
                "type": "IN",
                "quantity": quantity,
                "source": source,
                "invoice_number": invoice_number,
            },
        )
        return str(row["id"])

    def get_idempotent_result(self, idempotency_key: str) -> Optional[tuple[str, dict]]:
        rows = self._select(
            self.import_runs_table,
            filters=[("idempotency_key", idempotency_key)],
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return str(row["request_hash"]), dict(row["response_payload"])

    def save_idempotent_result(
        self, *, idempotency_key: str, request_hash: str, response_payload: dict
    ) -> None:
        existing = self._select(
            self.import_runs_table,
            filters=[("idempotency_key", idempotency_key)],
            limit=1,
        )
        payload = {
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "response_payload": response_payload,
            "status": response_payload.get("import_status", "completed"),
            "updated_at": _utcnow().isoformat(),
        }
        if existing:
            self._update_one(
                self.import_runs_table,
                payload,
                filters=[("idempotency_key", idempotency_key)],
            )
            return
        self._insert_one(self.import_runs_table, payload)

    def create_or_reuse_product_sync(
        self, data: ProductSyncRecordInput
    ) -> tuple[ProductSyncRecord, bool]:
        raw = cast(
            list[dict[str, Any]],
            self.client.rpc(
                "create_or_reuse_product_sync_row",
                self._product_sync_input_payload(data),
            ).execute().data,
        )
        result: dict[str, Any] = dict(raw[0])
        created = bool(result.pop("created", False))
        return self._map_product_sync(result), created

    def claim_next_product_sync(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_timeout: timedelta,
    ) -> Optional[ProductSyncRecord]:
        rows = cast(
            list[dict[str, Any]],
            self.client.rpc(
                "claim_next_product_sync_row",
                {
                    "p_worker_id": worker_id,
                    "p_now": now.isoformat(),
                    "p_lease_timeout_seconds": int(lease_timeout.total_seconds()),
                },
            ).execute().data,
        )
        if not rows:
            return None
        return self._map_product_sync(dict(rows[0]))

    def mark_product_sync_synced(
        self,
        *,
        sync_id: str,
        synced_at: datetime,
    ) -> ProductSyncRecord:
        row = self._update_one(
            self.product_sync_table,
            {
                "sync_status": "synced",
                "last_error": None,
                "next_retry_at": None,
                "last_synced_at": synced_at.isoformat(),
                "updated_at": synced_at.isoformat(),
            },
            filters=[("id", sync_id)],
        )
        return self._map_product_sync(row)

    def mark_product_sync_failed(
        self,
        *,
        sync_id: str,
        failed_at: datetime,
        last_error: str,
        next_retry_at: datetime,
    ) -> ProductSyncRecord:
        current = self.get_product_sync(sync_id)
        if current is None:
            raise KeyError(f"Unknown sync_id: {sync_id}")
        row = self._update_one(
            self.product_sync_table,
            {
                "sync_status": "failed",
                "attempt_count": current.attempt_count + 1,
                "last_error": last_error,
                "next_retry_at": next_retry_at.isoformat(),
                "updated_at": failed_at.isoformat(),
            },
            filters=[("id", sync_id)],
        )
        return self._map_product_sync(row)

    def get_product_sync(self, sync_id: str) -> Optional[ProductSyncRecord]:
        rows = self._select(self.product_sync_table, filters=[("id", sync_id)], limit=1)
        if not rows:
            return None
        return self._map_product_sync(rows[0])

    def list_product_sync_records(self) -> list[ProductSyncRecord]:
        return [self._map_product_sync(row) for row in self._select(self.product_sync_table)]

    def upsert_product_catalog_embedding(
        self, data: ProductCatalogEmbeddingRecordInput
    ) -> ProductCatalogEmbeddingRecord:
        payload = {
            "product_id": data.product_id,
            "product_snapshot_hash": data.product_snapshot_hash,
            "embedding_model": data.embedding_model,
            "embedding_text": data.embedding_text,
            "embedding": data.embedding,
            "metadata": data.metadata,
            "updated_at": _utcnow().isoformat(),
        }
        rows = cast(
            list[dict[str, Any]],
            self.client.table(self.embeddings_table).upsert(
                payload,  # type: ignore[arg-type]
                on_conflict="product_id,product_snapshot_hash,embedding_model",
            ).execute().data,
        )
        return self._map_product_catalog_embedding(rows[0])

    def list_product_catalog_embeddings(
        self,
        *,
        embedding_model: Optional[str] = None,
    ) -> list[ProductCatalogEmbeddingRecord]:
        filters: list[tuple[str, Any]] = []
        if embedding_model is not None:
            filters.append(("embedding_model", embedding_model))
        return [
            self._map_product_catalog_embedding(row)
            for row in self._select(self.embeddings_table, filters=filters)
        ]

    def search_product_catalog_embeddings(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        rows = cast(
            list[dict[str, Any]],
            self.client.rpc(
                "match_product_catalog_embeddings",
                {
                    "p_query_embedding": query_embedding,
                    "p_embedding_model": embedding_model,
                    "p_match_count": top_k,
                },
            ).execute().data,
        )
        return [
            ProductCatalogEmbeddingMatch(
                product_id=str(row["product_id"]),
                product_snapshot_hash=str(row["product_snapshot_hash"]),
                embedding_model=str(row["embedding_model"]),
                embedding_text=str(row["embedding_text"]),
                metadata=dict(row["metadata"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    def search_product_catalog_embeddings_lexical(
        self,
        *,
        query_text: str,
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        rows = cast(
            list[dict[str, Any]],
            self.client.rpc(
                "search_product_catalog_embeddings_lexical",
                {
                    "p_query_text": query_text,
                    "p_embedding_model": embedding_model,
                    "p_match_count": top_k,
                },
            ).execute().data,
        )
        return [
            ProductCatalogEmbeddingMatch(
                product_id=str(row["product_id"]),
                product_snapshot_hash=str(row["product_snapshot_hash"]),
                embedding_model=str(row["embedding_model"]),
                embedding_text=str(row["embedding_text"]),
                metadata=dict(row["metadata"]),
                score=float(row["score"]),
            )
            for row in rows
        ]

    def _select(
        self,
        table_name: str,
        *,
        filters: list[tuple[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = self.client.table(table_name).select("*")
        for field, value in filters or []:
            query = query.eq(field, value)
        if limit is not None:
            query = query.limit(limit)
        return cast(list[dict[str, Any]], query.execute().data)

    def _insert_one(self, table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        rows = cast(
            list[dict[str, Any]],
            self.client.table(table_name).insert(payload).execute().data,  # type: ignore[arg-type]
        )
        return dict(rows[0])

    def _update_one(
        self,
        table_name: str,
        payload: dict[str, Any],
        *,
        filters: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        query = self.client.table(table_name).update(payload)  # type: ignore[arg-type]
        for field, value in filters:
            query = query.eq(field, value)
        rows = cast(list[dict[str, Any]], query.execute().data)
        if not rows:
            raise KeyError(f"No row found in {table_name} for filters={filters!r}")
        return dict(rows[0])

    def _product_sync_input_payload(self, data: ProductSyncRecordInput) -> dict[str, Any]:
        return {
            "product_id": data.product_id,
            "product_snapshot_hash": data.product_snapshot_hash,
            "embedding_model": data.embedding_model,
            "name": data.name,
            "barcode": data.barcode,
            "category": data.category,
            "uom": data.uom,
            "supplier": data.supplier,
            "price_eur": data.price_eur,
            "price_50": data.price_50,
            "price_70": data.price_70,
            "price_100": data.price_100,
            "markup": data.markup,
            "source_import_id": data.source_import_id,
            "source_row_id": data.source_row_id,
            "invoice_number": data.invoice_number,
            "sync_status": data.sync_status,
            "attempt_count": data.attempt_count,
            "last_error": data.last_error,
            "claimed_at": data.claimed_at.isoformat() if data.claimed_at else None,
            "claimed_by": data.claimed_by,
            "next_retry_at": data.next_retry_at.isoformat() if data.next_retry_at else None,
            "last_synced_at": data.last_synced_at.isoformat() if data.last_synced_at else None,
        }

    def _map_product(self, row: dict[str, Any]) -> ProductRecord:
        return ProductRecord(
            product_id=str(row["id"]),
            barcode=row.get("barcode"),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
            supplier=row.get("supplier"),
        )

    def _map_product_sync(self, row: dict[str, Any]) -> ProductSyncRecord:
        return ProductSyncRecord(
            id=str(row["id"]),
            product_id=str(row["product_id"]),
            product_snapshot_hash=str(row["product_snapshot_hash"]),
            embedding_model=str(row["embedding_model"]),
            name=str(row["name"]),
            barcode=row.get("barcode"),
            category=row.get("category"),
            uom=row.get("uom"),
            supplier=row.get("supplier"),
            price_eur=row.get("price_eur"),
            price_50=row.get("price_50"),
            price_70=row.get("price_70"),
            price_100=row.get("price_100"),
            markup=row.get("markup"),
            source_import_id=str(row["source_import_id"]),
            source_row_id=str(row["source_row_id"]),
            invoice_number=row.get("invoice_number"),
            sync_status=str(row["sync_status"]),
            attempt_count=int(row["attempt_count"]),
            last_error=row.get("last_error"),
            claimed_at=_parse_datetime(row.get("claimed_at")),
            claimed_by=row.get("claimed_by"),
            next_retry_at=_parse_datetime(row.get("next_retry_at")),
            last_synced_at=_parse_datetime(row.get("last_synced_at")),
            created_at=_parse_datetime(row.get("created_at")) or _utcnow(),
            updated_at=_parse_datetime(row.get("updated_at")) or _utcnow(),
        )

    def _map_product_catalog_embedding(
        self, row: dict[str, Any]
    ) -> ProductCatalogEmbeddingRecord:
        return ProductCatalogEmbeddingRecord(
            id=str(row["id"]),
            product_id=str(row["product_id"]),
            product_snapshot_hash=str(row["product_snapshot_hash"]),
            embedding_model=str(row["embedding_model"]),
            embedding_text=str(row["embedding_text"]),
            embedding=_parse_embedding(row["embedding"]),
            metadata=dict(row["metadata"]),
            created_at=_parse_datetime(row.get("created_at")) or _utcnow(),
            updated_at=_parse_datetime(row.get("updated_at")) or _utcnow(),
        )
