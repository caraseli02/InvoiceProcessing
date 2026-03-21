"""Focused tests for auth helpers added in recent API/config changes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from invproc.auth import (
    SupabaseClientProvider,
    fetch_supabase_user,
    verify_supabase_jwt,
)
from invproc.config import InvoiceConfig


def test_supabase_client_provider_requires_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should fail fast when Supabase settings are missing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    provider = SupabaseClientProvider(InvoiceConfig(_env_file=None, mock=True))

    with pytest.raises(HTTPException, match="Authentication service is not configured") as exc:
        provider.get_client()

    assert exc.value.status_code == 500


def test_supabase_client_provider_caches_created_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider should create the Supabase client only once per app instance."""
    calls: list[tuple[str, str]] = []
    cached_client = object()

    def fake_create_client(url: str, key: str) -> object:
        calls.append((url, key))
        return cached_client

    monkeypatch.setattr("invproc.auth.create_client", fake_create_client)

    provider = SupabaseClientProvider(
        InvoiceConfig(
            _env_file=None,
            mock=True,
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
        )
    )

    assert provider.get_client() is cached_client
    assert provider.get_client() is cached_client
    assert calls == [("https://example.supabase.co", "service-role")]


def test_fetch_supabase_user_uses_model_dump_payload() -> None:
    """Pydantic-style user objects should be serialized via model_dump."""

    class FakeUser:
        def model_dump(self, mode: str = "json") -> dict[str, str]:
            assert mode == "json"
            return {"id": "user-123", "email": "user@example.com"}

    client = SimpleNamespace(
        auth=SimpleNamespace(get_user=lambda token: SimpleNamespace(user=FakeUser()))
    )

    payload = fetch_supabase_user("token", client)

    assert payload == {"id": "user-123", "email": "user@example.com"}


def test_verify_supabase_jwt_allows_configured_api_key_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured API keys should bypass Supabase lookup in local mode."""
    monkeypatch.setenv("API_KEYS", "secret-key, another-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

    provider = SupabaseClientProvider(InvoiceConfig(_env_file=None, mock=True))

    result = asyncio.run(
        verify_supabase_jwt(
            credentials=HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials="secret-key",
            ),
            config=InvoiceConfig(
                _env_file=None,
                mock=True,
                allow_api_key_auth=True,
            ),
            provider=provider,
        )
    )

    assert result == {"id": "api-key-user", "auth": "api_key"}


def test_verify_supabase_jwt_api_key_bypass_skips_unconfigured_supabase_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local API key auth should not require Supabase client construction."""
    monkeypatch.setenv("API_KEYS", "dev-key-12345")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    provider = SupabaseClientProvider(InvoiceConfig(_env_file=None, mock=True))

    result = asyncio.run(
        verify_supabase_jwt(
            credentials=HTTPAuthorizationCredentials(
                scheme="Bearer",
                credentials="dev-key-12345",
            ),
            config=InvoiceConfig(
                _env_file=None,
                mock=True,
                allow_api_key_auth=True,
            ),
            provider=provider,
        )
    )

    assert result == {"id": "api-key-user", "auth": "api_key"}
