"""Tests for the Supabase-backed repository adapter."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from invproc.api import build_app_resources
from invproc.catalog_sync import RepositoryCatalogSyncProducer
from invproc.config import InvoiceConfig
from invproc.rag import (
    CatalogRetrievalService,
    CatalogSyncWorker,
    OpenAIEmbeddingClient,
)
from invproc.repositories.base import ProductSyncRecordInput, UpsertProductInput
from invproc.repositories.supabase import SupabaseInvoiceImportRepository


class FakeSupabaseResult:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self.data = data


class FakeSupabaseQuery:
    def __init__(self, client: "FakeSupabaseClient", table_name: str) -> None:
        self.client = client
        self.table_name = table_name
        self.operation = "select"
        self.payload: dict[str, Any] | None = None
        self.filters: list[tuple[str, Any]] = []
        self._limit: int | None = None
        self.on_conflict: list[str] | None = None

    def select(self, _fields: str) -> "FakeSupabaseQuery":
        self.operation = "select"
        return self

    def eq(self, field: str, value: Any) -> "FakeSupabaseQuery":
        self.filters.append((field, value))
        return self

    def limit(self, count: int) -> "FakeSupabaseQuery":
        self._limit = count
        return self

    def insert(self, payload: dict[str, Any]) -> "FakeSupabaseQuery":
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload: dict[str, Any], **_: Any) -> "FakeSupabaseQuery":
        self.operation = "update"
        self.payload = payload
        return self

    def upsert(self, payload: dict[str, Any], *, on_conflict: str) -> "FakeSupabaseQuery":
        self.operation = "upsert"
        self.payload = payload
        self.on_conflict = [field.strip() for field in on_conflict.split(",")]
        return self

    def execute(self) -> FakeSupabaseResult:
        return FakeSupabaseResult(self.client.execute(self))


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.sequences: dict[str, int] = defaultdict(int)

    def table(self, table_name: str) -> FakeSupabaseQuery:
        return FakeSupabaseQuery(self, table_name)

    def rpc(self, fn_name: str, params: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(execute=lambda: FakeSupabaseResult(self.execute_rpc(fn_name, params)))

    def execute(self, query: FakeSupabaseQuery) -> list[dict[str, Any]]:
        table_rows = self.rows[query.table_name]
        if query.operation == "select":
            rows = self._filter_rows(table_rows, query.filters)
            if query._limit is not None:
                rows = rows[: query._limit]
            return [row.copy() for row in rows]

        if query.operation == "insert":
            row = self._prepare_row(query.table_name, query.payload or {})
            table_rows.append(row)
            return [row.copy()]

        if query.operation == "update":
            rows = self._filter_rows(table_rows, query.filters)
            for row in rows:
                row.update(query.payload or {})
                row.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
            return [row.copy() for row in rows]

        if query.operation == "upsert":
            assert query.on_conflict is not None
            payload = query.payload or {}
            for row in table_rows:
                if all(row.get(field) == payload.get(field) for field in query.on_conflict):
                    row.update(payload)
                    row.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
                    return [row.copy()]
            row = self._prepare_row(query.table_name, payload)
            table_rows.append(row)
            return [row.copy()]

        raise AssertionError(f"Unsupported operation: {query.operation}")

    def execute_rpc(self, fn_name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if fn_name == "create_or_reuse_product_sync_row":
            table_rows = self.rows["product_embedding_sync"]
            for row in table_rows:
                if (
                    row["product_id"] == params["product_id"]
                    and row["product_snapshot_hash"] == params["product_snapshot_hash"]
                ):
                    return [{**row.copy(), "created": False}]
            row = self._prepare_row("product_embedding_sync", params)
            table_rows.append(row)
            return [{**row.copy(), "created": True}]

        if fn_name == "claim_next_product_sync_row":
            table_rows = self.rows["product_embedding_sync"]
            now = datetime.fromisoformat(params["p_now"])
            lease_timeout = timedelta(seconds=params["p_lease_timeout_seconds"])
            candidates = []
            for row in table_rows:
                if row["sync_status"] == "pending":
                    candidates.append(row)
                    continue
                next_retry_at = (
                    datetime.fromisoformat(row["next_retry_at"])
                    if row.get("next_retry_at")
                    else None
                )
                claimed_at = (
                    datetime.fromisoformat(row["claimed_at"]) if row.get("claimed_at") else None
                )
                if row["sync_status"] == "failed" and (
                    next_retry_at is None or next_retry_at <= now
                ):
                    candidates.append(row)
                elif row["sync_status"] == "processing" and (
                    claimed_at is not None and claimed_at <= now - lease_timeout
                ):
                    candidates.append(row)
            if not candidates:
                return []
            chosen = min(candidates, key=lambda row: row["created_at"])
            chosen.update(
                {
                    "sync_status": "processing",
                    "claimed_at": params["p_now"],
                    "claimed_by": params["p_worker_id"],
                    "updated_at": params["p_now"],
                }
            )
            return [chosen.copy()]

        if fn_name == "match_product_catalog_embeddings":
            rows = [
                row
                for row in self.rows["product_catalog_embeddings"]
                if row["embedding_model"] == params["p_embedding_model"]
            ]
            query_embedding = [float(value) for value in params["p_query_embedding"]]

            def cosine_similarity(left: list[float], right: list[float]) -> float:
                numerator = sum(a * b for a, b in zip(left, right, strict=True))
                left_norm = sum(value * value for value in left) ** 0.5
                right_norm = sum(value * value for value in right) ** 0.5
                if left_norm == 0.0 or right_norm == 0.0:
                    return 0.0
                return numerator / (left_norm * right_norm)

            scored = [
                {
                    "product_id": row["product_id"],
                    "product_snapshot_hash": row["product_snapshot_hash"],
                    "embedding_model": row["embedding_model"],
                    "embedding_text": row["embedding_text"],
                    "metadata": row["metadata"],
                    "score": cosine_similarity(query_embedding, row["embedding"]),
                }
                for row in rows
            ]
            scored.sort(key=lambda row: row["score"], reverse=True)
            return scored[: params["p_match_count"]]

        if fn_name == "search_product_catalog_embeddings_lexical":
            rows = [
                row
                for row in self.rows["product_catalog_embeddings"]
                if row["embedding_model"] == params["p_embedding_model"]
            ]
            query_tokens = params["p_query_text"].lower().split()

            def _term_overlap(text: str) -> float:
                tokens = text.lower().split()
                return sum(1.0 for t in query_tokens if t in tokens)

            scored = [
                {
                    "product_id": row["product_id"],
                    "product_snapshot_hash": row["product_snapshot_hash"],
                    "embedding_model": row["embedding_model"],
                    "embedding_text": row["embedding_text"],
                    "metadata": row["metadata"],
                    "score": _term_overlap(row["embedding_text"]),
                }
                for row in rows
            ]
            scored = [s for s in scored if s["score"] > 0.0]
            scored.sort(key=lambda row: row["score"], reverse=True)
            return scored[: params["p_match_count"]]

        raise AssertionError(f"Unsupported RPC: {fn_name}")

    def _prepare_row(self, table_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sequences[table_name] += 1
        now = datetime.now(timezone.utc).isoformat()
        row = payload.copy()
        row.setdefault("id", f"{table_name}_{self.sequences[table_name]}")
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        return row

    @staticmethod
    def _filter_rows(
        rows: list[dict[str, Any]], filters: list[tuple[str, Any]]
    ) -> list[dict[str, Any]]:
        results = list(rows)
        for field, value in filters:
            results = [row for row in results if row.get(field) == value]
        return results


def test_supabase_repository_round_trip_supports_import_sync_and_embeddings() -> None:
    client = FakeSupabaseClient()
    repository = SupabaseInvoiceImportRepository(client)

    created = repository.create_product(
        UpsertProductInput(
            name="Greek Yogurt",
            barcode="123456",
            supplier="METRO",
            price=2.5,
            price_50=3.0,
            price_70=3.5,
            price_100=4.0,
            markup=70,
        )
    )
    assert repository.find_product_by_barcode("123456") == created
    assert repository.find_products_by_normalized_name("greek yogurt") == [created]

    movement_id = repository.add_stock_movement_in(
        product_id=created.product_id,
        quantity=2,
        source="invoice_import",
        invoice_number="INV-1",
    )
    assert movement_id.startswith("stock_movements_")

    repository.save_idempotent_result(
        idempotency_key="idem-1",
        request_hash="hash-1",
        response_payload={"import_status": "completed"},
    )
    assert repository.get_idempotent_result("idem-1") == (
        "hash-1",
        {"import_status": "completed"},
    )

    sync_record, created_sync = repository.create_or_reuse_product_sync(
        ProductSyncRecordInput(
            product_id=created.product_id,
            product_snapshot_hash="snapshot-1",
            embedding_model="text-embedding-3-small",
            name="Greek Yogurt",
            barcode="123456",
            category=None,
            uom=None,
            supplier="METRO",
            price_eur=2.5,
            price_50=3.0,
            price_70=3.5,
            price_100=4.0,
            markup=70,
            source_import_id="imp-1",
            source_row_id="row-1",
            invoice_number="INV-1",
            sync_status="pending",
            attempt_count=0,
        )
    )
    assert created_sync is True
    claimed = repository.claim_next_product_sync(
        worker_id="worker-a",
        now=datetime(2026, 3, 20, tzinfo=timezone.utc),
        lease_timeout=timedelta(minutes=10),
    )
    assert claimed is not None
    assert claimed.sync_status == "processing"

    failed = repository.mark_product_sync_failed(
        sync_id=sync_record.id,
        failed_at=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc),
        last_error="embedding unavailable",
        next_retry_at=datetime(2026, 3, 20, 12, 1, tzinfo=timezone.utc),
    )
    assert failed.attempt_count == 1

    synced = repository.mark_product_sync_synced(
        sync_id=sync_record.id,
        synced_at=datetime(2026, 3, 20, 12, 2, tzinfo=timezone.utc),
    )
    assert synced.sync_status == "synced"

    embedding = repository.upsert_product_catalog_embedding(
        data=SimpleNamespace(
            product_id=created.product_id,
            product_snapshot_hash="snapshot-1",
            embedding_model="text-embedding-3-small",
            embedding_text="Greek Yogurt 123456",
            embedding=[0.1, 0.2, 0.3],
            metadata={"name": "Greek Yogurt"},
        )
    )
    assert embedding.product_id == created.product_id
    assert repository.list_product_catalog_embeddings(
        embedding_model="text-embedding-3-small"
    )[0].embedding_text == "Greek Yogurt 123456"


def test_build_app_resources_uses_supabase_repository_when_configured(
    monkeypatch,
) -> None:
    fake_client = FakeSupabaseClient()
    monkeypatch.setattr("invproc.auth.create_client", lambda url, key: fake_client)

    resources = build_app_resources(
        InvoiceConfig(
            _env_file=None,
            mock=True,
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
            import_repository_backend="supabase",
            catalog_sync_enabled=True,
        )
    )

    assert isinstance(resources.import_repository, SupabaseInvoiceImportRepository)
    assert isinstance(resources.catalog_sync_producer, RepositoryCatalogSyncProducer)


def test_shared_supabase_repository_state_is_visible_to_worker_and_retrieval() -> None:
    client = FakeSupabaseClient()
    repository = SupabaseInvoiceImportRepository(client)
    product = repository.create_product(
        UpsertProductInput(
            name="Greek Yogurt",
            barcode="123456",
            supplier="METRO",
            price=2.5,
            price_50=3.0,
            price_70=3.5,
            price_100=4.0,
            markup=70,
        )
    )
    repository.create_or_reuse_product_sync(
        ProductSyncRecordInput(
            product_id=product.product_id,
            product_snapshot_hash="snapshot-shared",
            embedding_model="text-embedding-3-small",
            name="Greek Yogurt",
            barcode="123456",
            category=None,
            uom=None,
            supplier="METRO",
            price_eur=2.5,
            price_50=3.0,
            price_70=3.5,
            price_100=4.0,
            markup=70,
            source_import_id="imp-1",
            source_row_id="row-1",
            invoice_number="INV-1",
            sync_status="pending",
            attempt_count=0,
        )
    )

    worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        worker_id="worker-a",
    )
    result = worker.process_one()
    assert result.status == "synced"

    retrieval = CatalogRetrievalService(
        repository=SupabaseInvoiceImportRepository(client),
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )
    query_result = retrieval.query("greek yogurt", top_k=5)

    assert query_result.matches
    assert query_result.matches[0].product_id == product.product_id
