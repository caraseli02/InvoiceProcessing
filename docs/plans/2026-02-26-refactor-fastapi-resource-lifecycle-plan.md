---
title: refactor: Replace module-global API resources with FastAPI-managed lifecycles
type: refactor
date: 2026-02-26
---

# refactor: Replace module-global API resources with FastAPI-managed lifecycles

## Overview

Refactor FastAPI resource ownership so extraction cache, configuration lifecycle, and Supabase client lifecycle are provided through FastAPI dependencies and/or app startup state instead of module-level globals.

This targets three current global-state patterns:

- extraction cache in `src/invproc/api.py`
- config singleton in `src/invproc/config.py`
- Supabase client cache in `src/invproc/auth.py`

The goal is to make runtime behavior explicit, improve test isolation, and let tests override dependencies without mutating imported module globals.

## Problem Statement / Motivation

The API still mixes dependency injection with module-global lifecycle state:

- `extract_cache = InMemoryExtractCache(...)` is created at import time and shared across tests/requests (`src/invproc/api.py:46`)
- `/extract` bypasses DI and calls `get_config()` directly inside the route (`src/invproc/api.py:181`)
- `config.py` uses `_config_instance` singleton state (`src/invproc/config.py:276`)
- `auth.py` caches Supabase client and credentials in `_client`, `_client_url`, `_client_key` (`src/invproc/auth.py:14`)

Current tests work by resetting globals and reloading config:

- `tests/test_api.py` imports `extract_cache` directly and resets it in an autouse fixture (`tests/test_api.py:9`, `tests/test_api.py:25`, `tests/test_api.py:37`)
- `tests/test_api.py` and `tests/test_error_paths.py` call `reload_config()` to force config changes (`tests/test_api.py:10`, `tests/test_api.py:26`, `tests/test_api.py:38`, `tests/test_error_paths.py:15`, `tests/test_error_paths.py:26`, `tests/test_error_paths.py:31`)

This creates hidden coupling between import order, environment mutation, and test execution, and it makes resource initialization behavior harder to reason about in multi-worker deployments.

## Proposed Solution

Use a FastAPI application factory (`create_app()`) with lifespan-initialized `app.state` resources and route dependencies.

Proposed direction:

- Introduce `create_app()` as the canonical API composition entrypoint (module-level `app = create_app()` can remain for runtime compatibility)
- Introduce a dedicated app resource container (typed dataclass or lightweight class) attached to `app.state`
- Initialize long-lived resources during app startup/lifespan:
- `InvoiceConfig` (validated or unvalidated depending on use case)
- `InMemoryExtractCache` built from config cache settings
- Supabase auth provider/client object bound to the app's startup config (overrideable in tests)
- Add dependency functions that read resources from `Request.app.state` instead of module globals
- Update endpoints/auth dependencies to consume injected resources only
- Add a canonical test app fixture/factory pattern that creates isolated app instances per test module/case as needed
- Update tests to override these dependencies explicitly via `app.dependency_overrides` on the test app, instead of mutating module globals

## Technical Considerations

- FastAPI lifecycle model:
  - Standardize on a lifespan function (preferred over mixed startup patterns) for resource initialization and predictable test setup/teardown.
  - Avoid import-time side effects for cache/config/client creation.
  - Keep a single composition path: `create_app()` builds middleware/routes and registers lifespan-managed resources.
- Dependency design:
  - `verify_supabase_jwt` currently depends on `get_config` (`src/invproc/auth.py:77`); after refactor, it should depend on an injected config provider and a Supabase client provider (directly or indirectly).
  - `/extract` should inject config and extract cache dependencies rather than calling `get_config()` and using module `extract_cache` (`src/invproc/api.py:181`, `src/invproc/api.py:206`).
  - Avoid “current config” key-matching caches in auth; app-scoped auth provider/client should be derived from startup config for the app instance.
- Test ergonomics:
  - Tests currently rely on `reload_config()` and imported globals for cache reset. Replace with fixture-owned config/cache instances and explicit dependency overrides to make each test independent.
  - Define one canonical fixture pattern (for example `test_app` + `client`) so tests do not share a long-lived imported app when config/cache behavior is under test.
  - Environment variable changes during a test should require either (a) a new app/client created after env mutation or (b) direct dependency overrides; do not rely on `reload_config()` to mutate a shared running app.
  - Ensure `limiter.reset()` behavior remains available in tests (this can remain global if not in scope for this refactor).
- Backward compatibility:
  - CLI code and non-FastAPI modules may still use `get_config()` today. This refactor should avoid breaking CLI behavior while improving API-side lifecycle management.
  - If `get_config()` remains for CLI, document that API paths no longer depend on the module singleton.
- Concurrency/thread-safety:
  - App-level cache is intentionally shared process-wide and should remain explicit, bounded, and resettable.
  - Per-request processors (`PDFProcessor`, `LLMExtractor`, `InvoiceValidator`) already use DI and should remain per-request (`src/invproc/api.py:67`, `src/invproc/api.py:72`, `src/invproc/api.py:77`).

## SpecFlow Analysis (Flows, Gaps, Edge Cases)

Primary flow:

- App startup initializes resource container on `app.state`
- `create_app()` is the only constructor used by tests when resource behavior is under test
- Request resolves config/cache/client via dependencies
- `/extract` uses injected cache + config to execute pipeline
- `verify_supabase_jwt` uses injected Supabase client provider and returns auth payload

Key edge cases to cover:

- Missing Supabase config should still return `500` auth configuration error (current behavior in `src/invproc/auth.py:37`)
- Config changes in tests should not leak across test cases when a `TestClient` context closes and a new app/client is created
- Tests that mutate env after client creation should not expect runtime app-state resources to refresh unless they rebuild the app/client
- Cache enable/disable toggles should create the correct cache behavior and headers without requiring module reloads
- Dependency override omissions should fail loudly (startup error or clear test fixture failure), not silently fall back to stale globals
- Lifespan startup should run in tests that use `TestClient`; if tests bypass lifespan, fixtures must initialize app state explicitly

## Acceptance Criteria

- [x] `src/invproc/api.py` no longer defines a module-global extraction cache instance for request handling
- [x] `/extract` route receives config and extract cache through FastAPI dependencies (no direct `get_config()` call inside handler)
- [x] `src/invproc/config.py` no longer requires module-global `_config_instance` for API request paths (CLI compatibility can be preserved via separate path)
- [x] `src/invproc/auth.py` no longer relies on module-global `_client`, `_client_url`, `_client_key` for API auth lifecycle
- [x] `create_app()` is introduced as the canonical API composition entrypoint, with runtime compatibility preserved via exported module-level `app`
- [x] FastAPI lifespan initializes and attaches required resources to `app.state`
- [x] Tests use a canonical test app fixture/factory and override config/cache/auth-related dependencies explicitly using `app.dependency_overrides` on that test app instead of resetting imported globals
- [x] API tests do not rely on `reload_config()` to mutate resources for an already-created `TestClient` instance
- [x] Existing API auth and extract cache tests continue to validate cache hit/miss and auth error behavior
- [x] Refactor preserves current endpoint behavior and status codes for `/health`, `/extract`, and `/invoice/preview-pricing`
- [x] Quality gates pass:
- [x] `python -m ruff check src/ tests/`
- [x] `python -m mypy src/`
- [x] `python -m pytest -q` (coverage fail-under remains 80%)

## Success Metrics

- Tests no longer need `reload_config()` for API dependency setup in endpoint tests
- Endpoint tests no longer import and reset `extract_cache` from `invproc.api`
- API resource initialization becomes traceable to startup/lifespan and dependency providers
- Reduced hidden coupling between environment mutation and module import order
- API resource behavior tests construct isolated app instances through `create_app()` rather than sharing a process-global app object

## Dependencies & Risks

Dependencies:

- FastAPI app lifecycle support via lifespan/startup events
- Stable dependency override points for tests (`app.dependency_overrides`)
- A canonical `create_app()` path that tests and production import use consistently
- Careful coordination between `invproc.api`, `invproc.auth`, and `invproc.config`

Risks:

- Breaking test assumptions that depend on current singleton/reload behavior
- Accidentally changing auth error semantics while moving Supabase client creation behind providers
- Lifespan not executing in some tests if fixture construction is inconsistent
- Introducing duplicate resource initialization if both import-time and startup-time paths coexist temporarily
- Scope creep from partial app-factory migration if routes/middleware setup is split between old and new composition paths

Mitigations:

- Refactor in small steps with temporary compatibility shims
- Add/adjust focused tests for dependency override paths before removing globals
- Centralize resource initialization in one function used by startup and tests
- Make `create_app()` the sole place that assembles middleware/routes/resources to avoid dual initialization paths

## Implementation Suggestions (Phased)

### Phase 1: Resource container and providers

- Add app-state resource container in `src/invproc/api.py` or a new module (for example `src/invproc/dependencies.py`)
- Introduce `create_app()` (exporting `app = create_app()` for backward compatibility)
- Implement lifespan initializer that constructs config and extract cache using config values
- Add dependency functions such as `get_app_config(request)`, `get_extract_cache(request)`, and a Supabase client provider dependency
- Update `/extract` to inject config/cache dependencies and remove direct `get_config()` call

### Phase 2: Auth lifecycle refactor

- Move Supabase client lifecycle responsibility out of module globals in `src/invproc/auth.py` into an app-scoped provider bound to startup config
- Make `verify_supabase_jwt` depend on injected provider(s) rather than hidden module cache state
- Preserve current API key auth fallback path (`src/invproc/auth.py:90`)
- Add tests for missing Supabase config and token verification with the new dependency wiring

### Phase 3: Test migration and cleanup

- Introduce shared test fixtures (for example in `tests/conftest.py`) that build a fresh app via `create_app()` and provide `TestClient`
- Replace global mutation/reset patterns in `tests/test_api.py` and `tests/test_error_paths.py` with explicit dependency overrides and fixture-owned resources
- Convert tests that currently mutate env + call `reload_config()` mid-test to either rebuild app/client or override dependencies directly
- Keep `limiter.reset()` reset behavior unless scope expands
- Remove deprecated `reload_config()` usage from API tests once new fixtures are stable
- Document the preferred testing pattern for dependency overrides in `tests/conftest.py` or contributor docs

## Alternative Approaches Considered

- Keep module globals and add more reset helpers:
  - Rejected because it preserves hidden state coupling and fragile test behavior.
- Full application factory rewrite immediately:
  - Selected in a scoped form (`create_app()` for API composition) because it clarifies lifecycle ownership and test isolation without requiring a broader package-wide rewrite.
- Per-request config object creation only (no app-state config/cache):
  - Simpler for config, but not ideal for shared extraction cache lifecycle and test override clarity.

## References & Research

### Internal References

- Global extraction cache import-time state: `src/invproc/api.py:46`
- Per-request DI providers already in place (processors/validator): `src/invproc/api.py:67`, `src/invproc/api.py:72`, `src/invproc/api.py:77`
- `/extract` direct `get_config()` call and module cache usage: `src/invproc/api.py:181`, `src/invproc/api.py:206`
- Config singleton globals: `src/invproc/config.py:276`, `src/invproc/config.py:279`
- Supabase client globals and lazy cache logic: `src/invproc/auth.py:14`, `src/invproc/auth.py:31`
- Auth dependency wiring using `Depends(get_config)`: `src/invproc/auth.py:77`
- API tests relying on imported globals + reload: `tests/test_api.py:9`, `tests/test_api.py:25`, `tests/test_api.py:26`, `tests/test_api.py:37`, `tests/test_api.py:38`
- Error path tests relying on `reload_config()`: `tests/test_error_paths.py:15`, `tests/test_error_paths.py:26`, `tests/test_error_paths.py:31`
- Config singleton/reload tests (likely to be adjusted or split API-vs-CLI semantics): `tests/test_config.py:49`, `tests/test_config.py:61`

### Institutional Learnings

- `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md:17` documents prior FastAPI concurrency issues caused by global mutable state and recommends dependency injection/app state patterns
- `docs/solutions/runtime-errors/global-state-thread-safety-race-conditions.md:71` captures the team’s chosen pattern: FastAPI dependency injection for request-scoped services
- `docs/solutions/logic-errors/cache-disabled-extract-path-computes-cache-key-invoice-processing-api-20260226.md` highlights recent extract cache refactor regressions and reinforces targeted tests around cache-enabled vs disabled control flow

### Canonical Project Policy References

- CI workflow: `.github/workflows/ci.yml`
- Quality gate policy: `docs/quality-gates.md`
- PR template: `.github/pull_request_template.md`

## PR / Validation Notes

For the eventual PR, ensure policy alignment:

- Include exactly one label: `change:refactor`
- Include `### Refactor Regression Evidence` with concrete test evidence (no placeholders)
- Keep required checks green: `lint`, `typecheck`, `tests`, `health-smoke`, `pr-policy`, `quality-gate-pr`
