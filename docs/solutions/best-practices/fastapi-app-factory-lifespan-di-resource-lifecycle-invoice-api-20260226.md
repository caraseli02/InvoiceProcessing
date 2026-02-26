---
module: Invoice Processing API
date: 2026-02-26
problem_type: best_practice
component: development_workflow
symptoms:
  - "FastAPI API still used module-global lifecycle state for extraction cache, config singleton, and Supabase client cache despite partial DI usage"
  - "API tests reset imported globals and called reload_config(), coupling behavior to import order and shared process state"
  - "Refactoring resource ownership risked regressions unless startup/lifespan initialization and dependency override points were made explicit"
root_cause: test_isolation
resolution_type: code_fix
severity: medium
tags: [fastapi, dependency-injection, app-factory, lifespan, test-isolation, supabase, extract-cache]
related:
  - docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md
  - docs/solutions/logic-errors/cache-disabled-extract-path-computes-cache-key-invoice-processing-api-20260226.md
  - todos/050-complete-p2-auth-dependency-bypasses-api-provider-layer.md
---

# Troubleshooting: FastAPI Resource Lifecycle Refactor with `create_app()` + Lifespan + Dependency Overrides

## Problem

The invoice API had already adopted dependency injection for request-scoped processors, but it still relied on module-global lifecycle state for extraction cache, config singleton, and Supabase client caching. Tests worked by mutating environment variables, resetting imported globals, and calling `reload_config()`, which made resource behavior implicit and fragile.

## Environment

- Module: Invoice Processing API
- Affected Component: FastAPI application composition, authentication dependency wiring, API test fixtures
- Date: 2026-02-26

## Symptoms

- `src/invproc/api.py` created a module-global `extract_cache` at import time and `/extract` still called `get_config()` directly inside the route.
- `src/invproc/config.py` and `src/invproc/auth.py` retained singleton/global caches (`_config_instance`, `_client`, `_client_url`, `_client_key`) for API paths.
- API tests imported the shared `app` and `extract_cache`, then reset globals / called `reload_config()` to force behavior changes.
- It was hard to reason about which resources were startup-owned vs request-owned and which overrides applied during tests.

## What Didn't Work

**Attempted Solution 1:** Keep module globals and add more reset helpers.
- **Why it failed:** This preserved hidden coupling to import order and did not produce explicit dependency override points for tests.

**Attempted Solution 2:** Only inject more route dependencies but leave app composition unchanged.
- **Why it failed:** Long-lived resources (cache/config/auth client lifecycle) still needed a clear owner and startup timing, and tests would continue sharing a process-global app.

**Direct solution:** Introduce a scoped `create_app()` composition path with lifespan-managed app resources and test fixtures that use explicit dependency overrides.

## Solution

Refactored the FastAPI app to use `create_app()` with lifespan-managed `app.state` resources, and migrated API tests to create fresh app instances with `app.dependency_overrides` instead of mutating module-global state.

**Key code changes**:

```python
# src/invproc/api.py (new app composition pattern)
@dataclass
class AppResources:
    config: InvoiceConfig
    extract_cache: InMemoryExtractCache
    supabase_client_provider: SupabaseClientProvider

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    app.state.invproc_resources = build_app_resources()
    try:
        yield
    finally:
        app.state.invproc_resources = None


def create_app() -> FastAPI:
    app = FastAPI(..., lifespan=app_lifespan)
    app.middleware("http")(add_observability_headers)
    app.include_router(router)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(ContractError, contract_error_handler)
    return app


app = create_app()  # runtime compatibility
```

```python
# /extract route now uses injected config/cache instead of direct get_config() call
async def extract_invoice(
    ...,
    config: InvoiceConfig = Depends(get_app_config),
    extract_cache: InMemoryExtractCache = Depends(get_extract_cache),
    ...,
):
    result = await run_in_threadpool(..., config=config, cache=extract_cache)
```

```python
# tests/conftest.py (explicit test-owned app + overrides)
@pytest.fixture
def api_test_app(...):
    app = create_app()
    app.dependency_overrides[get_app_config] = lambda: api_test_config
    app.dependency_overrides[get_extract_cache] = lambda: api_test_extract_cache
    app.dependency_overrides[get_supabase_client] = lambda: object()
    yield app
    app.dependency_overrides.clear()
```

**Config lifecycle support**:

```python
# src/invproc/config.py

def build_config(*, validate: bool = True) -> InvoiceConfig:
    config = InvoiceConfig()
    if validate:
        config.validate_config()
    return config
```

**Commands run (verification):**

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

## Why This Works

The underlying problem was not only thread safety; it was lifecycle ownership and test isolation. The codebase had a mixed model: some dependencies were request-scoped via FastAPI `Depends`, but other resources were created once at import time and mutated/reset in tests.

The refactor works because it establishes a clear ownership model:

1. **App-scoped resources** (config, extract cache, Supabase provider) are created in FastAPI lifespan and attached to `app.state`.
2. **Request-scoped services** (PDF processor, LLM extractor, validator, import service) remain standard FastAPI dependencies built from injected config.
3. **Tests use explicit overrides** on a fresh app instance, so resource behavior changes are local to that test app and no longer depend on mutating module globals.
4. **Runtime compatibility is preserved** by exporting `app = create_app()` for existing imports and `uvicorn` entrypoints.

This makes the dependency graph more explicit, reduces hidden state coupling, and aligns API behavior with FastAPI lifecycle conventions.

## Prevention

- Treat app resource ownership as a first-class design decision: define whether a dependency is app-scoped (lifespan) or request-scoped (`Depends`) before coding.
- Avoid import-time creation of mutable API resources (caches, clients, config singletons) unless they are intentionally process-global and documented.
- For API tests, prefer `create_app()` + `app.dependency_overrides` over environment mutation plus module-global resets.
- When refactoring from globals to DI, verify both runtime behavior and test isolation behavior (e.g., cache toggles, auth wiring, per-test config changes).
- Keep a single canonical composition path for middleware, routers, and lifecycle hooks to avoid dual initialization bugs.

## Related Issues

- See also: `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md`
- Related cache refactor regression lesson: `docs/solutions/logic-errors/cache-disabled-extract-path-computes-cache-key-invoice-processing-api-20260226.md`
- Architecture review finding (resolved): `todos/050-complete-p2-auth-dependency-bypasses-api-provider-layer.md`
