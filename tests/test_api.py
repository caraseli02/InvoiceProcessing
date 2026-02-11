"""FastAPI endpoint tests."""

import os
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from invproc.api import app, extract_cache, limiter
from invproc.config import reload_config
from invproc.llm_extractor import LLMExtractor, LLMOutputIntegrityError


@pytest.fixture(autouse=True)
def setup_test_config():
    """Setup test configuration with API keys."""
    os.environ["API_KEYS"] = "test-api-key"
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
    os.environ["MOCK"] = "true"
    os.environ["DEV_BYPASS_API_KEY"] = "false"
    os.environ["MAX_PDF_SIZE_MB"] = "2"
    os.environ["EXTRACT_CACHE_ENABLED"] = "false"
    os.environ["EXTRACT_CACHE_TTL_SEC"] = "3600"
    os.environ["EXTRACT_CACHE_MAX_ENTRIES"] = "64"
    limiter.reset()
    extract_cache.reset()
    reload_config()
    yield
    os.environ.pop("API_KEYS", None)
    os.environ.pop("ALLOWED_ORIGINS", None)
    os.environ.pop("MOCK", None)
    os.environ.pop("MAX_PDF_SIZE_MB", None)
    os.environ.pop("DEV_BYPASS_API_KEY", None)
    os.environ.pop("MODEL", None)
    os.environ.pop("EXTRACT_CACHE_ENABLED", None)
    os.environ.pop("EXTRACT_CACHE_TTL_SEC", None)
    os.environ.pop("EXTRACT_CACHE_MAX_ENTRIES", None)
    limiter.reset()
    extract_cache.reset()
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
    assert "row_id" in data["products"][0]
    assert "weight_kg_candidate" in data["products"][0]


def test_extract_with_valid_bearer_auth(client):
    """Test extraction with valid bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-api-key"},
        )
    assert response.status_code == 200


def test_extract_with_invalid_bearer_auth(client):
    """Test extraction with invalid bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert response.status_code == 401


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


def test_extract_without_auth_with_dev_bypass(client):
    """Test extraction without API key when dev bypass is enabled."""
    os.environ["DEV_BYPASS_API_KEY"] = "true"
    reload_config()
    try:
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            response = client.post(
                "/extract", files={"file": ("test.pdf", f, "application/pdf")}
            )
        assert response.status_code == 200
    finally:
        os.environ.pop("DEV_BYPASS_API_KEY", None)
        reload_config()


def test_extract_returns_422_for_malformed_llm_output(client):
    """Test extraction returns 422 when LLM output has malformed product rows."""
    with patch(
        "invproc.llm_extractor.LLMExtractor.parse_with_llm",
        side_effect=LLMOutputIntegrityError("LLM returned 2 malformed product rows"),
    ):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            response = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )
    assert response.status_code == 422
    assert "malformed product rows" in response.json()["detail"]


def test_health_check_structure(client):
    """Test health check returns expected fields."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "service" in data
    assert "version" in data


def test_extract_cache_hit_skips_second_llm_call(client):
    """Test identical file upload is served from cache on second request."""
    os.environ["EXTRACT_CACHE_ENABLED"] = "true"
    reload_config()

    call_count = 0
    original_parse = LLMExtractor.parse_with_llm

    def counting_parse(self, text_grid):
        nonlocal call_count
        call_count += 1
        return original_parse(self, text_grid)

    with patch.object(LLMExtractor, "parse_with_llm", new=counting_parse):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            first = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers.get("X-Extract-Cache") == "miss"
    assert second.headers.get("X-Extract-Cache") == "hit"
    assert first.json() == second.json()
    assert call_count == 1


def test_extract_cache_config_change_forces_miss(client):
    """Test config signature changes invalidate cached entry."""
    os.environ["EXTRACT_CACHE_ENABLED"] = "true"
    reload_config()

    call_count = 0
    original_parse = LLMExtractor.parse_with_llm

    def counting_parse(self, text_grid):
        nonlocal call_count
        call_count += 1
        return original_parse(self, text_grid)

    with patch.object(LLMExtractor, "parse_with_llm", new=counting_parse):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            first = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )
        assert first.status_code == 200
        assert call_count == 1

        os.environ["MODEL"] = "gpt-4o"
        reload_config()
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )

    assert second.status_code == 200
    assert second.headers.get("X-Extract-Cache") == "miss"
    assert call_count == 2


def test_extract_does_not_cache_422_errors(client):
    """Test malformed LLM output errors are not cached."""
    os.environ["EXTRACT_CACHE_ENABLED"] = "true"
    reload_config()

    call_count = 0

    def always_fail(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        raise LLMOutputIntegrityError("LLM returned malformed rows")

    with patch("invproc.llm_extractor.LLMExtractor.parse_with_llm", side_effect=always_fail):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            first = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"X-API-Key": "test-api-key"},
            )

    assert first.status_code == 422
    assert second.status_code == 422
    assert call_count == 2
