---
date: 2026-02-03
topic: fastapi-docker-invoice-service
---

# Implementation Plan: FastAPI + Docker Invoice Processing Service

## Overview

Transform the existing CLI invoice processing tool into a containerized REST API service, enabling remote access for internal team members while preserving CLI functionality.

**Brainstorm:** `docs/brainstorms/2026-02-03-fastapi-docker-invoice-service-brainstorm.md`

**Approach:** FastAPI wrapper pattern - add API layer alongside existing CLI, reuse all extraction logic.

## Objectives

- [x] Brainstorm requirements and approach (completed)
- [x] Implement FastAPI API layer with `/extract` endpoint
- [x] Add Docker containerization with multi-stage build
- [x] Implement API key authentication for internal team
- [x] Add health check endpoint for orchestration
- [x] Test API functionality with existing test invoices
- [x] Validate Docker build and deployment

**Success Criteria:**
- API accepts PDF uploads via HTTP POST and returns structured JSON
- Docker container builds successfully and runs the service
- Internal team can access service with API key authentication
- Existing CLI functionality remains intact

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Docker Container                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │            FastAPI Application (port 8000)           │   │
│  │  ┌────────────────────────────────────────────┐     │   │
│  │  │  API Key Auth Middleware                  │     │   │
│  │  └────────────────────────────────────────────┘     │   │
│  │                                                      │   │
│  │  Endpoints:                                          │   │
│  │  - GET  /health  (no auth)                          │   │
│  │  - POST /extract (auth required)                     │   │
│  │                                                      │   │
│  │  ┌────────────────────────────────────────────┐     │   │
│  │  │  PDFProcessor (existing)                   │     │   │
│  │  │  LLMExtractor (existing)                   │     │   │
│  │  │  InvoiceValidator (existing)              │     │   │
│  │  └────────────────────────────────────────────┘     │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Data Flow:**
1. Client sends PDF to `/extract` with API key header
2. Auth middleware validates API key
3. Endpoint receives PDF file
4. PDFProcessor extracts text and generates text grid
5. LLMExtractor calls OpenAI API for structured extraction
6. InvoiceValidator validates results
7. Return InvoiceData as JSON

## File Structure

```
InvoiceProcessing/
├── src/
│   └── invproc/
│       ├── __init__.py
│       ├── __main__.py           # CLI entry point (unchanged)
│       ├── cli.py                # CLI app (unchanged)
│       ├── api.py                # NEW - FastAPI application
│       ├── config.py             # Add API-specific config
│       ├── pdf_processor.py      # (unchanged)
│       ├── llm_extractor.py      # (unchanged)
│       ├── models.py             # (unchanged)
│       └── validator.py          # (unchanged)
├── docs/
│   ├── plans/
│   │   └── 2026-02-03-fastapi-docker-invoice-service-plan.md
│   └── brainstorms/
│       └── 2026-02-03-fastapi-docker-invoice-service-brainstorm.md
├── Dockerfile                    # NEW
├── docker-compose.yml            # NEW
├── requirements.txt              # Update with FastAPI deps
├── pyproject.toml                # Update dependencies
├── .env                          # Add API keys
└── .env.example                  # Update with new env vars
```

## Implementation Phases

### Phase 1: FastAPI API Layer (Days 1-3)

**Dependencies:**

Add to `requirements.txt`:
```txt
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.9
```

Update `pyproject.toml`:
```toml
[project.optional-dependencies]
api = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "python-multipart>=0.0.9",
]
```

**Create `src/invproc/api.py`:**

```python
"""FastAPI application for invoice processing service."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from invproc.config import get_config
from invproc.llm_extractor import LLMExtractor
from invproc.pdf_processor import PDFProcessor
from invproc.models import InvoiceData
from invproc.validator import InvoiceValidator

# Global state
_pdf_processor: PDFProcessor = None
_llm_extractor: LLMExtractor = None
_validator: InvoiceValidator = None
_config = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global _pdf_processor, _llm_extractor, _validator, _config

    # Startup
    _config = get_config()
    _pdf_processor = PDFProcessor(_config)
    _llm_extractor = LLMExtractor(_config, mock=False)
    _validator = InvoiceValidator(_config)

    yield

    # Shutdown (cleanup if needed)
    _pdf_processor = None
    _llm_extractor = None
    _validator = None


# Create FastAPI app
app = FastAPI(
    title="Invoice Processing Service",
    description="Extract structured data from invoice PDFs using AI",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API key storage (in-memory for internal team)
# TODO: Move to database or secret management for production
API_KEYS: Dict[str, str] = {}


def load_api_keys():
    """Load API keys from environment."""
    global API_KEYS
    if _config:
        keys = _config.api_keys or ""
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
    x_api_key: str = None,  # Custom header for API key
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
    # Verify API key
    if not await verify_api_key(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported",
        )

    # Save uploaded file temporarily
    temp_pdf_path = Path(f"/tmp/{file.filename}")
    try:
        content = await file.read()
        temp_pdf_path.write_bytes(content)

        # Extract invoice data
        text, pages = _pdf_processor.extract_content(temp_pdf_path)
        text_grid = _pdf_processor.generate_text_grid(text)

        invoice_data = _llm_extractor.extract(text_grid, pages)
        validated_invoice = _validator.validate_invoice(invoice_data)

        return validated_invoice

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction failed: {str(e)}",
        )
    finally:
        # Cleanup temporary file
        if temp_pdf_path.exists():
            temp_pdf_path.unlink()


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom exception handler for better error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "invproc.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
```

**Update `src/invproc/config.py`:**

Add API configuration to `InvoiceConfig` class:

```python
class InvoiceConfig(BaseSettings):
    # ... existing fields ...

    # API configuration
    api_host: str = Field(default="0.0.0.0", description="API host address")
    api_port: int = Field(default=8000, description="API port")
    api_keys: str = Field(
        default="",
        description="Comma-separated API keys for authentication",
    )
```

**Update `src/invproc/__main__.py`:**

Add option to run API server:

```python
import typer
from typing import Optional

from invproc.cli import app as cli_app
from invproc.api import app as api_app

# ...

def main(
    mode: str = typer.Option(
        "cli",
        "--mode",
        help="Run mode: cli (default) or api",
    ),
):
    """Invoice processing tool - CLI or API mode."""
    if mode == "api":
        import uvicorn
        uvicorn.run(
            "invproc.api:app",
            host="0.0.0.0",
            port=8000,
            reload=False,
        )
    else:
        cli_app()

if __name__ == "__main__":
    typer.run(main)
```

### Phase 2: Docker Configuration (Days 4-5)

**Create `Dockerfile`:**

```dockerfile
# Multi-stage build for optimized image size

# Stage 1: Builder
FROM python:3.12-slim as builder

# Install system dependencies for Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ron \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ron \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Install application
RUN pip install --no-cache-dir -e .

# Create output directories
RUN mkdir -p output/grids output/ocr_debug output/results

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run API server by default
CMD ["python", "-m", "invproc", "--mode", "api"]
```

**Create `docker-compose.yml`:**

```yaml
version: "3.8"

services:
  invoice-api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: invoice-processing-api
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - API_KEYS=${API_KEYS:-dev-key-12345}
      - SCALE_FACTOR=0.2
      - TOLERANCE=3
      - OCR_DPI=300
      - OCR_LANGUAGES=ron+eng+rus
      - LLM_MODEL=gpt-4o-mini
      - LLM_TEMPERATURE=0
    volumes:
      # Mount for development (optional - remove for production)
      - ./src:/app/src
      - ./output:/app/output
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 5s
```

**Create `.dockerignore`:**

```dockerignore
__pycache__
*.pyc
*.pyo
*.pyd
.Python
*.so
.git
.gitignore
docs/
.pytest_cache/
.vscode/
.idea/
*.log
.env.local
output/grids/*
output/ocr_debug/*
output/results/*
!output/.gitkeep
```

**Create `output/.gitkeep`:**

```bash
# Ensure output directories exist
touch output/grids/.gitkeep
touch output/ocr_debug/.gitkeep
touch output/results/.gitkeep
```

### Phase 3: Testing (Days 6-7)

**Test API locally:**

```bash
# Start API server
python -m invproc --mode api

# In another terminal, test with curl
curl -X GET http://localhost:8000/health

curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: dev-key-12345" \
  -F "file=@test_invoices/invoice-test.pdf" \
  | jq .
```

**Test with Docker:**

```bash
# Build image
docker-compose build

# Start service
docker-compose up -d

# Check logs
docker-compose logs -f

# Test health endpoint
curl http://localhost:8000/health

# Test extraction endpoint
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: dev-key-12345" \
  -F "file=@test_invoices/invoice-test.pdf" \
  | jq .

# Stop service
docker-compose down
```

**Test authentication:**

```bash
# Test without API key (should fail)
curl -X POST "http://localhost:8000/extract" \
  -F "file=@test_invoices/invoice-test.pdf"

# Test with invalid API key (should fail)
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: invalid-key" \
  -F "file=@test_invoices/invoice-test.pdf"

# Test with valid API key (should succeed)
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: dev-key-12345" \
  -F "file=@test_invoices/invoice-test.pdf"
```

**Create test script `tests/test_api.py`:**

```python
"""FastAPI endpoint tests."""

import pytest
from fastapi.testclient import TestClient
from invproc.api import app

client = TestClient(app)

def test_health_check():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_extract_without_auth():
    """Test extraction without API key."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")}
        )
    assert response.status_code == 401

def test_extract_with_invalid_auth():
    """Test extraction with invalid API key."""
    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"X-API-Key": "invalid-key"}
        )
    assert response.status_code == 401

def test_extract_with_valid_auth():
    """Test extraction with valid API key."""
    # Set valid API key for test
    import os
    os.environ["API_KEYS"] = "test-api-key"

    with open("test_invoices/invoice-test.pdf", "rb") as f:
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", f, "application/pdf")},
            headers={"X-API-Key": "test-api-key"}
        )
    assert response.status_code == 200
    data = response.json()
    assert "supplier" in data
    assert "products" in data
    assert len(data["products"]) > 0
```

### Phase 4: Documentation & Cleanup (Day 8)

**Update `.env.example`:**

```env
# OpenAI API Key
OPENAI_API_KEY=sk-proj-...

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_KEYS=dev-key-12345

# ... existing configuration ...
```

**Update `README.md`:**

Add API usage section:

```markdown
## API Usage

### Local Development

```bash
# Start API server
python -m invproc --mode api

# Access API documentation at http://localhost:8000/docs
```

### Docker Deployment

```bash
# Build and run
docker-compose up -d

# Access API documentation at http://localhost:8000/docs
```

### API Endpoints

#### Health Check
```bash
GET /health
```

Returns service health status.

#### Extract Invoice
```bash
POST /extract
Headers: X-API-Key: <your-api-key>
Body: multipart/form-data with "file" field
```

Extracts structured data from invoice PDF.

**Example:**
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: your-api-key" \
  -F "file=@invoice.pdf"
```

### Authentication

API key authentication is required for the `/extract` endpoint. Set your API keys in the `API_KEYS` environment variable (comma-separated).

For development: `API_KEYS=dev-key-12345`

For production: Generate secure API keys using:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
```

## Configuration

### Environment Variables

Add to `.env`:

```env
# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_KEYS=dev-key-12345,another-key-67890
```

### API Key Management

**For development:**
```env
API_KEYS=dev-key-12345
```

**For production:**
```env
# Generate secure API keys
API_KEYS=prod-key-<token>,team-key-<token>
```

Generate secure API key:
```python
import secrets
api_key = secrets.token_urlsafe(32)
print(api_key)  # e.g., "xKj8mN2pQ5rT7vY9wZ3b"
```

## Testing Strategy

### Unit Tests
- Health check endpoint
- Authentication middleware
- Error handling

### Integration Tests
- End-to-end invoice extraction
- PDF upload handling
- Validation logic

### Manual Tests
- API via Swagger UI (`/docs`)
- cURL commands
- Docker deployment

### Test Coverage Goals
- API endpoints: 100%
- Authentication: 100%
- Error handling: 80%+

## Deployment Strategy

### Development
```bash
python -m invproc --mode api
```

### Docker (Recommended for production)

**Build:**
```bash
docker build -t invoice-processing-api:1.0.0 .
```

**Run:**
```bash
docker run -d \
  --name invoice-api \
  -p 8000:8000 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e API_KEYS=prod-key-12345 \
  invoice-processing-api:1.0.0
```

**Docker Compose (Development):**
```bash
docker-compose up -d
```

### Production Considerations

**Future Enhancements (out of scope):**
- API key rotation
- Rate limiting (slowapi)
- Metrics (Prometheus)
- Logging (structured JSON)
- Secret management (AWS Secrets Manager, HashiCorp Vault)
- HTTPS/TLS termination
- Load balancing (nginx, Traefik)

## Rollback Plan

If issues occur after deployment:

1. **Immediate rollback:**
   ```bash
   docker-compose down
   docker-compose up -d --scale invoice-api=0
   # Revert to previous version
   docker tag invoice-processing-api:prev invoice-processing-api:latest
   docker-compose up -d
   ```

2. **Diagnostic mode:**
   ```bash
   docker-compose logs --tail=100
   docker exec -it invoice-api bash
   ```

3. **Fallback to CLI:**
   ```bash
   python -m invproc process test_invoices/invoice-test.pdf
   ```

## Success Metrics

- [ ] API `/extract` endpoint accepts PDF uploads
- [ ] Returns structured InvoiceData JSON matching Pydantic model
- [ ] API key authentication works (401 on invalid, 200 on valid)
- [ ] Docker image builds successfully
- [ ] Docker container runs and serves API on port 8000
- [ ] Health check endpoint returns 200 OK
- [ ] Swagger UI accessible at `/docs`
- [ ] All tests pass
- [ ] CLI functionality unchanged

## Timeline

- **Phase 1:** Days 1-3 - FastAPI implementation
- **Phase 2:** Days 4-5 - Docker configuration
- **Phase 3:** Days 6-7 - Testing
- **Phase 4:** Day 8 - Documentation & cleanup

**Total:** 8 working days (1.6 weeks)

## Dependencies

### Required Packages
- fastapi>=0.109.0
- uvicorn[standard]>=0.27.0
- python-multipart>=0.0.9

### System Dependencies
- tesseract-ocr (for Docker image)
- curl (for health checks)

## Checklist

### Pre-Implementation
- [x] Brainstorm completed
- [x] Requirements clarified
- [x] Approach chosen
- [x] Update dependencies in requirements.txt
- [x] Update dependencies in pyproject.toml

### Implementation
- [x] Create `src/invproc/api.py`
- [x] Update `src/invproc/config.py` with API config
- [x] Update `src/invproc/__main__.py` with mode selection
- [x] Create `Dockerfile`
- [x] Create `docker-compose.yml`
- [x] Create `.dockerignore`
- [x] Update `.env.example`

### Testing
- [x] Create `tests/test_api.py`
- [x] Test API endpoints locally
- [ ] Test Docker build and run
- [x] Test authentication
- [x] Run all tests

### Documentation
- [ ] Update `README.md` with API usage
- [ ] Document API endpoints
- [ ] Document authentication
- [ ] Document Docker deployment

### Cleanup
- [x] Remove debug code
- [x] Verify no hardcoded secrets
- [x] Update gitignore for output files
- [ ] Commit changes with descriptive message
