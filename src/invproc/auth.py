"""Supabase JWT authentication helpers for FastAPI endpoints."""

from threading import Lock
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from invproc.config import InvoiceConfig
from invproc.dependencies import get_app_config, get_supabase_client_provider

bearer_scheme = HTTPBearer(auto_error=False)


def _get_api_keys(config: InvoiceConfig) -> set[str]:
    if not config.api_keys:
        return set()
    raw = config.api_keys.get_secret_value()
    return {k.strip() for k in raw.split(",") if k.strip()}


def _get_internal_api_keys(config: InvoiceConfig) -> set[str]:
    if not config.internal_api_keys:
        return set()
    raw = config.internal_api_keys.get_secret_value()
    return {k.strip() for k in raw.split(",") if k.strip()}


class SupabaseClientProvider:
    """App-scoped lazy Supabase client provider bound to startup config."""

    def __init__(self, config: InvoiceConfig) -> None:
        self._config = config
        self._client: Optional[Client] = None
        self._lock = Lock()

    def get_client(self) -> Client:
        if not self._config.supabase_url or not self._config.supabase_service_role_key:
            raise RuntimeError("Authentication service is not configured")

        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is None:
                self._client = create_client(
                    self._config.supabase_url,
                    self._config.supabase_service_role_key.get_secret_value(),
                )
        return self._client
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
    return user.model_dump(mode="json")


async def verify_internal_caller(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    config: InvoiceConfig = Depends(get_app_config),
) -> dict[str, Any]:
    """Verify Bearer token against internal_api_keys. Rejects Supabase JWTs."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    token = credentials.credentials
    internal_keys = _get_internal_api_keys(config)

    if not internal_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal API not configured",
        )

    if token not in internal_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    return {"id": "internal-caller", "auth": "internal_api_key"}


async def verify_supabase_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    config: InvoiceConfig = Depends(get_app_config),
    provider: SupabaseClientProvider = Depends(get_supabase_client_provider),
) -> dict[str, Any]:
    """Verify Bearer token with Supabase and return authenticated user payload."""
    if config.allow_api_key_auth and not credentials:
        return {"id": "dev-user", "auth": "dev_bypass"}

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    token = credentials.credentials

    api_keys = _get_api_keys(config)
    if config.allow_api_key_auth and api_keys and token in api_keys:
        return {"id": "api-key-user", "auth": "api_key"}

    try:
        client = provider.get_client()
        return fetch_supabase_user(token, client)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
