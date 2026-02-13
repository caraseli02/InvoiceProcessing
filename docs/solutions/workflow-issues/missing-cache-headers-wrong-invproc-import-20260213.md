---
module: Invoice Processing API
date: 2026-02-13
problem_type: workflow_issue
component: development_workflow
symptoms:
  - "Repeated identical /extract uploads did not get faster (no cache hits observed)"
  - "Response headers lacked X-Extract-Cache / X-Instance-Id / X-Process-Id even though the repo code set them"
  - "Local runs were inconsistent depending on how the API was started"
root_cause: python_import_path_mismatch
resolution_type: workflow_improvement
severity: medium
tags: [fastapi, pythonpath, imports, extract-cache, observability, devex]
related:
  - docs/solutions/workflow-issues/extract-cache-verification-observability-and-coverage-20260211.md
  - README.md
---

# Troubleshooting: Cache Headers Missing Because The Server Imported The Wrong `invproc`

## Problem

Frontend observed stable slow `/extract` latency even on repeated identical uploads, and did not see cache indicator headers in responses.

This was confusing because the repository code path includes:
- an in-memory cache for `/extract`
- explicit response headers for cache observability (`X-Extract-Cache`, plus instance/process headers on newer builds)

## Symptoms

- Repeated calls to `POST /extract` with the same PDF took ~60-80s each.
- Response headers only included defaults (`200 OK`, `server: uvicorn`, `date`), with no `X-Extract-Cache`.
- Restarting or changing environment variables sometimes “fixed” it, sometimes not.

## Root Cause

The API server was started in a way that caused Python to import `invproc` from a different location (an installed package or another checkout), not from the current repository’s `src/` tree.

In this state:
- `/extract` cache and observability headers may be missing (because that imported version doesn’t include them)
- performance measurements become invalid (you aren’t measuring the intended code)

This commonly happens when starting the API without `PYTHONPATH=src` and without a venv pinned to the same repo.

## Solution

### 1) Start the API using the repo code

From the repository root:

```bash
PYTHONPATH=src python -m invproc --mode api
```

Or use the dev script if present:

```bash
./bin/dev-api
```

### 2) Verify which file is being imported

```bash
PYTHONPATH=src python -c "import invproc.api; print(invproc.api.__file__)"
```

Expected output points into this repo (for example `.../InvoiceProcessing/src/invproc/api.py`), not `site-packages` and not a different checkout path.

### 3) Confirm cache behavior deterministically

Do two identical uploads and inspect headers:

```bash
curl -sS -D - -o /dev/null \
  -H "X-API-Key: <key>" \
  -F "file=@invoice.pdf;type=application/pdf" \
  http://127.0.0.1:8000/extract | rg -i 'x-extract-cache|x-instance-id|x-process-id|x-extract-file-hash|^http/'
```

Expected:
- first request: `X-Extract-Cache: miss`
- second request (same server process): `X-Extract-Cache: hit`

## Verification

- Manual: confirmed that starting with `PYTHONPATH=src` immediately restored headers and produced `miss` then `hit` behavior.
- Timing: cache hits were on the order of milliseconds, while misses remained dominated by PDF processing + LLM call.

## Prevention

- Document one canonical API startup command in `README.md` and prefer a script like `bin/dev-api`.
- When debugging “missing headers”, always check `/health` first and verify import path with `invproc.api.__file__`.
- For production cache verification, ensure response headers are forwarded through any proxy layer (for example Vercel routes) and are readable from browser JS (CORS `Access-Control-Expose-Headers`).

