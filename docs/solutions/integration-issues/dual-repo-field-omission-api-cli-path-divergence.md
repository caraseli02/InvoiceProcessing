---
title: "Three P1 bugs: eval API 500 on annotated fixture, silent category/uom loss in Supabase repo, and optional hash params masking missing call sites"
category: integration-issues
date: 2026-03-22
symptoms:
  - "POST /internal/rag/eval returns HTTP 500 (TypeError) when fixture cases contain annotation fields such as `notes` or `expected_fail`"
  - "category and uom values written during product create/update are silently discarded against the real Supabase backend while the in-memory repo forwards them correctly"
  - "mypy does not catch call sites that omit `category`/`uom` from `build_product_snapshot_hash`, allowing silent wrong-hash computation and missed re-embedding"
root_cause: "The `_case_from_dict` key-filtering helper added for the CLI/file path in `rag.py` was not wired into the API endpoint, and `category`/`uom` fields were missing from the Supabase repository INSERT/UPDATE payloads and were silently optional in the hash function signature."
components:
  - "src/invproc/api.py"
  - "src/invproc/rag.py"
  - "src/invproc/catalog_sync.py"
  - "src/invproc/repositories/supabase.py"
tags:
  - rag
  - eval
  - catalog-sync
  - supabase
  - category
  - uom
  - product-hash
  - data-loss
  - fixture
  - repository-pattern
---

# Three P1 bugs: eval API crash, silent Supabase data loss, and hash param defaults

Discovered during code review of PR #31 (`fix/rag-null-category-fixture-expansion`). Three distinct bugs that all stem from the same root pattern: **parallel code paths diverge when a shared dataclass gains new fields**.

---

## Problem

PR #31 threaded `category` and `uom` from LLM extraction through 7 files into the embedding text. After the PR, a code review revealed three P1 bugs:

**Bug 1 — `/internal/rag/eval` crashed with 500 on annotated fixture cases**

`api.py:450` deserialized `CatalogEvalCase` directly via `CatalogEvalCase(**case)`. The PR's 34-case eval fixture contained `notes` and `expected_fail` annotation fields. Any request with annotated cases caused a `TypeError` → 500. The CLI path (`load_eval_cases`) already filtered unknown keys but the API path did not share that logic.

**Bug 2 — `build_product_snapshot_hash` silently accepted missing `category`/`uom`**

After `ProductRecord` and `UpsertProductInput` gained `category` and `uom` fields, the hash function retained `category: str | None = None, uom: str | None = None`. Call sites that omitted the args produced no error — mypy passed, tests passed, but the hash was computed without the new fields, making it impossible for the sync pipeline to detect category/uom drift between imports.

**Bug 3 — Supabase `create_product`/`update_product` silently discarded `category`/`uom`**

The payload dicts in `supabase.py` were built by explicitly enumerating fields. When `category` and `uom` were added to the shared dataclass, neither `create_product` nor `update_product` was updated. Data written to Supabase had null category and uom. The in-memory repository used in tests forwarded the fields correctly, so **all tests passed while production was silently broken**. (auto memory [claude]: this is the null-category metadata weak spot previously tracked in the RAG eval baseline)

---

## Root Cause

**Bug 1:** Two independent deserialization paths existed for the same type. The shared guard logic lived only in `load_eval_cases`. When fixture schema evolved (annotation fields added), only one path broke.

**Bug 2:** Keyword-only arguments with default values are not enforced by mypy unless the defaults are removed. `= None` masked the omission at every call site. The parameter existed but carried no type-level obligation.

**Bug 3 — the most dangerous:** The in-memory and Supabase repositories are structurally asymmetric. The in-memory repo constructs `ProductRecord` from `UpsertProductInput` by passing the object through (all fields forwarded automatically). The Supabase repo builds an explicit `dict` from individual field reads. Every new field added to the shared model requires a manual edit to the Supabase payload dict. There is no compile-time or test-time enforcement of this invariant.

---

## Solution

### Bug 1 — Centralize `CatalogEvalCase` construction

Extract a single private factory and use it in both the CLI and API paths:

```python
# rag.py — add before load_eval_cases
_EVAL_CASE_KEYS: frozenset[str] = frozenset({"query", "expected_product_id", "expected_name"})

def _case_from_dict(d: dict[str, Any]) -> CatalogEvalCase:
    """Construct a CatalogEvalCase from a raw dict, silently ignoring unknown keys."""
    return CatalogEvalCase(**{k: v for k, v in d.items() if k in _EVAL_CASE_KEYS})

def load_eval_cases(path: Path) -> list[CatalogEvalCase]:
    ...
    return [_case_from_dict(raw_case) for raw_case in raw_cases]
```

```python
# api.py:450 — before:
cases = [CatalogEvalCase(**case) for case in payload.cases]

# api.py:450 — after:
cases = [_case_from_dict(case) for case in payload.cases]
```

### Bug 2 — Remove defaults from `build_product_snapshot_hash`

```python
# catalog_sync.py — before (dangerous):
def build_product_snapshot_hash(
    *,
    product: ProductRecord,
    upsert_input: UpsertProductInput,
    embedding_model: str,
    category: str | None = None,   # ← default hides missing call sites
    uom: str | None = None,        # ← default hides missing call sites
) -> str:

# after (mypy error if any call site omits the args):
def build_product_snapshot_hash(
    *,
    product: ProductRecord,
    upsert_input: UpsertProductInput,
    embedding_model: str,
    category: str | None,   # ← no default
    uom: str | None,        # ← no default
) -> str:
```

Update any existing test that was silently omitting the args:

```python
# tests/test_catalog_sync.py
first_hash = build_product_snapshot_hash(
    product=product,
    upsert_input=upsert_input,
    embedding_model="text-embedding-3-small",
    category=None,   # now required
    uom=None,        # now required
)
```

### Bug 3 — Add `category`/`uom` to Supabase payload dicts

```python
# supabase.py — create_product (same change in update_product):
def create_product(self, data: UpsertProductInput) -> ProductRecord:
    payload = {
        "name": data.name,
        "barcode": data.barcode,
        "normalized_name": normalize_name(data.name),
        "supplier": data.supplier,
        "price": data.price,
        "price_50": data.price_50,
        "price_70": data.price_70,
        "price_100": data.price_100,
        "markup": data.markup,
        "category": data.category,   # ← added
        "uom": data.uom,             # ← added
    }
```

---

## Key Insight: In-Memory / Supabase Repository Asymmetry is a Structural Failure Mode

The in-memory repository forwards `UpsertProductInput` fields implicitly (via dataclass construction), so new fields propagate automatically. The Supabase repository enumerates fields explicitly in a `dict` literal — new fields must be added manually. The two repos share the same interface but diverge in implementation style. Result: **tests always pass** (they use in-memory), **production silently drops new fields**.

This is not a one-off oversight. It is a structural risk that recurs every time a field is added to any shared dataclass until one of these mitigations is in place:

1. Make the Supabase payload construction derive from `dataclasses.asdict(data)` or `.model_dump()`, then selectively exclude DB-only fields.
2. Add a shared contract test that runs against both the in-memory and Supabase implementations.
3. At minimum: add a snapshot test that asserts the INSERT dict contains all expected keys.

---

## Prevention

### Parallel Path Checklist

Run mentally whenever adding a new field to a shared dataclass, model, or schema:

```
□ 1. ENTRY POINTS — grep for every place this type is constructed or parsed.
     CLI path, API path, test fixtures — all of them. Updated all?

□ 2. SERIALIZATION SITES — grep for dict literals building INSERT/UPDATE payloads.
     Does each one include the new field?

□ 3. DESERIALIZATION SITES — grep for places reading DB rows, JSON, or fixture files.
     Does each one map the new field?

□ 4. FUNCTION SIGNATURE DEFAULTS — if the field appears as a param with a default,
     ask: is None still semantically valid, or is it now silently wrong?
     If wrong → remove the default, or use a _MISSING sentinel.

□ 5. TEST DOUBLE PARITY — find all in-memory/fake/stub implementations.
     Does each stub forward the new field everywhere the real impl does?

□ 6. CONTRACT TEST — shared test suite running against both real and fake impl.
     Add one assertion per new field. 30 seconds of work.

□ 7. HASH/FINGERPRINT FUNCTIONS — if this type contributes to a hash,
     does the hash function now include the new field?
     Is there a test proving different field values produce different hashes?
```

### Pattern A: API vs CLI Divergence

- Extract all parsing of a shared type into a single function. No inline construction anywhere.
- Contract test: feed the same annotated fixture dict to both CLI loader and API handler and assert equal output.
- Alternative: use `model_config = ConfigDict(extra="ignore")` on the Pydantic model so unknown keys are silently dropped at parse time in all paths.

### Pattern B: Optional Defaults Masking Required Data

- Use a `_MISSING = object()` sentinel instead of `= None` for fields that have been promoted to semantically required. The function raises `TypeError` at runtime rather than silently returning wrong output.
- Pin the hash to known test vectors. Add an assertion that different field values produce different hashes.

### Pattern C: Repository Payload Asymmetry

```python
# Detection test: asserts INSERT dict covers all model fields
def test_supabase_insert_dict_covers_all_product_fields():
    product = make_fully_populated_product()
    repo = SupabaseInvoiceImportRepository(mock_client)
    payload = repo._to_insert_dict(product)

    product_fields = {f.name for f in dataclasses.fields(UpsertProductInput)}
    db_excluded = {"id"}  # fields intentionally omitted
    expected = product_fields - db_excluded

    missing = expected - payload.keys()
    assert not missing, f"Fields not serialized to Supabase INSERT: {missing}"
```

This test auto-expands as the model grows. No manual update needed.

---

## Related

- `docs/solutions/integration-issues/supabase-backed-rag-persistence-needed-rls-atomic-queue-and-api-parity-20260320.md` — the Supabase adapter layer; this doc establishes the field-parity rule that was missing there
- `docs/solutions/integration-issues/catalog-sync-runtime-wiring-and-fail-open-idempotency-20260320.md` — `build_product_snapshot_hash` context; this doc establishes that hash function params must not have silent defaults for semantically required fields
- `docs/solutions/architecture-issues/hybrid-search-concurrent-dispatch-rag-eval-endpoint.md` — `/internal/rag/eval` endpoint design; this doc adds the fixture-format constraint (annotated keys must be filtered, not splatted)
- `docs/solutions/integration-issues/rag-runtime-ownership-split-caused-mock-embedding-fallback-20260320.md` — same class of CLI/API ownership-split bugs
