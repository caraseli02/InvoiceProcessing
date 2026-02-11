---
module: System
date: 2026-02-10
problem_type: security_issue
component: tooling
symptoms:
  - "API returned 413 for a valid file exactly at 50MB"
  - "Upload handler loaded full file into memory before enforcing size limits"
  - "Concurrent large uploads could still cause memory pressure despite size checks"
root_cause: wrong_api
resolution_type: code_fix
severity: high
tags: [upload-limits, multipart, dos-risk, memory-safety, fastapi]
---

# Troubleshooting: Multipart Upload Size Enforcement Rejected Valid Files and Preserved OOM Risk

## Problem
The invoice upload endpoint introduced a 50MB guard, but it used request-level multipart `Content-Length` and a full-buffer `read()`. This caused false `413` responses for valid boundary-size uploads and left memory pressure risk under concurrency.

## Environment
- Module: System
- Affected Component: FastAPI upload flow (`/extract`)
- Date: 2026-02-10

## Symptoms
- Uploading a file of exactly `50 * 1024 * 1024` bytes returned `413`.
- Error detail showed request body size greater than file size due to multipart overhead.
- Endpoint path still called `await file.read()`, materializing full upload in memory.

## What Didn't Work

**Attempted Solution 1:** Pre-read check using `request.headers["content-length"]` against `MAX_FILE_SIZE`.
- **Why it failed:** multipart `Content-Length` includes boundaries and form metadata, not only file bytes.

**Attempted Solution 2:** Post-read check using `len(content)` after `await file.read()`.
- **Why it failed:** enforcement occurred after full memory allocation, so it did not provide true memory safety.

## Solution
Replaced header-based/full-buffer validation with chunked stream-to-disk enforcement based on actual file bytes.

**Code changes:**
```python
# Before (broken)
content_length = request.headers.get("content-length")
if content_length and int(content_length) > MAX_FILE_SIZE:
    raise HTTPException(status_code=413, detail="...")

content = await file.read()
if len(content) > MAX_FILE_SIZE:
    raise HTTPException(status_code=413, detail="...")

await run_in_threadpool(temp_pdf_path.write_bytes, content)

# After (fixed)
UPLOAD_CHUNK_SIZE = 1024 * 1024

def _save_upload_with_limit(source: BinaryIO, destination: Path, max_file_size: int) -> int:
    source.seek(0)
    total_bytes = 0
    with destination.open("wb") as output_file:
        while True:
            chunk = source.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_file_size:
                raise HTTPException(status_code=413, detail="...")
            output_file.write(chunk)
    return total_bytes

await run_in_threadpool(_save_upload_with_limit, file.file, temp_pdf_path, MAX_FILE_SIZE)
```

**Test updates:**
- Boundary test now uses exactly 50MB and asserts it is not rejected by size guard.
- Concurrency test no longer accepts `500`; only expected statuses are permitted.
- Full suite passed after fix (`53 passed`).

## Why This Works
1. It enforces limits on actual uploaded file bytes, not multipart envelope size.
2. It enforces the limit during streaming, preventing full-file memory allocation before rejection.
3. It aligns test assertions with reliability goals (server errors must fail tests).

## Prevention
- Never use multipart `Content-Length` as a proxy for uploaded file size.
- Enforce upload limits while streaming in chunks.
- Include explicit boundary tests (`limit`, `limit+1`) for file-size controls.
- In concurrency tests, fail on `500` to avoid masking regressions.

## Related Issues
- See also: [no-rate-limiting-dos-attacks.md](./no-rate-limiting-dos-attacks.md)
- Related performance hardening: [blocking-io-async-prevents-concurrency.md](../performance-issues/blocking-io-async-prevents-concurrency.md)
- Related runtime hardening: [llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md](../runtime-errors/llm-malformed-product-rows-500-and-test-limiter-flakes-20260210.md)
