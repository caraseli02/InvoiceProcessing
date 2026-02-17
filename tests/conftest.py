"""Shared test fixtures."""

from collections.abc import Generator

import pytest
from fastapi import HTTPException

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
