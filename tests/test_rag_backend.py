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
