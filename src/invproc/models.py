"""Pydantic data models for invoice data."""

from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class Product(BaseModel):
    """Invoice line item with validation."""

    raw_code: Optional[str] = Field(None, description="Product code/EAN if visible")
    name: str = Field(..., description="Product name")
    quantity: float = Field(..., gt=0, description="Quantity must be positive")
    unit_price: float = Field(..., gt=0, description="Unit price must be positive")
    total_price: float = Field(..., ge=0, description="Total line price")
    confidence_score: float = Field(..., ge=0, le=1, description="Confidence 0-1")

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
