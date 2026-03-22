"""Tests for Phase 2 catalog sync producer behavior."""

from __future__ import annotations

import logging

import pytest

from invproc.api import build_app_resources
from invproc.catalog_sync import (
    NoopCatalogSyncProducer,
    CatalogSyncContext,
    CatalogSyncResult,
    RepositoryCatalogSyncProducer,
    build_product_snapshot_hash,
)
from invproc.config import InvoiceConfig
from invproc.import_service import InvoiceImportService
from invproc.models import InvoiceImportRequest
from invproc.repositories.base import ProductRecord, UpsertProductInput
from invproc.repositories.memory import InMemoryInvoiceImportRepository


class FailingCatalogSyncProducer:
    """Test double that fails during sync emission."""

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
        raise RuntimeError("sync unavailable")


@pytest.fixture
def repository() -> InMemoryInvoiceImportRepository:
    return InMemoryInvoiceImportRepository()


@pytest.fixture
def config() -> InvoiceConfig:
    return InvoiceConfig(_env_file=None, mock=True, catalog_sync_enabled=True)


@pytest.fixture
def payload() -> InvoiceImportRequest:
    return InvoiceImportRequest.model_validate(
        {
            "invoice_meta": {
                "supplier": "METRO",
                "invoice_number": "INV-200",
                "date": "2026-03-20",
            },
            "rows": [
                {
                    "row_id": "r1",
                    "name": " Greek Yogurt  ",
                    "barcode": "123456",
                    "quantity": 2,
                    "line_total_lei": 40.0,
                    "weight_kg": 0.5,
                }
            ],
        }
    )


def build_service(
    *,
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
) -> InvoiceImportService:
    producer = RepositoryCatalogSyncProducer(
        repository,
        embedding_model=config.catalog_sync_embedding_model,
    )
    return InvoiceImportService(
        config=config,
        repository=repository,
        catalog_sync_producer=producer,
    )


def test_snapshot_hash_normalizes_whitespace() -> None:
    product = ProductRecord(
        product_id="prod_1",
        barcode=" 123456 ",
        name=" Greek Yogurt ",
        normalized_name="greek yogurt",
        supplier=" METRO ",
    )
    upsert_input = UpsertProductInput(
        name="Greek Yogurt",
        barcode="123456",
        supplier="METRO",
        price=1.0256,
        price_50=1.9885,
        price_70=2.2536,
        price_100=2.6513,
        markup=70,
    )

    first_hash = build_product_snapshot_hash(
        product=product,
        upsert_input=upsert_input,
        embedding_model="text-embedding-3-small",
        category=None,
        uom=None,
    )
    normalized_product = ProductRecord(
        product_id="prod_1",
        barcode="123456",
        name="Greek Yogurt",
        normalized_name="greek yogurt",
        supplier="METRO",
    )
    second_hash = build_product_snapshot_hash(
        product=normalized_product,
        upsert_input=upsert_input,
        embedding_model="text-embedding-3-small",
        category=None,
        uom=None,
    )

    assert first_hash == second_hash


def test_import_rows_emits_catalog_sync_for_successful_product(
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
) -> None:
    service = build_service(config=config, repository=repository)

    response = service.import_rows(payload, idempotency_key="idem-1")

    assert response.summary.created_count == 1
    sync_records = repository.list_product_sync_records()
    assert len(sync_records) == 1
    assert sync_records[0].product_id == response.rows[0].product_id
    assert sync_records[0].source_row_id == "r1"
    assert sync_records[0].sync_status == "pending"
    assert sync_records[0].embedding_model == config.catalog_sync_embedding_model


def test_import_rows_skips_duplicate_sync_for_unchanged_reimport(
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
) -> None:
    service = build_service(config=config, repository=repository)

    first_response = service.import_rows(payload, idempotency_key="idem-1")
    second_payload = InvoiceImportRequest.model_validate(payload.model_dump(mode="json"))
    second_response = service.import_rows(second_payload, idempotency_key="idem-2")

    sync_records = repository.list_product_sync_records()
    assert len(sync_records) == 1
    assert first_response.rows[0].product_id == second_response.rows[0].product_id


def test_import_rows_idempotent_replay_does_not_emit_extra_sync_work(
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
) -> None:
    service = build_service(config=config, repository=repository)

    first_response = service.import_rows(payload, idempotency_key="idem-1")
    replay_response = service.import_rows(payload, idempotency_key="idem-1")

    assert replay_response.model_dump(mode="json") == first_response.model_dump(mode="json")
    assert len(repository.list_product_sync_records()) == 1


def test_import_rows_partial_failure_only_syncs_successful_rows(
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
) -> None:
    service = build_service(config=config, repository=repository)
    payload = InvoiceImportRequest.model_validate(
        {
            "invoice_meta": {
                "supplier": "METRO",
                "invoice_number": "INV-201",
                "date": "2026-03-20",
            },
            "rows": [
                {
                    "row_id": "ok-row",
                    "name": "Greek Yogurt",
                    "barcode": "123456",
                    "quantity": 2,
                    "line_total_lei": 40.0,
                    "weight_kg": 0.5,
                },
                {
                    "row_id": "bad-row",
                    "name": "Missing weight",
                    "barcode": None,
                    "quantity": 1,
                    "line_total_lei": 5.0,
                    "weight_kg": None,
                },
            ],
        }
    )

    response = service.import_rows(payload, idempotency_key="idem-partial")

    assert response.import_status == "partial_failed"
    assert len(repository.list_product_sync_records()) == 1
    assert repository.list_product_sync_records()[0].source_row_id == "ok-row"


def test_import_rows_fail_open_logs_and_preserves_import_success(
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = InvoiceConfig(_env_file=None, mock=True)
    service = InvoiceImportService(
        config=config,
        repository=repository,
        catalog_sync_producer=FailingCatalogSyncProducer(),
    )

    with caplog.at_level(logging.ERROR):
        response = service.import_rows(payload, idempotency_key="idem-fail-open")

    assert response.summary.created_count == 1
    assert len(repository.list_product_sync_records()) == 0
    assert "Catalog sync emission failed" in caplog.text


def test_import_rows_sync_failure_still_allows_idempotent_replay_without_new_side_effects(
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
) -> None:
    config = InvoiceConfig(_env_file=None, mock=True)
    service = InvoiceImportService(
        config=config,
        repository=repository,
        catalog_sync_producer=FailingCatalogSyncProducer(),
    )

    first_response = service.import_rows(payload, idempotency_key="idem-fail-open-replay")
    replay_response = service.import_rows(payload, idempotency_key="idem-fail-open-replay")

    assert first_response.model_dump(mode="json") == replay_response.model_dump(mode="json")
    assert len(repository.list_product_sync_records()) == 0
    assert len(repository._movements) == 1


def test_import_rows_with_noop_producer_keeps_import_behavior_unchanged(
    config: InvoiceConfig,
    repository: InMemoryInvoiceImportRepository,
    payload: InvoiceImportRequest,
) -> None:
    service = InvoiceImportService(config=config, repository=repository)

    response = service.import_rows(payload, idempotency_key="idem-noop")

    assert response.summary.created_count == 1
    assert len(repository.list_product_sync_records()) == 0


def test_build_app_resources_wires_repository_backed_sync_producer_when_enabled() -> None:
    config = InvoiceConfig(_env_file=None, mock=True, catalog_sync_enabled=True)

    resources = build_app_resources(config)

    assert isinstance(resources.catalog_sync_producer, RepositoryCatalogSyncProducer)
    assert resources.catalog_sync_producer.repository is resources.import_repository
    assert resources.catalog_sync_producer.embedding_model == config.catalog_sync_embedding_model


def test_build_app_resources_uses_noop_sync_producer_when_disabled() -> None:
    config = InvoiceConfig(_env_file=None, mock=True, catalog_sync_enabled=False)

    resources = build_app_resources(config)

    assert isinstance(resources.catalog_sync_producer, NoopCatalogSyncProducer)
