"""FastAPI endpoint tests."""

import os
import pytest
from fastapi.testclient import TestClient

from invproc.api import app, load_api_keys
from invproc.config import reload_config


@pytest.fixture(autouse=True)
def setup_test_config():
    """Setup test configuration with API keys."""
    os.environ["API_KEYS"] = "test-api-key"
    os.environ["MOCK"] = "true"
    reload_config()
    load_api_keys()
    yield
    os.environ.pop("API_KEYS", None)
    os.environ.pop("MOCK", None)
    reload_config()
    load_api_keys()


@pytest.fixture
def client():
    """Create test client for each test."""
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_extract_without_auth(client):
    """Test extraction without API key."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract", files={"file": ("test.pdf", f, "application/pdf")}
        )
    assert response.status_code == 401


def test_extract_with_invalid_auth(client):
    """Test extraction with invalid API key."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"X-API-Key": "invalid-key"},
        )
    assert response.status_code == 401


def test_extract_with_valid_auth(client):
    """Test extraction with valid API key."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"X-API-Key": "test-api-key"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "supplier" in data
    assert "products" in data
    assert len(data["products"]) > 0
