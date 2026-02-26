# Newcomer Guide: InvoiceProcessing

This guide is a practical orientation for engineers joining the project.

## What this service does

InvoiceProcessing extracts structured invoice data from PDF files and exposes it through:

- a CLI (`invproc process ...`) for local workflows
- a FastAPI service (`/extract`, `/invoice/preview-pricing`, `/health`) for app integrations

The extraction flow combines deterministic PDF/OCR parsing with LLM normalization and a validation layer.

## High-level architecture

The code follows a layered design:

1. **Entry points**
   - `src/invproc/__main__.py`: mode switch (CLI vs API)
   - `src/invproc/cli.py`: local command interface
   - `src/invproc/api.py`: HTTP API interface
2. **Extraction pipeline**
   - `src/invproc/pdf_processor.py`: PDF text extraction + OCR fallback + grid rendering
   - `src/invproc/llm_extractor.py`: OpenAI-driven structured extraction
   - `src/invproc/extract_cache.py`: file-hash cache for extraction responses
3. **Domain and validation**
   - `src/invproc/models.py`: core Pydantic data contracts
   - `src/invproc/validator.py`: normalization, scoring, and consistency checks
   - `src/invproc/weight_parser.py`: parsed support for weighted/KG line items
   - `src/invproc/pricing.py`: pricing preview/import-oriented calculations
4. **Integration services**
   - `src/invproc/import_service.py`: invoice import prep flow
   - `src/invproc/repositories/`: repository interface + in-memory implementation
5. **Infrastructure and security**
   - `src/invproc/config.py`: runtime configuration and env handling
   - `src/invproc/auth.py`: auth dependencies for protected API routes

## Request/data flow to understand first

For `/extract`, the common path is:

1. Request enters FastAPI route in `api.py`.
2. File is validated and hashed; cache is consulted.
3. PDF content is extracted using native text or OCR fallback.
4. Text is transformed into a spatially-aligned grid for LLM robustness.
5. LLM output is parsed into `models.py` schemas.
6. Validation and normalization rules are applied (including KG-specific behavior).
7. Response is returned with optional cache/observability headers.

## Important project conventions

- **Quality gates are strict**: run Ruff, mypy, and pytest before merge-ready work.
- **Coverage matters**: tests enforce fail-under 80%.
- **PR policy is enforced**: include exactly one change label and matching evidence section.
- **Configuration-first behavior**: most runtime behavior is env-controlled via `config.py`.
- **Defensive extraction**: parser and validator logic intentionally constrain malformed LLM output.

## Where to look for key behavior

- **API surface and contracts**: `src/invproc/api.py`, `src/invproc/models.py`
- **LLM prompt and extraction behavior**: `src/invproc/llm_extractor.py`
- **Invoice math and line consistency checks**: `src/invproc/validator.py`
- **KG/weighed row handling**: `src/invproc/weight_parser.py`, `tests/test_uom_kg_weight_candidate.py`
- **Pricing preview/import semantics**: `src/invproc/pricing.py`, `tests/test_pricing_validation.py`, `tests/test_invoice_pricing.py`
- **End-to-end behavior expectations**: `tests/test_e2e.py`, `tests/test_api.py`, `tests/test_cli.py`

## Suggested learning path

1. **Start with runtime entry points**
   - read `__main__.py`, then `cli.py`, then `api.py`
2. **Understand data contracts**
   - read `models.py` and note field-level assumptions
3. **Trace one extraction end to end**
   - walk `pdf_processor.py` → `llm_extractor.py` → `validator.py`
4. **Read tests as executable docs**
   - prioritize API, pricing, and KG normalization test modules
5. **Study operational docs**
   - `docs/quality-gates.md` and CI workflow for what must pass in CI

## Good first contributions

- Add test coverage around edge-case invoice rows.
- Improve error messages and exception mapping in API responses.
- Tighten type hints around extraction/normalization transformations.
- Extend docs for known integration pitfalls in `docs/solutions/`.
