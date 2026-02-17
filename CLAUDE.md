# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 💬 Communication Standards

**Be extremely concise. Sacrifice grammar for the sake of concision.**

Apply throughout all interactions: plans, explanations, code reviews, feedback. Prioritize clarity & brevity over perfect English.

## Git Hook Policy

- Hook setup (run once per clone): `git config core.hooksPath .githooks`
- Do not use `git commit --no-verify` by default.
- Use `--no-verify` only with explicit user approval in the current thread.
- If a hook blocks a commit, fix hook/config/staged files first, then retry normal commit.

## PR Quality Gate Policy (2026-02-17)

- Strict merge gates are required for `main`.
- Before opening/updating a PR, run:
  - `python -m ruff check src/ tests/`
  - `python -m mypy src/`
  - `python -m pytest -q`
- Coverage is enforced in `pytest` with fail-under `80%`.
- PRs must include exactly one label:
  - `change:feature`
  - `change:refactor`
  - `change:deploy`
- PR body must include the evidence section that matches the label:
  - `### Feature Test Evidence`
  - `### Refactor Regression Evidence`
  - `### Deploy Verification Plan`
- Required branch protection checks for `main`:
  - `lint`
  - `typecheck`
  - `tests`
  - `health-smoke`
  - `pr-policy`
  - `quality-gate-pr`
- CI workflow path: `.github/workflows/ci.yml`
- Policy reference: `docs/quality-gates.md`

---

## Commands

```bash
# Install in editable mode (required before first use)
pip install -e .

# Install dev dependencies (pytest, ruff, black, mypy)
pip install -e ".[dev]"

# Run the CLI
invproc process <invoice.pdf>
python -m invproc process <invoice.pdf>

# Run with mock data (no OpenAI API key needed)
invproc process test_invoices/invoice-test.pdf --mock

# Run with debug output (saves text grids to output/grids/)
invproc process test_invoices/invoice-test.pdf --mock --debug

# Run with verbose logging
invproc process test_invoices/invoice-test.pdf --mock --verbose

# Consistency check: run extraction N times and compare
invproc process test_invoices/invoice-test.pdf --retry 3

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/

# Tests
pytest tests/
pytest tests/test_specific.py -v
```

## Environment

API key is loaded from `.env` file or environment variable. Either of these works:
- `OPENAI_API_KEY`
- `INVPROC_OPENAI_API_KEY`

The `.env` file is gitignored. All output (grids, OCR debug images, JSON results) goes to `output/`, which is also gitignored.

## Architecture

The pipeline is: **PDF → Text Grid → LLM → Validation → JSON output**.

### Backend ownership rule (permanent)

This repository is the backend for the React app. There is no separate app backend.

Treat this service as the source of truth for backend behavior, contracts, and business logic.

All config lives in a single `InvoiceConfig` (Pydantic Settings) singleton accessed via `get_config()` in `config.py`. CLI flags mutate this singleton before passing it downstream — there is no separate config-passing mechanism.

### Module responsibilities

| Module | Role |
|---|---|
| `cli.py` | Typer app, the only entry point. Wires the pipeline together in `_extract_single()`. |
| `pdf_processor.py` | Extracts words with coordinates via pdfplumber. Builds a space-padded text grid that preserves column alignment. Falls back to Tesseract OCR if a page has < 10 words. |
| `llm_extractor.py` | Sends the text grid to GPT-4o-mini with a detailed system prompt (column identification rules, hallucination prevention). Parses the JSON response into `InvoiceData`. Contains `--mock` fallback. |
| `validator.py` | Re-scores every product's `confidence_score` using math validation (qty × price ≈ total), field completeness, and value-range checks. Logs overall confidence. |
| `models.py` | Pydantic models (`Product`, `InvoiceData`) with built-in validators: `Product` caps confidence to 0.6 if math is off by >5%; `InvoiceData` validates currency against a fixed set. |
| `config.py` | Pydantic Settings with `.env` support. Exposes `get_config()` (singleton) and `reload_config()` (used by `--retry`). |

### Text grid — the core technique

The text grid is the key innovation that prevents LLM column-swapping. `pdf_processor.py` takes pdfplumber's per-word `(x0, top)` coordinates, groups words into rows by vertical position (configurable tolerance, default 3px), then lays them out horizontally using space-padding scaled by `scale_factor` (default 0.2, meaning 1 PDF point ≈ 0.2 characters). This produces a plain-text representation where columns line up visually, making it much harder for the LLM to confuse Quantity vs. Price columns.

### Validation flow

Validation runs twice. First, Pydantic's `model_validator` on `Product` runs during LLM response parsing and caps `confidence_score` if math diverges >5%. Then `InvoiceValidator.validate_invoice()` recalculates all confidence scores from scratch using its multi-factor scoring (math, completeness, value ranges), overwriting whatever the LLM returned.

## Key details to keep in mind

- The `README.md` contains an older FastAPI blueprint (not the current CLI). The actual running code is entirely under `src/invproc/`.
- `tests/` is active and enforces project coverage >= 80% via pytest config.
- The system prompt in `llm_extractor.py:_get_system_prompt()` is METRO Cash & Carry-specific (Romanian column headers like "Cant.", "Pret unitar", "Valoare incl.TVA"). Generalizing to other invoice formats will require prompt changes.
- `response_format={"type": "json_object"}` is used instead of Pydantic-native structured output (`client.chat.completions.parse`). The JSON is manually parsed and fed to the Pydantic model.
- Supported currencies are hardcoded in `models.py`: EUR, USD, MDL, RUB, RON.
