---
title: "feat: Add app_env + production config hardening"
type: feat
date: 2026-02-27
---

# feat: Add app_env + production config hardening

## Overview

Add `app_env` (`local|production`) to `InvoiceConfig` and enforce production-only security checks in `InvoiceConfig.validate_config()` so the app fails fast on insecure production configuration.

Core goals (per request):

- Add `app_env` to `InvoiceConfig` (default local).
- In production (`app_env=production`):
  - Require explicit `ALLOWED_ORIGINS` (no fallback).
  - Disallow `ALLOW_API_KEY_AUTH=true`.
  - Optionally disallow debug/observability headers toggles unless explicitly allowed.
- Tie CORS origin resolution to validated config (single source of truth).
- Avoid silent permissive defaulting in production path.
- Add tests in `tests/test_config.py`.
- Document behavior (README + config/deploy docs).

## Local Research Findings (Current State)

- Config validation lives in `InvoiceConfig.validate_config()` and is called via `build_config()`/`get_config()`:
  - `src/invproc/config.py:228` (`validate_config`) and `src/invproc/config.py:279` (`build_config`).
- CORS allowed origins are resolved outside of `InvoiceConfig`, directly from env with a permissive fallback:
  - `src/invproc/api.py:80-86` (`get_allowed_origins()` uses default `"http://localhost:5173,https://lavio.vercel.app"`).
  - `src/invproc/api.py:304-316` wires `CORSMiddleware(allow_origins=get_allowed_origins(), ...)`.
- API key auth bypass is gated by an env flag checked at request time (not config-validated):
  - `src/invproc/auth.py:97-100` honors `ALLOW_API_KEY_AUTH` + `API_KEYS`.
- Debug/observability headers toggles are env-driven in middleware:
  - `src/invproc/api.py:134-140` uses `EXTRACT_CACHE_DEBUG_HEADERS` / `EXTRACT_OBSERVABILITY_HEADERS`.
- Existing CORS security institutional learning exists and aligns with stricter production rules:
  - `docs/solutions/security-issues/cors-security-vulnerability.md` (avoid wildcard origins; explicit allowlists).
- API server entrypoint uses `uvicorn.run("invproc.api:create_app", factory=True, ...)`:
  - `src/invproc/__main__.py` and `src/invproc/api.py`.
  - Using factory mode avoids import-time app creation/config validation.

No relevant brainstorm within the last 14 days matched this specific change (nearest are 2026-02-17 and earlier).

## Proposed Solution

### 1. Add Environment Mode to `InvoiceConfig`

Add new settings fields to `src/invproc/config.py`:

- `app_env: Literal["local", "production"] = "local"`
  - Loaded from env var `APP_ENV` (Pydantic Settings will map `app_env` → `APP_ENV` by default).
- `allowed_origins: Optional[str] = None`
  - Loaded from env var `ALLOWED_ORIGINS`.
  - Used to compute CORS allowlist; production requires it to be set.
- `allow_api_key_auth: bool = False`
  - Loaded from env var `ALLOW_API_KEY_AUTH`.
  - Production requires this to be `False`.
- Debug headers toggles in config (so validation can reason about them):
  - `extract_cache_debug_headers: bool = False` (env `EXTRACT_CACHE_DEBUG_HEADERS`)
  - `extract_observability_headers: bool = False` (env `EXTRACT_OBSERVABILITY_HEADERS`)
  - Optional explicit production override:
    - `allow_prod_debug_headers: bool = False` (env `ALLOW_PROD_DEBUG_HEADERS`)

Add one canonical resolver method (names TBD, but keep a single source of truth):

- `def cors_allowed_origins(self) -> list[str]:`
  - If `self.allowed_origins` is set: parse comma-separated origins into a list.
  - If `self.allowed_origins` is not set:
    - `app_env != "production"`: return the current dev fallback list:
      - `["http://localhost:5173", "https://lavio.vercel.app"]` (to preserve existing dev behavior).
    - `app_env == "production"`: return `[]` (and rely on `validate_config()` to fail) OR raise a clear `ValueError`.
  - Consider additional hygiene checks (especially in production):
    - reject `"*"` origins; reject empty/whitespace; optionally enforce scheme (`http://` or `https://`).

### 2. Enforce Production-Only Strict Checks in `validate_config()`

Extend `InvoiceConfig.validate_config()` (`src/invproc/config.py:228`) with:

- `if self.app_env == "production":`
  - Require `ALLOWED_ORIGINS` explicitly configured:
    - Fail if `self.allowed_origins` is missing/empty after trimming.
    - Ensure production path never uses the dev fallback.
  - Disallow API key bypass:
    - Fail if `self.allow_api_key_auth` is `True`.
  - Debug/observability headers toggles:
    - If either `extract_cache_debug_headers` or `extract_observability_headers` is enabled:
      - Fail unless `allow_prod_debug_headers` is `True`.

Error messaging requirements:

- Make failures actionable and specific:
  - `ALLOWED_ORIGINS is required when APP_ENV=production (no fallback).`
  - `ALLOW_API_KEY_AUTH must be false in production.`
  - `EXTRACT_CACHE_DEBUG_HEADERS / EXTRACT_OBSERVABILITY_HEADERS are not allowed in production unless ALLOW_PROD_DEBUG_HEADERS=true.`

### 3. Tie CORS Resolution to Validated Config

Goal: remove duplicated env parsing and ensure CORS allowlist is derived from the same config object that passed `validate_config()`.

Implementation approach:

- Move CORS origin resolution out of `src/invproc/api.py:get_allowed_origins()` and into `InvoiceConfig.cors_allowed_origins()`.
- In `src/invproc/api.py:create_app()`:
  - Build a single config instance via `build_config()` (validated).
  - Use that config for:
    - `CORSMiddleware(allow_origins=config.cors_allowed_origins(), ...)`
    - building app resources so the same validated config instance is injected via `Depends(get_app_config)`.
- Update `build_app_resources()` to accept the already-validated config instance instead of constructing a new one:
  - Today: `build_app_resources()` calls `build_config()` internally (`src/invproc/api.py:89-101`).
  - Proposed: `build_app_resources(config: InvoiceConfig) -> AppResources`.

### 4. Ensure Auth Bypass Uses Validated Config (and is Blocked in Production)

To avoid a situation where config validation says one thing but runtime auth does another, update `src/invproc/auth.py`:

- Replace `if _env_truthy("ALLOW_API_KEY_AUTH") ...` (`src/invproc/auth.py:97-100`) with:
  - `if config.allow_api_key_auth and ...`

This ensures:

- Production validation forbids bypass and runtime honors the same setting.
- Local dev behavior stays intact (developers can opt-in with env var).

### 5. Avoid Import-Time Side Effects (Needed If `create_app()` Starts Validating)

If `create_app()` starts constructing validated config at app creation time, avoid initializing the FastAPI app at module import time.

Recommended entrypoint change:

- Remove (or stop relying on) `app = create_app()` (`src/invproc/api.py:325`).
- Switch uvicorn entrypoints to a factory:
  - `uvicorn.run("invproc.api:create_app", factory=True, ...)`
    - Update both:
      - `src/invproc/__main__.py:29-34`
      - `src/invproc/api.py:332-337` (the standalone `main()` path)
- Update tests that assert the uvicorn target string:
  - `tests/test_main_entrypoint.py:31-49` currently expects `"invproc.api:app"`.

This is the cleanest way to ensure:

- No app construction (and no config validation) happens at import time.
- Validation happens deterministically at server startup.

## Testing Plan

Add tests to `tests/test_config.py`:

- Production + missing origins fails:
  - `InvoiceConfig(_env_file=None, app_env="production", allowed_origins=None, mock=True).validate_config()` raises.
  - Assert error message matches `ALLOWED_ORIGINS` required.
- Production + API key auth bypass enabled fails:
  - `InvoiceConfig(_env_file=None, app_env="production", allowed_origins="https://app.example.com", allow_api_key_auth=True, mock=True).validate_config()` raises.
- Local dev mode with bypass toggles passes (developer workflow unchanged):
  - `InvoiceConfig(_env_file=None, app_env="local", allow_api_key_auth=True, mock=True).validate_config()` does not raise.
- (If implemented) Production + debug headers enabled fails unless explicitly allowed:
  - Fail when `extract_cache_debug_headers=True` and `allow_prod_debug_headers=False`.
  - Pass when both enabled and `allow_prod_debug_headers=True`.

Notes:

- Use `_env_file=None` in new tests to avoid coupling to a developer’s local `.env`.
- Keep error messages stable enough for regex `match=...` assertions.

## Documentation Updates

Update docs to clearly separate dev defaults from production requirements.

- [x] `README.md`
  - Add `APP_ENV` with allowed values and default (`local`).
  - Add “Production required configuration” section:
    - `APP_ENV=production`
    - `ALLOWED_ORIGINS` required (no fallback)
    - `ALLOW_API_KEY_AUTH` must be unset/false
    - debug header toggles disallowed unless explicitly allowed (if implemented)
- [x] `.env.example`
  - Add `APP_ENV=local` (or commented default).
  - Add/clarify production-only vars:
    - `ALLOW_API_KEY_AUTH=false`
    - `EXTRACT_CACHE_DEBUG_HEADERS=false`
    - `EXTRACT_OBSERVABILITY_HEADERS=false`
    - `ALLOW_PROD_DEBUG_HEADERS=false` (if used)
- [x] `DEPLOYMENT.md`
  - Mark `ALLOWED_ORIGINS` as required in production (it currently says “No” in the Render vars table).
  - Add `APP_ENV=production` to Render env var list.
- [x] `render.yaml`
  - Update comments indicating which env vars are required in production.
  - (Optional) Remove baked-in `ALLOWED_ORIGINS` default value to force explicit configuration when deploying with `APP_ENV=production`.

## Acceptance Criteria

- `InvoiceConfig` includes `app_env` and defaults to local behavior.
- `validate_config()` fails fast for insecure production configuration:
  - Production + missing `ALLOWED_ORIGINS` fails.
  - Production + `ALLOW_API_KEY_AUTH=true` fails.
  - (If implemented) Production + debug headers enabled fails unless explicitly allowed.
- CORS middleware allowlist is resolved from config (not a separate env-parsing function) and cannot silently fall back in production.
- Local/dev behavior remains unchanged:
  - Developers can still opt-in to API key auth bypass locally.
  - Default dev CORS behavior remains as-is unless `ALLOWED_ORIGINS` is set.
- CI quality gates remain green:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q` (coverage >= 80%)

## Risks / Notes

- Switching uvicorn to factory mode affects:
  - `tests/test_main_entrypoint.py`
  - Any deployment scripts expecting `invproc.api:app`
  - Documentation references
- If keeping a module-global `app`, ensure it does not validate config at import time (tests currently import from `invproc.api` before env is set).
- Consider whether `staging` should follow production rules; current scope is production-only strictness as requested.

## References

- CORS origins fallback (current): `src/invproc/api.py:80-86`
- CORS middleware wiring (current): `src/invproc/api.py:304-316`
- Config validation: `src/invproc/config.py:228-273`
- API key auth bypass: `src/invproc/auth.py:97-100`
- API server entrypoint: `src/invproc/__main__.py:25-35`
- Institutional learning: `docs/solutions/security-issues/cors-security-vulnerability.md`
