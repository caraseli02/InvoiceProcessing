"""In-memory repository for MVP import flow and tests."""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from invproc.rag import cosine_similarity
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


def _bm25_scores(
    query_text: str,
    documents: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Compute BM25 relevance scores for documents against a query."""
    query_tokens = query_text.lower().split()
    if not query_tokens or not documents:
        return [0.0] * len(documents)

    tokenized = [doc.lower().split() for doc in documents]
    n = len(documents)
    avgdl = sum(len(t) for t in tokenized) / n

    df: dict[str, int] = {}
    for doc_tokens in tokenized:
        for token in set(doc_tokens):
            df[token] = df.get(token, 0) + 1

    scores: list[float] = []
    for doc_tokens in tokenized:
        tf_map: dict[str, int] = {}
        for token in doc_tokens:
            tf_map[token] = tf_map.get(token, 0) + 1
        dl = len(doc_tokens)
        score = 0.0
        for token in query_tokens:
            if token not in df:
                continue
            tf = tf_map.get(token, 0)
            idf = math.log((n - df[token] + 0.5) / (df[token] + 0.5) + 1.0)
            score += idf * (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * dl / avgdl))
        scores.append(score)
    return scores


class InMemoryInvoiceImportRepository(InvoiceImportRepository):
    """Thread-safe in-memory storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Reset all in-memory state (used by tests)."""
        with self._lock:
            self._products: dict[str, ProductRecord] = {}
            self._products_by_barcode: dict[str, str] = {}
            self._movements: dict[str, dict] = {}
            self._idempotency: dict[str, tuple[str, dict]] = {}
            self._product_sync: dict[tuple[str, str], ProductSyncRecord] = {}
            self._product_sync_by_id: dict[str, ProductSyncRecord] = {}
            self._product_catalog_embeddings: dict[
                tuple[str, str, str], ProductCatalogEmbeddingRecord
            ] = {}
            self._product_seq = 1
            self._movement_seq = 1
            self._sync_seq = 1
            self._embedding_seq = 1

    def find_product_by_barcode(self, barcode: str) -> Optional[ProductRecord]:
        with self._lock:
            product_id = self._products_by_barcode.get(barcode)
            if not product_id:
                return None
            return self._products.get(product_id)

    def find_products_by_normalized_name(self, normalized_name: str) -> list[ProductRecord]:
        with self._lock:
            return [
                p for p in self._products.values() if p.normalized_name == normalized_name
            ]

    def create_product(self, data: UpsertProductInput) -> ProductRecord:
        from invproc.import_service import normalize_name

        with self._lock:
            product_id = f"prod_{self._product_seq}"
            self._product_seq += 1
            product = ProductRecord(
                product_id=product_id,
                barcode=data.barcode,
                name=data.name,
                normalized_name=normalize_name(data.name),
                supplier=data.supplier,
            )
            self._products[product_id] = product
            if data.barcode:
                self._products_by_barcode[data.barcode] = product_id
            return product

    def update_product(self, product_id: str, data: UpsertProductInput) -> ProductRecord:
        from invproc.import_service import normalize_name

        with self._lock:
            if product_id not in self._products:
                raise KeyError(f"Unknown product_id: {product_id}")

            product = ProductRecord(
                product_id=product_id,
                barcode=data.barcode,
                name=data.name,
                normalized_name=normalize_name(data.name),
                supplier=data.supplier,
            )
            self._products[product_id] = product
            if data.barcode:
                self._products_by_barcode[data.barcode] = product_id
            return product

    def add_stock_movement_in(
        self,
        *,
        product_id: str,
        quantity: float,
        source: str,
        invoice_number: Optional[str],
    ) -> str:
        with self._lock:
            movement_id = f"mov_{self._movement_seq}"
            self._movement_seq += 1
            self._movements[movement_id] = {
                "product_id": product_id,
                "quantity": quantity,
                "source": source,
                "invoice_number": invoice_number,
                "type": "IN",
            }
            return movement_id

    def get_idempotent_result(self, idempotency_key: str) -> Optional[tuple[str, dict]]:
        with self._lock:
            return self._idempotency.get(idempotency_key)

    def save_idempotent_result(
        self, *, idempotency_key: str, request_hash: str, response_payload: dict
    ) -> None:
        with self._lock:
            self._idempotency[idempotency_key] = (request_hash, response_payload)

    def create_or_reuse_product_sync(
        self, data: ProductSyncRecordInput
    ) -> tuple[ProductSyncRecord, bool]:
        with self._lock:
            key = (data.product_id, data.product_snapshot_hash)
            existing = self._product_sync.get(key)
            if existing is not None:
                return existing, False

            now = datetime.now(timezone.utc)
            record = ProductSyncRecord(
                id=f"sync_{self._sync_seq}",
                product_id=data.product_id,
                product_snapshot_hash=data.product_snapshot_hash,
                embedding_model=data.embedding_model,
                name=data.name,
                barcode=data.barcode,
                category=data.category,
                uom=data.uom,
                supplier=data.supplier,
                price_eur=data.price_eur,
                price_50=data.price_50,
                price_70=data.price_70,
                price_100=data.price_100,
                markup=data.markup,
                source_import_id=data.source_import_id,
                source_row_id=data.source_row_id,
                invoice_number=data.invoice_number,
                sync_status=data.sync_status,
                attempt_count=data.attempt_count,
                last_error=data.last_error,
                claimed_at=data.claimed_at,
                claimed_by=data.claimed_by,
                next_retry_at=data.next_retry_at,
                last_synced_at=data.last_synced_at,
                created_at=now,
                updated_at=now,
            )
            self._sync_seq += 1
            self._product_sync[key] = record
            self._product_sync_by_id[record.id] = record
            return record, True

    def claim_next_product_sync(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_timeout: timedelta,
    ) -> Optional[ProductSyncRecord]:
        with self._lock:
            candidates: list[ProductSyncRecord] = []
            lease_cutoff = now - lease_timeout
            for record in self._product_sync_by_id.values():
                if record.sync_status == "pending":
                    candidates.append(record)
                    continue
                if record.sync_status == "failed" and (
                    record.next_retry_at is None or record.next_retry_at <= now
                ):
                    candidates.append(record)
                    continue
                if record.sync_status == "processing" and (
                    record.claimed_at is not None and record.claimed_at <= lease_cutoff
                ):
                    candidates.append(record)

            if not candidates:
                return None

            record = min(candidates, key=lambda candidate: candidate.created_at)
            claimed = ProductSyncRecord(
                id=record.id,
                product_id=record.product_id,
                product_snapshot_hash=record.product_snapshot_hash,
                embedding_model=record.embedding_model,
                name=record.name,
                barcode=record.barcode,
                category=record.category,
                uom=record.uom,
                supplier=record.supplier,
                price_eur=record.price_eur,
                price_50=record.price_50,
                price_70=record.price_70,
                price_100=record.price_100,
                markup=record.markup,
                source_import_id=record.source_import_id,
                source_row_id=record.source_row_id,
                invoice_number=record.invoice_number,
                sync_status="processing",
                attempt_count=record.attempt_count,
                last_error=record.last_error,
                claimed_at=now,
                claimed_by=worker_id,
                next_retry_at=record.next_retry_at,
                last_synced_at=record.last_synced_at,
                created_at=record.created_at,
                updated_at=now,
            )
            self._replace_sync_record(claimed)
            return claimed

    def mark_product_sync_synced(
        self,
        *,
        sync_id: str,
        synced_at: datetime,
    ) -> ProductSyncRecord:
        with self._lock:
            record = self._require_product_sync(sync_id)
            synced = ProductSyncRecord(
                id=record.id,
                product_id=record.product_id,
                product_snapshot_hash=record.product_snapshot_hash,
                embedding_model=record.embedding_model,
                name=record.name,
                barcode=record.barcode,
                category=record.category,
                uom=record.uom,
                supplier=record.supplier,
                price_eur=record.price_eur,
                price_50=record.price_50,
                price_70=record.price_70,
                price_100=record.price_100,
                markup=record.markup,
                source_import_id=record.source_import_id,
                source_row_id=record.source_row_id,
                invoice_number=record.invoice_number,
                sync_status="synced",
                attempt_count=record.attempt_count,
                last_error=None,
                claimed_at=record.claimed_at,
                claimed_by=record.claimed_by,
                next_retry_at=None,
                last_synced_at=synced_at,
                created_at=record.created_at,
                updated_at=synced_at,
            )
            self._replace_sync_record(synced)
            return synced

    def mark_product_sync_failed(
        self,
        *,
        sync_id: str,
        failed_at: datetime,
        last_error: str,
        next_retry_at: datetime,
    ) -> ProductSyncRecord:
        with self._lock:
            record = self._require_product_sync(sync_id)
            failed = ProductSyncRecord(
                id=record.id,
                product_id=record.product_id,
                product_snapshot_hash=record.product_snapshot_hash,
                embedding_model=record.embedding_model,
                name=record.name,
                barcode=record.barcode,
                category=record.category,
                uom=record.uom,
                supplier=record.supplier,
                price_eur=record.price_eur,
                price_50=record.price_50,
                price_70=record.price_70,
                price_100=record.price_100,
                markup=record.markup,
                source_import_id=record.source_import_id,
                source_row_id=record.source_row_id,
                invoice_number=record.invoice_number,
                sync_status="failed",
                attempt_count=record.attempt_count + 1,
                last_error=last_error,
                claimed_at=record.claimed_at,
                claimed_by=record.claimed_by,
                next_retry_at=next_retry_at,
                last_synced_at=record.last_synced_at,
                created_at=record.created_at,
                updated_at=failed_at,
            )
            self._replace_sync_record(failed)
            return failed

    def get_product_sync(self, sync_id: str) -> Optional[ProductSyncRecord]:
        with self._lock:
            return self._product_sync_by_id.get(sync_id)

    def upsert_product_catalog_embedding(
        self, data: ProductCatalogEmbeddingRecordInput
    ) -> ProductCatalogEmbeddingRecord:
        with self._lock:
            key = (data.product_id, data.product_snapshot_hash, data.embedding_model)
            existing = self._product_catalog_embeddings.get(key)
            now = datetime.now(timezone.utc)
            if existing is not None:
                updated = ProductCatalogEmbeddingRecord(
                    id=existing.id,
                    product_id=existing.product_id,
                    product_snapshot_hash=existing.product_snapshot_hash,
                    embedding_model=existing.embedding_model,
                    embedding_text=data.embedding_text,
                    embedding=list(data.embedding),
                    metadata=dict(data.metadata),
                    created_at=existing.created_at,
                    updated_at=now,
                )
                self._product_catalog_embeddings[key] = updated
                return updated

            record = ProductCatalogEmbeddingRecord(
                id=f"embed_{self._embedding_seq}",
                product_id=data.product_id,
                product_snapshot_hash=data.product_snapshot_hash,
                embedding_model=data.embedding_model,
                embedding_text=data.embedding_text,
                embedding=list(data.embedding),
                metadata=dict(data.metadata),
                created_at=now,
                updated_at=now,
            )
            self._embedding_seq += 1
            self._product_catalog_embeddings[key] = record
            return record

    def list_product_catalog_embeddings(
        self,
        *,
        embedding_model: Optional[str] = None,
    ) -> list[ProductCatalogEmbeddingRecord]:
        with self._lock:
            records = list(self._product_catalog_embeddings.values())
            if embedding_model is not None:
                records = [
                    record
                    for record in records
                    if record.embedding_model == embedding_model
                ]
            return sorted(records, key=lambda record: record.created_at)

    def search_product_catalog_embeddings(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        records = self.list_product_catalog_embeddings(embedding_model=embedding_model)
        matches = [
            ProductCatalogEmbeddingMatch(
                product_id=record.product_id,
                product_snapshot_hash=record.product_snapshot_hash,
                embedding_model=record.embedding_model,
                embedding_text=record.embedding_text,
                metadata=dict(record.metadata),
                score=cosine_similarity(query_embedding, record.embedding),
            )
            for record in records
        ]
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[:top_k]

    def search_product_catalog_embeddings_lexical(
        self,
        *,
        query_text: str,
        embedding_model: str,
        top_k: int,
    ) -> list[ProductCatalogEmbeddingMatch]:
        records = self.list_product_catalog_embeddings(embedding_model=embedding_model)
        if not records:
            return []
        documents = [record.embedding_text for record in records]
        scores = _bm25_scores(query_text, documents)
        ranked = sorted(
            zip(records, scores),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [
            ProductCatalogEmbeddingMatch(
                product_id=record.product_id,
                product_snapshot_hash=record.product_snapshot_hash,
                embedding_model=record.embedding_model,
                embedding_text=record.embedding_text,
                metadata=dict(record.metadata),
                score=score,
            )
            for record, score in ranked[:top_k]
            if score > 0.0
        ]

    def list_product_sync_records(self) -> list[ProductSyncRecord]:
        """Return sync rows in insertion order for tests."""
        with self._lock:
            return sorted(self._product_sync.values(), key=lambda record: record.id)

    def _require_product_sync(self, sync_id: str) -> ProductSyncRecord:
        record = self._product_sync_by_id.get(sync_id)
        if record is None:
            raise KeyError(f"Unknown sync_id: {sync_id}")
        return record

    def _replace_sync_record(self, record: ProductSyncRecord) -> None:
        key = (record.product_id, record.product_snapshot_hash)
        self._product_sync[key] = record
        self._product_sync_by_id[record.id] = record
