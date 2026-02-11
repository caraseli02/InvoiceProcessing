"""Tests for invoice preview API contract (MVP simple mode)."""

import os

import pytest
from fastapi.testclient import TestClient

from invproc.api import app
from invproc.config import reload_config


@pytest.fixture(autouse=True)
def setup_test_config() -> None:
    os.environ["API_KEYS"] = "test-api-key"
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
    os.environ["MOCK"] = "true"
    os.environ["DEV_BYPASS_API_KEY"] = "false"
    reload_config()
    yield
    os.environ.pop("API_KEYS", None)
    os.environ.pop("ALLOWED_ORIGINS", None)
    os.environ.pop("MOCK", None)
    os.environ.pop("DEV_BYPASS_API_KEY", None)
    reload_config()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_preview_pricing_handles_missing_weight(client: TestClient) -> None:
    response = client.post(
        "/invoice/preview-pricing",
        json={
            "invoice_meta": {
                "supplier": "JLC",
                "invoice_number": "INV-1",
                "date": "2026-02-11",
            },
            "rows": [
                {
                    "row_id": "r1",
                    "name": "200G UNT CIOCOLATA JLC",
                    "barcode": "123",
                    "quantity": 10,
                    "line_total_lei": 200.0,
                    "weight_kg": 0.2,
                },
                {
                    "row_id": "r2",
                    "name": "Produs fara marime",
                    "barcode": None,
                    "quantity": 4,
                    "line_total_lei": 120.0,
                    "weight_kg": None,
                },
            ],
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["ok_count"] == 1
    assert data["summary"]["needs_input_count"] == 1
    assert data["rows"][1]["messages"] == ["MISSING_WEIGHT"]


def test_preview_pricing_returns_liquid_warning(client: TestClient) -> None:
    response = client.post(
        "/invoice/preview-pricing",
        json={
            "invoice_meta": {"supplier": "JLC", "invoice_number": "INV-WARN"},
            "rows": [
                {
                    "row_id": "r-liquid",
                    "name": "6x0,5L Apa minerala",
                    "barcode": None,
                    "quantity": 1,
                    "line_total_lei": 30.0,
                    "weight_kg": 3.0,
                }
            ],
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["status"] == "ok"
    assert "LIQUID_DENSITY_ASSUMPTION" in row["warnings"]


def test_preview_pricing_with_valid_bearer_auth(client: TestClient) -> None:
    response = client.post(
        "/invoice/preview-pricing",
        json={
            "invoice_meta": {"supplier": "JLC", "invoice_number": "INV-BEARER"},
            "rows": [
                {
                    "row_id": "r-bearer",
                    "name": "200G UNT CIOCOLATA JLC",
                    "barcode": None,
                    "quantity": 2,
                    "line_total_lei": 40.0,
                    "weight_kg": 0.2,
                }
            ],
        },
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200


def test_preview_pricing_with_invalid_bearer_auth(client: TestClient) -> None:
    response = client.post(
        "/invoice/preview-pricing",
        json={
            "invoice_meta": {"supplier": "JLC", "invoice_number": "INV-BEARER-BAD"},
            "rows": [
                {
                    "row_id": "r-bearer-bad",
                    "name": "200G UNT CIOCOLATA JLC",
                    "barcode": None,
                    "quantity": 2,
                    "line_total_lei": 40.0,
                    "weight_kg": 0.2,
                }
            ],
        },
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401


def test_import_endpoint_not_available_in_mvp_simple_mode(client: TestClient) -> None:
    response = client.post(
        "/invoice/import",
        json={
            "invoice_meta": {"supplier": "JLC", "invoice_number": "INV-2"},
            "rows": [
                {
                    "row_id": "r1",
                    "name": "200G UNT CIOCOLATA JLC",
                    "barcode": "123",
                    "quantity": 10,
                    "line_total_lei": 200.0,
                    "weight_kg": 0.2,
                }
            ],
        },
        headers={"X-API-Key": "test-api-key"},
    )

    assert response.status_code == 404
