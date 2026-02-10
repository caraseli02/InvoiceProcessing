---
module: Invoice Processing
date: 2026-02-10
problem_type: performance_issue
component: tooling
symptoms:
  - "OCR processing risked OOM kills on Render free tier (512Mi RAM) for multi-page PDFs"
  - "Production reliability was blocked by high OCR memory usage with OCR_DPI=300"
  - "Large uploads could amplify memory pressure during /extract requests"
root_cause: config_error
resolution_type: config_change
severity: critical
tags: [ocr, memory, render-free-tier, upload-limits, invoice-processing]
---

# Troubleshooting: OCR Memory Exhaustion on Render Free Tier

## Problem
The API was configured for high OCR quality (`OCR_DPI=300`) on an infrastructure tier with only `512Mi` memory. Under multi-page OCR load, this increased OOM risk and made production reliability unacceptable.

## Environment
- Module: Invoice Processing
- Affected Component: OCR + API upload pipeline + deployment configuration
- Date: 2026-02-10

## Symptoms
- Potential OOM kills for OCR-heavy requests on Render free tier.
- Reliability concerns for PDFs with multiple pages.
- No tight upload-size cap aligned with memory constraints.

## What Didn't Work

**Attempted Solution 1:** Keep current OCR settings and rely on free-tier limits.
- **Why it failed:** Memory headroom was too small for worst-case OCR scenarios.

**Attempted Solution 2:** Validate OCR quality using strict text-level similarity only.
- **Why it failed:** Text-level OCR similarity did not map cleanly to business correctness for invoice extraction.

## Solution
Implemented a low-risk configuration and API guard strategy focused on memory safety.

**Code/config changes:**
```yaml
# render.yaml
- key: OCR_DPI
  value: 150
- key: MAX_PDF_SIZE_MB
  value: 2
```

```env
# .env.example
OCR_DPI=150
MAX_PDF_SIZE_MB=2
```

```python
# src/invproc/config.py
ocr_dpi: int = Field(default=150, ge=150, le=600)
max_pdf_size_mb: int = Field(default=2, ge=1, le=50)
```

```python
# src/invproc/api.py
max_file_size = config.max_pdf_size_mb * 1024 * 1024
await run_in_threadpool(_save_upload_with_limit, file.file, temp_pdf_path, max_file_size)
```

## Verification

**Memory profiling under load:**
- API load run (`10` parallel `/extract` requests, `OCR_DPI=150`, `MAX_PDF_SIZE_MB=2`)
  - Peak RSS: `346.4 MB`
  - Avg RSS: `287.7 MB`
  - Responses: `9x 200`, `1x 429`, `0x 500`
- Direct OCR-path stress run
  - Peak RSS: `229.0 MB`

Result: local load stayed below the `<400MB` target.

**Accuracy policy decision:**
- Raw OCR text-level `>95%` was not achieved on sample invoice.
- Team chose business-level acceptance for this fix: critical invoice fields correct.
- Critical fields validated successfully on sample invoice:
  - Supplier: pass
  - Invoice number: pass
  - Currency: pass
  - Total amount: pass

## Why This Works
1. Lowering DPI directly reduces per-page raster memory during OCR.
2. Enforcing `MAX_PDF_SIZE_MB` at upload time bounds memory pressure before expensive processing.
3. Combining both controls addresses the actual failure mode (resource exhaustion), not just symptom timing.

## Prevention
- Keep memory-sensitive defaults aligned with deployment tier limits.
- Treat OCR resolution and upload limits as coupled settings.
- Require load/memory profiling evidence before raising OCR resolution in production.
- Track line-item quality separately from memory hardening acceptance.

## Related Issues
- See also: [/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/security-issues/multipart-upload-size-enforcement-system-20260210.md](../security-issues/multipart-upload-size-enforcement-system-20260210.md)
- See also: [/Users/vladislavcaraseli/Documents/InvoiceProcessing/docs/solutions/performance-issues/blocking-io-async-prevents-concurrency.md](./blocking-io-async-prevents-concurrency.md)
