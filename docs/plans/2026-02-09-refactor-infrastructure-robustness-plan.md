---
title: Infrastructure and Robustness Improvements
type: refactor
date: 2026-02-09
---

# Infrastructure and Robustness Improvements

## Overview

Improve production reliability by addressing critical gaps in error handling, test coverage, API resilience, resource limits, and logging. These improvements prevent production failures (OOM, hung requests) and make debugging easier in cloud deployments.

## Problem Statement

The invoice processing service lacks critical production hardening:

1. **No timeout on OpenAI API calls** - Hung requests block the entire pipeline
2. **Missing error path tests** - Only happy paths are covered; malformed PDFs, timeouts, and invalid responses aren't tested
3. **No PDF size limits** - Large PDFs can cause OOM crashes
4. **Print-style logging** - Non-structured logs are hard to parse in cloud environments (Render, etc.)
5. **METRO-specific system prompt** - Not reusable for other invoice formats
6. **Dockerfile mismatch** - CMD uses non-existent `--mode api` flag

## Critical Gaps to Address

SpecFlow analysis revealed 5 critical blockers that must be addressed:

1. **Timeout in retry loop** - With `--retry 5` + 60s timeout = 5 minute block. Each retry must also timeout.

2. **Memory safety in API** - API loads entire file into RAM (`await file.read()`) before size check. 50MB file = 50MB in RAM per request. Must stream-read or check Content-Length header first.

3. **Global config race condition** - Singleton config shared across FastAPI workers. Concurrent requests with hot-reload could cause inconsistency. Make config immutable or thread-safe.

4. **No per-request column headers** - Single-tenant only. Multi-tenant API needs client-specific invoice formats. Add headers parameter to API endpoint.

5. **CLI JSON logging** - No `--json-logs` flag to enable structured logging from CLI. Only config file supports it.

## Proposed Solution

Implement 7 focused improvements in priority order, each as a small, independently deployable PR:

### High Priority (Week 1)

**1. Add OpenAI API Timeout**
- Configure 60-second timeout on OpenAI client initialization
- Catches slow/hung API responses

**2. Error Path Test Coverage**
- Test malformed PDFs, LLM API timeouts, empty pages, invalid JSON responses
- Uses pytest with `pytest.raises()` pattern
- Leverages existing mock mode for API timeout simulation

### Medium Priority (Week 2)

**3. Generalize System Prompt**
- Extract METRO-specific Romanian headers to configurable template
- Add auto-detection layer or template injection
- Makes service reusable for other invoice formats

**4. Large PDF Guard**
- Add page limit (max 50 pages) in `pdf_processor.py`
- Add file size check (max 50 MB) in CLI and API
- Prevents OOM on unexpectedly large PDFs

**5. Structured JSON Logging**
- Replace print-style logging with JSON-structured logs
- Cloud-friendly: parseable by log aggregators (Datadog, CloudWatch, etc.)
- Preserve existing module-level logger pattern

### Low Priority (Week 3)

**6. Property-Based Tests for Text Grid**
- Use hypothesis or pydantic-fuzz for fuzzing word coordinates
- Tests grid generation edge cases (overlapping words, extreme coordinates)
- Core innovation validation

**7. Fix Dockerfile CMD**
- Remove non-existent `--mode api` flag
- Match actual CLI interface from `cli.py`

## Technical Considerations

### 1. OpenAI API Timeout

**Location:** `src/invproc/llm_extractor.py:23`

**Current code:**
```python
self.client = OpenAI(api_key=config.openai_api_key)
```

**Proposed:**
```python
self.client = OpenAI(
    api_key=config.openai_api_key,
    timeout=60.0  # 60 second timeout
)
```

**Impact:**
- Raises `openai.APITimeoutError` on timeout
- Already handled by existing exception handling (line 74-85)

**Critical: Retry Loop Handling**
- CLI `--retry N` flag calls `_extract_single()` N times
- Each call uses same timeout (60s)
- With `--retry 5`: worst case = 5 × 60s = 300s (5 min) blocked
- **No fix needed** - timeout is per-call, user expects total time = N × timeout
- Document this behavior in CLI help text

### 2. Error Path Tests

**New test file:** `tests/test_error_paths.py`

**Test cases:**
```python
# Malformed PDF - not actually a PDF
@pytest.mark.parametrize("filename", ["test.txt", "corrupt.pdf", "empty.pdf"])
def test_malformed_pdf(pdf_processor, filename):
    with pytest.raises(ValueError, match="Could not process PDF"):
        pdf_processor.extract_content(f"test_data/{filename}")

# LLM API timeout - use mock mode
def test_llm_timeout(llm_extractor):
    llm_extractor.mock = False  # Force real API
    # Patch OpenAI client to simulate timeout
    with patch.object(llm_extractor.client, "chat.completions.create") as mock_create:
        mock_create.side_effect = APITimeoutError("Request timed out")
        with pytest.raises(APITimeoutError):
            llm_extractor.parse_with_llm("test grid")

# Empty pages in PDF
def test_empty_pages(pdf_processor):
    text_grid, metadata = pdf_processor.extract_content("test_data/empty_pages.pdf")
    assert len(text_grid) == 0 or all(page.strip() == "" for page in text_grid)

# Invalid JSON from GPT
def test_invalid_json_response(llm_extractor):
    llm_extractor.mock = False
    with patch.object(llm_extractor.client, "chat.completions.create") as mock_create:
        mock_create.return_value = Mock(
            choices=[Mock(message=Mock(content="{invalid json}"))]
        )
        with pytest.raises(json.JSONDecodeError):
            llm_extractor.parse_with_llm("test grid")

# API file size guard (memory safety)
def test_api_file_size_guard():
    from fastapi.testclient import TestClient
    from io import BytesIO

    client = TestClient(app)

    # Create 51 MB file (exceeds 50 MB limit)
    large_file = BytesIO(b"x" * (51 * 1024 * 1024))
    large_file.name = "large.pdf"

    response = client.post(
        "/extract",
        files={"file": ("large.pdf", large_file, "application/pdf")},
        headers={"X-API-Key": "test-api-key"}
    )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()

# API config race condition (concurrent requests)
def test_api_config_race_condition():
    from fastapi.testclient import TestClient
    import threading

    client = TestClient(app)
    results = []

    def make_request():
        response = client.post(
            "/extract",
            files={"file": ("test.pdf", open("test_invoices/invoice-test.pdf", "rb"), "application/pdf")},
            headers={"X-API-Key": "test-api-key"}
        )
        results.append(response.status_code)

    # Make 10 concurrent requests
    threads = [threading.Thread(target=make_request) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All requests should succeed (no race conditions)
    assert all(status == 200 for status in results)
```

**Dependencies:**
- `pytest` (already in dev dependencies)
- `pytest-mock` (add to dev dependencies)
- Mock test data files: `tests/test_data/malformed.pdf`, `empty.pdf`
- **Threading support** (for config race condition test)

### 3. Generalize System Prompt

**Location:** `src/invproc/llm_extractor.py:115-182`

**Current approach:**
- Hardcoded Romanian headers: "Cant.", "Pret unitar", "Valoare incl.TVA"
- Single system prompt for all invoices

**Proposed approach:**
- Extract column headers to config template
- Add `column_headers` config field with default METRO values
- Use f-string injection in system prompt

**New config fields:**
```python
# In config.py
column_headers: ColumnHeadersConfig = Field(
    default_factory=lambda: ColumnHeadersConfig(
        quantity="Cant.",
        unit_price="Pret unitar",
        total_price="Valoare incl.TVA"
    ),
    description="Column header names for invoice format detection"
)

class ColumnHeadersConfig(BaseModel):
    quantity: str = "Cant."
    unit_price: str = "Pret unitar"
    total_price: str = "Valoare incl.TVA"
```

**System prompt changes:**
```python
system_prompt = f"""Extract invoice data from text grid.

Column mapping:
- Quantity column: {self.config.column_headers.quantity}
- Unit price column: {self.config.column_headers.unit_price}
- Total column: {self.config.column_headers.total_price}

[rest of prompt...]
"""
```

**Future extension:**
- Auto-detection via header matching
- Multi-format support (configurable per invoice)

### 4. Large PDF Guard

**Two guards needed:**

**A. Page limit in `pdf_processor.py:45`**
```python
MAX_PAGES = 50  # Configurable via config.pdf_max_pages

# In extract_content()
for i, page in enumerate(pdf.pages):
    if i >= MAX_PAGES:
        raise ValueError(f"PDF exceeds maximum page limit of {MAX_PAGES}")
    # rest of processing
```

**B. File size check in CLI and API**

**CLI (`cli.py:50`):**
```python
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

if input_file.stat().st_size > MAX_FILE_SIZE:
    raise typer.BadParameter(f"File too large: {input_file.stat().st_size} bytes (max {MAX_FILE_SIZE})")
```

**API (`api.py:166`):**
```python
# CRITICAL: Check size BEFORE loading into RAM
# FastAPI provides content-length header automatically
content_length = request.headers.get("content-length", 0)
if int(content_length) > MAX_FILE_SIZE:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"File too large: {content_length} bytes (max {MAX_FILE_SIZE})"
    )

# Stream-read to avoid OOM
content = await file.read()
```

### 5. Structured JSON Logging

**Option A: Python `structlog` library (recommended)**
- Industry standard for structured logging
- Easy integration with existing logger pattern
- Supports JSON format out of the box

**Option B: Custom JSON formatter**
- Lighter dependency
- More control over output format
- Less feature-rich

**Recommended: Option A with structlog**

**Installation:**
```bash
pip install structlog
```

**Configuration in `config.py`:**
```python
structured_logging: bool = Field(
    default=False,
    description="Enable JSON-structured logging (for cloud deployments)"
)
```

**Logger setup in `cli.py:87-90`:**
```python
import structlog

if config.structured_logging:
    structlog.configure(
        processors=[
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    logger = structlog.get_logger()
else:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
```

**API logging setup in `api.py:__init__`:**
```python
if config.structured_logging:
    structlog.configure(processors=[structlog.processors.JSONRenderer()])
```

**Log format comparison:**

**Before (print-style):**
```
2026-02-09 14:30:45 - invproc.pdf_processor - INFO - Page 1: Native text, 234 words
```

**After (JSON):**
```json
{
  "event": "Page processed",
  "logger": "invproc.pdf_processor",
  "level": "info",
  "page": 1,
  "extraction_type": "Native text",
  "word_count": 234,
  "timestamp": "2026-02-09T14:30:45.123456Z"
}
```

### 6. Property-Based Tests for Text Grid

**Library:** `hypothesis` (property-based testing framework)

**Installation:**
```bash
pip install hypothesis
```

**New test file:** `tests/test_text_grid_fuzzing.py`

**Test cases:**
```python
from hypothesis import given, strategies as st
from hypothesis.extra.pydantic import from_type
from invproc.pdf_processor import PDFProcessor

@given(
    words=st.lists(
        st.builds(
            dict,
            x0=st.floats(min_value=0, max_value=1000),
            top=st.floats(min_value=0, max_value=1000),
            text=st.text(min_size=1, max_size=50)
        ),
        min_size=0,
        max_size=500
    ),
    scale_factor=st.floats(min_value=0.1, max_value=1.0),
    row_tolerance=st.integers(min_value=1, max_value=10)
)
def test_text_grid_generation_hypothesis(words, scale_factor, row_tolerance):
    """Test grid generation with various word coordinates."""
    processor = PDFProcessor(get_config())
    text_grid = processor._build_text_grid(words, scale_factor, row_tolerance)

    # Invariants
    assert isinstance(text_grid, str)
    assert len(text_grid) <= len(words) * 100  # Reasonable max length

    # No word should be lost
    all_texts = " ".join(w["text"] for w in words)
    assert all(text in text_grid for text in words if text["text"])

@given(
    overlapping_words=st.lists(
        st.builds(
            dict,
            x0=st.just(100.0),  # Same X position
            top=st.just(200.0),  # Same Y position
            text=st.text(min_size=1, max_size=10)
        ),
        min_size=2,
        max_size=10
    )
)
def test_overlapping_words(overlapping_words):
    """Test grid handles overlapping words gracefully."""
    processor = PDFProcessor(get_config())
    text_grid = processor._build_text_grid(overlapping_words, 0.2, 3)
    assert isinstance(text_grid, str)
    assert len(text_grid) > 0

@given(
    extreme_coords=st.lists(
        st.builds(
            dict,
            x0=st.floats(min_value=-1000, max_value=10000),
            top=st.floats(min_value=-1000, max_value=10000),
            text=st.text(min_size=1, max_size=10)
        ),
        min_size=1,
        max_size=20
    )
)
def test_extreme_coordinates(extreme_coords):
    """Test grid handles extreme coordinates."""
    processor = PDFProcessor(get_config())
    text_grid = processor._build_text_grid(extreme_coords, 0.2, 3)
    assert isinstance(text_grid, str)
```

### 7. Fix Dockerfile CMD

**Current Dockerfile CMD (line XX):**
```dockerfile
CMD ["python", "-m", "invproc", "api", "--mode", "api"]
```

**Problem:** `--mode api` flag doesn't exist in CLI

**Fixed CMD:**
```dockerfile
CMD ["uvicorn", "invproc.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

Or for CLI mode:
```dockerfile
CMD ["python", "-m", "invproc", "process"]
```

**Check `Dockerfile` to determine intended mode (API vs CLI).**

### 8. Global Config Race Condition (CRITICAL)

**Issue:** Singleton config (`_config_instance`) shared across FastAPI workers. With uvicorn's `--workers N`, multiple workers share the same Python process state. Concurrent requests could cause inconsistent config state.

**Location:** `src/invproc/config.py:194-219`

**Current code:**
```python
_config_instance = None

def get_config() -> InvoiceConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()
    return _config_instance
```

**Options:**

**Option A: Make config immutable (Recommended)**
- Pydantic models are already immutable by default (frozen=True)
- Prevent config modification after initialization
- Workers read from shared immutable state

**Option B: Per-worker config initialization**
- Each worker initializes its own config at startup
- Use `@lru_cache` with no arguments to cache per-process

**Option C: Thread-safe singleton with lock**
- Add threading.Lock around initialization
- Prevents race during first-time creation
- Overkill for read-only config

**Recommended: Option A (Immutable config)**
- Add `frozen=True` to Pydantic Settings
- Remove `reload_config()` or deprecate it
- Document that config is read-only after initialization

**Code changes:**
```python
# In config.py
class InvoiceConfig(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,  # Make config immutable
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

**Impact:**
- FastAPI workers safely share config (read-only)
- No race conditions on hot-reload
- `reload_config()` will fail (intentional - forces restart)

### 9. Per-Request Column Headers (Multi-Tenant API)

**Issue:** Current design assumes single tenant (global config). Multi-tenant API needs client-specific invoice formats.

**Current approach:**
```python
# API endpoint uses global config
extractor = LLMExtractor(get_config())  # Global column headers
```

**Proposed approach:**
```python
# API endpoint accepts optional headers parameter
@app.post("/extract")
async def extract_invoice(
    file: UploadFile,
    column_headers: Optional[str] = None,  # JSON string
    # ... other params
):
    config = get_config()
    if column_headers:
        # Override column headers for this request
        headers_dict = json.loads(column_headers)
        config = config.model_copy(update={
            "column_headers": ColumnHeadersConfig(**headers_dict)
        })
    extractor = LLMExtractor(config)
```

**API example:**
```bash
curl -X POST "http://localhost:8000/extract" \
  -H "X-API-Key: key" \
  -H "Column-Headers: {\"quantity\":\"Qty\",\"unit_price\":\"Price\",\"total_price\":\"Total\"}" \
  -F "file=@invoice.pdf"
```

**Impact:**
- Enables multi-tenant API (different clients, different formats)
- Backward compatible (defaults to global config)
- Adds complexity (JSON parsing, validation)

**Decision:** Mark as **future enhancement** (out of scope for this plan). Single-tenant only for now.

### 10. CLI JSON Logging Flag

**Issue:** `structured_logging` config flag only works via environment variables or config file. No CLI flag to enable.

**Proposed:** Add `--json-logs` CLI flag in `cli.py`

**Code changes:**
```python
@app.command()
def process(
    input_file: Annotated[Path, typer.Option(exists=True)],
    # ... existing flags
    json_logs: Annotated[bool, typer.Option("--json-logs", help="Enable JSON structured logging")] = False,
):
    config = get_config()
    if json_logs:
        config.structured_logging = True
    # ... rest of function
```

**Impact:**
- Enables JSON logging for one-off CLI calls
- Useful for debugging cloud deployments
- Minimal code change

**Decision:** Add as part of improvement #5 (Structured JSON Logging)

## Acceptance Criteria

### High Priority

- [ ] OpenAI client configured with 60-second timeout
- [ ] All error paths tested: malformed PDFs, API timeouts, empty pages, invalid JSON, large files, config race conditions
- [ ] New test file `tests/test_error_paths.py` with 6+ test cases
- [ ] Tests pass with `pytest tests/test_error_paths.py -v`
- [ ] Mock mode simulates timeout without real API call

### Medium Priority

- [ ] System prompt template uses configurable column headers
- [ ] `column_headers` config field with default METRO values
- [ ] Page limit guard (50 pages max) raises ValueError when exceeded
- [ ] File size guard (50 MB max) enforced in both CLI and API
- [ ] **API checks Content-Length header BEFORE loading file into RAM** (prevents OOM)
- [ ] Structured logging optional via `structured_logging` config flag
- [ ] **CLI `--json-logs` flag to enable structured logging**
- [ ] JSON logs parseable by log aggregators
- [ ] Existing print-style logs preserved when `structured_logging=False`

### Low Priority

- [ ] Property-based tests for text grid using hypothesis
- [ ] Test file `tests/test_text_grid_fuzzing.py` with 3+ hypothesis tests
- [ ] Dockerfile CMD matches actual CLI interface
- [ ] Docker build and run succeeds with fixed CMD
- [ ] **Config made immutable (frozen=True) to prevent race conditions**
- [ ] **Per-request column headers documented as future enhancement**

## Success Metrics

- **Test coverage:** Increase from happy-path-only to include error paths (target: 80%+ coverage)
- **Production incidents:** Eliminate OOM crashes from large PDFs (API memory safety)
- **API reliability:** Reduce hung request incidents with timeout handling (document retry behavior)
- **Config safety:** No race conditions on concurrent requests (immutable config)
- **Debug time:** Cloud logs are 2x faster to parse (structured vs unstructured)
- **Format support:** System prompt generalized to support at least 2 invoice formats (METRO + one other)

## Dependencies & Risks

### Dependencies

**Internal:**
- All improvements are independent (can ship in any order)
- No interdependencies between the 7 tasks
- Each PR is reviewable and deployable independently

**External:**
- `pytest-mock` (for error path tests)
- `hypothesis` (for property-based tests)
- `structlog` (for JSON logging)

**Add to dev dependencies in `pyproject.toml`:**
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-mock>=3.12.0",
    "hypothesis>=6.100.0",
    "structlog>=24.1.0",
    "ruff>=0.1.0",
    "mypy>=1.8.0"
]
```

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Timeout too aggressive (false timeouts) | Low | Medium | Use 60s (generous), make configurable |
| Retry loop blocks for 5 minutes | Low | Medium | Document `--retry N` behavior, recommend small N |
| API OOM before size check | Medium | High | Check Content-Length header before `file.read()` |
| Config race condition with hot-reload | Medium | High | Make config immutable (frozen=True) |
| Page limit blocks legitimate use cases | Low | Medium | Make configurable, document limit |
| Structured logging breaks existing log parsers | Low | Low | Opt-in via config flag, default off |
| Hypothesis tests slow CI | Medium | Low | Limit test runs with max_examples=100 |
| System prompt generalization breaks METRO format | Low | High | Keep METRO defaults, thorough testing |
| Multi-tenant API complexity | Low | Medium | Defer to future enhancement |

### Backward Compatibility

- All changes preserve existing CLI and API interfaces
- Config flags use opt-in defaults (structured_logging, page limit)
- **Config immutability: breaking change for `reload_config()` - requires restart to update config**
- **Per-request column headers: future enhancement only (not implemented)**
- No breaking changes to function signatures
- Dockerfile fix only corrects existing functionality

## Implementation Phases

### Phase 1: Production Stability (High Priority)

**Week 1:**

1. **Add OpenAI Timeout (30 min)**
   - Modify `llm_extractor.py`
   - Test with mock timeout simulation
   - Deploy, monitor for timeout errors

2. **Error Path Tests (2 hours)**
   - Create `tests/test_error_paths.py`
   - Add test data files
   - Verify all tests pass

**Deliverable:** 2 PRs, production more resilient to API issues

### Phase 2: Robustness & Reusability (Medium Priority)

**Week 2:**

3. **Generalize System Prompt (1 hour)**
   - Add `column_headers` config
   - Modify system prompt template
   - Test with existing METRO invoices
   - Document multi-tenant support as future enhancement

4. **Large PDF Guard (1.5 hours)**
   - Add page limit check in `pdf_processor.py`
   - Add file size check in CLI
   - **API: Check Content-Length header BEFORE loading file (prevents OOM)**
   - Add config fields for limits

5. **Structured JSON Logging (2 hours)**
   - Add `structlog` dependency
   - Implement JSON formatter
   - Add `structured_logging` config flag
   - **Add `--json-logs` CLI flag**
   - Document usage for cloud deployments

**Deliverable:** 3 PRs, service hardened against resource exhaustion and config race conditions

### Phase 3: Code Quality & Polish (Low Priority)

**Week 3:**

6. **Property-Based Tests (2 hours)**
   - Add `hypothesis` dependency
   - Create `tests/test_text_grid_fuzzing.py`
   - Verify hypothesis tests pass

7. **Fix Dockerfile CMD (15 min)**
   - Update CMD to match actual CLI
   - Test Docker build and run
   - Verify no regressions

8. **Config Immutability (30 min)**
   - Add `frozen=True` to `InvoiceConfig`
   - Test with multiple FastAPI workers
   - Verify no race conditions on concurrent requests

**Deliverable:** 3 PRs, codebase more maintainable and thread-safe

**Total:** 8 PRs, ~10 hours total, 3 weeks

## References & Research

### Internal References

- Error handling pattern: `src/invproc/llm_extractor.py:74-85`
- Testing framework: `pyproject.toml:22-23` (pytest)
- Configuration pattern: `src/invproc/config.py:194-219` (singleton)
- PDF processing: `src/invproc/pdf_processor.py:45` (page iteration)
- Logging setup: `src/invproc/cli.py:87-90` (basicConfig)

### External References

- OpenAI timeout: https://github.com/openai/openai-python#timeouts
- pytest-mock: https://pytest-mock.readthedocs.io/
- hypothesis: https://hypothesis.readthedocs.io/
- structlog: https://www.structlog.org/
- JSON logging best practices: https://12factor.net/logs

### Related Work

- Brainstorm: `docs/brainstorms/2026-02-06-comprehensive-project-improvement-brainstorm.md`
- Dockerfile: `Dockerfile`
- Current tests: `tests/test_*.py` (4 test files, 16 tests total)
