---
module: Invoice Processing API
date: 2026-03-26
problem_type: runtime_error
component: llm_extraction_pipeline
symptoms:
  - "Uploading a large multi-page invoice through `/docs` returned `500 Internal Server Error` with `json.decoder.JSONDecodeError`"
  - "The same invoice later returned `422` with `Model output was truncated before valid JSON was completed. Please retry.` after error classification was added"
  - "A 4-page Metro invoice included discount-detail pages/sections that inflated the LLM payload without contributing importable product rows"
root_cause: oversized_llm_payload_with_discount_noise
resolution_type: code_fix
severity: high
tags: [llm, invoice-extraction, runtime-error, truncation, pdf-processing, chunking, discount-filtering]
related:
  - docs/solutions/best-practices/llm-column-swap-prevention-spatial-layout-invoice-extraction-20260202.md
  - todos/090-pending-p1-handle-llm-json-truncation.md
  - todos/091-pending-p2-prune-discardable-pages-before-extraction.md
  - todos/092-pending-p2-order-dependent-chunk-merge.md
  - todos/093-pending-p2-brittle-page-pruning-heuristics.md
  - todos/094-pending-p3-add-public-path-regression-test-for-page-pruning.md
---

# Troubleshooting: Large Invoice Extraction Failed Because Discount Pages Helped Push the LLM Past Valid JSON Output

## Problem

Large invoices were failing in `/extract` because the model response was too large to stay within the current JSON-output budget. The failure surfaced as `json.decoder.JSONDecodeError` in `LLMExtractor.parse_with_llm()`, which meant the API either crashed with `500` or, after an intermediate fix, returned a controlled `422`.

The concrete trigger was a 4-page Metro invoice where pages 3-4 contained mostly discount-detail rows and totals. Those rows were not useful as importable products, but they still bloated the prompt and increased the chance of truncated JSON output.

## Environment

- Module: Invoice Processing API
- Affected Component: `src/invproc/llm_extractor.py` and `src/invproc/pdf_processor.py`
- Entry Point: `POST /extract` via Swagger at `/docs`
- Date: 2026-03-26

## Symptoms

- Swagger returned:

```json
{
  "detail": "Processing failed: Expecting property name enclosed in double quotes: line 466 column 6 (char 12010)"
}
```

- Server logs showed the exception path:

```python
File "src/invproc/llm_extractor.py", line 91, in parse_with_llm
    invoice_data_dict = json.loads(content)
json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes
```

- After adding explicit invalid-JSON handling, the same invoice produced:

```json
{
  "detail": "Model output was truncated before valid JSON was completed. Please retry."
}
```

- Inspecting the invoice page text revealed:
  - pages 1-2 contained real product rows
  - page 3 was discount-detail noise only
  - page 4 started with more discount-detail lines, then ended with final totals like `Total de plata 14454,99`

## What Didn’t Work

**Attempted Solution 1:** Trust the LLM to always return valid JSON.
- **Why it failed:** `chat.completions.create(..., response_format={"type": "json_object"})` still produced malformed JSON when the response became too large, and `json.loads()` crashed immediately.

**Attempted Solution 2:** Only classify malformed JSON as a `422`.
- **Why it helped but did not solve the root issue:** This improved the API contract and stopped the `500`, but users still had to retry manually because the payload was still too large.

**Attempted Solution 3:** Treat the last two pages as globally irrelevant.
- **Why it failed conceptually:** page 3 was safe to remove, but page 4 still contained the authoritative final totals and payment summary. The correct solution was selective pruning, not dropping both pages wholesale.

## Root Cause

Two things combined to cause the failure:

1. **The LLM was asked to produce one large JSON object for the full invoice**
   - The extraction path originally sent the full text grid in one request.
   - Large invoices with many rows produced large JSON arrays.
   - The model response could truncate before closing the JSON structure.

2. **The input still contained discount-detail noise that did not need to be modeled as products**
   - The affected invoice contained discount-only rows like:

```text
250075360  2,49-  20%  0,50-  2,99-
```

   - These rows increased prompt size and model output burden without adding usable product data.

## Solution

Implemented a two-part fix:

### 1. Split large invoice text grids into chunked LLM requests

`LLMExtractor.parse_with_llm()` now splits oversized text grids into bounded chunks, requests one JSON payload per chunk, and merges the normalized payloads before final `InvoiceData` validation.

Key changes:

```python
chunks = self._split_text_grid_into_chunks(text_grid)
chunk_payloads = [
    self._request_invoice_chunk(
        chunk_text=chunk,
        chunk_index=index,
        chunk_count=len(chunks),
    )
    for index, chunk in enumerate(chunks, start=1)
]
invoice_data_dict = self._merge_chunk_payloads(chunk_payloads)
return InvoiceData(**invoice_data_dict)
```

Supporting changes:
- chunk-aware user prompt (`This is chunk X of Y`)
- chunk-level JSON parse error classification
- merge logic that combines products and invoice metadata after per-chunk normalization

### 2. Prune discount-detail lines before sending page text to the LLM

`PDFProcessor.extract_content()` now sanitizes each page’s text grid before it is appended to the final LLM payload:

```python
sanitized_page_text = self._sanitize_page_text_for_llm(
    page_text, page_number=i + 1
)
if not sanitized_page_text:
    continue

full_text_grid.append(
    f"--- Page {i + 1} (...) ---\n{sanitized_page_text}"
)
```

The sanitizer removes:
- discount-only detail rows
- `PL/PA:` noise lines

It keeps:
- real product rows
- final summary lines such as:
  - `Total de plata`
  - `Total cantitate`
  - `Total platit`
  - `Rest de primit`

### Real invoice validation

After the fix, the reviewed Metro invoice produced:
- **page 1** kept
- **page 2** kept
- **page 3** dropped from the LLM payload
- **page 4** kept, but only with final totals/payment summary after discount-detail rows were removed

Observed payload sections sent to the LLM: `1`, `2`, and `4`.

## Why This Works

- Chunking reduces the size of any single JSON response the model has to produce.
- Discount-detail pruning removes rows that the prompt already says to ignore, reducing token waste.
- Preserving page 4 summary lines ensures the final total still reaches the model.
- Returning `LLMOutputIntegrityError` instead of bubbling raw `JSONDecodeError` gives the API a controlled failure mode when the model still misbehaves.

## Verification

Quality gates run after the fix:

```bash
python -m ruff check src/ tests/
python -m mypy src/
python -m pytest -q
```

Result at fix time:
- `197 passed`
- `Required test coverage of 80% reached. Total coverage: 90.44%`

Manual verification performed:
- reproduced the failure through `/docs`
- confirmed the intermediate `422` truncation message
- inspected the real PDF page-by-page with `pdfplumber`
- confirmed that the sanitized extracted content dropped page 3 and pruned discount rows from page 4 while preserving `Total de plata`

## Prevention

- When an invoice format includes discount-detail continuation pages, treat those rows as preprocessing candidates instead of relying only on prompt instructions.
- For large structured outputs, prefer chunked extraction over one huge JSON object.
- Preserve authoritative totals pages even if they also contain discount noise.
- Keep explicit tests for malformed JSON/truncation behavior in the LLM integration layer.
- Review follow-up risks before considering the solution fully generalized:
  - page-pruning heuristics are currently format-sensitive
  - chunk merging is still order-dependent
  - page pruning currently saves LLM tokens more than PDF/OCR preprocessing cost

## Related Issues

- Best-practice reference for discount-line handling:
  - `docs/solutions/best-practices/llm-column-swap-prevention-spatial-layout-invoice-extraction-20260202.md`
- Follow-up todos created during review:
  - `todos/090-pending-p1-handle-llm-json-truncation.md`
  - `todos/091-pending-p2-prune-discardable-pages-before-extraction.md`
  - `todos/092-pending-p2-order-dependent-chunk-merge.md`
  - `todos/093-pending-p2-brittle-page-pruning-heuristics.md`
  - `todos/094-pending-p3-add-public-path-regression-test-for-page-pruning.md`
