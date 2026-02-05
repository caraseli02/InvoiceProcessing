"""FastAPI application for invoice processing service."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
import uuid
from typing import Dict, Optional, Set

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from invproc.config import get_config, InvoiceConfig
from invproc.llm_extractor import LLMExtractor
from invproc.pdf_processor import PDFProcessor
from invproc.models import InvoiceData
from invproc.validator import InvoiceValidator


def get_pdf_processor(config: InvoiceConfig = Depends(get_config)) -> PDFProcessor:
    """Get PDF processor instance (per-request)."""
    return PDFProcessor(config)


def get_llm_extractor(config: InvoiceConfig = Depends(get_config)) -> LLMExtractor:
    """Get LLM extractor instance (per-request)."""
    return LLMExtractor(config)


def get_validator() -> InvoiceValidator:
    """Get validator instance (per-request)."""
    return InvoiceValidator()


def get_allowed_origins() -> list[str]:
    """Get allowed CORS origins from environment."""
    origins = os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:3000,https://yourdomain.com"
    )
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def get_api_keys(config: InvoiceConfig = Depends(get_config)) -> Set[str]:
    """Get API keys from configuration."""
    keys = config.api_keys or ""
    return {k.strip() for k in keys.split(",") if k.strip()}


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(
    api_key: Optional[str] = Security(api_key_header),
    valid_keys: Set[str] = Depends(get_api_keys),
) -> str:
    """Verify API key against allowed keys."""
    if not api_key or api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key


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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10/minute"],
    swallow_errors=True,
)


@app.get("/health")
@limiter.exempt
async def health_check():
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
        429: {"description": "Rate limit exceeded"},
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
):
    """
    Extract structured data from uploaded invoice PDF.

    **Authentication:**
    - API key required in `X-API-Key` header

    **Returns:**
    - InvoiceData: Structured invoice data with products and metadata

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/extract" \\
      -H "X-API-Key: your-api-key" \\
      -F "file=@invoice.pdf"
    ```
    """
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
        # Offload blocking file read to thread pool
        content = await file.read()

        # Offload blocking file write to thread pool
        await run_in_threadpool(temp_pdf_path.write_bytes, content)

        # Offload CPU-intensive PDF processing to thread pool
        text_grid, metadata = await run_in_threadpool(
            pdf_processor.extract_content, temp_pdf_path
        )

        # Offload blocking LLM call to thread pool
        invoice_data = await run_in_threadpool(llm_extractor.parse_with_llm, text_grid)

        # Offload CPU-intensive validation to thread pool
        validated_invoice = await run_in_threadpool(
            validator.validate_invoice, invoice_data
        )

        return validated_invoice

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Processing failed",
        )
    finally:
        # Offload blocking file delete to thread pool
        if temp_pdf_path.exists():
            await run_in_threadpool(temp_pdf_path.unlink)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Rate limit exceeded handler."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


def main():
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
