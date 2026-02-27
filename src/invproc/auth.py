"""Supabase JWT authentication helpers for FastAPI endpoints."""

import os
from threading import Lock
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from invproc.config import InvoiceConfig
from invproc.dependencies import get_app_config, get_supabase_client_provider

bearer_scheme = HTTPBearer(auto_error=False)


def _get_api_keys() -> set[str]:
    keys_raw = os.getenv("API_KEYS", "")
    return {k.strip() for k in keys_raw.split(",") if k.strip()}


class SupabaseClientProvider:
    """App-scoped lazy Supabase client provider bound to startup config."""

    def __init__(self, config: InvoiceConfig) -> None:
        self._config = config
        self._client: Optional[Client] = None
        self._lock = Lock()

    def get_client(self) -> Client:
        if not self._config.supabase_url or not self._config.supabase_service_role_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service is not configured",
            )

        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is None:
                self._client = create_client(
                    self._config.supabase_url,
                    self._config.supabase_service_role_key,
                )
        return self._client
def get_supabase_client(
    provider: SupabaseClientProvider = Depends(get_supabase_client_provider),
) -> Client:
    """Resolve a Supabase client through the app-scoped provider."""
    return provider.get_client()


def fetch_supabase_user(token: str, client: Client) -> dict[str, Any]:
    """Return user payload for a verified Supabase JWT."""
    response = client.auth.get_user(token)
    if response is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = response.user
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if hasattr(user, "model_dump"):
        return user.model_dump(mode="json")
    if hasattr(user, "dict"):
        return user.dict()
    return dict(user) if isinstance(user, dict) else {"id": getattr(user, "id", None)}


async def verify_supabase_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    config: InvoiceConfig = Depends(get_app_config),
    client: Client = Depends(get_supabase_client),
) -> dict[str, Any]:
    """Verify Bearer token with Supabase and return authenticated user payload."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    token = credentials.credentials

    api_keys = _get_api_keys()
    if config.allow_api_key_auth and api_keys and token in api_keys:
        return {"id": "api-key-user", "auth": "api_key"}

    try:
        return fetch_supabase_user(token, client)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
