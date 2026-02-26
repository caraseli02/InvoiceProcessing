"""Shared test fixtures."""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from invproc.api import create_app
from invproc.auth import get_supabase_client
from invproc.config import InvoiceConfig
from invproc.dependencies import get_app_config, get_extract_cache
from invproc.extract_cache import InMemoryExtractCache

TEST_SUPABASE_TOKEN = "test-supabase-jwt"


@pytest.fixture(autouse=True)
def mock_supabase_auth(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Generator[None, None, None]:
    """Mock Supabase JWT verification for offline tests."""
    if request.module.__name__.endswith("test_config"):
        yield
        return

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

    def _fake_fetch_supabase_user(token: str, client: object) -> dict[str, str]:
        _ = client
        if token == TEST_SUPABASE_TOKEN:
            return {"id": "test-user-id", "email": "test@example.com"}
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    monkeypatch.setattr("invproc.auth.fetch_supabase_user", _fake_fetch_supabase_user)
    yield


@pytest.fixture
def api_test_config() -> InvoiceConfig:
    """Provide a test-owned API config instance for dependency overrides."""
    return InvoiceConfig(
        _env_file=None,
        mock=True,
        max_pdf_size_mb=2,
        extract_cache_enabled=False,
        extract_cache_ttl_sec=3600,
        extract_cache_max_entries=64,
    )


@pytest.fixture
def api_test_extract_cache(api_test_config: InvoiceConfig) -> InMemoryExtractCache:
    """Provide a test-owned extract cache for dependency overrides."""
    return InMemoryExtractCache(
        ttl_sec=api_test_config.extract_cache_ttl_sec,
        max_entries=api_test_config.extract_cache_max_entries,
    )


@pytest.fixture
def api_test_app(
    monkeypatch: pytest.MonkeyPatch,
    api_test_config: InvoiceConfig,
    api_test_extract_cache: InMemoryExtractCache,
) -> Generator[Any, None, None]:
    """Create a fresh FastAPI app with explicit dependency overrides."""
    monkeypatch.setenv("MOCK", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    app = create_app()
    app.dependency_overrides[get_app_config] = lambda: api_test_config
    app.dependency_overrides[get_extract_cache] = lambda: api_test_extract_cache
    app.dependency_overrides[get_supabase_client] = lambda: object()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def api_test_client(api_test_app: Any) -> Generator[TestClient, None, None]:
    """Create a TestClient for the overridden API app."""
    with TestClient(api_test_app) as client:
        yield client
