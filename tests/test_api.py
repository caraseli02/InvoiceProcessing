"""FastAPI endpoint tests."""

import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from invproc.api import limiter
from invproc.config import InvoiceConfig
from invproc.extract_cache import InMemoryExtractCache
from invproc.llm_extractor import LLMExtractor, LLMOutputIntegrityError
from invproc.models import InvoiceData


@pytest.fixture(autouse=True)
def setup_test_config():
    """Setup test configuration."""
    os.environ["ALLOWED_ORIGINS"] = "http://localhost:5173"
    os.environ["MOCK"] = "true"
    os.environ["MAX_PDF_SIZE_MB"] = "2"
    limiter.reset()
    yield
    os.environ.pop("ALLOWED_ORIGINS", None)
    os.environ.pop("MOCK", None)
    os.environ.pop("MAX_PDF_SIZE_MB", None)
    os.environ.pop("MODEL", None)
    limiter.reset()


@pytest.fixture
def client(api_test_client: TestClient):
    """Create test client for each test."""
    yield api_test_client


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_extract_without_auth(client):
    """Test extraction without auth token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract", files={"file": ("test.pdf", f, "application/pdf")}
        )
    assert response.status_code == 401


def test_extract_with_invalid_auth(client):
    """Test extraction with invalid bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert response.status_code == 401


def test_extract_with_valid_auth(client):
    """Test extraction with valid bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-supabase-jwt"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "supplier" in data
    assert "products" in data
    assert len(data["products"]) > 0
    assert "row_id" in data["products"][0]
    assert "weight_kg_candidate" in data["products"][0]
    assert "uom" in data["products"][0]
    assert "category_suggestion" in data["products"][0]


def test_extract_with_valid_bearer_auth(client):
    """Test extraction with valid bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-supabase-jwt"},
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
            headers={"Authorization": "Bearer test-supabase-jwt"},
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
        headers={"Authorization": "Bearer test-supabase-jwt"},
    )
    assert response.status_code == 400


def test_extract_missing_header(client):
    """Test extraction without Authorization header."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract", files={"file": ("test.pdf", f, "application/pdf")}
        )
    assert response.status_code == 401


def test_extract_empty_bearer_token(client):
    """Test extraction with empty bearer token."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer "},
        )
    assert response.status_code == 401


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
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
    assert response.status_code == 422
    assert "malformed product rows" in response.json()["detail"]


def test_extract_returns_422_for_invalid_json_llm_output(client):
    """Invalid JSON from the model should not bubble up as a 500."""
    with patch(
        "invproc.llm_extractor.LLMExtractor.parse_with_llm",
        side_effect=LLMOutputIntegrityError(
            "Model returned invalid JSON for this invoice. Please retry."
        ),
    ):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            response = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert response.status_code == 422
    assert "invalid JSON" in response.json()["detail"]


def test_health_check_structure(client):
    """Test health check returns expected fields."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "service" in data
    assert "version" in data


def test_extract_cache_hit_skips_second_llm_call(
    client,
    api_test_config: InvoiceConfig,
    api_test_extract_cache: InMemoryExtractCache,
):
    """Test identical file upload is served from cache on second request."""
    api_test_config.extract_cache_enabled = True
    api_test_config.extract_observability_headers = True
    api_test_extract_cache.configure(
        ttl_sec=api_test_config.extract_cache_ttl_sec,
        max_entries=api_test_config.extract_cache_max_entries,
    )

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
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers.get("X-Extract-Cache") == "miss"
    assert second.headers.get("X-Extract-Cache") == "hit"
    assert second.headers.get("X-Instance-Id")
    assert second.headers.get("X-Process-Id")
    assert first.json() == second.json()
    assert call_count == 1


def test_extract_cache_header_off_when_disabled(client):
    """When cache is disabled, header should be present for observability."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-supabase-jwt"},
        )
    assert response.status_code == 200
    assert response.headers.get("X-Extract-Cache") == "off"
    assert response.headers.get("X-Instance-Id") is None
    assert response.headers.get("X-Process-Id") is None


def test_extract_debug_headers_include_file_hash_and_observability_ids(
    client,
    api_test_config: InvoiceConfig,
):
    """Debug headers should expose file hash and observability identifiers."""
    api_test_config.extract_cache_debug_headers = True

    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-supabase-jwt"},
        )

    assert response.status_code == 200
    assert response.headers.get("X-Extract-Cache") == "off"
    assert response.headers.get("X-Extract-File-Hash")
    assert response.headers.get("X-Instance-Id")
    assert response.headers.get("X-Process-Id")


def test_observability_headers_ignore_env_drift_after_app_creation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime headers should follow startup-owned config, not later env changes."""
    monkeypatch.setenv("EXTRACT_OBSERVABILITY_HEADERS", "true")
    monkeypatch.setenv("EXTRACT_CACHE_DEBUG_HEADERS", "true")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("X-Instance-Id") is None
    assert response.headers.get("X-Process-Id") is None


def test_extract_cache_config_change_forces_miss(
    client,
    api_test_config: InvoiceConfig,
    api_test_extract_cache: InMemoryExtractCache,
):
    """Test config signature changes invalidate cached entry."""
    api_test_config.extract_cache_enabled = True
    api_test_extract_cache.configure(
        ttl_sec=api_test_config.extract_cache_ttl_sec,
        max_entries=api_test_config.extract_cache_max_entries,
    )

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
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
        assert first.status_code == 200
        assert call_count == 1

        api_test_config.model = "gpt-4o"
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert second.status_code == 200
    assert second.headers.get("X-Extract-Cache") == "miss"
    assert call_count == 2


def test_extract_does_not_cache_422_errors(
    client,
    api_test_config: InvoiceConfig,
    api_test_extract_cache: InMemoryExtractCache,
):
    """Test malformed LLM output errors are not cached."""
    api_test_config.extract_cache_enabled = True
    api_test_extract_cache.configure(
        ttl_sec=api_test_config.extract_cache_ttl_sec,
        max_entries=api_test_config.extract_cache_max_entries,
    )

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
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert first.status_code == 422
    assert second.status_code == 422
    assert call_count == 2


def _mock_invoice_data() -> InvoiceData:
    return InvoiceData(
        supplier="MOCK SUPPLIER",
        invoice_number="INV-ASYNC",
        date="27-03-2026",
        total_amount=10.0,
        currency="MDL",
        products=[],
    )


def _wait_for_job_terminal_state(client: TestClient, job_id: str) -> TestClient:
    deadline = time.time() + 2
    last_response = None
    while time.time() < deadline:
        last_response = client.get(
            f"/invoice/extraction-jobs/{job_id}",
            headers={"Authorization": "Bearer test-supabase-jwt"},
        )
        assert last_response.status_code == 200
        if last_response.json()["status"] in {"succeeded", "failed"}:
            return last_response
        time.sleep(0.02)
    assert last_response is not None
    return last_response


def test_extract_returns_202_for_async_routed_invoice(
    client: TestClient,
    api_test_config: InvoiceConfig,
) -> None:
    api_test_config.extract_async_page_threshold = 1

    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"Authorization": "Bearer test-supabase-jwt"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job_id"].startswith("ext_")
    assert payload["status"] in {"queued", "processing", "succeeded"}
    assert payload["status_url"] == f"/invoice/extraction-jobs/{payload['job_id']}"
    assert response.headers["Location"] == payload["status_url"]
    assert response.headers["Retry-After"] == str(api_test_config.extract_job_retry_after_sec)


def test_extract_job_endpoint_returns_terminal_success(
    client: TestClient,
    api_test_config: InvoiceConfig,
) -> None:
    api_test_config.extract_async_page_threshold = 1

    with patch("invproc.api.run_extract_pipeline") as mock_run_extract_pipeline:
        mock_run_extract_pipeline.return_value = type(
            "Result",
            (),
            {"invoice_data": _mock_invoice_data(), "cache_status": "off"},
        )()
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            submit = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert submit.status_code == 202
    job_id = submit.json()["job_id"]
    status_response = _wait_for_job_terminal_state(client, job_id)
    payload = status_response.json()
    assert payload["status"] == "succeeded"
    assert payload["result"]["invoice_number"] == "INV-ASYNC"
    assert payload["error"] is None


def test_extract_job_endpoint_returns_terminal_failure(
    client: TestClient,
    api_test_config: InvoiceConfig,
) -> None:
    api_test_config.extract_async_page_threshold = 1

    with patch(
        "invproc.api.run_extract_pipeline",
        side_effect=LLMOutputIntegrityError("LLM returned malformed rows"),
    ):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            submit = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )

    assert submit.status_code == 202
    job_id = submit.json()["job_id"]
    status_response = _wait_for_job_terminal_state(client, job_id)
    payload = status_response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "EXTRACTION_FAILED"
    assert payload["result"] is None


def test_extract_duplicate_async_submit_reuses_canonical_job(
    client: TestClient,
    api_test_config: InvoiceConfig,
) -> None:
    api_test_config.extract_async_page_threshold = 1

    started = threading.Event()
    release = threading.Event()

    def slow_extract(*_args, **_kwargs):
        started.set()
        release.wait(timeout=1)
        return type(
            "Result",
            (),
            {"invoice_data": _mock_invoice_data(), "cache_status": "off"},
        )()

    with patch("invproc.api.run_extract_pipeline", side_effect=slow_extract):
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            first = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
        started.wait(timeout=1)
        with open("test_invoices/invoice-test.pdf", "rb") as f:
            second = client.post(
                "/extract",
                files={"file": ("test.pdf", f, "application/pdf")},
                headers={"Authorization": "Bearer test-supabase-jwt"},
            )
        release.set()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
