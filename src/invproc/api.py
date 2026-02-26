"""FastAPI application for invoice processing service."""

import logging
import os
from pathlib import Path
import uuid
from typing import Any, Dict, cast

from fastapi import (
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

from openai import APITimeoutError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from invproc.auth import verify_supabase_jwt
from invproc.config import InvoiceConfig, get_config
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
from invproc.services.extract_service import run_extract_pipeline
from invproc.services.upload_service import save_upload_with_limit
from invproc.validator import InvoiceValidator

logger = logging.getLogger(__name__)

extract_cache = InMemoryExtractCache(ttl_sec=86400, max_entries=256)

# Env parsing helpers
def _env_truthy(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Used for debugging in multi-instance and/or multi-worker deployments.
# Prefer a platform-provided stable id when available.
INSTANCE_ID = (
    os.getenv("INSTANCE_ID")
    or os.getenv("RENDER_INSTANCE_ID")
    or os.getenv("DYNO")
    or os.getenv("HOSTNAME")
    or f"local-{uuid.uuid4().hex[:12]}"
)


def get_pdf_processor(config: InvoiceConfig = Depends(get_config)) -> PDFProcessor:
    """Get PDF processor instance (per-request)."""
    return PDFProcessor(config)


def get_llm_extractor(config: InvoiceConfig = Depends(get_config)) -> LLMExtractor:
    """Get LLM extractor instance (per-request)."""
    return LLMExtractor(config)


def get_validator(config: InvoiceConfig = Depends(get_config)) -> InvoiceValidator:
    """Get validator instance (per-request)."""
    return InvoiceValidator(config)


def get_import_service(
    config: InvoiceConfig = Depends(get_config),
) -> InvoiceImportService:
    """Get invoice preview service instance."""
    return InvoiceImportService(config=config)


def get_allowed_origins() -> list[str]:
    """Get allowed CORS origins from environment."""
    origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,https://lavio.vercel.app",
    )
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


# Initialize app and rate limiter
app = FastAPI(
    title="Invoice Processing Service",
    description="Extract structured data from invoice PDFs using AI",
    version="1.0.0",
)

# Load CORS origins
allowed_origins = get_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let browser JS read these for production cache verification.
    expose_headers=["X-Extract-Cache", "X-Instance-Id", "X-Process-Id", "X-Extract-File-Hash"],
)

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10/minute"],
    swallow_errors=True,
)


@app.middleware("http")
async def add_observability_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Attach debugging headers to all responses (including /health)."""
    response = await call_next(request)
    debug_enabled = _env_truthy("EXTRACT_CACHE_DEBUG_HEADERS")
    observability_enabled = _env_truthy("EXTRACT_OBSERVABILITY_HEADERS") or debug_enabled
    if observability_enabled:
        response.headers.setdefault("X-Instance-Id", INSTANCE_ID)
        response.headers.setdefault("X-Process-Id", str(os.getpid()))
    return response


@app.get("/health")
@limiter.exempt
async def health_check() -> Dict[str, Any]:
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "service": "invoice-processing",
        "version": "1.0.0",
    }


@app.post(
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
    pdf_processor: PDFProcessor = Depends(get_pdf_processor),
    llm_extractor: LLMExtractor = Depends(get_llm_extractor),
    validator: InvoiceValidator = Depends(get_validator),
) -> InvoiceData:
    """Extract structured data from uploaded invoice PDF."""
    _ = user
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    config = get_config()
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

        if _env_truthy("EXTRACT_CACHE_DEBUG_HEADERS"):
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


@app.post(
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
    _ = user
    return await run_in_threadpool(import_service.preview_pricing, payload)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Rate limit exceeded handler."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


@app.exception_handler(ContractError)
async def contract_error_handler(request: Request, exc: ContractError) -> JSONResponse:
    """Map domain contract errors to stable API error payload."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def main() -> None:
    """Run API server."""
    import uvicorn

    uvicorn.run(
        "invproc.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
