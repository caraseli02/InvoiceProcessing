"""FastAPI application for invoice processing service."""

from contextlib import asynccontextmanager
from pathlib import Path
import uuid
from typing import Dict, Optional

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from invproc.config import get_config
from invproc.llm_extractor import LLMExtractor
from invproc.pdf_processor import PDFProcessor
from invproc.models import InvoiceData
from invproc.validator import InvoiceValidator

_pdf_processor: PDFProcessor | None = None
_llm_extractor: LLMExtractor | None = None
_validator: InvoiceValidator | None = None
_config = None


def _initialize_processors():
    """Initialize processors if not already initialized."""
    global _pdf_processor, _llm_extractor, _validator, _config
    if _pdf_processor is None:
        _config = get_config()
        _pdf_processor = PDFProcessor(_config)
        _llm_extractor = LLMExtractor(_config)
        _validator = InvoiceValidator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    _initialize_processors()
    yield


app = FastAPI(
    title="Invoice Processing Service",
    description="Extract structured data from invoice PDFs using AI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEYS: Dict[str, str] = {}


def load_api_keys():
    """Load API keys from environment."""
    global API_KEYS
    config = get_config()
    keys = config.api_keys or ""
    API_KEYS = {k.strip(): k.strip() for k in keys.split(",") if k.strip()}


async def verify_api_key(api_key: str) -> bool:
    """Verify API key against allowed keys."""
    if not API_KEYS:
        load_api_keys()
    return api_key in API_KEYS


@app.get("/health")
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
        500: {"description": "Internal server error"},
    },
)
async def extract_invoice(
    file: UploadFile = File(..., description="Invoice PDF file"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Extract structured data from uploaded invoice PDF.

    **Authentication:**
    - API key required in `X-API-Key` header

    **Returns:**
    - InvoiceData: Structured invoice data with products and metadata

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/extract" \
      -H "X-API-Key: your-api-key" \
      -F "file=@invoice.pdf"
    ```
    """
    _initialize_processors()

    if not x_api_key or not await verify_api_key(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    temp_dir = _config.output_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_pdf_path = temp_dir / f"{uuid.uuid4()}-{file.filename}"
    try:
        content = await file.read()
        temp_pdf_path.write_bytes(content)

        text_grid, metadata = _pdf_processor.extract_content(temp_pdf_path)

        invoice_data = _llm_extractor.parse_with_llm(text_grid)
        validated_invoice = _validator.validate_invoice(invoice_data)

        return validated_invoice

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction failed: {str(e)}",
        )
    finally:
        if temp_pdf_path.exists():
            temp_pdf_path.unlink()


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom exception handler for better error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code},
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
