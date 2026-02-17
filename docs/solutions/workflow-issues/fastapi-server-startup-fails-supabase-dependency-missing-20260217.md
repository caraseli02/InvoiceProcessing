---
module: Development Workflow
date: 2026-02-17
problem_type: workflow_issue
component: tooling
symptoms:
  - "Starting API with `python -m invproc --mode api` crashed with `ModuleNotFoundError: No module named 'supabase'`"
  - "Health check to `http://localhost:8000/health` returned connection failure after attempted startup"
  - "`pip` command was unavailable in shell (`command not found: pip`)"
root_cause: incomplete_setup
resolution_type: environment_setup
severity: medium
tags: [fastapi, supabase, startup, python-environment, dependency-setup]
---

# Troubleshooting: FastAPI Server Startup Fails with Missing Supabase Dependency

## Problem
FastAPI failed to start after JWT auth refactor because the runtime Python environment did not have the new `supabase` dependency installed. Startup attempts looked valid but crashed during ASGI import.

## Environment
- Module: Development Workflow
- Affected Component: CLI/API startup tooling (`python -m invproc --mode api`)
- Date: 2026-02-17

## Symptoms
- `python -m invproc --mode api` produced stack trace ending in:
  - `ModuleNotFoundError: No module named 'supabase'`
- `curl http://localhost:8000/health` failed with connection errors because server never stayed up.
- Running `pip install ...` failed with `zsh: command not found: pip`.
- In one run, a stale process already held port `8000`, adding confusion.

## What Didn't Work

**Attempted Solution 1:** Start server directly with plain Python.
- **Why it failed:** Environment had `invproc` code but not API extras including `supabase`.

**Attempted Solution 2:** Install deps with `pip install -e ".[api]"`.
- **Why it failed:** `pip` binary was not on shell `PATH`.

## Solution

Install API extras using interpreter-scoped pip, then start API with the same interpreter.

**Commands run:**
```bash
python -m pip install -e ".[api]"
python -m invproc --mode api
curl -i http://localhost:8000/health
```

**If port is already in use:**
```bash
lsof -i :8000
kill <PID>
```

## Why This Works

The auth refactor introduced `from supabase import ...` in API import path. Without API extras installed in the active interpreter, import fails before app startup completes. Installing via `python -m pip` guarantees dependencies are installed into the same interpreter used to run `python -m invproc`.

## Prevention
- After pulling changes that modify dependencies, run:
  - `python -m pip install -e ".[api]"`
- Prefer interpreter-scoped package commands over bare `pip` when PATH is uncertain.
- Add startup preflight to local workflow:
  - `python -m invproc --help`
  - `python -m invproc --mode api`
  - `curl -f http://localhost:8000/health`
- If startup behaves inconsistently, always check for port conflicts with `lsof -i :8000`.

## Related Issues
- See also: [missing-cache-headers-wrong-invproc-import-20260213.md](./missing-cache-headers-wrong-invproc-import-20260213.md)
