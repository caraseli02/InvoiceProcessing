"""Invoice preview/import orchestration service."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from invproc.config import InvoiceConfig
from invproc.exceptions import ContractError
from invproc.models import (
    ComputedPricing,
    ImportRowResult,
    ImportSummary,
    InvoiceImportRequest,
    InvoiceImportResponse,
    InvoicePreviewPricingRequest,
    InvoicePreviewPricingResponse,
    MatchCandidate,
    PreviewPricingRowResult,
    PreviewPricingSummary,
    PricingConstantsResponse,
)
from invproc.pricing import compute_pricing
from invproc.repositories.base import InvoiceImportRepository, ProductRecord, UpsertProductInput


_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")
_LIQUID_HINT_PATTERN = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?\s*(l|ml)\b", re.IGNORECASE)


@dataclass(frozen=True)
class MatchResolution:
    """Result of product match lookup."""

    product: Optional[ProductRecord]
    strategy: Optional[str]
    error_code: Optional[str]


def normalize_name(value: str) -> str:
    """Normalize product name for fallback matching."""
    lowered = value.lower().strip()
    normalized = _NORMALIZE_PATTERN.sub(" ", lowered)
    return " ".join(normalized.split())


class InvoiceImportService:
    """Coordinates preview pricing and import write-path."""

    def __init__(
        self,
        config: InvoiceConfig,
        repository: Optional[InvoiceImportRepository] = None,
    ) -> None:
        self.config = config
        self.repository = repository

    @staticmethod
    def _map_pricing_error(exc: ValueError) -> str:
        text = str(exc)
        if "quantity" in text:
            return "INVALID_QUANTITY"
        if "line_total_lei" in text:
            return "INVALID_LINE_TOTAL"
        if "weight_kg" in text:
            return "INVALID_WEIGHT"
        if "fx_lei_to_eur" in text:
            return "INVALID_FX_RATE"
        if "transport_rate_per_kg" in text:
            return "INVALID_TRANSPORT_RATE"
        return "COMPUTATION_ERROR"

    @staticmethod
    def _row_warnings(name: str) -> list[str]:
        warnings: list[str] = []
        if _LIQUID_HINT_PATTERN.search(name):
            warnings.append("LIQUID_DENSITY_ASSUMPTION")
        return warnings

    def preview_pricing(
        self, payload: InvoicePreviewPricingRequest
    ) -> InvoicePreviewPricingResponse:
        """Compute pricing preview for all rows."""
        rows: list[PreviewPricingRowResult] = []
        ok_count = 0
        needs_input_count = 0
        error_count = 0

        for row in payload.rows:
            if row.weight_kg is None:
                rows.append(
                    PreviewPricingRowResult(
                        row_id=row.row_id,
                        status="needs_input",
                        messages=["MISSING_WEIGHT"],
                        warnings=self._row_warnings(row.name),
                        computed=None,
                        match_candidate=None,
                    )
                )
                needs_input_count += 1
                continue

            try:
                pricing = compute_pricing(
                    line_total_lei=row.line_total_lei,
                    quantity=row.quantity,
                    weight_kg=row.weight_kg,
                    fx_lei_to_eur=self.config.fx_lei_to_eur,
                    transport_rate_per_kg=self.config.transport_rate_per_kg,
                )
            except ValueError as exc:
                rows.append(
                    PreviewPricingRowResult(
                        row_id=row.row_id,
                        status="error",
                        messages=[self._map_pricing_error(exc)],
                        warnings=self._row_warnings(row.name),
                        computed=None,
                        match_candidate=None,
                    )
                )
                error_count += 1
                continue

            match = self._find_match(row.barcode, row.name)
            if match.error_code:
                rows.append(
                    PreviewPricingRowResult(
                        row_id=row.row_id,
                        status="error",
                        messages=[match.error_code],
                        warnings=self._row_warnings(row.name),
                        computed=ComputedPricing(**pricing.__dict__),
                        match_candidate=None,
                    )
                )
                error_count += 1
                continue

            match_candidate = (
                MatchCandidate(strategy=match.strategy, product_id=match.product.product_id)
                if match.product and match.strategy
                else None
            )

            rows.append(
                PreviewPricingRowResult(
                    row_id=row.row_id,
                    status="ok",
                    messages=[],
                    warnings=self._row_warnings(row.name),
                    computed=ComputedPricing(**pricing.__dict__),
                    match_candidate=match_candidate,
                )
            )
            ok_count += 1

        return InvoicePreviewPricingResponse(
            pricing_constants=PricingConstantsResponse(
                fx_lei_to_eur=self.config.fx_lei_to_eur,
                transport_rate_per_kg=self.config.transport_rate_per_kg,
            ),
            rows=rows,
            summary=PreviewPricingSummary(
                ok_count=ok_count,
                needs_input_count=needs_input_count,
                error_count=error_count,
            ),
        )

    def import_rows(
        self, payload: InvoiceImportRequest, *, idempotency_key: str
    ) -> InvoiceImportResponse:
        """Execute import write path with idempotency support."""
        if self.repository is None:
            raise ContractError(
                "IMPORT_DISABLED",
                "Import endpoint is disabled in MVP simple mode",
                status_code=501,
            )

        if not idempotency_key.strip():
            raise ContractError(
                "INVALID_PAYLOAD",
                "Idempotency key is required",
                status_code=400,
            )

        request_hash = hashlib.sha256(
            json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        ).hexdigest()

        existing = self.repository.get_idempotent_result(idempotency_key)
        if existing:
            existing_hash, existing_payload = existing
            if existing_hash != request_hash:
                raise ContractError(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency key already used with different payload",
                    status_code=409,
                )
            return InvoiceImportResponse(**existing_payload)

        rows: list[ImportRowResult] = []
        created_count = 0
        updated_count = 0
        stock_in_count = 0
        error_count = 0

        for row in payload.rows:
            if row.weight_kg is None:
                rows.append(
                    ImportRowResult(
                        row_id=row.row_id,
                        status="error",
                        messages=["MISSING_WEIGHT"],
                        warnings=self._row_warnings(row.name),
                    )
                )
                error_count += 1
                continue

            try:
                pricing = compute_pricing(
                    line_total_lei=row.line_total_lei,
                    quantity=row.quantity,
                    weight_kg=row.weight_kg,
                    fx_lei_to_eur=self.config.fx_lei_to_eur,
                    transport_rate_per_kg=self.config.transport_rate_per_kg,
                )
            except ValueError as exc:
                rows.append(
                    ImportRowResult(
                        row_id=row.row_id,
                        status="error",
                        messages=[self._map_pricing_error(exc)],
                        warnings=self._row_warnings(row.name),
                    )
                )
                error_count += 1
                continue

            match = self._find_match(row.barcode, row.name)
            if match.error_code:
                rows.append(
                    ImportRowResult(
                        row_id=row.row_id,
                        status="error",
                        messages=[match.error_code],
                        warnings=self._row_warnings(row.name),
                        computed=ComputedPricing(**pricing.__dict__),
                    )
                )
                error_count += 1
                continue

            upsert_data = UpsertProductInput(
                name=row.name,
                barcode=row.barcode,
                supplier=payload.invoice_meta.supplier,
                price=pricing.base_price_eur,
                price_50=pricing.price_50,
                price_70=pricing.price_70,
                price_100=pricing.price_100,
                markup=70,
            )

            if match.product is None:
                product = self.repository.create_product(upsert_data)
                action = "created"
                match_strategy = "created"
                created_count += 1
            else:
                product = self.repository.update_product(match.product.product_id, upsert_data)
                action = "updated"
                match_strategy = match.strategy or "normalized_name"
                updated_count += 1

            movement_id = self.repository.add_stock_movement_in(
                product_id=product.product_id,
                quantity=row.quantity,
                source="invoice_import",
                invoice_number=payload.invoice_meta.invoice_number,
            )
            stock_in_count += 1

            rows.append(
                ImportRowResult(
                    row_id=row.row_id,
                    status="ok",
                    action=action,
                    match_strategy=match_strategy,
                    product_id=product.product_id,
                    stock_movement_id=movement_id,
                    computed=ComputedPricing(**pricing.__dict__),
                    messages=[],
                    warnings=self._row_warnings(row.name),
                )
            )

        import_status = "completed"
        if error_count == len(rows):
            import_status = "failed"
        elif error_count > 0:
            import_status = "partial_failed"

        import_id = datetime.now(timezone.utc).strftime("imp_%Y%m%d_%H%M%S")
        response = InvoiceImportResponse(
            import_id=import_id,
            import_status=import_status,
            rows=rows,
            summary=ImportSummary(
                created_count=created_count,
                updated_count=updated_count,
                stock_in_count=stock_in_count,
                error_count=error_count,
            ),
        )

        self.repository.save_idempotent_result(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response.model_dump(mode="json"),
        )
        return response

    def _find_match(
        self, barcode: Optional[str], name: str
    ) -> MatchResolution:
        if self.repository is None:
            return MatchResolution(product=None, strategy=None, error_code=None)

        if barcode:
            matched = self.repository.find_product_by_barcode(barcode)
            if matched:
                return MatchResolution(
                    product=matched,
                    strategy="barcode",
                    error_code=None,
                )

        normalized = normalize_name(name)
        candidates = self.repository.find_products_by_normalized_name(normalized)
        if len(candidates) == 1:
            return MatchResolution(
                product=candidates[0],
                strategy="normalized_name",
                error_code=None,
            )
        if len(candidates) > 1:
            return MatchResolution(
                product=None,
                strategy=None,
                error_code="AMBIGUOUS_NAME_MATCH",
            )

        return MatchResolution(product=None, strategy=None, error_code=None)
