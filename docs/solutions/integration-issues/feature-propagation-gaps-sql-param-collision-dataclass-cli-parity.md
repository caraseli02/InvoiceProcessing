---
title: "Feature propagation gaps: PL/pgSQL param collision, frozen dataclass missing default, CLI/API top_k divergence"
problem_type:
  - database-issue
  - runtime-error
  - integration-issue
component:
  - supabase/migrations/003_missing_tables.sql
  - src/invproc/repositories/supabase.py
  - src/invproc/rag.py
  - src/invproc/api.py
  - src/invproc/cli.py
  - tests/test_supabase_repository.py
symptoms:
  - "ERROR 42P13: parameter name 'product_id' used more than once"
  - "ERROR 42702: column reference 'product_id' is ambiguous"
  - "TypeError: missing required argument: 'top_k_hits'"
  - "CLI --top-k flag missing; evaluate_all_modes() silently ignores top_k"
tags:
  - plpgsql
  - returns-table
  - parameter-naming
  - frozen-dataclass
  - default-value
  - cli-api-divergence
  - rag-eval
  - supabase
  - migration
  - parallel-path-checklist
date: 2026-03-23
---

# Feature propagation gaps: PL/pgSQL param collision, frozen dataclass missing default, CLI/API top_k divergence

## Problem Frame

When adding `top_k` support to the RAG eval pipeline (renaming SQL function params to `p_` prefix + adding `top_k` to the evaluator), three distinct bugs surfaced simultaneously because the change was applied inconsistently across the stack:

1. SQL function input params collided with `RETURNS TABLE` output column names → 42P13/42702 errors
2. New `top_k_hits` field added to a frozen dataclass without a default → `TypeError` at every existing construction site
3. `top_k` added to the API model and service method, but not to the CLI command or `evaluate_all_modes()` → API/CLI divergence

All three share a single root cause: **a feature addition applied to one layer without being propagated consistently across all consumers.**

---

## Root Cause

**Bug 1 — PL/pgSQL RETURNS TABLE parameter scope collision**

PostgreSQL PL/pgSQL uses a single unified namespace for input parameters and `RETURNS TABLE` output columns. When both declare the same identifier (e.g., `product_id text`), PG raises `42P13` at function creation or `42702` at call time. Fully qualifying with `function_name.product_id` syntax also fails — PL/pgSQL resolves `RETURNS TABLE` columns in the same scope as parameters.

**Bug 2 — Frozen dataclass new required field**

Adding `new_field: int` without a default to a frozen dataclass is a silent breaking change. Every existing call site that constructs the dataclass without the new field raises `TypeError` at runtime. There is no deprecation path — either all sites are updated atomically, or the field gets a default.

**Bug 3 — CLI/`evaluate_all_modes` parity gap**

`top_k` was correctly wired into `CatalogRagEvaluator.evaluate()` and `EvalRequest`, but not threaded through to `invproc rag eval` CLI command or the `evaluate_all_modes()` wrapper. Same class of dual-path divergence as in [dual-repo-field-omission-api-cli-path-divergence.md](dual-repo-field-omission-api-cli-path-divergence.md) — treat CLI as a "third repo" that must be updated manually.

---

## Investigation Steps

**Bug 1:**
- Migration failed with `ERROR 42P13: parameter name 'product_id' used more than once` on `CREATE OR REPLACE FUNCTION`
- Inspected function: both `input params` and `RETURNS TABLE (...)` declared `product_id text`
- Attempted fully-qualified `function_name.product_id` syntax → still raised `42702` on call
- Confirmed: PL/pgSQL shares scope for input params and return-table columns; `p_` prefix is the standard escape

**Bug 2:**
- Full test suite failed immediately with `TypeError: missing required argument: 'top_k_hits'`
- `test_rag_backend.py:1165`: `CatalogEvalResult(total_queries=0, top_1_hits=0, top_5_hits=0, cases=[])` — new field missing
- Also found `field` import missing from `dataclasses`

**Bug 3:**
- `invproc rag eval --top-k 20` silently used `top_k=10`
- Traced: CLI had no `--top-k` option; `evaluate_all_modes()` had no `top_k` param; only `evaluate()` accepted it
- API path was correct; CLI and `evaluate_all_modes` were not updated

**Bug 4 (downstream):**
- After fixing Bug 1, `test_supabase_repository.py` raised `KeyError: 'product_id'`
- `FakeSupabaseClient.execute_rpc` for `create_or_reuse_product_sync_row` looked up `params["product_id"]` (bare name) but params now had `p_product_id` keys
- Fix: fake must strip `p_` prefix to simulate what the SQL function does — maps `p_col` → column `col`

---

## Working Solution

### Bug 1 — PL/pgSQL `p_` prefix convention

Rename all input parameters to `p_<name>`. `RETURNS TABLE` output columns keep bare names.

```sql
create or replace function create_or_reuse_product_sync_row(
    p_product_id text,
    p_product_snapshot_hash text,
    p_name text,
    p_category text,
    -- etc
) returns table (
    product_id text,              -- bare names in RETURNS TABLE
    product_snapshot_hash text,
    name text,
    category text,
    -- etc
) language plpgsql security definer as $$
begin
    insert into product_embedding_sync (...) values (p_product_id, p_name, ...);
    -- p_ prefix in body; no ambiguity
end;
$$;
```

Update Python payload builder to match the `p_` prefix:

```python
def _product_sync_input_payload(self, data: ProductSyncRecordInput) -> dict[str, Any]:
    return {
        "p_product_id": data.product_id,
        "p_product_snapshot_hash": data.product_snapshot_hash,
        "p_name": data.name,
        "p_category": data.category,
        # etc
    }
```

Update `FakeSupabaseClient.execute_rpc` to strip `p_` prefix when storing rows:

```python
if fn_name == "create_or_reuse_product_sync_row":
    table_rows = self.rows["product_embedding_sync"]
    # Strip p_ prefix to simulate SQL function's param→column mapping
    cols = {k[2:] if k.startswith("p_") else k: v for k, v in params.items()}
    for row in table_rows:
        if (
            row["product_id"] == cols["product_id"]
            and row["product_snapshot_hash"] == cols["product_snapshot_hash"]
        ):
            return [{**row.copy(), "created": False}]
    row = self._prepare_row("product_embedding_sync", cols)
    table_rows.append(row)
    return [{**row.copy(), "created": True}]
```

### Bug 2 — Frozen dataclass new field with default

Always provide a default for additive metric fields. Also import `field` from dataclasses when using `field(default_factory=...)`.

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class CatalogEvalResult:
    total_queries: int
    top_1_hits: int
    top_5_hits: int
    top_k_hits: int = 0                              # default preserves existing construction sites
    cases: list[dict[str, Any]] = field(default_factory=list)
```

### Bug 3 — CLI/`evaluate_all_modes` missing `top_k`

Thread `top_k` through the full call chain:

```python
# rag.py — add top_k to evaluate_all_modes and forward to all evaluate() calls
def evaluate_all_modes(
    self,
    cases: list[CatalogEvalCase],
    *,
    top_k: int = 10,
) -> CatalogModeComparisonResult:
    return CatalogModeComparisonResult(
        semantic=self.evaluate(cases, mode="semantic", top_k=top_k),
        lexical=self.evaluate(cases, mode="lexical", top_k=top_k),
        hybrid=self.evaluate(cases, mode="hybrid", top_k=top_k),
    )
```

```python
# cli.py — expose --top-k and pass through both paths
def rag_eval(
    fixture_path: Path = typer.Argument(...),
    mode: str = typer.Option("hybrid", "--mode"),
    all_modes: bool = typer.Option(False, "--all-modes"),
    top_k: int = typer.Option(10, "--top-k", min=1, max=50),
) -> None:
    evaluator = CatalogRagEvaluator(retrieval_service)
    cases = load_eval_cases(fixture_path)
    if all_modes:
        comparison = evaluator.evaluate_all_modes(cases, top_k=top_k)
    else:
        result = evaluator.evaluate(cases, mode=mode, top_k=top_k)
```

---

## Prevention

### Extended Parallel Path Checklist

When adding a parameter to any shared service method or model, verify ALL of:

1. **Service layer** — add with a default so it is non-breaking
2. **API path** — update request model + route handler
3. **CLI path** — add Typer option + thread through to service call
4. **Aggregate wrappers** — any method that calls the modified method (e.g., `evaluate_all_modes`) needs the parameter forwarded
5. **Supabase repo** — explicitly add the field to INSERT/UPDATE dict (not picked up automatically)
6. **In-memory / fake** — update if the parameter affects stored state or returned data; strip SQL naming conventions (e.g., `p_` prefix) to match column names
7. **Tests** — add coverage for both API and CLI paths for the new parameter

### PL/pgSQL function convention

**Rule:** All input parameters in any `CREATE FUNCTION` / `CREATE OR REPLACE FUNCTION` MUST use `p_` prefix. `RETURNS TABLE` output columns use bare names. No exceptions.

**Verification at review time:** For any migration that adds or modifies a function, explicitly check: input param names (stripped of `p_`) must not overlap with any `RETURNS TABLE` column names.

### Frozen dataclass convention

**Rule:** New additive fields on frozen dataclasses MUST have a default (`= 0`, `= None`, `= ""`, etc.) unless all construction sites are updated atomically in the same commit.

**Catch early:** `grep -rn "ClassName(" src/ tests/` before merging any dataclass field addition.

### Test double convention

Any `Fake*` class that mimics a SQL RPC function must normalize parameter names to match what the real function returns. For `p_`-prefixed params: strip the prefix when storing rows.

```python
# Shared helper for any fake that calls p_-prefixed RPCs
def _strip_p_prefix(params: dict[str, Any]) -> dict[str, Any]:
    return {k[2:] if k.startswith("p_") else k: v for k, v in params.items()}
```

---

## Related Docs

- [dual-repo-field-omission-api-cli-path-divergence.md](dual-repo-field-omission-api-cli-path-divergence.md) — same class of propagation gap; Parallel Path Checklist origin
- [hybrid-search-concurrent-dispatch-rag-eval-endpoint.md](../architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md) — established the eval endpoint contract this fix extended
- [postgrest-schema-cache-stale-after-migration.md](../database-issues/postgrest-schema-cache-stale-after-migration.md) — related Supabase migration mechanics; does not yet document RETURNS TABLE column name collision risk
