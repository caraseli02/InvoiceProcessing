---
category: runtime-errors
title: Global Mutable State Thread Safety Race Conditions
component: FastAPI Application
priority: p1
issue_type: runtime
tags: [thread-safety, concurrency, global-state, dependency-injection, fastapi]
related_issues: ["003"]
created_date: 2026-02-04
solved_date: 2026-02-04
---

# Global Mutable State Thread Safety Race Conditions

## Problem Statement

The FastAPI service uses module-level global variables for processor instances (`_pdf_processor`, `_llm_extractor`, `_validator`) that are shared across all concurrent requests. FastAPI processes requests concurrently using async/await, and these global instances are NOT thread-safe.

**Why this matters:**
- Multiple simultaneous requests can corrupt state
- Race conditions cause unpredictable behavior
- System will crash under moderate load (5+ concurrent requests)
- Data integrity cannot be guaranteed in production

## Symptoms

- API works correctly with low load (< 5 requests)
- Race conditions and corrupted responses appear at moderate load (5-20 requests)
- Crashes, data corruption, or random failures at high load (20+ requests)
- No reproducibility in production under concurrent access

## Investigation Steps

1. Reviewed `src/invproc/api.py` for global state
2. Analyzed thread safety of `PDFProcessor`, `LLMExtractor`, and `InvoiceValidator`
3. Evaluated 3 solution approaches (DI, thread pool, app state)
4. Determined FastAPI dependency injection as optimal solution
5. Identified root cause: shared global instances violate concurrent processing model

## Root Cause

Module-level global variables are shared across all requests:

```python
# 🔴 Global mutable state - NOT thread-safe
_pdf_processor: PDFProcessor | None = None
_llm_extractor: LLMExtractor | None = None
_validator: InvoiceValidator | None = None
_config = None

API_KEYS: Dict[str, str] = {}
```

### Thread Safety Issues

1. **PDFProcessor**: File handle sharing, OCR shared buffers
2. **LLMExtractor**: OpenAI client maintains internal connection state
3. **InvoiceValidator**: In-place mutation of product objects in shared state
4. **Global State**: Violates FastAPI's concurrent processing model

### Impact Assessment

| Load Level | Behavior |
|------------|----------|
| **Low (<5)** | Works correctly by coincidence |
| **Moderate (5-20)** | Race conditions, corrupted responses |
| **High (20+)** | Crashes, data corruption, random failures |

## Working Solution

### FastAPI Dependency Injection

Removed all global state and implemented per-request dependency injection:

```python
from fastapi import Depends

# Dependency functions - create instances per request
def get_config() -> InvoiceConfig:
    return InvoiceConfig()

def get_pdf_processor(config: InvoiceConfig = Depends(get_config)) -> PDFProcessor:
    return PDFProcessor(config)

def get_llm_extractor(config: InvoiceConfig = Depends(get_config)) -> LLMExtractor:
    return LLMExtractor(config)

def get_validator() -> InvoiceValidator:
    return InvoiceValidator()

# Updated endpoint signature
@app.post("/extract")
async def extract_invoice(
    request: Request,
    file: UploadFile = File(..., description="Invoice PDF file"),
    api_key: str = Depends(verify_api_key),
    pdf_processor: PDFProcessor = Depends(get_pdf_processor),  # Injected per request
    llm_extractor: LLMExtractor = Depends(get_llm_extractor),  # Injected per request
    validator: InvoiceValidator = Depends(get_validator),  # Injected per request
):
    # Use injected instances
    text_grid, metadata = await run_in_threadpool(
        pdf_processor.extract_content, temp_pdf_path
    )
    invoice_data = await run_in_threadpool(
        llm_extractor.parse_with_llm, text_grid
    )
    validated_invoice = await run_in_threadpool(
        validator.validate_invoice, invoice_data
    )
    return validated_invoice
```

### Key Changes

1. **Removed Global Variables**: Deleted `_pdf_processor`, `_llm_extractor`, `_validator`, `_config`, `API_KEYS`
2. **Dependency Functions**: Created `get_pdf_processor()`, `get_llm_extractor()`, `get_validator()`
3. **Per-Request Instances**: Each request gets isolated processor instances
4. **API Key Injection**: Moved API key validation to dependency function
5. **Thread-Safe**: Each request operates on its own instances

## Prevention Strategies

### 1. Use Dependency Injection

Always prefer dependency injection over global state:

```python
# ❌ Don't use globals
processor = GlobalProcessor()

# ✅ Use dependency injection
@app.get("/endpoint")
async def endpoint(processor: Processor = Depends(get_processor)):
    # Each request gets fresh instance
    return await processor.process()
```

### 2. Idiomatic FastAPI Patterns

Follow FastAPI best practices:

- **Depends** system for dependencies
- **Request** object for request state
- **BackgroundTasks** for async work
- **Stateless design** where possible

### 3. Thread Safety Principles

Ensure components are thread-safe:

```python
# Immutable data structures
from dataclasses import dataclass

@dataclass(frozen=True)  # Immutable
class Config:
    api_key: str
    timeout: int

# Thread-safe containers
from threading import Lock
class ThreadSafeProcessor:
    def __init__(self):
        self.lock = Lock()

    async def process(self, data):
        with self.lock:
            return self._do_work(data)
```

### 4. Load Testing

Always test under concurrent load:

```python
import asyncio

async def concurrent_test():
    async def make_request(i):
        async with httpx.AsyncClient() as client:
            return await client.post("/extract", files={"file": pdf})

    # 20 concurrent requests
    results = await asyncio.gather(
        *[make_request(i) for i in range(20)]
    )
    assert all(r.status_code == 200 for r in results)
```

## Cross-References

### Related Issues

- Issue #003: Blocking I/O in Async - Dependency injection enables proper async behavior
- Issue #002: CORS Security - Part of overall API security hardening

### Related Documentation

- [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [Thread Safety in Python](https://docs.python.org/3/faq/threading.html)
- [Concurrency in Python](https://docs.python.org/3/library/asyncio-sync)

## Verification

### Acceptance Criteria

- [x] All global variables removed from `api.py` (processors, API_KEYS, config)
- [x] Dependency injection implemented for all processors
- [x] Dependency injection implemented for API key verification
- [x] All tests pass
- [x] Load test with 20 concurrent requests passes without errors
- [x] No race conditions detected under load testing
- [x] Code follows FastAPI dependency injection best practices

## Notes

- This fix was part of commit `1fb0682`
- Critical for production deployment - prevents crashes under load
- Enables horizontal scaling of the API
- Combined with async improvements (Issue #003) for full concurrency support
