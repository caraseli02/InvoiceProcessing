"""FastAPI application for invoice processing service."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import uuid
from typing import Any, AsyncIterator, Dict, cast

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from openai import APITimeoutError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from invproc.auth import SupabaseClientProvider, verify_supabase_jwt
from invproc.catalog_sync import (
    CatalogSyncProducer,
    NoopCatalogSyncProducer,
    RepositoryCatalogSyncProducer,
)
from invproc.config import InvoiceConfig, build_config
from invproc.dependencies import (
    AppResources,
    get_app_config,
    get_catalog_sync_producer,
    get_extract_cache,
    get_import_repository,
)
from invproc.extract_cache import InMemoryExtractCache
from invproc.exceptions import ContractError
from invproc.import_service import InvoiceImportService
from invproc.llm_extractor import LLMExtractor, LLMOutputIntegrityError
from invproc.models import (
    InvoiceData,
    InvoicePreviewPricingRequest,
    InvoicePreviewPricingResponse,
)
from invproc.pdf_processor import PDFProcessor
from invproc.rag import (
    CatalogRetrievalService,
    CatalogSyncWorker,
    build_rag_worker,
    build_retrieval_service,
    build_sync_status_snapshot,
    serialize_query_result,
    serialize_sync_status_snapshot,
)
from invproc.services.extract_service import run_extract_pipeline
from invproc.services.upload_service import save_upload_with_limit
from invproc.validator import InvoiceValidator
from invproc.repositories.base import InvoiceImportRepository
from invproc.repositories.memory import InMemoryInvoiceImportRepository

logger = logging.getLogger(__name__)
router = APIRouter()


class CatalogQueryRequest(BaseModel):
    """Request payload for backend catalog semantic lookup."""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


# Used for debugging in multi-instance and/or multi-worker deployments.
# Prefer a platform-provided stable id when available.
INSTANCE_ID = (
    os.getenv("INSTANCE_ID")
    or os.getenv("RENDER_INSTANCE_ID")
    or os.getenv("DYNO")
    or os.getenv("HOSTNAME")
    or f"local-{uuid.uuid4().hex[:12]}"
)


# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10/minute"],
    swallow_errors=True,
)


def build_app_resources(config: InvoiceConfig) -> AppResources:
    """Create app-scoped resources for a FastAPI app instance."""
    extract_cache = InMemoryExtractCache(
        ttl_sec=config.extract_cache_ttl_sec,
        max_entries=config.extract_cache_max_entries,
    )
    supabase_client_provider = SupabaseClientProvider(config)
    import_repository = InMemoryInvoiceImportRepository()
    if config.catalog_sync_enabled:
        catalog_sync_producer: CatalogSyncProducer = RepositoryCatalogSyncProducer(
            import_repository,
            embedding_model=config.catalog_sync_embedding_model,
        )
    else:
        catalog_sync_producer = NoopCatalogSyncProducer()
    return AppResources(
        config=config,
        extract_cache=extract_cache,
        supabase_client_provider=supabase_client_provider,
        import_repository=import_repository,
        catalog_sync_producer=catalog_sync_producer,
    )


def get_pdf_processor(config: InvoiceConfig = Depends(get_app_config)) -> PDFProcessor:
    """Get PDF processor instance (per-request)."""
    return PDFProcessor(config)


def get_llm_extractor(config: InvoiceConfig = Depends(get_app_config)) -> LLMExtractor:
    """Get LLM extractor instance (per-request)."""
    return LLMExtractor(config)


def get_validator(config: InvoiceConfig = Depends(get_app_config)) -> InvoiceValidator:
    """Get validator instance (per-request)."""
    return InvoiceValidator(config)


def get_import_service(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
    catalog_sync_producer: CatalogSyncProducer = Depends(get_catalog_sync_producer),
) -> InvoiceImportService:
    """Get invoice preview service instance."""
    return InvoiceImportService(
        config=config,
        repository=repository,
        catalog_sync_producer=catalog_sync_producer,
    )


def get_rag_worker(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
) -> CatalogSyncWorker:
    """Get backend RAG sync worker bound to app resources."""
    return build_rag_worker(
        repository=repository,
        config=config,
        worker_id=f"api-{INSTANCE_ID}",
    )


def get_rag_retrieval_service(
    config: InvoiceConfig = Depends(get_app_config),
    repository: InvoiceImportRepository = Depends(get_import_repository),
) -> CatalogRetrievalService:
    """Get backend RAG retrieval service bound to app resources."""
    return build_retrieval_service(repository=repository, config=config)


async def add_observability_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Attach debugging headers to all responses (including /health)."""
    response = await call_next(request)
    resources = getattr(request.app.state, "invproc_resources", None)
    config = getattr(resources, "config", None)
    debug_enabled = bool(getattr(config, "extract_cache_debug_headers", False))
    observability_enabled = bool(
        getattr(config, "extract_observability_headers", False) or debug_enabled
    )
    if observability_enabled:
        response.headers.setdefault("X-Instance-Id", INSTANCE_ID)
        response.headers.setdefault("X-Process-Id", str(os.getpid()))
    return response


@router.get("/health")
@limiter.exempt
async def health_check() -> Dict[str, Any]:
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "service": "invoice-processing",
        "version": "1.0.0",
    }


@router.post(
    "/extract",
    response_model=InvoiceData,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"description": "Invalid or expired token"},
        400: {"description": "Invalid PDF file"},
        422: {"description": "Unprocessable extraction output"},
        413: {"description": "PDF exceeds configured size limit"},
        429: {"description": "Rate limit exceeded"},
        504: {"description": "Model request timed out"},
        500: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")
async def extract_invoice(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="Invoice PDF file"),
    user: dict[str, Any] = Depends(verify_supabase_jwt),
    config: InvoiceConfig = Depends(get_app_config),
    extract_cache: InMemoryExtractCache = Depends(get_extract_cache),
    pdf_processor: PDFProcessor = Depends(get_pdf_processor),
    llm_extractor: LLMExtractor = Depends(get_llm_extractor),
    validator: InvoiceValidator = Depends(get_validator),
) -> InvoiceData:
    """Extract structured data from uploaded invoice PDF."""
    _ = request
    _ = user
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    temp_dir = config.output_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    temp_pdf_path = temp_dir / f"{uuid.uuid4()}-{safe_name}"

    try:
        max_file_size = config.max_pdf_size_mb * 1024 * 1024

        # Stream upload to disk and enforce size limit by actual file bytes.
        _, file_hash = await run_in_threadpool(
            save_upload_with_limit, file.file, temp_pdf_path, max_file_size
        )

        if config.extract_cache_debug_headers:
            response.headers["X-Extract-File-Hash"] = file_hash[:12]

        result = await run_in_threadpool(
            run_extract_pipeline,
            config=config,
            pdf_path=temp_pdf_path,
            file_hash=file_hash,
            pdf_processor=pdf_processor,
            llm_extractor=llm_extractor,
            validator=validator,
            cache=extract_cache,
        )
        response.headers["X-Extract-Cache"] = result.cache_status

        return cast(InvoiceData, result.invoice_data)

    except HTTPException:
        raise
    except ContractError:
        raise
    except LLMOutputIntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(e),
        )
    except APITimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Model request timed out. Please retry.",
        )
    except Exception as e:
        logger.exception("PDF processing failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Processing failed: {str(e)}",
        )
    finally:
        if temp_pdf_path.exists():
            await run_in_threadpool(temp_pdf_path.unlink)


@router.post(
    "/invoice/preview-pricing",
    response_model=InvoicePreviewPricingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"description": "Invalid or expired token"},
        400: {"description": "Invalid payload"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/minute")
async def preview_invoice_pricing(
    request: Request,
    payload: InvoicePreviewPricingRequest,
    user: dict[str, Any] = Depends(verify_supabase_jwt),
    import_service: InvoiceImportService = Depends(get_import_service),
) -> InvoicePreviewPricingResponse:
    """Compute canonical pricing preview for invoice rows."""
    _ = request
    _ = user
    return await run_in_threadpool(import_service.preview_pricing, payload)


@router.post(
    "/internal/rag/sync-pending",
    status_code=status.HTTP_200_OK,
    responses={401: {"description": "Invalid or expired token"}},
)
async def sync_pending_catalog_embeddings(
    request: Request,
    limit: int = 100,
    user: dict[str, Any] = Depends(verify_supabase_jwt),
    worker: CatalogSyncWorker = Depends(get_rag_worker),
) -> dict[str, Any]:
    """Process pending backend RAG sync rows using app-owned resources."""
    _ = request
    _ = user
    results = await run_in_threadpool(worker.sync_pending, limit=limit)
    return {
        "processed": len(results),
        "results": [result.__dict__ for result in results],
    }


@router.post(
    "/internal/rag/query",
    status_code=status.HTTP_200_OK,
    responses={401: {"description": "Invalid or expired token"}},
)
async def query_catalog_embeddings(
    request: Request,
    payload: CatalogQueryRequest,
    user: dict[str, Any] = Depends(verify_supabase_jwt),
    retrieval_service: CatalogRetrievalService = Depends(get_rag_retrieval_service),
) -> dict[str, Any]:
    """Query backend-owned catalog embeddings through the app resource graph."""
    _ = request
    _ = user
    result = await run_in_threadpool(
        retrieval_service.query,
        payload.query,
        top_k=payload.top_k,
    )
    return serialize_query_result(result)


@router.get(
    "/internal/rag/status",
    status_code=status.HTTP_200_OK,
    responses={401: {"description": "Invalid or expired token"}},
)
async def rag_status(
    request: Request,
    user: dict[str, Any] = Depends(verify_supabase_jwt),
    repository: InvoiceImportRepository = Depends(get_import_repository),
) -> dict[str, Any]:
    """Inspect backend RAG sync queue state through the app resource graph."""
    _ = request
    _ = user
    snapshot = await run_in_threadpool(build_sync_status_snapshot, repository)
    return serialize_sync_status_snapshot(snapshot)


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Rate limit exceeded handler."""
    _ = request
    _ = exc
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


async def contract_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map domain contract errors to stable API error payload."""
    _ = request
    contract_error = cast(ContractError, exc)
    return JSONResponse(
        status_code=contract_error.status_code,
        content={
            "error": {
                "code": contract_error.code,
                "message": contract_error.message,
                "details": contract_error.details,
            }
        },
    )


def create_app(
    *,
    resources: AppResources | None = None,
) -> FastAPI:
    """Create a configured FastAPI app instance."""
    if resources is None:
        config = build_config()
        resources = build_app_resources(config)
    else:
        config = resources.config

    @asynccontextmanager
    async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Initialize app-scoped resources once per app instance."""
        app.state.invproc_resources = resources
        try:
            yield
        finally:
            app.state.invproc_resources = None

    app = FastAPI(
        title="Invoice Processing Service",
        description="Extract structured data from invoice PDFs using AI",
        version="1.0.0",
        lifespan=app_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Extract-Cache",
            "X-Instance-Id",
            "X-Process-Id",
            "X-Extract-File-Hash",
        ],
    )

    app.middleware("http")(add_observability_headers)
    app.include_router(router)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(ContractError, contract_error_handler)
    return app

def main() -> None:
    """Run API server."""
    import uvicorn

    uvicorn.run(
        "invproc.api:create_app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        factory=True,
    )


if __name__ == "__main__":
    main()
