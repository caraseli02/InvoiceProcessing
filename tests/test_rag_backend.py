"""Tests for Phase 3 backend-owned RAG workflow."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from invproc.api import build_app_resources, create_app
from invproc.catalog_sync import build_product_snapshot_hash
from invproc import cli as cli_module
from invproc.cli import (
    _build_import_request_from_invoice,
    _get_cli_rag_resources,
    app,
)
from invproc.config import InvoiceConfig
from invproc.import_service import InvoiceImportService
from invproc.models import InvoiceData, InvoiceImportRequest, Product
from invproc.rag import (
    CatalogEvalCase,
    CatalogRagEvaluator,
    CatalogRetrievalService,
    CatalogSyncWorker,
    OpenAIEmbeddingClient,
    build_catalog_embedding_text,
    rrf_merge,
)
from invproc.repositories.base import ProductCatalogEmbeddingMatch
from invproc.repositories.base import ProductRecord, ProductSyncRecordInput, UpsertProductInput
from invproc.repositories.memory import InMemoryInvoiceImportRepository

runner = CliRunner()


def configure_cli_for_memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep CLI tests isolated from ambient backend configuration."""
    cli_module._CLI_RAG_RESOURCES = None
    cli_module._CLI_RAG_RESOURCES_KEY = None

    def fake_get_config() -> InvoiceConfig:
        return InvoiceConfig(
            _env_file=None,
            mock=True,
            catalog_sync_enabled=True,
            import_repository_backend="memory",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
        )

    monkeypatch.setattr(cli_module, "get_config_unvalidated", fake_get_config)


def strip_ansi(text: str) -> str:
    """Normalize Typer/Rich CLI output for stable assertions across environments."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class FlakyEmbeddingClient:
    """Embedding client that fails a configurable number of times."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.delegate = OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True))

    def embed(self, *, model: str, text: str) -> list[float]:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("embedding unavailable")
        return self.delegate.embed(model=model, text=text)


def build_sync_record_input(
    *,
    product_id: str = "prod_1",
    name: str = "Greek Yogurt",
    barcode: str | None = "123456",
    category: str | None = "Dairy",
    uom: str | None = "bucket",
    embedding_model: str = "text-embedding-3-small",
) -> ProductSyncRecordInput:
    product = ProductRecord(
        product_id=product_id,
        barcode=barcode,
        name=name,
        normalized_name=name.lower(),
        supplier="METRO",
    )
    upsert_input = UpsertProductInput(
        name=name,
        barcode=barcode,
        supplier="METRO",
        price=2.5,
        price_50=3.0,
        price_70=3.5,
        price_100=4.0,
        markup=70,
    )
    return ProductSyncRecordInput(
        product_id=product_id,
        product_snapshot_hash=build_product_snapshot_hash(
            product=product,
            upsert_input=upsert_input,
            embedding_model=embedding_model,
            category=category,
            uom=uom,
        ),
        embedding_model=embedding_model,
        name=name,
        barcode=barcode,
        category=category,
        uom=uom,
        supplier="METRO",
        price_eur=2.5,
        price_50=3.0,
        price_70=3.5,
        price_100=4.0,
        markup=70,
        source_import_id="imp_1",
        source_row_id="row_1",
        invoice_number="INV-1",
        sync_status="pending",
        attempt_count=0,
    )


def seed_synced_product(
    repository: InMemoryInvoiceImportRepository,
    *,
    product_id: str,
    name: str,
    barcode: str | None,
    category: str | None,
    uom: str | None,
) -> None:
    record, _ = repository.create_or_reuse_product_sync(
        build_sync_record_input(
            product_id=product_id,
            name=name,
            barcode=barcode,
            category=category,
            uom=uom,
        )
    )
    worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        worker_id=f"worker-{product_id}",
    )
    result = worker.process_one()
    assert result.status == "synced"
    synced = repository.get_product_sync(record.id)
    assert synced is not None
    assert synced.sync_status == "synced"


def test_build_catalog_embedding_text_skips_empty_fields() -> None:
    record = build_sync_record_input(barcode=None, category=None, uom=" ").__dict__
    sync_record, _ = InMemoryInvoiceImportRepository().create_or_reuse_product_sync(
        ProductSyncRecordInput(**record)
    )

    assert build_catalog_embedding_text(sync_record) == "Greek Yogurt"


def test_repository_claim_prevents_duplicate_worker_claims() -> None:
    repository = InMemoryInvoiceImportRepository()
    record, _ = repository.create_or_reuse_product_sync(build_sync_record_input())
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)

    first = repository.claim_next_product_sync(
        worker_id="worker-a",
        now=now,
        lease_timeout=timedelta(minutes=10),
    )
    second = repository.claim_next_product_sync(
        worker_id="worker-b",
        now=now,
        lease_timeout=timedelta(minutes=10),
    )

    assert first is not None
    assert first.id == record.id
    assert second is None


def test_repository_reclaims_expired_processing_rows() -> None:
    repository = InMemoryInvoiceImportRepository()
    record, _ = repository.create_or_reuse_product_sync(build_sync_record_input())
    claimed_at = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
    repository.claim_next_product_sync(
        worker_id="worker-a",
        now=claimed_at,
        lease_timeout=timedelta(minutes=10),
    )

    reclaimed = repository.claim_next_product_sync(
        worker_id="worker-b",
        now=claimed_at + timedelta(minutes=11),
        lease_timeout=timedelta(minutes=10),
    )

    assert reclaimed is not None
    assert reclaimed.id == record.id
    assert reclaimed.claimed_by == "worker-b"


def test_worker_success_upserts_vector_and_marks_sync_synced() -> None:
    repository = InMemoryInvoiceImportRepository()
    record, _ = repository.create_or_reuse_product_sync(build_sync_record_input())
    worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        worker_id="worker-a",
    )

    result = worker.process_one()

    assert result.status == "synced"
    synced = repository.get_product_sync(record.id)
    assert synced is not None
    assert synced.sync_status == "synced"
    embeddings = repository.list_product_catalog_embeddings()
    assert len(embeddings) == 1
    assert embeddings[0].product_id == "prod_1"
    assert embeddings[0].embedding_model == record.embedding_model


def test_worker_failure_marks_row_failed_with_retry_metadata() -> None:
    repository = InMemoryInvoiceImportRepository()
    record, _ = repository.create_or_reuse_product_sync(build_sync_record_input())
    worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=FlakyEmbeddingClient(failures=1),
        worker_id="worker-a",
    )
    now = datetime(2026, 3, 20, 11, 0, tzinfo=timezone.utc)

    result = worker.process_one(now=now)

    assert result.status == "failed"
    failed = repository.get_product_sync(record.id)
    assert failed is not None
    assert failed.sync_status == "failed"
    assert failed.attempt_count == 1
    assert failed.last_error == "embedding unavailable"
    assert failed.next_retry_at == now + timedelta(seconds=30)
    assert repository.list_product_catalog_embeddings() == []


def test_retrying_failed_snapshot_reuses_same_vector_row() -> None:
    repository = InMemoryInvoiceImportRepository()
    record, _ = repository.create_or_reuse_product_sync(build_sync_record_input())
    worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=FlakyEmbeddingClient(failures=1),
        worker_id="worker-a",
    )
    first_now = datetime(2026, 3, 20, 11, 0, tzinfo=timezone.utc)
    retry_now = first_now + timedelta(seconds=31)

    first = worker.process_one(now=first_now)
    second = worker.process_one(now=retry_now)

    assert first.status == "failed"
    assert second.status == "synced"
    embeddings = repository.list_product_catalog_embeddings()
    assert len(embeddings) == 1
    assert repository.get_product_sync(record.id) is not None
    assert repository.get_product_sync(record.id).sync_status == "synced"


def test_retrieval_returns_expected_product_in_top_5() -> None:
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    seed_synced_product(
        repository,
        product_id="prod_water",
        name="Mineral Water",
        barcode="999999",
        category="Beverages",
        uom="bottle",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )

    result = retrieval.query("need greek yogurt for order", top_k=5)

    assert result.embedding_model == "text-embedding-3-small"
    assert result.matches
    assert result.matches[0].product_id == "prod_yogurt"
    assert "Greek Yogurt" in result.matches[0].embedding_text


def test_evaluator_reports_top_1_and_top_5_hit_rates() -> None:
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    seed_synced_product(
        repository,
        product_id="prod_juice",
        name="Orange Juice",
        barcode="654321",
        category="Beverages",
        uom="carton",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )
    evaluator = CatalogRagEvaluator(retrieval)

    result = evaluator.evaluate(
        [
            CatalogEvalCase(
                query="yogurt from metro",
                expected_product_id="prod_yogurt",
            ),
            CatalogEvalCase(
                query="orange juice carton",
                expected_product_id="prod_juice",
            ),
        ]
    )

    assert result.total_queries == 2
    assert result.top_1_hits == 2
    assert result.top_5_hits == 2
    assert result.top_1_hit_rate == 1.0
    assert result.top_5_hit_rate == 1.0


def test_embedding_client_requires_api_key_when_not_in_mock_mode() -> None:
    client = OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=False, openai_api_key=None))

    with pytest.raises(ValueError, match="OpenAI embedding client not initialized"):
        client.embed(model="text-embedding-3-small", text="hello")


def test_api_rag_endpoints_use_app_owned_repository() -> None:
    from pydantic import SecretStr
    config = InvoiceConfig(
        _env_file=None,
        mock=True,
        catalog_sync_enabled=True,
        internal_api_keys=SecretStr("test-internal-key"),
    )
    resources = build_app_resources(config)
    app_instance = create_app(resources=resources)
    payload = InvoiceImportRequest.model_validate(
        {
            "invoice_meta": {
                "supplier": "METRO",
                "invoice_number": "INV-API-RAG",
                "date": "2026-03-20",
            },
            "rows": [
                {
                    "row_id": "r1",
                    "name": "Greek Yogurt",
                    "barcode": "123456",
                    "quantity": 2,
                    "line_total_lei": 40.0,
                    "weight_kg": 0.5,
                }
            ],
        }
    )
    service = InvoiceImportService(
        config=config,
        repository=resources.import_repository,
        catalog_sync_producer=resources.catalog_sync_producer,
    )
    service.import_rows(payload, idempotency_key="idem-api-rag")

    with TestClient(app_instance) as client:
        sync_response = client.post(
            "/internal/rag/sync-pending?limit=10",
            headers={"Authorization": "Bearer test-internal-key"},
        )
        query_response = client.post(
            "/internal/rag/query",
            json={"query": "greek yogurt order", "top_k": 5},
            headers={"Authorization": "Bearer test-internal-key"},
        )
        status_response = client.get(
            "/internal/rag/status",
            headers={"Authorization": "Bearer test-internal-key"},
        )

    assert sync_response.status_code == 200
    assert query_response.status_code == 200
    assert status_response.status_code == 200
    assert sync_response.json()["processed"] == 1
    assert query_response.json()["matches"][0]["product_id"] == "prod_1"
    assert status_response.json()["counts"]["synced"] == 1


def test_api_rag_import_endpoint_runs_import_and_sync() -> None:
    from pydantic import SecretStr
    config = InvoiceConfig(
        _env_file=None,
        mock=True,
        catalog_sync_enabled=True,
        internal_api_keys=SecretStr("test-internal-key"),
    )
    resources = build_app_resources(config)
    app_instance = create_app(resources=resources)

    with TestClient(app_instance) as client:
        response = client.post(
            "/internal/rag/import",
            json={
                "idempotency_key": "idem-api-import",
                "payload": {
                    "invoice_meta": {
                        "supplier": "METRO",
                        "invoice_number": "INV-API-IMPORT",
                        "date": "2026-03-20",
                    },
                    "rows": [
                        {
                            "row_id": "r1",
                            "name": "Greek Yogurt",
                            "barcode": "123456",
                            "quantity": 2,
                            "line_total_lei": 40.0,
                            "weight_kg": 0.5,
                        }
                    ],
                },
                "sync_after_import": True,
                "sync_limit": 10,
            },
            headers={"Authorization": "Bearer test-internal-key"},
        )
        query_response = client.post(
            "/internal/rag/query",
            json={"query": "greek yogurt order", "top_k": 5},
            headers={"Authorization": "Bearer test-internal-key"},
        )

    assert response.status_code == 200
    assert response.json()["import"]["summary"]["created_count"] == 1
    assert response.json()["sync"]["processed"] == 1
    assert query_response.status_code == 200
    assert query_response.json()["matches"][0]["product_id"] == "prod_1"


def test_api_rag_eval_endpoint_returns_metrics() -> None:
    from pydantic import SecretStr
    config = InvoiceConfig(
        _env_file=None,
        mock=True,
        catalog_sync_enabled=True,
        internal_api_keys=SecretStr("test-internal-key"),
    )
    resources = build_app_resources(config)
    app_instance = create_app(resources=resources)
    service = InvoiceImportService(
        config=config,
        repository=resources.import_repository,
        catalog_sync_producer=resources.catalog_sync_producer,
    )
    service.import_rows(
        InvoiceImportRequest.model_validate(
            {
                "invoice_meta": {
                    "supplier": "METRO",
                    "invoice_number": "INV-EVAL",
                    "date": "2026-03-20",
                },
                "rows": [
                    {
                        "row_id": "r1",
                        "name": "Greek Yogurt",
                        "barcode": "123456",
                        "quantity": 2,
                        "line_total_lei": 40.0,
                        "weight_kg": 0.5,
                    }
                ],
            }
        ),
        idempotency_key="idem-eval-test",
    )

    with TestClient(app_instance) as client:
        client.post(
            "/internal/rag/sync-pending?limit=10",
            headers={"Authorization": "Bearer test-internal-key"},
        )
        eval_response = client.post(
            "/internal/rag/eval",
            json={
                "cases": [
                    {"query": "greek yogurt order", "expected_product_id": "prod_1"}
                ]
            },
            headers={"Authorization": "Bearer test-internal-key"},
        )

    assert eval_response.status_code == 200
    body = eval_response.json()
    assert body["total_queries"] == 1
    assert body["top_1_hits"] == 1
    assert body["top_1_hit_rate"] == 1.0


def test_cli_rag_query_and_eval_surfaces_return_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    repository = _get_cli_rag_resources(mock=True).import_repository
    repository.reset()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    fixture_path = tmp_path / "rag_queries.json"
    fixture_path.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query": "greek yogurt order",
                        "expected_product_id": "prod_yogurt",
                    }
                ]
            }
        )
    )

    query_result = runner.invoke(app, ["rag", "query", "greek yogurt order", "--mock"])
    eval_result = runner.invoke(app, ["rag", "eval", str(fixture_path), "--mock"])

    assert query_result.exit_code == 0
    assert eval_result.exit_code == 0
    query_payload = json.loads(query_result.output)
    eval_payload = json.loads(eval_result.output)
    assert query_payload["matches"][0]["product_id"] == "prod_yogurt"
    assert eval_payload["top_1_hits"] == 1


def test_cli_rag_status_reports_counts_for_operational_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    repository = _get_cli_rag_resources(mock=True).import_repository
    repository.reset()
    repository.create_or_reuse_product_sync(build_sync_record_input(product_id="prod_pending"))
    failing_worker = CatalogSyncWorker(
        repository=repository,
        embedding_client=FlakyEmbeddingClient(failures=2),
        worker_id="worker-failing",
    )
    failing_worker.process_one(now=datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc))
    failing_worker.process_one(now=datetime(2026, 3, 20, 12, 1, tzinfo=timezone.utc))

    result = runner.invoke(app, ["rag", "status", "--mock"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["counts"]["failed"] == 1
    assert payload["counts"]["pending"] == 0
    assert payload["repeated_failures"][0]["attempt_count"] == 2


def test_cli_rag_ingest_invoice_runs_extract_import_sync_and_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    resources = _get_cli_rag_resources(mock=True, enable_catalog_sync=True)
    repository = resources.import_repository
    repository.reset()

    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock pdf\n")

    monkeypatch.setattr(
        cli_module,
        "_extract_single",
        lambda *args, **kwargs: InvoiceData(
            supplier="METRO",
            invoice_number="INV-CLI-RAG",
            date="2026-03-20",
            total_amount=40.0,
            currency="RON",
            products=[
                Product(
                    raw_code="123456",
                    name="Greek Yogurt",
                    quantity=2,
                    unit_price=20.0,
                    total_price=40.0,
                    confidence_score=0.98,
                    row_id="row-1",
                    weight_kg_candidate=0.5,
                    uom="bucket",
                )
            ],
        ),
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "ingest-invoice",
            str(pdf_path),
            "--mock",
            "--json",
            "--query",
            "greek yogurt order",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["invoice"]["invoice_number"] == "INV-CLI-RAG"
    assert payload["import"]["summary"]["created_count"] == 1
    assert payload["sync"]["processed"] == 1
    assert payload["query"]["matches"][0]["product_id"] == "prod_1"
    assert len(repository.list_product_catalog_embeddings()) == 1


def test_build_import_request_from_invoice_applies_default_weight_to_missing_rows() -> None:
    invoice = InvoiceData(
        supplier="METRO",
        invoice_number="INV-WEIGHT",
        date="2026-03-20",
        total_amount=60.0,
        currency="RON",
        products=[
            Product(
                raw_code="123456",
                name="Greek Yogurt",
                quantity=2,
                unit_price=20.0,
                total_price=40.0,
                confidence_score=0.98,
                row_id="row-1",
                weight_kg_candidate=None,
            ),
            Product(
                raw_code="654321",
                name="Mineral Water",
                quantity=1,
                unit_price=20.0,
                total_price=20.0,
                confidence_score=0.97,
                row_id="row-2",
                weight_kg_candidate=0.75,
            ),
        ],
    )

    request = _build_import_request_from_invoice(invoice, default_weight_kg=0.5)

    assert request.rows[0].weight_kg == 0.5
    assert request.rows[1].weight_kg == 0.75


def test_cli_rag_ingest_invoice_supports_default_weight_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock pdf\n")

    monkeypatch.setattr(
        cli_module,
        "_extract_single",
        lambda *args, **kwargs: InvoiceData(
            supplier="MOCK SUPPLIER",
            invoice_number="MOCK-001",
            date="02-02-2026",
            total_amount=383.47,
            currency="MDL",
            products=[
                Product(
                    raw_code="4840167001399",
                    name="200G UNT CIOCOLATA JLC",
                    quantity=5,
                    unit_price=43.43,
                    total_price=217.15,
                    confidence_score=1.0,
                    weight_kg_candidate=None,
                ),
                Product(
                    raw_code="4840167002500",
                    name="CIOCOLATA ALBA 70% 200G",
                    quantity=4,
                    unit_price=41.58,
                    total_price=166.32,
                    confidence_score=1.0,
                    weight_kg_candidate=None,
                ),
            ],
        ),
    )

    result = runner.invoke(
        app,
        [
            "rag",
            "ingest-invoice",
            str(pdf_path),
            "--mock",
            "--json",
            "--idempotency-key",
            "cli-weight-override-test",
            "--default-weight-kg",
            "0.5",
            "--query",
            "ciocolata",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["default_weight_kg"] == 0.5
    assert payload["invoice"]["missing_weight_count"] == 2
    assert payload["import"]["import_status"] == "completed"
    assert payload["import"]["summary"]["created_count"] == 2
    assert payload["sync"]["processed"] == 2
    assert payload["query"]["matches"]


def test_cli_rag_ingest_invoice_defaults_to_redacted_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    resources = _get_cli_rag_resources(mock=True, enable_catalog_sync=True)
    resources.import_repository.reset()
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock pdf\n")

    monkeypatch.setattr(
        cli_module,
        "_extract_single",
        lambda *args, **kwargs: InvoiceData(
            supplier="METRO",
            invoice_number="INV-CLI-SUMMARY",
            date="2026-03-20",
            total_amount=40.0,
            currency="RON",
            products=[
                Product(
                    raw_code="123456",
                    name="Greek Yogurt",
                    quantity=2,
                    unit_price=20.0,
                    total_price=40.0,
                    confidence_score=0.98,
                    row_id="row-1",
                    weight_kg_candidate=0.5,
                    uom="bucket",
                )
            ],
        ),
    )

    result = runner.invoke(
        app,
        ["rag", "ingest-invoice", str(pdf_path), "--mock", "--query", "greek yogurt"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["invoice_number"] == "INV-CLI-SUMMARY"
    assert payload["created_count"] == 1
    assert payload["synced_count"] == 1
    assert "invoice" not in payload
    assert "import" not in payload
    assert payload["top_match_product_ids"] == ["prod_1"]


def test_cli_rag_resources_cache_key_includes_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_module._CLI_RAG_RESOURCES = None
    cli_module._CLI_RAG_RESOURCES_KEY = None
    seen_backends: list[str] = []

    def fake_get_config() -> InvoiceConfig:
        backend = "memory" if not seen_backends else "supabase"
        return InvoiceConfig(
            _env_file=None,
            mock=True,
            catalog_sync_enabled=True,
            import_repository_backend=backend,
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
        )

    def fake_build_app_resources(config: InvoiceConfig):
        seen_backends.append(config.import_repository_backend)
        return build_app_resources(
            config.model_copy(
                update={
                    "import_repository_backend": "memory",
                    "catalog_sync_enabled": True,
                }
            )
        )

    monkeypatch.setattr(cli_module, "get_config_unvalidated", fake_get_config)
    monkeypatch.setattr(cli_module, "build_app_resources", fake_build_app_resources)

    first = _get_cli_rag_resources(mock=True, enable_catalog_sync=True)
    second = _get_cli_rag_resources(mock=True, enable_catalog_sync=True)

    assert isinstance(first.import_repository, InMemoryInvoiceImportRepository)
    assert isinstance(second.import_repository, InMemoryInvoiceImportRepository)
    assert seen_backends == ["memory", "supabase"]
    cli_module._CLI_RAG_RESOURCES = None
    cli_module._CLI_RAG_RESOURCES_KEY = None

def test_cli_rag_ingest_invoice_rejects_query_without_sync(tmp_path: Path) -> None:
    pdf_path = tmp_path / "invoice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock pdf\n")

    result = runner.invoke(
        app,
        [
            "rag",
            "ingest-invoice",
            str(pdf_path),
            "--mock",
            "--no-sync",
            "--query",
            "greek yogurt order",
        ],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.exception.code == 2
    normalized_output = strip_ansi(result.output)
    assert "Invalid value:" in normalized_output
    assert "requires --sync" in normalized_output


# ---------------------------------------------------------------------------
# Hybrid search tests
# ---------------------------------------------------------------------------


def test_lexical_search_finds_product_by_exact_barcode() -> None:
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="8001480015630",
        category="Dairy",
        uom="bucket",
    )
    seed_synced_product(
        repository,
        product_id="prod_water",
        name="Mineral Water",
        barcode="999999",
        category="Beverages",
        uom="bottle",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )

    result = retrieval.query("8001480015630", top_k=5, mode="lexical")

    assert result.search_mode == "lexical"
    assert result.matches
    assert result.matches[0].product_id == "prod_yogurt"


def test_lexical_search_returns_empty_for_no_term_match() -> None:
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )

    result = retrieval.query("xyzzy-no-match-term", top_k=5, mode="lexical")

    assert result.search_mode == "lexical"
    assert result.matches == []


def test_rrf_merge_combines_ranked_results() -> None:
    def _match(product_id: str, score: float) -> ProductCatalogEmbeddingMatch:
        return ProductCatalogEmbeddingMatch(
            product_id=product_id,
            product_snapshot_hash="hash",
            embedding_model="text-embedding-3-small",
            embedding_text=product_id,
            metadata={},
            score=score,
        )

    semantic = [_match("a", 0.95), _match("b", 0.80), _match("c", 0.60)]
    lexical = [_match("b", 0.90), _match("d", 0.70), _match("a", 0.50)]

    merged = rrf_merge(semantic, lexical, k=60, top_k=4)

    ids = [m.product_id for m in merged]
    # "b" appears in both lists at rank 2 (semantic) and rank 1 (lexical) → highest RRF
    # "a" appears in both lists at rank 1 (semantic) and rank 3 (lexical)
    assert ids[0] in {"a", "b"}
    assert "b" in ids
    assert "a" in ids
    assert "d" in ids
    assert len(merged) == 4
    # scores are strictly RRF values, not original cosine scores
    assert all(m.score < 1.0 for m in merged)


def test_rrf_merge_deduplicates_by_product_id() -> None:
    def _match(product_id: str) -> ProductCatalogEmbeddingMatch:
        return ProductCatalogEmbeddingMatch(
            product_id=product_id,
            product_snapshot_hash="hash",
            embedding_model="text-embedding-3-small",
            embedding_text=product_id,
            metadata={},
            score=0.5,
        )

    semantic = [_match("x"), _match("y")]
    lexical = [_match("x"), _match("z")]

    merged = rrf_merge(semantic, lexical, k=60, top_k=5)

    product_ids = [m.product_id for m in merged]
    assert len(product_ids) == len(set(product_ids))
    assert "x" in product_ids


def test_hybrid_search_surface_returns_search_mode_field() -> None:
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )

    hybrid_result = retrieval.query("greek yogurt", top_k=5, mode="hybrid")
    semantic_result = retrieval.query("greek yogurt", top_k=5, mode="semantic")
    lexical_result = retrieval.query("greek yogurt", top_k=5, mode="lexical")

    assert hybrid_result.search_mode == "hybrid"
    assert semantic_result.search_mode == "semantic"
    assert lexical_result.search_mode == "lexical"


def test_api_rag_query_accepts_search_mode_field() -> None:
    from pydantic import SecretStr
    config = InvoiceConfig(
        _env_file=None,
        mock=True,
        catalog_sync_enabled=True,
        internal_api_keys=SecretStr("test-internal-key"),
    )
    resources = build_app_resources(config)
    app_instance = create_app(resources=resources)
    service = InvoiceImportService(
        config=config,
        repository=resources.import_repository,
        catalog_sync_producer=resources.catalog_sync_producer,
    )
    service.import_rows(
        InvoiceImportRequest.model_validate(
            {
                "invoice_meta": {
                    "supplier": "METRO",
                    "invoice_number": "INV-HYBRID",
                    "date": "2026-03-20",
                },
                "rows": [
                    {
                        "row_id": "r1",
                        "name": "Greek Yogurt",
                        "barcode": "123456",
                        "quantity": 2,
                        "line_total_lei": 40.0,
                        "weight_kg": 0.5,
                    }
                ],
            }
        ),
        idempotency_key="idem-hybrid-mode",
    )

    with TestClient(app_instance) as client:
        client.post(
            "/internal/rag/sync-pending?limit=10",
            headers={"Authorization": "Bearer test-internal-key"},
        )
        hybrid = client.post(
            "/internal/rag/query",
            json={"query": "greek yogurt", "top_k": 5, "search_mode": "hybrid"},
            headers={"Authorization": "Bearer test-internal-key"},
        )
        lexical = client.post(
            "/internal/rag/query",
            json={"query": "123456", "top_k": 5, "search_mode": "lexical"},
            headers={"Authorization": "Bearer test-internal-key"},
        )

    assert hybrid.status_code == 200
    assert hybrid.json()["search_mode"] == "hybrid"
    assert lexical.status_code == 200
    assert lexical.json()["search_mode"] == "lexical"
    assert lexical.json()["matches"][0]["product_id"] == "prod_1"


def test_cli_rag_query_mode_flag_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_cli_for_memory_backend(monkeypatch)
    repository = _get_cli_rag_resources(mock=True).import_repository
    repository.reset()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )

    result = runner.invoke(
        app, ["rag", "query", "Greek Yogurt", "--mock", "--mode", "lexical"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["search_mode"] == "lexical"


def _make_retrieval(repository: InMemoryInvoiceImportRepository) -> CatalogRetrievalService:
    return CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
    )


def test_evaluator_supports_mode_param() -> None:
    """evaluate() with explicit mode records the mode in each case result."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(repository, product_id="prod_yogurt", name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket")
    evaluator = CatalogRagEvaluator(_make_retrieval(repository))

    result = evaluator.evaluate(
        [CatalogEvalCase(query="yogurt from metro", expected_product_id="prod_yogurt")],
        mode="semantic",
    )

    assert result.cases[0]["search_mode"] == "semantic"


def test_evaluator_expected_name_substring_match() -> None:
    """expected_name triggers a case-insensitive embedding_text substring match."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(repository, product_id="prod_yogurt", name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket")
    evaluator = CatalogRagEvaluator(_make_retrieval(repository))

    result = evaluator.evaluate(
        [CatalogEvalCase(query="yogurt", expected_name="greek yogurt")],
        mode="hybrid",
    )

    assert result.top_5_hits == 1


def test_evaluator_expected_name_case_insensitive() -> None:
    """expected_name match is case-insensitive."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(repository, product_id="p1", name="Mineral Water", barcode="999", category="Bev", uom="bottle")
    evaluator = CatalogRagEvaluator(_make_retrieval(repository))

    result = evaluator.evaluate(
        [CatalogEvalCase(query="water", expected_name="MINERAL WATER")],
        mode="semantic",
    )

    assert result.top_5_hits == 1


def test_eval_case_requires_at_least_one_identifier() -> None:
    """CatalogEvalCase raises ValueError when both expected fields are empty."""
    with pytest.raises(ValueError, match="expected_product_id or expected_name"):
        CatalogEvalCase(query="anything")


def test_evaluate_all_modes_returns_comparison_for_all_three() -> None:
    """evaluate_all_modes() populates semantic, lexical, and hybrid results."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(repository, product_id="prod_yogurt", name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket")
    evaluator = CatalogRagEvaluator(_make_retrieval(repository))
    cases = [CatalogEvalCase(query="greek yogurt", expected_product_id="prod_yogurt")]

    comparison = evaluator.evaluate_all_modes(cases)

    assert comparison.semantic.total_queries == 1
    assert comparison.lexical.total_queries == 1
    assert comparison.hybrid.total_queries == 1
    assert comparison.semantic.cases[0]["search_mode"] == "semantic"
    assert comparison.lexical.cases[0]["search_mode"] == "lexical"
    assert comparison.hybrid.cases[0]["search_mode"] == "hybrid"


def test_serialize_mode_comparison_structure() -> None:
    """serialize_mode_comparison produces summary + by_mode sections."""
    from invproc.rag import (
        CatalogEvalResult,
        CatalogModeComparisonResult,
        serialize_mode_comparison,
    )

    empty = CatalogEvalResult(total_queries=0, top_1_hits=0, top_5_hits=0, cases=[])
    comparison = CatalogModeComparisonResult(semantic=empty, lexical=empty, hybrid=empty)

    out = serialize_mode_comparison(comparison)

    assert set(out["summary"]) == {"semantic", "lexical", "hybrid"}
    assert set(out["by_mode"]) == {"semantic", "lexical", "hybrid"}
    for mode_key in ("semantic", "lexical", "hybrid"):
        assert "top_1_hit_rate" in out["summary"][mode_key]


def test_cli_eval_all_modes_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--all-modes flag triggers multi-mode comparison output."""
    configure_cli_for_memory_backend(monkeypatch)
    repository = _get_cli_rag_resources(mock=True).import_repository
    repository.reset()
    seed_synced_product(repository, product_id="prod_yogurt", name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket")

    fixture = tmp_path / "queries.json"
    fixture.write_text(json.dumps({"queries": [{"query": "yogurt", "expected_product_id": "prod_yogurt"}]}))

    result = runner.invoke(app, ["rag", "eval", str(fixture), "--mock", "--all-modes"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "summary" in payload
    assert set(payload["summary"]) == {"semantic", "lexical", "hybrid"}


def test_cli_eval_mode_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--mode flag selects a specific search mode for single-mode eval."""
    configure_cli_for_memory_backend(monkeypatch)
    repository = _get_cli_rag_resources(mock=True).import_repository
    repository.reset()
    seed_synced_product(repository, product_id="prod_yogurt", name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket")

    fixture = tmp_path / "queries.json"
    fixture.write_text(json.dumps({"queries": [{"query": "yogurt", "expected_product_id": "prod_yogurt"}]}))

    result = runner.invoke(app, ["rag", "eval", str(fixture), "--mock", "--mode", "lexical"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cases"][0]["search_mode"] == "lexical"


def test_cli_eval_invalid_mode_exits_with_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An invalid --mode value exits with code 1."""
    configure_cli_for_memory_backend(monkeypatch)
    fixture = tmp_path / "queries.json"
    fixture.write_text(json.dumps({"queries": [{"query": "yogurt", "expected_product_id": "prod_yogurt"}]}))

    result = runner.invoke(app, ["rag", "eval", str(fixture), "--mock", "--mode", "bad-mode"])

    assert result.exit_code == 1


def test_match_threshold_filters_low_score_results() -> None:
    """A threshold above all RRF scores should produce an empty match list."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    # RRF scores are always < 1.0 (they are rank-based fractions).
    # Setting threshold=1.0 guarantees all matches are pruned.
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
        match_threshold=1.0,
    )

    result = retrieval.query("greek yogurt", top_k=5)

    assert result.match_threshold == 1.0
    assert result.matches == []


def test_match_threshold_zero_returns_all_results() -> None:
    """Default threshold of 0.0 must not filter any matches."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
        match_threshold=0.0,
    )

    result = retrieval.query("greek yogurt", top_k=5)

    assert result.match_threshold == 0.0
    assert result.matches  # at least one match when threshold is open


def test_per_call_threshold_overrides_service_default() -> None:
    """A per-call threshold keyword overrides the service-level default."""
    repository = InMemoryInvoiceImportRepository()
    seed_synced_product(
        repository,
        product_id="prod_yogurt",
        name="Greek Yogurt",
        barcode="123456",
        category="Dairy",
        uom="bucket",
    )
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(InvoiceConfig(_env_file=None, mock=True)),
        default_embedding_model="text-embedding-3-small",
        match_threshold=0.0,
    )

    # Service default is 0.0 (open) but we override with 1.0 (prune all).
    result = retrieval.query("greek yogurt", top_k=5, match_threshold=1.0)

    assert result.match_threshold == 1.0
    assert result.matches == []


def test_serialize_query_result_includes_match_threshold() -> None:
    """serialize_query_result must expose match_threshold for API consumers."""
    from invproc.rag import CatalogQueryResult, CatalogRagMatch, serialize_query_result

    result = CatalogQueryResult(
        query="yogurt",
        embedding_model="text-embedding-3-small",
        top_k=5,
        search_mode="hybrid",
        match_threshold=0.05,
        matches=[
            CatalogRagMatch(
                product_id="p1",
                product_snapshot_hash="h1",
                embedding_model="text-embedding-3-small",
                score=0.1,
                metadata={},
                embedding_text="yogurt",
            )
        ],
    )

    serialized = serialize_query_result(result)

    assert serialized["match_threshold"] == 0.05
    assert len(serialized["matches"]) == 1


# ---------------------------------------------------------------------------
# Part A — Category / UOM propagation tests
# ---------------------------------------------------------------------------


def test_build_catalog_embedding_text_includes_category_when_set() -> None:
    """Category token appears in embedding text when category is non-null."""
    record = build_sync_record_input(
        name="Greek Yogurt", barcode="123456", category="Dairy", uom="bucket"
    )
    sync_record, _ = InMemoryInvoiceImportRepository().create_or_reuse_product_sync(record)

    assert build_catalog_embedding_text(sync_record) == "Greek Yogurt 123456 Dairy bucket"


def test_build_catalog_embedding_text_omits_null_category() -> None:
    """No category token when category is None."""
    record = build_sync_record_input(name="CEAI VERDE", barcode=None, category=None, uom="BUC")
    sync_record, _ = InMemoryInvoiceImportRepository().create_or_reuse_product_sync(record)

    assert build_catalog_embedding_text(sync_record) == "CEAI VERDE BUC"


def test_emit_product_sync_forwards_category_and_uom() -> None:
    """RepositoryCatalogSyncProducer populates category/uom in the sync row."""
    from invproc.catalog_sync import CatalogSyncContext, RepositoryCatalogSyncProducer

    repository = InMemoryInvoiceImportRepository()
    producer = RepositoryCatalogSyncProducer(
        repository=repository,
        embedding_model="text-embedding-3-small",
    )
    product = ProductRecord(
        product_id="prod_tea",
        barcode="111222333",
        name="CEAI VERDE",
        normalized_name="ceai verde",
        supplier="METRO",
        category="Beverages",
        uom="BUC",
    )
    upsert_input = UpsertProductInput(
        name="CEAI VERDE",
        barcode="111222333",
        supplier="METRO",
        price=1.5,
        price_50=2.0,
        price_70=2.5,
        price_100=3.0,
        markup=70,
        category="Beverages",
        uom="BUC",
    )
    result = producer.emit_product_sync(
        product=product,
        upsert_input=upsert_input,
        context=CatalogSyncContext(
            import_id="imp_1", source_row_id="row_1", invoice_number="INV-1"
        ),
    )

    assert result.record is not None
    assert result.record.category == "Beverages"
    assert result.record.uom == "BUC"


def test_emit_product_sync_different_hash_when_category_added() -> None:
    """Re-import with newly-available category produces a different snapshot hash
    and therefore a new pending sync row (re-embedding triggered)."""
    from invproc.catalog_sync import CatalogSyncContext, RepositoryCatalogSyncProducer

    repository = InMemoryInvoiceImportRepository()
    producer = RepositoryCatalogSyncProducer(
        repository=repository,
        embedding_model="text-embedding-3-small",
    )

    def _make_product(category: str | None) -> tuple[ProductRecord, UpsertProductInput]:
        product = ProductRecord(
            product_id="prod_milk",
            barcode="999000111",
            name="Lapte Integral",
            normalized_name="lapte integral",
            supplier="METRO",
            category=category,
            uom="L",
        )
        upsert = UpsertProductInput(
            name="Lapte Integral",
            barcode="999000111",
            supplier="METRO",
            price=1.0,
            price_50=1.3,
            price_70=1.5,
            price_100=1.8,
            markup=70,
            category=category,
            uom="L",
        )
        return product, upsert

    ctx = CatalogSyncContext(import_id="imp_1", source_row_id="r1", invoice_number="INV-1")

    # First import — no category yet
    p1, u1 = _make_product(None)
    r1 = producer.emit_product_sync(product=p1, upsert_input=u1, context=ctx)
    assert r1.created is True

    # Second import — same product, now category is known
    p2, u2 = _make_product("Dairy")
    r2 = producer.emit_product_sync(product=p2, upsert_input=u2, context=ctx)
    assert r2.created is True
    assert r1.record is not None and r2.record is not None
    assert r1.record.product_snapshot_hash != r2.record.product_snapshot_hash
    assert r2.record.category == "Dairy"


def test_build_import_request_forwards_category_and_suppresses_general() -> None:
    """CLI import builder forwards real categories and converts 'General' → None."""
    invoice = InvoiceData(
        supplier="METRO",
        invoice_number="INV-CAT",
        date="2026-03-22",
        total_amount=50.0,
        currency="RON",
        products=[
            Product(
                raw_code="111",
                name="HALVA ARAHIDE",
                quantity=1,
                unit_price=10.0,
                total_price=10.0,
                confidence_score=0.95,
                row_id="row-1",
                category_suggestion="Snacks",
                uom="BUC",
            ),
            Product(
                raw_code="222",
                name="PRODUS NECUNOSCUT",
                quantity=2,
                unit_price=5.0,
                total_price=10.0,
                confidence_score=0.80,
                row_id="row-2",
                category_suggestion="General",
                uom="KG",
            ),
            Product(
                raw_code="333",
                name="LAPTE INTEGRAL",
                quantity=3,
                unit_price=1.0,
                total_price=3.0,
                confidence_score=0.92,
                row_id="row-3",
                category_suggestion=None,
                uom=None,
            ),
        ],
    )

    request = _build_import_request_from_invoice(invoice)

    assert request.rows[0].category == "Snacks"
    assert request.rows[0].uom == "BUC"
    assert request.rows[1].category is None  # "General" suppressed → None
    assert request.rows[1].uom == "KG"
    assert request.rows[2].category is None
    assert request.rows[2].uom is None


# ---------------------------------------------------------------------------
# Part B — Expanded eval fixture catalog
# ---------------------------------------------------------------------------

_METRO_CATALOG = [
    # Dairy
    dict(product_id="prod_yogurt",      name="Greek Yogurt",             barcode="123456",      category="Dairy",     uom="bucket"),
    dict(product_id="prod_lapte_1l",    name="LAPTE INTEGRAL 1L",        barcode="4840123001",  category="Dairy",     uom="L"),
    dict(product_id="prod_lapte_2l",    name="LAPTE INTEGRAL 2L",        barcode="4840123002",  category="Dairy",     uom="L"),
    dict(product_id="prod_unt",         name="UNT CIOCOLATA JLC 200G",   barcode="4840167001399", category="Dairy",   uom="BUC"),
    dict(product_id="prod_smantana",    name="SMANTANA 20% 400G",        barcode="4840167003001", category="Dairy",   uom="BUC"),
    # Beverages
    dict(product_id="prod_juice",       name="Orange Juice",             barcode="654321",      category="Beverages", uom="carton"),
    dict(product_id="prod_water",       name="Mineral Water",            barcode="999999",      category="Beverages", uom="bottle"),
    dict(product_id="prod_ceai_verde",  name="CEAI VERDE RIOBA 25PL",    barcode="4820012682966", category="Beverages", uom="BUC"),
    dict(product_id="prod_ceai_zmeura", name="CEAI ZMEURA MENTA RIOBA",  barcode="4820012683001", category="Beverages", uom="BUC"),
    dict(product_id="prod_ceai_bebe",   name="CEAI MUSETEL BEBE 20PL",   barcode="4820000111222", category="Beverages", uom="BUC"),
    # Snacks / Sweets
    dict(product_id="prod_halva",       name="HALVA ARAHIDE 350G",       barcode="4841259001754", category="Snacks",  uom="BUC"),
    dict(product_id="prod_cioc_alba",   name="CIOCOLATA ALBA 70% 200G",  barcode="4840167002500", category="Snacks",  uom="BUC"),
    dict(product_id="prod_turta",       name="TURTA DULCE CU PRUNE",     barcode="4841259002001", category="Snacks",  uom="BUC"),
    # Cereale / Pantry
    dict(product_id="prod_sem_fl",      name="SEM FL 500G",              barcode="4841259003001", category="Cereale", uom="BUC"),
    dict(product_id="prod_orez",        name="OREZ ROTUND 1KG",          barcode="4841259004001", category="Cereale", uom="BUC"),
    # No-category products (the weak spot)
    dict(product_id="prod_no_cat_1",    name="MORCOVI BABY 400G",        barcode="4841000001001", category=None,      uom="BUC"),
    dict(product_id="prod_no_cat_2",    name="ROSII CHERRY 250G",        barcode="4841000002001", category=None,      uom="BUC"),
]


def seed_metro_catalog(repository: InMemoryInvoiceImportRepository) -> None:
    """Seed the full METRO catalog fixture into the in-memory repository."""
    for item in _METRO_CATALOG:
        seed_synced_product(
            repository,
            product_id=item["product_id"],
            name=item["name"],
            barcode=item["barcode"],
            category=item["category"],
            uom=item["uom"],
        )


def test_eval_fixture_covers_all_query_patterns(tmp_path: Path) -> None:
    """Smoke test: unit fixture loads cleanly and has sufficient cases."""
    fixture_path = Path(__file__).parent / "fixtures" / "rag_queries_unit.json"
    from invproc.rag import load_eval_cases
    cases = load_eval_cases(fixture_path)
    assert len(cases) >= 30, f"Expected ≥30 eval cases, got {len(cases)}"


def test_eval_fixture_notes_field_silently_ignored(tmp_path: Path) -> None:
    """load_eval_cases ignores unknown keys like notes and expected_fail."""
    import json
    from invproc.rag import CatalogEvalCase, load_eval_cases
    fixture = tmp_path / "test.json"
    fixture.write_text(json.dumps({
        "queries": [
            {
                "query": "test query",
                "expected_product_id": "prod_1",
                "notes": "this is a note",
                "expected_fail": True,
                "some_future_key": "ignored",
            }
        ]
    }))
    cases = load_eval_cases(fixture)
    assert len(cases) == 1
    assert cases[0] == CatalogEvalCase(query="test query", expected_product_id="prod_1")


def test_eval_metro_catalog_hybrid_top5_hit_rate(tmp_path: Path) -> None:
    """Hybrid search achieves ≥80% top-5 hit rate on the expanded fixture."""
    from invproc.rag import CatalogRagEvaluator, CatalogRetrievalService, load_eval_cases
    from invproc.config import InvoiceConfig

    repository = InMemoryInvoiceImportRepository()
    seed_metro_catalog(repository)

    config = InvoiceConfig(_env_file=None, mock=True)
    retrieval = CatalogRetrievalService(
        repository=repository,
        embedding_client=OpenAIEmbeddingClient(config),
        default_embedding_model=config.catalog_sync_embedding_model,
    )
    evaluator = CatalogRagEvaluator(retrieval)

    fixture_path = Path(__file__).parent / "fixtures" / "rag_queries_unit.json"
    cases = load_eval_cases(fixture_path)
    result = evaluator.evaluate(cases)

    assert result.top_5_hit_rate >= 0.80, (
        f"Hybrid top-5 hit rate {result.top_5_hit_rate:.0%} is below 80% threshold. "
        f"Failing cases: {[c for c in result.cases if not c['top_5_hit']]}"
    )
