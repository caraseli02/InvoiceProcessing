---
date: 2026-02-12
topic: invoice-product-category-autofill
---

# Invoice Product Category Autofill

## What We're Building

Reduce manual category selection by auto-assigning a product `Category` during invoice import (and ideally on product create) using the best available signals:

- Product name from invoice OCR/PDF
- Barcode (when present)
- Existing inventory data (your own previously-categorized products)

Category remains a plain string on the product record, with the UI’s “official” set:
`General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale`.

## Why This Approach

Today you already have:

- A keyword-based `inferCategoryFromName(name)` in the invoice upload flow
- A barcode-based `suggestProductDetails(barcode)` (OpenFoodFacts mapping) that can fill category
- Defaults that effectively treat missing category as `General` in the editor and create flow

The main opportunity is to increase coverage/accuracy while avoiding bad overwrites. The simplest way to do that is to add a “use our own history first” signal, and tighten the semantics around `null` vs `"General"`.

## Approaches

### Approach A: Improve The Existing Keyword Rules (Name -> Category)

Expand the deterministic rules to handle your real invoice language(s) and common abbreviations/brands, with normalization and tests.

Pros:
- Predictable, cheap, fast
- Easy to constrain output to the official set

Cons:
- Requires continuous maintenance (new products/terms)
- Harder to get high accuracy on ambiguous names

Best when:
- Invoices have consistent naming patterns
- You want deterministic behavior and no external dependencies

### Approach B (Recommended): Learn From Your Own Inventory (Name -> Category Memory)

When importing invoice rows, try to match the incoming normalized name against existing products (and/or previously imported rows) and reuse the most common/most confident category for similar items.

Signals could include:
- Exact match on normalized name
- Token overlap / fuzzy match (conservative threshold)
- Optional supplier-specific mappings (same supplier uses stable naming)

Pros:
- Improves over time organically
- Captures your business-specific categorization better than OpenFoodFacts
- Keeps output within your existing category set

Cons:
- Cold start: needs existing labeled products
- Requires care to avoid wrong fuzzy matches

Best when:
- You already have a growing catalog with categories set
- You want “it gets better automatically” without AI costs

### Approach C: Add An AI Fallback (Name -> Category Classifier)

If barcode is missing and name/inventory match are inconclusive, classify the product name into one of the official categories via an LLM (or improve existing OpenFoodFacts mapping with more robust tag mapping).

Pros:
- Better coverage on long-tail items
- Minimal rules maintenance once tuned

Cons:
- Cost/latency/reliability
- Needs strict constraints (must choose from official set + return confidence/source)

Best when:
- Keyword + inventory memory still leaves too many `General` rows

## Key Decisions

- Category suggestions must be from the official set only (no free-form strings for now).
- Preserve `null` as “unknown”: Only set `"General"` when the user explicitly selects it, or when an automation sets it with a clear `source`.
- Never overwrite a user-set category automatically: If a product has a non-null category that is not `"General"`, keep it.
- Add “source” and optional “confidence” in the inference pipeline (even if not persisted): `manual | inventory_memory | rules | openfoodfacts | ai`.
- Constrain output to the official category set for filtering/i18n stability.

## Open Questions

- What languages dominate product names on invoices (RO/RU/EN)? Any frequent abbreviations?
- What’s the success metric: e.g. “reduce manual category edits from 80% of rows to <20%”?
- Is invoice import expected to update the product record’s category, or only prefill the UI selector for that import session?
- Should category inference happen only in the frontend, or should the FastAPI service also return `category_suggestion` with each extracted product row?
 - If both frontend + backend produce suggestions, which one wins when they disagree (and is there a confidence threshold)?

## Notes: Using An LLM To Determine Category From Name

Yes: you can instruct an LLM to classify an invoice product `name` into a fixed enum of categories.

Guardrails required:
- Output must be **one of** the official category strings (or `null`), no other text.
- Include a `confidence` (0-1) and `source="ai"` so the UI can decide to apply it only above a threshold.
- Never apply AI if the product already has a non-null category that is not `"General"`.
- Prefer AI as a *fallback* after:
  - barcode-based suggestion (OpenFoodFacts)
  - reuse from your own inventory (name match)
  - deterministic keyword rules

## Next Steps

1. Decide constraints: official category set only vs free-form.
2. Pick the initial strategy to ship first: A only, B only, or A+B (recommended), with optional C later.
3. Proceed to `/workflows:plan` once the choice is made.
