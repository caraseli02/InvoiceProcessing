---
module: System
date: 2026-02-27
problem_type: security_issue
component: development_workflow
symptoms:
  - "Production could start with missing ALLOWED_ORIGINS and silently use a permissive fallback allowlist"
  - "Production could run with ALLOW_API_KEY_AUTH=true (dev auth bypass) if misconfigured"
  - "Production could expose debug/observability headers unless explicitly constrained"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags: [app-env, production, config-validation, cors, api-key-auth, fastapi]
---

# Troubleshooting: Fail-fast production config guards (`APP_ENV`)

## Problem

The service had developer-friendly defaults (CORS fallback origins, optional API key auth bypass, optional debug headers) that are fine locally, but risky in production if environment variables are missing or mis-set.

The issue was not a single bug; it was the absence of a clear "production mode" and missing validation to prevent insecure production configuration.

## Environment

- Module: System-wide (config + FastAPI app composition + auth)
- Affected Component: `InvoiceConfig.validate_config()` + CORS middleware wiring + auth bypass
- Date: 2026-02-27

## Symptoms

- `ALLOWED_ORIGINS` could be omitted in production and the API would still start, using a built-in fallback allowlist.
- `ALLOW_API_KEY_AUTH=true` could accidentally be set in production, enabling a dev-only auth bypass path.
- Debug/observability headers could be enabled without an explicit production opt-in.

## What Didn’t Work

**Attempted Solution 1:** Keep CORS/auth/debug toggles env-driven and “just document it”.
- **Why it failed:** Docs do not prevent misconfiguration. The goal is fail-fast, not “hope the deployer reads the README”.

**Attempted Solution 2:** Validate production toggles but keep runtime toggles reading env directly.
- **Why it failed:** It created two sources of truth (validated config vs runtime env), and tests revealed the mismatch (headers missing because the middleware used a different signal than validation).

## Solution

Add an explicit environment mode and enforce strict security validation in production.

### 1) Add `APP_ENV` + production-only validation

In `src/invproc/config.py`, add `app_env` (`local|production`) and validate security constraints when `APP_ENV=production`:

```python
# src/invproc/config.py
app_env: Literal["local", "production"] = "local"
allowed_origins: Optional[str] = None
allow_api_key_auth: bool = False

def validate_config(self) -> None:
    errors = []
    ...
    if self.app_env == "production":
        if not self.allowed_origins or not self.allowed_origins.strip():
            errors.append("ALLOWED_ORIGINS is required when APP_ENV=production (no fallback)")
        if self.allow_api_key_auth:
            errors.append("ALLOW_API_KEY_AUTH must be false in production")
    ...
    if errors:
        raise ValueError(...)
```

### 2) Tie CORS allowlist to validated config

Stop parsing CORS origins separately in `api.py`. Resolve it from the validated config:

```python
# src/invproc/api.py
config = build_config()  # validates

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_allowed_origins(),
    ...
)
```

### 3) Tie auth bypass to validated config

Ensure the dev-only bypass is controlled by the same validated config flag:

```python
# src/invproc/auth.py
if config.allow_api_key_auth and token in api_keys:
    return {"id": "api-key-user", "auth": "api_key"}
```

### 4) Avoid import-time startup side-effects

Switch uvicorn to factory mode so app creation (and config validation) happens at server start, not module import:

```python
uvicorn.run("invproc.api:create_app", factory=True, ...)
```

### Verification

Added tests to lock in the contract:

- production + missing `ALLOWED_ORIGINS` => validation fails
- production + `ALLOW_API_KEY_AUTH=true` => validation fails
- local dev bypass => still allowed when explicitly enabled

Commands used to verify:

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

## Why This Works

- `APP_ENV=production` creates an explicit "security boundary" where dev conveniences become invalid configuration.
- Validation fails fast at startup, preventing deployments that would otherwise run with permissive defaults.
- CORS and auth behavior are derived from the same config that passed validation, reducing drift.
- Factory mode prevents import-time app creation and makes startup validation deterministic.

## Prevention

- Always set `APP_ENV=production` in real deployments.
- Require explicit `ALLOWED_ORIGINS` in production; never rely on fallback allowlists.
- Keep dev auth bypass behind an explicit opt-in flag and ensure production validation forbids it.
- Add config validation tests for any new security-relevant env toggles.

## Related Issues

- See also: `docs/solutions/configuration-issues/startup-config-validation.md`
- See also: `docs/solutions/security-issues/cors-security-vulnerability.md`
- See also: `docs/solutions/integration-issues/fastapi-docs-auth-fails-supabase-jwt-missing-use-api-keys-20260225.md`
