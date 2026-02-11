"""FastAPI application for invoice processing service."""

import hashlib
import os
from pathlib import Path
import uuid
from typing import Any, BinaryIO, Dict, Optional, Set, cast

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from openai import APITimeoutError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from invproc.config import InvoiceConfig, get_config
from invproc.exceptions import ContractError
from invproc.import_service import InvoiceImportService
from invproc.llm_extractor import LLMExtractor, LLMOutputIntegrityError
from invproc.models import (
    InvoiceData,
    InvoicePreviewPricingRequest,
    InvoicePreviewPricingResponse,
)
from invproc.pdf_processor import PDFProcessor
from invproc.validator import InvoiceValidator
from invproc.weight_parser import parse_weight_candidate

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


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
        (
            "http://localhost:3000,http://localhost:5173,"
            "http://127.0.0.1:5173,https://lavio.vercel.app"
        ),
    )
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def get_api_keys(config: InvoiceConfig = Depends(get_config)) -> Set[str]:
    """Get API keys from configuration."""
    keys = config.api_keys or ""
    return {k.strip() for k in keys.split(",") if k.strip()}


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def verify_api_key(
    api_key: Optional[str] = Security(api_key_header),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Security(
        bearer_scheme
    ),
    valid_keys: Set[str] = Depends(get_api_keys),
    config: InvoiceConfig = Depends(get_config),
) -> str:
    """Verify API key from X-API-Key or Authorization Bearer token."""
    if config.dev_bypass_api_key:
        return "dev-bypass"

    candidate = api_key
    if not candidate and bearer_credentials:
        candidate = bearer_credentials.credentials

    if not candidate or candidate not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return candidate


def _save_upload_with_limit(
    source: BinaryIO, destination: Path, max_file_size: int
) -> int:
    """Stream upload to disk while enforcing max file size."""
    source.seek(0)
    total_bytes = 0

    with destination.open("wb") as output_file:
        while True:
            chunk = source.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break

            total_bytes += len(chunk)
            if total_bytes > max_file_size:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"File too large: {total_bytes:,} bytes "
                        f"(max {max_file_size:,} bytes)"
                    ),
                )

            output_file.write(chunk)

    return total_bytes


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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10/minute"],
    swallow_errors=True,
)


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
        401: {"description": "Invalid API key"},
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
    file: UploadFile = File(..., description="Invoice PDF file"),
    api_key: str = Depends(verify_api_key),
    pdf_processor: PDFProcessor = Depends(get_pdf_processor),
    llm_extractor: LLMExtractor = Depends(get_llm_extractor),
    validator: InvoiceValidator = Depends(get_validator),
) -> InvoiceData:
    """Extract structured data from uploaded invoice PDF."""
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
        await run_in_threadpool(
            _save_upload_with_limit, file.file, temp_pdf_path, max_file_size
        )

        text_grid, _metadata = await run_in_threadpool(
            pdf_processor.extract_content, temp_pdf_path
        )
        invoice_data = await run_in_threadpool(llm_extractor.parse_with_llm, text_grid)
        validated_invoice = await run_in_threadpool(
            validator.validate_invoice, invoice_data
        )
        _add_row_metadata(validated_invoice)

        return cast(InvoiceData, validated_invoice)

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
        import logging

        logging.exception("PDF processing failed: %s", str(e))
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
        401: {"description": "Invalid API key"},
        400: {"description": "Invalid payload"},
        429: {"description": "Rate limit exceeded"},
    },
)
@limiter.limit("20/minute")
async def preview_invoice_pricing(
    request: Request,
    payload: InvoicePreviewPricingRequest,
    api_key: str = Depends(verify_api_key),
    import_service: InvoiceImportService = Depends(get_import_service),
) -> InvoicePreviewPricingResponse:
    """Compute canonical pricing preview for invoice rows."""
    return await run_in_threadpool(import_service.preview_pricing, payload)


def _add_row_metadata(invoice_data: InvoiceData) -> None:
    """Populate extracted rows with stable IDs and weight candidates."""
    for idx, product in enumerate(invoice_data.products):
        raw = (
            f"{idx}|{product.raw_code or ''}|{product.name}|"
            f"{product.quantity}|{product.total_price}"
        )
        row_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        product.row_id = f"r_{row_hash}"

        parsed = parse_weight_candidate(product.name)
        product.weight_kg_candidate = parsed.weight_kg
        product.size_token = parsed.size_token
        product.parse_confidence = parsed.parse_confidence


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
