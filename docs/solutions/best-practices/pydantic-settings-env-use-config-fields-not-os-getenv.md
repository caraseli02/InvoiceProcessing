---
name: Pydantic Settings .env values are not visible to os.getenv — always use config fields
description: pydantic-settings reads .env into model fields but does NOT populate os.environ; os.getenv() returns None even for values present in .env
type: solution
category: best-practices
date: 2026-03-21
tags: [pydantic-settings, configuration, env, os-getenv, fastapi, auth]
---

## Problem

A FastAPI endpoint reads an environment variable with `os.getenv("API_KEYS")` and gets `None`, even though `API_KEYS` is set in `.env` and the value is loaded correctly by `InvoiceConfig`.

```python
# broken
def _get_api_keys() -> set[str]:
    raw = os.getenv("API_KEYS") or ""   # always "" when loaded via pydantic-settings
    return {k.strip() for k in raw.split(",") if k.strip()}
```

## Root Cause

`pydantic-settings` reads `.env` by parsing the file directly and populating model fields. It does **not** call `os.environ.__setitem__`. The OS process environment is unchanged, so `os.getenv()` returns `None` for any key that was only in `.env` and not already exported in the shell.

This is by design: pydantic-settings isolates config loading from process environment mutation.

## Solution

Add the field to `InvoiceConfig` and read it through the config object everywhere.

```python
# config.py
class InvoiceConfig(BaseSettings):
    api_keys: str = Field(
        default="",
        description="Comma-separated API keys for dev bypass (used when ALLOW_API_KEY_AUTH=true).",
    )
```

```python
# auth.py — after fix
def _get_api_keys(config: InvoiceConfig) -> set[str]:
    return {k.strip() for k in config.api_keys.split(",") if k.strip()}
```

Pass `config` explicitly or inject it via FastAPI's `Depends(get_app_config)`.

## Prevention

**Rule: never call `os.getenv()` in application code for config values. Always read from `InvoiceConfig` fields.**

- If a new config value is needed, add a typed field to `InvoiceConfig` — this gets validation, defaults, and docs for free.
- `os.getenv()` is only safe for values that are explicitly set in the shell environment (e.g., CI secrets injected directly). It is not a reliable way to read `.env` file values.
- Apply the same rule to `os.environ.get()` and `os.environ["KEY"]`.

Quick detection: search the codebase periodically for `os.getenv` or `os.environ` outside of `config.py` and `tests/`.

```bash
grep -r "os\.getenv\|os\.environ" src/ --include="*.py" | grep -v config.py
```

Any hit outside `config.py` is a candidate bug.
