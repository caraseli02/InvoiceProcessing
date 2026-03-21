---
name: pydantic-secretstr-internal-endpoint-auth-hardening
description: Three security hardening patterns applied together — SecretStr for sensitive Pydantic Settings fields, a dedicated verify_internal_caller dependency for /internal/* endpoints, and replacing HTTPException with RuntimeError at ASGI startup.
type: solution
category: security-issues
date: 2026-03-21
tags: [pydantic, pydantic-settings, secretstr, security, config, fastapi, auth, jwt, internal-api, dependency-injection, supabase, startup, lifespan]
---

## Problems

Three separate but related security gaps in the FastAPI service:

1. `supabase_service_role_key`, `openai_api_key`, `api_keys` were plain `str`/`Optional[str]` Pydantic Settings fields — any `repr()`, `model_dump()`, or log call emitted them in plaintext. The Supabase service role key bypasses Row Level Security; exposure = full DB write access.

2. All `/internal/rag/*` endpoints used `Depends(verify_supabase_jwt)` — the same dependency as public user endpoints. Any valid user JWT granted catalog import, sync trigger, eval, and queue inspection access (OWASP A01 — Broken Access Control).

3. `SupabaseClientProvider.get_client()` raised `HTTPException(500)` when credentials were missing. Called inside `build_app_resources()` during ASGI lifespan startup (before any request context), this exception type is wrong and may be silently swallowed, producing a misconfigured app that appears to start cleanly.

## Root Causes

- `SecretStr` was not applied: nothing in plain `str` Pydantic fields prevents accidental serialization.
- Internal routes were added incrementally without auditing the auth dependency they inherited — the default was the user-facing JWT, which was wrong for machine-to-machine paths.
- `HTTPException` is a Starlette response-layer construct. Raising it outside a request handler violates its contract; the ASGI lifespan machinery does not translate it into a 500 response.

## Solution

### 1. SecretStr for all sensitive config fields

```python
# config.py
from pydantic import SecretStr

class InvoiceConfig(BaseSettings):
    openai_api_key: Optional[SecretStr] = Field(default=None, ...)
    api_keys: Optional[SecretStr] = Field(default=None, ...)
    supabase_service_role_key: Optional[SecretStr] = Field(default=None, ...)
    internal_api_keys: Optional[SecretStr] = Field(default=None, ...)
```

Update every call site to unwrap with `.get_secret_value()`:

```python
# auth.py — Supabase client init
self._client = create_client(
    self._config.supabase_url,
    self._config.supabase_service_role_key.get_secret_value(),
)

# auth.py — API key splitting helper
def _get_api_keys(config: InvoiceConfig) -> set[str]:
    if not config.api_keys:
        return set()
    return {k.strip() for k in config.api_keys.get_secret_value().split(",") if k.strip()}
```

After this change `repr(config)` prints `**********` for all secret fields. `model_dump()` does not expose raw values.

**Note:** `SecretStr` is part of `pydantic` itself — no extra package needed since `pydantic-settings` already depends on it.

---

### 2. Separate `verify_internal_caller` dependency

Add `internal_api_keys` to config (already above). Then create a dedicated dependency that only accepts configured internal API keys — never user JWTs:

```python
# auth.py
def _get_internal_api_keys(config: InvoiceConfig) -> set[str]:
    if not config.internal_api_keys:
        return set()
    return {k.strip() for k in config.internal_api_keys.get_secret_value().split(",") if k.strip()}

async def verify_internal_caller(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    config: InvoiceConfig = Depends(get_app_config),
) -> dict[str, Any]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = credentials.credentials
    internal_keys = _get_internal_api_keys(config)
    if internal_keys and token in internal_keys:
        return {"id": "internal-caller", "auth": "internal_api_key"}
    raise HTTPException(status_code=403, detail="Not authorized for internal endpoints")
```

Swap the dependency on every `/internal/*` route:

```python
# api.py
@router.post("/internal/rag/import")
async def import_catalog_rows(
    ...
    user: dict[str, Any] = Depends(verify_internal_caller),  # was verify_supabase_jwt
):
```

Set in `.env` / environment:
```bash
INTERNAL_API_KEYS=some-long-random-internal-key
```

---

### 3. RuntimeError instead of HTTPException at startup

Replace the startup-time `HTTPException` with `RuntimeError` and add explicit `validate_config()` checks:

```python
# auth.py
def get_client(self) -> Client:
    if not self._config.supabase_url or not self._config.supabase_service_role_key:
        raise RuntimeError(
            "Supabase credentials not configured (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)"
        )
    ...
```

```python
# config.py — validate_config()
if self.import_repository_backend == "supabase":
    if not self.supabase_url:
        errors.append("SUPABASE_URL required when IMPORT_REPOSITORY_BACKEND=supabase")
    if not self.supabase_service_role_key:
        errors.append("SUPABASE_SERVICE_ROLE_KEY required when IMPORT_REPOSITORY_BACKEND=supabase")
```

`RuntimeError` at startup crashes the process visibly rather than silently degrading. `validate_config()` fires at boot, before any request arrives, giving a clear config-time error rather than a runtime failure on the first actual DB call.

## Prevention

**Rule 1 — SecretStr:** Wrap every sensitive field in a `BaseSettings` subclass with `SecretStr`/`Optional[SecretStr]`. Grep for bare `str` fields whose names contain `key`, `secret`, `token`, `password`, or `credential`.

```python
# test that would have caught it
def test_secrets_not_leaked_in_repr():
    config = InvoiceConfig(supabase_service_role_key="super-secret")
    assert "super-secret" not in repr(config)
    assert "super-secret" not in str(config.model_dump())
```

**Rule 2 — Internal endpoint auth:** Any route under `/internal/` or `/admin/` must use a dependency that explicitly rejects user JWTs. Add a test:

```python
def test_internal_endpoint_rejects_user_jwt(client, user_jwt_headers):
    response = client.post("/internal/rag/import", headers=user_jwt_headers)
    assert response.status_code == 403
```

**Rule 3 — No HTTPException in lifespan:** Code called during ASGI startup must never raise `HTTPException`. Flag any `raise HTTPException` inside a function that lacks a `Request` parameter in its own or its callers' signatures. `RuntimeError` and `ValueError` are the correct signal types at startup.

## Related docs

- `docs/solutions/best-practices/pydantic-settings-env-use-config-fields-not-os-getenv.md` — extends the "always use InvoiceConfig fields" rule; note that `.get_secret_value()` is required when the field is `SecretStr`
- `docs/solutions/security-issues/fail-fast-production-config-guards-system-20260227.md` — production guard framework that validates `allow_api_key_auth` and `ALLOWED_ORIGINS`; `internal_api_keys` validation belongs in the same `validate_config()` method
- `docs/solutions/integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md` — introduced the `/internal/rag/*` routes that `verify_internal_caller` now guards
