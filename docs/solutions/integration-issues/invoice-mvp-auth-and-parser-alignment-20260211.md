---
category: integration-issues
module: invoice-processing-api
severity: medium
status: solved
date: 2026-02-11
tags: [invoice-import, auth, api-contract, parsing, frontend-backend-alignment, mvp]
related:
  - docs/plans/2026-02-11-feat-invoice-import-pricing-parity-backend-plan.md
  - docs/solutions/integration-issues/cors-405-errors-production-frontend-20250206.md
---

# Invoice MVP Auth and Parser Alignment (2026-02-11)

## Problem

After backend MVP implementation for invoice pricing preview, frontend testing exposed three integration issues:

1. `401` responses during frontend calls, despite valid auth session in frontend.
2. Weight parsing missed multipack tokens like `24X2G` (showed as missing weight).
3. Contract drift on ownership of DB writes (`/invoice/import` behavior changed during implementation decisions).

## Symptoms

- Frontend preview/import requests failed with `401 Invalid or missing API key`.
- Rows like `24X2G CEAI LOVARE ...` were flagged as missing weight in preview UI.
- Team expected DB writes after import click, but backend was running with in-memory orchestration and then pivoted to preview-only mode.

## Root Cause

- Auth dependency accepted only `X-API-Key`; frontend/proxy path used bearer token semantics in some calls.
- Initial parser handled only single-size tokens (`200G`, `0.5L`), not multipack expressions (`NxMUNIT`).
- Architectural decision changed mid-implementation: final persistence ownership moved to frontend app API layer for MVP simple mode, but backend code/docs initially still included `/invoice/import` write path.

## Solution

### 1) Auth compatibility hardening

Updated auth to accept either:
- `X-API-Key: <key>`
- `Authorization: Bearer <key>`

File:
- `src/invproc/api.py`

### 2) Multipack parser support

Added parser rule for multipack patterns:
- Example: `24X2G` -> `48g` -> `0.048kg`
- Also supports comma-decimal liquid forms like `6x0,5L`

File:
- `src/invproc/weight_parser.py`

### 3) MVP contract scope alignment

Final MVP backend scope set to:
- `POST /extract`
- `POST /invoice/preview-pricing`

Deferred from MVP backend:
- `POST /invoice/import`
- idempotency persistence in backend

Frontend is responsible for final DB writes via existing app API layer.

Files:
- `src/invproc/api.py`
- `docs/plans/2026-02-11-feat-invoice-import-pricing-parity-backend-plan.md`

## Verification

Automated:
- `python3 -m pytest tests/ -q`
- Result after alignment: all tests passing (`47 passed` during final auth-coverage pass, `45+` after scope pivots, then green again).

Manual:
- Frontend confirmed flow works after contract alignment.
- Multipack edge case acknowledged and covered in parser tests.

## Prevention

1. Keep one explicit MVP contract section in plan docs and update it immediately when ownership changes.
2. For shared auth dependencies, add endpoint-level tests for both `X-API-Key` and bearer modes.
3. Add parser regression tests for real catalog naming patterns (multipack, commas, uppercase/lowercase units).
4. Avoid shipping “transitional” write endpoints when ownership is undecided; remove or defer cleanly.

## Key Files

- `src/invproc/api.py`
- `src/invproc/weight_parser.py`
- `src/invproc/import_service.py`
- `tests/test_api.py`
- `tests/test_invoice_import_api.py`
- `tests/test_invoice_weight_parser.py`
- `docs/plans/2026-02-11-feat-invoice-import-pricing-parity-backend-plan.md`

## Notes

This solution intentionally favors delivery speed and contract clarity for MVP. Backend-side import persistence can be reintroduced as a separate v2 feature with explicit ownership and migration plan.
