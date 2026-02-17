"""Supabase JWT authentication helpers for FastAPI endpoints."""

from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from invproc.config import InvoiceConfig, get_config

bearer_scheme = HTTPBearer(auto_error=False)

_client: Optional[Client] = None
_client_url: Optional[str] = None
_client_key: Optional[str] = None


def get_supabase_client(config: InvoiceConfig) -> Client:
    """Create or reuse a Supabase client for server-side JWT verification."""
    global _client
    global _client_key
    global _client_url

    if not config.supabase_url or not config.supabase_service_role_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service is not configured",
        )

    if (
        _client is None
        or _client_url != config.supabase_url
        or _client_key != config.supabase_service_role_key
    ):
        _client = create_client(config.supabase_url, config.supabase_service_role_key)
        _client_url = config.supabase_url
        _client_key = config.supabase_service_role_key

    return _client


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
    config: InvoiceConfig = Depends(get_config),
) -> dict[str, Any]:
    """Verify Bearer token with Supabase and return authenticated user payload."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    client = get_supabase_client(config)
    token = credentials.credentials

    try:
        return fetch_supabase_user(token, client)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
