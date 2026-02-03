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
API_HOST=0.0.0.0
API_PORT=8000
API_KEYS=dev-key-12345
SCALE_FACTOR=0.2
TOLERANCE=3
OCR_DPI=300
OCR_LANGUAGES=ron+eng+rus
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0
```

See `.env.example` for all available options.

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
# Start API server
python -m invproc --mode api

# Access API documentation at http://localhost:8000/docs
```

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
Headers: X-API-Key: <your-api-key>
Body: multipart/form-data with "file" field
```

Extracts structured data from invoice PDF.

**Example:**
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: dev-key-12345" \
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
      "quantity": 10.0,
      "unit_price": 12.34,
      "total_price": 123.40,
      "confidence_score": 0.95
    }
  ]
}
```

### Authentication

API key authentication is required for the `/extract` endpoint. Set your API keys in the `API_KEYS` environment variable (comma-separated).

**Development:**
```env
API_KEYS=dev-key-12345
```

**Production:**
```env
API_KEYS=prod-key-abc123,team-key-xyz789
```

Generate secure API key:
```python
import secrets
api_key = secrets.token_urlsafe(32)
print(api_key)
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_api.py -v

# Run with coverage
pytest --cov=src --cov-report=html
```

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
  -e API_KEYS=prod-key-12345 \
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

## License

MIT
