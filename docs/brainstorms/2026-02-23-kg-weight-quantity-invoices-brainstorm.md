---
date: 2026-02-23
topic: kg-weight-quantity-invoices
---

# KG / weighed-item invoice rows (quantity vs weight)

## What We're Building

Support invoice line items where the packaging/UOM column (`Mod amb`) is `KG` and the `Cant.` column contains the **measured weight in kilograms** (for example `0,878`), while the product name also includes a generic size token like `1 KG`.

For these rows, the import pipeline should:

- Avoid trying to parse weight from the product name.
- Default the row to **quantity = 1** (a single weighed item / line) for pricing + persistence.
- Populate **weight_kg** from the invoice’s `Cant.` value (the measured weight).

This prevents current failures where:

- Weight is missing because it’s only parsed from the name.
- Quantity semantics are inconsistent across “piece” items vs weighed `KG` items.

## Why This Approach

The invoice provides a reliable signal (`Mod amb = KG`) that disambiguates “quantity” meaning:

- For `KG` rows, `Cant.` is weight, not units.
- For non-`KG` rows, `Cant.` remains units (1, 2, 6, 24, etc.).

Using this explicit column is more robust than heuristics on product names (which may include `1 KG` even when the measured weight differs).

## Approaches Considered

### Approach A (Recommended): Extract `uom` and apply a “KG-mode” mapping

Extend extraction to capture the UOM value from `Mod amb` per row. Then apply a deterministic mapping:

- If `uom == KG`:
  - `weight_kg_candidate = cant_value`
  - default import/preview `quantity = 1`
- Else:
  - keep existing behavior (weight parsed from name when possible)

**Pros:**
- Correct by construction when `Mod amb` is trustworthy.
- Minimal behavioral change for non-`KG` rows.
- Avoids brittle name parsing for these rows.

**Cons:**
- Requires extraction schema/prompt to include `uom` (new field).

**Best when:** The invoice has a consistent `Mod amb` column (confirmed).

### Approach B: Heuristic detection without `uom`

Infer “KG-mode” via patterns like `quantity` being a small decimal and the name containing `KG`.

**Pros:**
- No schema/prompt change.

**Cons:**
- More error-prone (false positives/negatives).
- Harder to explain and debug.

### Approach C: Frontend-only post-processing

Keep backend extraction unchanged; have the frontend detect `KG` rows and rewrite the preview row fields.

**Pros:**
- Fastest iteration.

**Cons:**
- Business semantics live in UI, not domain logic.
- Harder to keep API and future clients consistent.

## Key Decisions

- **KG-mode trigger:** Use the invoice’s packaging/UOM column (`Mod amb`). It is present and trustworthy for these invoices.
- **KG-mode semantics (pricing/import):**
  - `quantity = 1`
  - `weight_kg = Cant.` (measured weight)
- **Name parsing:** Do not rely on parsing `1 KG` from the product name for these rows.
- **Stock movements:** For KG-mode rows, stock-in uses `quantity = 1` (not kilograms). Weight is informational for this flow.

## Acceptance Criteria

- For a row like `... 1 KG 0,878 149,92 ... 150,04` with `Mod amb = KG`:
  - the system proposes `weight_kg = 0.878` from `Cant.`
  - the system defaults `quantity = 1` for import/preview
  - pricing preview/import no longer fails due to missing weight
- For non-`KG` rows:
  - behavior is unchanged (quantity from `Cant.`, weight candidate from name when parseable).

## Open Questions

- Product pricing semantics: should we store a per-kg price for `KG` products, or keep per-line pricing? (Current decision: unchanged; only stock movements must reflect `quantity = 1`.)
- Persistence semantics: should we store a per-kg price for `KG` products, or keep per-line pricing? (Depends on how the product catalog is used downstream.)

## Next Steps

→ Proceed to `/workflows:plan` to translate the KG-mode decision into concrete API/extraction changes and tests.
