---
module: API
date: 2025-02-06
problem_type: integration_issue
component: tooling
symptoms:
  - Production frontend (lavio.vercel.app) receiving 405 Method Not Allowed errors
  - CORS preflight OPTIONS requests blocked
  - POST requests to /extract endpoint failing from browser
root_cause: config_error
resolution_type: code_fix
severity: high
tags: [cors, preflight, fastapi, production-frontend]
---

# Troubleshooting: CORS 405 Errors with Production Frontend

## Problem

Production frontend (https://lavio.vercel.app) could not communicate with the deployed API, receiving 405 Method Not Allowed errors when attempting to upload invoices via the `/extract` endpoint.

## Environment

- Module: Invoice Processing API (FastAPI)
- Affected Component: FastAPI CORS Middleware configuration
- Date: 2025-02-06
- Production Frontend: https://lavio.vercel.app

## Symptoms

- Browser console shows 405 Method Not Allowed errors
- Preflight OPTIONS requests fail
- POST requests to `/extract` blocked by CORS policy
- API calls succeed from curl/Postman but fail from browser

## What Didn't Work

**Direct solution:** The problem was identified and fixed on the first attempt.

## Solution

Updated CORS middleware configuration to allow preflight requests and production origin.

### Code Changes in `src/invproc/api.py`

**Before (broken):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],  # Only GET and POST
    allow_headers=["Content-Type", "X-API-Key"],  # Specific headers
)
```

**After (fixed):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],  # Allow all methods including OPTIONS
    allow_headers=["*"],  # Allow all headers
)
```

### Updated Default Origins in `src/invproc/api.py`

```python
def get_allowed_origins() -> list[str]:
    """Get allowed CORS origins from environment."""
    origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,https://lavio.vercel.app",  # Added production frontend
    )
    return [origin.strip() for origin in origins.split(",") if origin.strip()]
```

### Updated `render.yaml`

```yaml
envVars:
  - key: ALLOWED_ORIGINS
    value: http://localhost:3000,http://localhost:5173,https://lavio.vercel.app
```

### Updated `.env.example`

```bash
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173,https://lavio.vercel.app
```

### Improved Error Logging

Added detailed error logging in `/extract` endpoint:

```python
except Exception as e:
    import logging

    logging.exception("PDF processing failed: %s", str(e))
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Processing failed: {str(e)}",
    )
```

## Why This Works

1. **Preflight Requests**: Browsers send OPTIONS requests before CORS-enabled requests to check if the actual request is allowed. By changing `allow_methods` from `["GET", "POST"]` to `["*"]`, we allow these preflight OPTIONS requests.

2. **Header Flexibility**: The CORS spec requires the server to indicate which headers are allowed in the actual request. Using `["*"]` for `allow_headers` allows any header (including `X-API-Key`, `Content-Type`, etc.) to be sent.

3. **Production Origin**: Added `https://lavio.vercel.app` to the default allowed origins list so the deployed frontend can communicate with the API.

4. **Root Cause**: The previous CORS configuration (from the security hardening) was too restrictive. While it fixed the wildcard origins security issue, it broke legitimate browser-based CORS by not allowing preflight requests.

## Prevention

- **Browser-based APIs must support preflight**: When serving browsers, always allow OPTIONS method or use `allow_methods=["*"]`
- **Test with real browser clients**: Don't rely solely on curl/Postman for API testing if the API will be consumed by browsers
- **Document production domains**: Keep production frontend origins in `.env.example`, `render.yaml`, and deployment guides
- **Monitor CORS errors**: Check browser console logs when deploying frontend-backend integrations
- **Gradual rollout**: Test CORS configuration with each new frontend domain before full deployment

## Related Issues

- See also: [CORS Security Vulnerability with Wildcard Origins](../security-issues/cors-security-vulnerability.md) - Previous security fix that was too restrictive
- Related to: Integration between FastAPI backend and Vercel-deployed frontend

## Cross-References

This issue occurred after applying the CORS security fix from `cors-security-vulnerability.md`. While that fix addressed a critical security issue (wildcard origins), it introduced a new problem by being too restrictive with methods and headers.
