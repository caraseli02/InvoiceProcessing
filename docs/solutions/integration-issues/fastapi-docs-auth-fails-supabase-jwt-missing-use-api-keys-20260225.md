---
module: Invoice Processing
date: 2026-02-25
problem_type: integration_issue
component: authentication
symptoms:
  - "Swagger /docs requests return {\"detail\": \"Invalid or expired token\"} for /extract"
  - "Unable to test real invoice extraction in /docs without a Supabase JWT"
root_cause: incomplete_setup
resolution_type: config_change
severity: medium
tags: [fastapi, swagger, docs, auth, supabase, api-keys]
---

# Troubleshooting: FastAPI `/docs` auth fails with “Invalid or expired token”

## Problem

When calling protected endpoints (like `POST /extract`) from FastAPI Swagger UI (`/docs`), requests fail with:

```json
{ "detail": "Invalid or expired token" }
```

This prevents testing invoice extraction in `/docs` unless you have a valid Supabase access token.

## Environment

- Module: Invoice Processing
- Affected Component: Authentication (Supabase JWT verification)
- Date: 2026-02-25

## Symptoms

- `POST /extract` in Swagger UI returns `401` with `{"detail":"Invalid or expired token"}`.
- The user has a local/dev key (e.g. `dev-key-12345`) but Supabase is not configured or no JWT is available.

## What Didn’t Work

**Attempted Solution 1:** Paste a non-JWT value in the Swagger “Authorize” modal.
- **Why it failed:** The backend attempted Supabase verification and correctly rejected it.

**Attempted Solution 2:** Rely on `docker-compose.yml` providing a default key.
- **Why it failed:** Defaulting a shared dev key is insecure; it can weaken auth unexpectedly.

## Solution

Enable an explicit local-dev auth bypass for Swagger UI by using API keys, guarded behind an opt-in flag:

1) Start the API with:

```bash
export ALLOW_API_KEY_AUTH=true
export API_KEYS=dev-key-12345
PYTHONPATH=src python -m invproc --mode api
```

2) In Swagger UI (`/docs`) click **Authorize** and paste the token:

```
dev-key-12345
```

3) Call `POST /extract` with your real PDF.

### Important

- API key auth is only honored when `ALLOW_API_KEY_AUTH=true`.
- Do not set `ALLOW_API_KEY_AUTH` in production.

## Why This Works

- The app’s protected endpoints use Supabase JWT verification by default.
- For local development, the auth layer can accept a token from `API_KEYS` *only* when explicitly enabled via `ALLOW_API_KEY_AUTH`, allowing `/docs` testing without Supabase.

## Prevention

- Avoid defaulting secrets/keys in compose or deployment configs.
- Keep the local-dev bypass behind an explicit opt-in environment flag.
- Prefer Supabase JWT auth for production environments.

## Related Issues

No related issues documented yet.

