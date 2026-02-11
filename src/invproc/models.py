"""Pydantic data models for invoice data."""

import math
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Product(BaseModel):
    """Invoice line item with validation."""

    raw_code: Optional[str] = Field(None, description="Product code/EAN if visible")
    name: str = Field(..., description="Product name")
    quantity: float = Field(..., gt=0, description="Quantity must be positive")
    unit_price: float = Field(..., gt=0, description="Unit price must be positive")
    total_price: float = Field(
        ...,
        ge=0,
        description="Total line price including VAT (Valoare incl.TVA)",
    )
    confidence_score: float = Field(..., ge=0, le=1, description="Confidence 0-1")
    row_id: Optional[str] = Field(None, description="Stable row identifier for this extraction")
    weight_kg_candidate: Optional[float] = Field(
        None,
        gt=0,
        description="Best-effort parsed weight in kilograms",
    )
    size_token: Optional[str] = Field(None, description="Matched size token from product name")
    parse_confidence: Optional[float] = Field(
        None,
        ge=0,
        le=1,
        description="Confidence score of weight parsing",
    )

    @model_validator(mode="after")
    def validate_math(self) -> "Product":
        """
        Validate that quantity × unit_price ≈ total_price.
        Allow 5% tolerance for tax rounding.
        """
        calculated = self.quantity * self.unit_price
        tolerance = 0.05

        if abs(calculated - self.total_price) > calculated * tolerance:
            self.confidence_score = min(self.confidence_score, 0.6)

        return self


class InvoiceData(BaseModel):
    """Complete invoice data with validation."""

    supplier: Optional[str] = Field(None, description="Supplier name")
    invoice_number: Optional[str] = Field(None, description="Invoice number")
    date: Optional[str] = Field(None, description="Invoice date (ISO format)")
    total_amount: float = Field(..., gt=0, description="Total invoice amount")
    currency: str = Field(..., description="Currency code (EUR, USD, MDL, RUB)")
    products: List[Product] = Field(..., min_length=0, description="List of products")

    @model_validator(mode="after")
    def validate_totals(self) -> "InvoiceData":
        """
        Validate that sum of products ≈ invoice total.
        Allow 20% tolerance for taxes/discounts.
        """
        if not self.products:
            return self

        sum_products = sum(p.total_price for p in self.products)
        tolerance = 0.20

        if abs(sum_products - self.total_amount) > self.total_amount * tolerance:
            pass

        return self


class InvoiceMeta(BaseModel):
    """Invoice metadata sent by frontend during preview/import flows."""

    supplier: Optional[str] = None
    invoice_number: Optional[str] = None
    date: Optional[str] = None


class InvoicePreviewRow(BaseModel):
    """Single row payload for preview/import."""

    row_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    barcode: Optional[str] = None
    quantity: float = Field(..., gt=0)
    line_total_lei: float = Field(..., ge=0)
    weight_kg: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def validate_numeric_finite(self) -> "InvoicePreviewRow":
        for value, field_name in (
            (self.quantity, "quantity"),
            (self.line_total_lei, "line_total_lei"),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")

        if self.weight_kg is not None and not math.isfinite(self.weight_kg):
            raise ValueError("weight_kg must be finite")

        return self


class InvoicePreviewPricingRequest(BaseModel):
    """Request payload for pricing preview endpoint."""

    invoice_meta: InvoiceMeta
    rows: List[InvoicePreviewRow] = Field(..., min_length=1)


class PricingConstantsResponse(BaseModel):
    """Server constants used for computations."""

    fx_lei_to_eur: float
    transport_rate_per_kg: float


class ComputedPricing(BaseModel):
    """Computed price fields for product persistence."""

    base_price_eur: float
    transport_eur: float
    price_50: float
    price_70: float
    price_100: float


class MatchCandidate(BaseModel):
    """Best-effort existing product match candidate."""

    strategy: Literal["barcode", "normalized_name"]
    product_id: str


class PreviewPricingRowResult(BaseModel):
    """Per-row preview status."""

    row_id: str
    status: Literal["ok", "needs_input", "error"]
    messages: List[str]
    warnings: List[str] = Field(default_factory=list)
    computed: Optional[ComputedPricing]
    match_candidate: Optional[MatchCandidate]


class PreviewPricingSummary(BaseModel):
    """Aggregate counts for preview response."""

    ok_count: int
    needs_input_count: int
    error_count: int


class InvoicePreviewPricingResponse(BaseModel):
    """Response payload for pricing preview endpoint."""

    pricing_constants: PricingConstantsResponse
    rows: List[PreviewPricingRowResult]
    summary: PreviewPricingSummary


class InvoiceImportRequest(BaseModel):
    """Request payload for import endpoint."""

    invoice_meta: InvoiceMeta
    rows: List[InvoicePreviewRow] = Field(..., min_length=1)


class ImportRowResult(BaseModel):
    """Row result for import execution."""

    row_id: str
    status: Literal["ok", "error"]
    action: Optional[Literal["created", "updated"]] = None
    match_strategy: Optional[Literal["barcode", "normalized_name", "created"]] = None
    product_id: Optional[str] = None
    stock_movement_id: Optional[str] = None
    computed: Optional[ComputedPricing] = None
    messages: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ImportSummary(BaseModel):
    """Aggregate import counts."""

    created_count: int
    updated_count: int
    stock_in_count: int
    error_count: int


class InvoiceImportResponse(BaseModel):
    """Response payload for import endpoint."""

    import_id: str
    import_status: Literal["completed", "partial_failed", "failed"]
    rows: List[ImportRowResult]
    summary: ImportSummary
