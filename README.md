# Invoice Processing Service

AI-powered invoice data extraction CLI and API service using GPT-4o-mini.

## Features

- **Hybrid Text Extraction**: Native PDF text (pdfplumber) with OCR fallback (Tesseract)
- **Text Grid Generation**: Preserves spatial layout to prevent column-swapping hallucinations
- **GPT-4o-mini Integration**: Structured JSON extraction with strict validation
- **Multiple Modes**: CLI for local processing, REST API for remote access
- **Docker Support**: Containerized deployment with multi-stage builds

## Installation

```bash
# Install in editable mode
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"

# Install with API dependencies (FastAPI, uvicorn)
pip install -e ".[api]"
```

## Configuration

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-proj-...
APP_ENV=local
API_HOST=0.0.0.0
API_PORT=8000
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
ALLOWED_ORIGINS=http://localhost:5173,https://lavio.vercel.app
SCALE_FACTOR=0.2
TOLERANCE=3
OCR_DPI=150
MAX_PDF_SIZE_MB=2
OCR_LANGUAGES=ron+eng+rus
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0
```

See `.env.example` for all available options.

### Production requirements

When `APP_ENV=production`, startup config validation fails fast unless:

- `ALLOWED_ORIGINS` is explicitly set (no fallback).
- `ALLOW_API_KEY_AUTH` is unset/false.
- Debug/observability headers are off unless explicitly allowed:
  - set `EXTRACT_CACHE_DEBUG_HEADERS=false` and `EXTRACT_OBSERVABILITY_HEADERS=false`
  - or set `ALLOW_PROD_DEBUG_HEADERS=true` (override)

## CLI Usage

```bash
# Process an invoice (default mode)
invproc process invoice.pdf

# Process with debug output (saves text grids)
invproc process invoice.pdf --debug

# Process with mock data (no API calls)
invproc process invoice.pdf --mock

# Process with verbose logging
invproc process invoice.pdf --verbose

# Run consistency check (N times, compare results)
invproc process invoice.pdf --retry 3

# Save output to file
invproc process invoice.pdf --output results/invoice.json
```

## API Usage

### Local Development

```bash
# Start API server (use repo code, not an accidentally-installed invproc from elsewhere)
PYTHONPATH=src python -m invproc --mode api

# Or:
./bin/dev-api

# Access API documentation at http://localhost:8000/docs
```

If debugging a running server, verify which code is imported:

```bash
PYTHONPATH=src python -c "import invproc.api; print(invproc.api.__file__)"
```

### Extract Cache Debug Headers

When enabled, `/extract` returns:

- `X-Extract-Cache: hit|miss|off`
- `X-Instance-Id` and `X-Process-Id` (only when `EXTRACT_OBSERVABILITY_HEADERS=true` or `EXTRACT_CACHE_DEBUG_HEADERS=true`)
- `X-Extract-File-Hash` (only when `EXTRACT_CACHE_DEBUG_HEADERS=true`)

### Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up -d

# Access API documentation at http://localhost:8000/docs
```

### API Endpoints

#### Health Check
```bash
GET /health
```

Returns service health status (no authentication required).

#### Extract Invoice
```bash
POST /extract
Headers: Authorization: Bearer <supabase-jwt>
Body: multipart/form-data with "file" field
```

Extracts structured data from invoice PDF.

**Example:**
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "Authorization: Bearer <supabase-jwt>" \
  -F "file=@invoice.pdf"
```

**Response:**
```json
{
  "supplier": "METRO Cash & Carry",
  "invoice_number": "12345",
  "date": "2026-02-02",
  "total_amount": 1234.56,
  "currency": "MDL",
  "products": [
    {
      "raw_code": "1234567890123",
      "name": "Product Name",
      "uom": "BU",
      "quantity": 10.0,
      "unit_price": 12.34,
      "total_price": 123.40,
      "confidence_score": 0.95
    }
  ]
}
```

**Note on weighed `KG` rows**

When `uom` is `"KG"`, the backend normalizes fields for the import UI workflow:

- `quantity` is set to `1` (one weighed item / line)
- `unit_price` is set to `total_price` (VAT-inclusive end price per weighed item)
- the measured weight from the invoice (`Cant.`) is exposed via `weight_kg_candidate`

### Authentication

Supabase JWT authentication is required for protected endpoints (`/extract` and `/invoice/preview-pricing`).

Configure the backend with:
```env
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

Clients must send:
```http
Authorization: Bearer <supabase-access-token>
```

#### Local dev shortcut (optional)

For local testing in Swagger (`/docs`) without Supabase, you can enable API key auth explicitly:

```bash
export ALLOW_API_KEY_AUTH=true
export API_KEYS=dev-key-12345
```

Then click **Authorize** in Swagger UI and paste `dev-key-12345` as the token.

### Troubleshooting

- For invoice MVP integration issues (auth mismatch, parser edge cases, API scope alignment), see:
  - `docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md`
- For FastAPI startup failure `ModuleNotFoundError: No module named 'supabase'`, see:
  - `docs/solutions/workflow-issues/fastapi-server-startup-fails-supabase-dependency-missing-20260217.md`
- For `/extract` 500 errors caused by zero-valued LLM product rows, see:
  - `docs/solutions/runtime-errors/zero-valued-llm-product-rows-caused-extract-500-20260211.md`

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test modules
pytest tests/test_api.py -v
pytest tests/test_cli.py -v

# Run with coverage report
pytest --cov=src/invproc --cov-report=html

# View HTML coverage report
open htmlcov/index.html
```

**Test Coverage:**
- API endpoints: 9 tests
- CLI commands: 6 tests
- End-to-end: 1 test
- Total: 16 tests

## Linting & Type Checking

```bash
# Lint code
ruff check src/

# Format code
ruff format src/

# Type check
mypy src/
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Application Layer                                    │
│  ┌─────────────────────┐  ┌─────────────────────┐   │
│  │      CLI          │  │      API          │   │
│  └─────────────────────┘  └─────────────────────┘   │
│            │                       │                │
└────────────┼───────────────────────┼────────────────┘
             │                       │
┌────────────┼───────────────────────┼────────────────┐
│  Extraction Layer                                │
│  ┌──────────────────────────────────────────────┐    │
│  │  PDFProcessor                          │    │
│  │  - pdfplumber (native text)            │    │
│  │  - Tesseract OCR (fallback)            │    │
│  │  - Text grid generation                │    │
│  └──────────────────────────────────────────────┘    │
└────────────┼────────────────────────────────────────┘
             │
┌────────────┼────────────────────────────────────────┐
│  AI Layer                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │  LLMExtractor                          │    │
│  │  - GPT-4o-mini                        │    │
│  │  - JSON structured output               │    │
│  └──────────────────────────────────────────────┘    │
└────────────┼────────────────────────────────────────┘
             │
┌────────────┼────────────────────────────────────────┐
│  Validation Layer                                │
│  ┌──────────────────────────────────────────────┐    │
│  │  InvoiceValidator                       │    │
│  │  - Math validation (qty × price = total) │    │
│  │  - Confidence scoring                  │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

## Text Grid Technique

The key innovation preventing LLM hallucinations:

1. **Extract words with coordinates** from pdfplumber (x0, y0, x1, y1)
2. **Group words into rows** by vertical position (configurable tolerance)
3. **Align horizontally** using space-padding (scale_factor = 0.2 chars per pixel)
4. **Generate plain-text representation** where columns line up visually

This spatial context prevents GPT from confusing Quantity vs. Price columns.

## Project Structure

```
InvoiceProcessing/
├── src/invproc/
│   ├── __init__.py
│   ├── __main__.py       # Entry point (CLI/API mode)
│   ├── cli.py            # CLI app (typer)
│   ├── api.py            # FastAPI application
│   ├── config.py         # Pydantic Settings
│   ├── pdf_processor.py   # PDF + OCR extraction
│   ├── llm_extractor.py  # OpenAI GPT integration
│   ├── models.py         # Pydantic data models
│   └── validator.py      # Validation & scoring
├── tests/
│   ├── __init__.py
│   └── test_api.py       # API endpoint tests
├── test_invoices/       # Sample PDFs for testing
├── output/              # Generated output (grids, results)
├── Dockerfile           # Multi-stage build
├── docker-compose.yml   # Service orchestration
├── pyproject.toml      # Project metadata
├── requirements.txt     # Python dependencies
└── .env.example        # Configuration template
```

## Docker

### Build Image

```bash
docker build -t invoice-processing-api:1.0.0 .
```

### Run Container

```bash
docker run -d \
  --name invoice-api \
  -p 8000:8000 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e SUPABASE_URL=https://your-project-ref.supabase.co \
  -e SUPABASE_SERVICE_ROLE_KEY=$SUPABASE_SERVICE_ROLE_KEY \
  invoice-processing-api:1.0.0
```

### Docker Compose

```bash
# Start service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop service
docker-compose down
```

## Cloud Deployment

### Render (Free Tier)

Quick deploy to Render with 1-click setup:

**Steps:**
1. Push code to GitHub
2. Create Render web service from repo
3. Add environment variables: `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
4. Deploy

**Detailed guide:** See [DEPLOYMENT.md](DEPLOYMENT.md)

**API URL:** `https://invoice-processing-api.onrender.com`

```bash
# Test health check
curl https://invoice-processing-api.onrender.com/health

# Test extraction
curl -X POST "https://invoice-processing-api.onrender.com/extract" \
  -H "Authorization: Bearer <supabase-jwt>" \
  -F "file=@invoice.pdf"
```

**Free tier limits:**
- 512MB RAM, 0.1 CPU
- Sleeps after 15min inactivity (cold start ~30s)
- 10 requests/minute rate limit

**Note:** For always-on service, upgrade to paid plan ($7/month).


## New to the project?

Start with [`docs/newcomer-guide.md`](docs/newcomer-guide.md) for a practical walkthrough of architecture, data flow, and what to learn first.

## License

MIT
