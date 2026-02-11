---
module: Development Workflow
date: 2026-02-11
problem_type: workflow_issue
component: development_workflow
symptoms:
  - "Pricing preview looked incorrect even after backend extraction fixes were merged"
  - "UI still showed old labeling and transport interpretation despite recent implementation updates"
  - "Debugging effort focused on backend regressions while issue source was stale frontend code"
root_cause: missing_workflow_step
resolution_type: workflow_improvement
severity: medium
tags: [frontend-backend-alignment, branch-sync, pricing-preview, workflow]
related:
  - docs/solutions/runtime-errors/zero-valued-llm-product-rows-caused-extract-500-20260211.md
  - docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md
---

# Troubleshooting: Stale Frontend Branch Masked Pricing Fixes

## Problem

Pricing behavior appeared wrong during invoice validation, even though backend extraction and validation fixes had already been applied. The observed mismatch came from testing with a frontend state that did not include latest UI and calculation updates.

## Environment

- Module: Development Workflow
- Affected Component: Frontend/backend integration test loop
- Date: 2026-02-11

## Symptoms

- Product rows still displayed legacy pricing semantics after backend fixes.
- Team suspected transport allocation and VAT conversion logic was still broken in backend.
- Repeated checks against backend responses showed correct API-side values while UI output remained inconsistent.

## What Didn't Work

**Attempted Solution 1:** Continue backend investigation for transport/VAT math.  
- **Why it failed:** Backend payload and validation were already corrected; additional backend debugging did not change UI behavior.

**Attempted Solution 2:** Re-run invoice imports without confirming branch parity first.  
- **Why it failed:** Reprocessing invoices on stale frontend code reproduced the same visual mismatch, creating a false regression signal.

## Solution

Enforced branch parity before evaluating pricing behavior:

1. Confirm active branch and commit in both repositories.
2. Pull latest `origin/main` for frontend and backend before verification.
3. Re-test using same invoice only after both sides are aligned.
4. Treat UI output mismatch as frontend issue unless backend response payload is also wrong.

Practical checks used:

```bash
git branch --show-current
git fetch origin
git pull --ff-only origin main
git rev-parse --short HEAD
```

## Why This Works

The root cause was process drift, not calculation logic. Without a mandatory "sync both repos first" step, the team compared new backend behavior against old frontend rendering logic. Aligning branches removed false negatives and restored trustworthy debugging signals.

## Prevention

- Add "sync frontend + backend to latest main" as a mandatory pre-check in invoice pricing QA.
- Record both commit SHAs in bug reports before investigating pricing mismatches.
- Compare raw API response and UI rendering side-by-side to identify the failing layer early.
- Treat cross-repo parity as a gate before concluding regressions in pricing math.

## Related Issues

- See also: `docs/solutions/runtime-errors/zero-valued-llm-product-rows-caused-extract-500-20260211.md`
- See also: `docs/solutions/integration-issues/invoice-mvp-auth-and-parser-alignment-20260211.md`
