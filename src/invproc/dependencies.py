"""Shared FastAPI app resource container and provider dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Depends, HTTPException, Request, status

from invproc.config import InvoiceConfig
from invproc.extract_cache import InMemoryExtractCache

if TYPE_CHECKING:
    from invproc.auth import SupabaseClientProvider


@dataclass
class AppResources:
    """App-scoped resources initialized during FastAPI lifespan."""

    config: InvoiceConfig
    extract_cache: InMemoryExtractCache
    supabase_client_provider: SupabaseClientProvider


def get_app_resources(request: Request) -> AppResources:
    """Return initialized app resources from state."""
    resources = getattr(request.app.state, "invproc_resources", None)
    if resources is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application resources are not initialized",
        )
    return cast(AppResources, resources)


def get_app_config(resources: AppResources = Depends(get_app_resources)) -> InvoiceConfig:
    """Get app-scoped config instance."""
    return resources.config


def get_extract_cache(
    resources: AppResources = Depends(get_app_resources),
) -> InMemoryExtractCache:
    """Get app-scoped extraction cache."""
    return resources.extract_cache


def get_supabase_client_provider(
    resources: AppResources = Depends(get_app_resources),
) -> "SupabaseClientProvider":
    """Get app-scoped Supabase client provider."""
    return resources.supabase_client_provider
