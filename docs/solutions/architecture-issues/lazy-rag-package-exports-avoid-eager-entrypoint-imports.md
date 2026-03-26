---
title: "Lazy RAG package exports avoid eager entrypoint imports"
category: "architecture-issues"
date: "2026-03-26"
tags: ["rag", "python", "imports", "lazy-loading", "compatibility", "architecture", "refactor"]
problem_type: "architecture-issue"
component:
  - "src/invproc/rag/__init__.py"
  - "tests/test_rag_import_compat.py"
symptoms:
  - "Plain `import invproc.rag` eagerly loaded eval, retrieval, sync, and transport"
  - "Lightweight consumers inherited the import cost of the full RAG stack"
  - "The package compatibility layer recreated coupling that the refactor was trying to remove"
root_cause: "The package entrypoint was implemented as a hard re-export layer, which forced import-time execution of the underlying workflow modules."
resolution_type: "code_fix"
severity: "medium"
related:
  - "docs/plans/2026-03-26-001-refactor-rag-workflow-boundaries-plan.md"
  - "docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md"
  - "docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md"
  - "docs/solutions/integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md"
  - "todos/095-pending-p2-rag-package-entrypoint-eager-imports.md"
---

# Lazy RAG package exports avoid eager entrypoint imports

## Problem

The RAG refactor split [`src/invproc/rag.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag.py) into workflow modules under [`src/invproc/rag/`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag), but `invproc.rag` still had to preserve the old compatibility surface for existing callers.

The first package version kept that surface by eagerly importing `eval`, `retrieval`, `sync`, and `transport` at package import time. That meant a plain `import invproc.rag` or a lightweight consumer like [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py) paid the import cost and coupling risk of the whole RAG stack even when it only needed a single helper such as `cosine_similarity`.

## Environment

- Module: Invoice Processing API
- Affected component: RAG package compatibility surface
- Key files:
  - [`src/invproc/rag/__init__.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/__init__.py)
  - [`tests/test_rag_import_compat.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_import_compat.py)
- Date: 2026-03-26

## Symptoms

- `import invproc.rag` preloaded:
  - `invproc.rag.eval`
  - `invproc.rag.retrieval`
  - `invproc.rag.sync`
  - `invproc.rag.transport`
- The compatibility layer preserved the old public API shape but also preserved monolithic import behavior.
- Package-level coupling stayed broader than intended after the workflow split.

## Investigation

- Reviewed the new package entrypoint in [`src/invproc/rag/__init__.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/rag/__init__.py).
- Confirmed the package root imported all workflow modules up front to provide compatibility exports.
- Traced a lightweight consumer path through [`src/invproc/repositories/memory.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/src/invproc/repositories/memory.py), which only needed `cosine_similarity` but still imported the package root.
- Used the review finding tracked in [todos/095-pending-p2-rag-package-entrypoint-eager-imports.md](/Users/vladislavcaraseli/Documents/InvoiceProcessing/todos/095-pending-p2-rag-package-entrypoint-eager-imports.md) as the execution target.
- Added a compatibility regression test to prove two requirements at once:
  1. legacy exports still resolve from `invproc.rag`
  2. a plain package import does not load the workflow submodules

## Root Cause

The package `__init__.py` was acting as a hard re-export layer instead of a lazy compatibility shim.

That pattern is fine for tiny packages, but it was the wrong fit here because:

- the RAG workflows import each other transitively
- some workflow modules pull in heavier dependencies
- the refactor goal was to narrow boundaries, not recreate one big import surface

In other words, the compatibility surface preserved API shape but accidentally preserved eager loading semantics too.

## Solution

Make `invproc.rag` a lazy compatibility surface.

### 1. Keep imports under `TYPE_CHECKING`

The package still needs static typing support, but type checkers do not require runtime imports:

```py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invproc.rag.eval import CatalogEvalCase, CatalogEvalResult
    from invproc.rag.retrieval import CatalogRetrievalService
```

This preserves editor and mypy visibility without importing the full workflow graph at runtime.

### 2. Add an explicit export map

Define one source of truth from public symbol name to implementation module:

```py
_EXPORT_TO_MODULE = {
    "CatalogEvalCase": "invproc.rag.eval",
    "CatalogRetrievalService": "invproc.rag.retrieval",
    "build_rag_worker": "invproc.rag.transport",
    "build_sync_status_snapshot": "invproc.rag.sync",
}
```

This keeps the compatibility surface explicit and narrow.

### 3. Resolve compatibility exports lazily with `__getattr__`

The package root now imports concrete workflow modules only when a caller actually accesses a specific export:

```py
from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
```

Caching the resolved value in `globals()` keeps repeated access cheap after first use.

## Verification

Added and kept the compatibility regression suite in [`tests/test_rag_import_compat.py`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/tests/test_rag_import_compat.py):

```py
def test_invproc_rag_package_import_is_lazy() -> None:
    for module_name in [
        "invproc.rag",
        "invproc.rag.eval",
        "invproc.rag.retrieval",
        "invproc.rag.sync",
        "invproc.rag.transport",
    ]:
        sys.modules.pop(module_name, None)

    importlib.import_module("invproc.rag")

    assert "invproc.rag.eval" not in sys.modules
    assert "invproc.rag.retrieval" not in sys.modules
    assert "invproc.rag.sync" not in sys.modules
    assert "invproc.rag.transport" not in sys.modules
```

Commands run:

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

Result:

- lint passed
- mypy passed
- full pytest passed with `199 passed`

## Why This Works

The fix separates two concerns that had been collapsed together:

1. **Public compatibility**: `invproc.rag` still exposes the legacy names callers expect.
2. **Runtime loading behavior**: those names no longer force the entire implementation graph to load on import.

That restores the architectural goal of the workflow split without breaking existing callers.

## Prevention

- Treat package roots as compatibility surfaces, not aggregation points.
- During refactors, preserve public import paths separately from preserving eager import behavior.
- Keep `__init__.py` narrow:
  - `TYPE_CHECKING` imports only
  - a small explicit export map
  - lazy `__getattr__`
- Avoid “helpful” top-level imports that quietly reintroduce coupling after a modularization effort.

### Recommended test guards

- Keep a cold-import regression test for `import invproc.rag`.
- Assert every name in `__all__` resolves through the package root.
- Keep at least one direct historical import test, for example:
  - `from invproc.rag import CatalogRetrievalService`
- Consider a subprocess smoke test in the future:

```bash
python -c "import invproc.rag"
```

This catches hidden import-time coupling that can be masked by a warm pytest process.

## Related Documentation

- [`docs/plans/2026-03-26-001-refactor-rag-workflow-boundaries-plan.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/plans/2026-03-26-001-refactor-rag-workflow-boundaries-plan.md)
- [`docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md)
- [`docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md)
- [`docs/solutions/integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md)
- [`docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md`](/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md)

## Refresh Candidates

The new learning suggests at least one narrow refresh target:

- `ce:compound-refresh docs/solutions/integration-issues/feature-propagation-gaps-sql-param-collision-dataclass-cli-parity.md`

That doc still references the older `src/invproc/rag.py` center of gravity and would benefit from a forward link to the package-entrypoint compatibility boundary.
