# Plan: Promptfoo Adoption Decision

## Context

After merging RAG Phase 5 (retrieval eval framework), the question is whether to integrate
promptfoo community edition or continue relying on the custom eval framework. The system is
still experimental. The goal is to pick the right tool for the current maturity level.

---

## Current Eval Framework (What We Have)

**Location:** `src/invproc/rag.py:CatalogRagEvaluator`

- **Metrics:** top-1 hit rate, top-5 hit rate only
- **Test cases:** 15 real queries from METRO invoice #94 (`tests/fixtures/rag_queries.json`)
- **Modes:** semantic / lexical / hybrid — side-by-side comparison works today
- **Surfaces:** CLI `invproc rag eval` + API `POST /internal/rag/eval`
- **Baseline:** hybrid ~60% top-1, ~93% top-5
- **Known gap:** null `category` metadata hurts disambiguation

What it does NOT measure:
- Is the retrieved context actually *useful* to the LLM?
- Is the LLM output *faithful* to retrieved products?
- Does retrieval quality impact final extracted `InvoiceData`?

---

## What Promptfoo Community Adds

| Feature | Value to this project |
|---|---|
| Context-faithfulness, context-recall metrics | High — but only when evaluating the *full* RAG→LLM pipeline |
| Multi-strategy parallel comparison | Already have `--all-modes` |
| Visual matrix UI | Nice to have, not blocking |
| Custom Python providers | Good — can wrap `CatalogRetrievalService` |
| CSV/JSON test case management | Already have this |
| LLM-as-judge assertions | High value for generation quality checks |
| CI/CD native integration | Achievable but requires config files |
| Zero extra code for boilerplate eval | Only matters if eval cases grow fast |

---

## Recommendation: Defer Promptfoo

**Reason: the current eval measures exactly what matters now — retrieval hit rate.**

Promptfoo's strongest value is LLM-as-judge metrics (context-faithfulness, answer-relevance).
These are only meaningful when you're evaluating the *full pipeline output* (retrieval → LLM
prompt → structured `InvoiceData`), not just "did the right product appear in top-5?"

The system is experimental. Retrieval quality is still being tuned. Adding promptfoo now:
- Adds a YAML config layer and external CLI dependency
- Requires learning promptfoo config syntax (non-trivial)
- Doesn't solve the known gap (null category metadata)
- Doesn't improve the baseline

**What would change this decision (trigger to revisit):**

1. Retrieval top-1 > 80% and you want to know if LLM is *using* the context correctly
2. You want to compare prompt variants for `_get_system_prompt()` across test cases at scale
3. Team grows and non-engineers need to view/share eval results (web UI)
4. Need to test multiple embedding models side by side (promptfoo handles this elegantly)

---

## What to Do Instead (Now)

The actual gap is the eval **coverage**, not the tooling:

1. **Fix the null category weak spot** — improve embedding text when `category` is null
   (e.g., fall back to barcode-only match, or fill from product name tokens)

2. **Expand fixture** — 15 cases is thin; aim for 30-40 to improve baseline confidence

3. **Add a failing-case annotation** — mark known failures in `rag_queries.json` with
   `"expected_fail": true` so regressions are visible

These are all achievable with the existing framework in `rag.py` + fixture JSON. No new
tooling needed.

---

## If Promptfoo Is Adopted Later

**Integration shape** (for future reference):

```yaml
# promptfooconfig.yaml
providers:
  - id: python:src/invproc/promptfoo_provider.py
    label: hybrid-rag

tests:
  - file://tests/fixtures/rag_queries.json

defaultTest:
  assert:
    - type: python
      value: file://tests/eval_assertions.py
```

- Write `src/invproc/promptfoo_provider.py` — thin wrapper around `CatalogRetrievalService`
- Write `tests/eval_assertions.py` — translate existing top-k logic into promptfoo assertion format
- Estimated effort: ~1 day

---

## No Code Changes Required

This is a decision record. No files to modify. The existing eval framework is kept as-is.
