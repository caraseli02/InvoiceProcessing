"""FastAPI endpoint tests."""

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from invproc.api import app
from invproc.config import reload_config


@pytest.fixture(autouse=True)
def setup_test_config():
    """Setup test configuration with API keys."""
    os.environ["API_KEYS"] = "test-api-key"
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
    os.environ["MOCK"] = "true"
    reload_config()
    yield
    os.environ.pop("API_KEYS", None)
    os.environ.pop("ALLOWED_ORIGINS", None)
    os.environ.pop("MOCK", None)
    reload_config()


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


def test_extract_invalid_file_type(client):
    """Test extraction with non-PDF file."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.txt", f, "text/plain")},
            headers={"X-API-Key": "test-api-key"},
        )
    assert response.status_code == 400
    assert "PDF files are supported" in response.json()["detail"]


def test_extract_missing_file_extension(client):
    """Test extraction with filename lacking .pdf extension."""
    from io import BytesIO

    pdf_content = Path("test_invoices/invoice-test.pdf").read_bytes()

    response = client.post(
        "/extract",
        files={"file": ("invoice", BytesIO(pdf_content), "application/pdf")},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 400


def test_extract_missing_header(client):
    """Test extraction without X-API-Key header at all."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract", files={"file": ("test.pdf", f, "application/pdf")}
        )
    assert response.status_code == 401


def test_extract_empty_api_key(client):
    """Test extraction with empty API key string."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"X-API-Key": ""},
        )
    assert response.status_code == 401


def test_health_check_structure(client):
    """Test health check returns expected fields."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "service" in data
    assert "version" in data
