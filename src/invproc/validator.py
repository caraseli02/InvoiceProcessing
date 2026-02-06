"""Invoice validation and confidence scoring."""

import logging
from typing import Tuple, TYPE_CHECKING

from .models import Product, InvoiceData

if TYPE_CHECKING:
    from .config import InvoiceConfig

logger = logging.getLogger(__name__)


class InvoiceValidator:
    """Validate and score invoice data."""

    def __init__(self, config: "InvoiceConfig"):
        """Initialize validator with configuration."""
        from .config import InvoiceConfig

        self.config: InvoiceConfig = config
        self.allowed_currencies = config.get_allowed_currencies()

    def validate_invoice(self, data: InvoiceData) -> InvoiceData:
        """
        Post-process validation and confidence scoring.

        Args:
            data: InvoiceData from LLM extraction

        Returns:
            InvoiceData with validated confidence scores
        """
        # Validate currency
        v_upper = data.currency.upper()
        if v_upper not in self.allowed_currencies:
            raise ValueError(
                f"Invalid currency: {data.currency}. "
                f"Valid: {', '.join(sorted(self.allowed_currencies))}"
            )
        data.currency = v_upper

        for product in data.products:
            confidence = self._score_product(product)
            product.confidence_score = confidence

        avg_confidence = self._calculate_overall_confidence(data)
        logger.info(f"Overall extraction confidence: {avg_confidence:.2f}")

        return data

    def _score_product(self, product: Product) -> float:
        """
        Calculate confidence score for a product line.

        Multi-factor scoring:
        1. Math validation (primary factor)
        2. Field completeness (name, code)
        3. Reasonable values (not too large/small)

        Returns:
            Confidence score 0.0-1.0
        """
        score = 1.0

        is_valid, discrepancy = self._validate_product_math(product)
        if not is_valid:
            score *= 1.0 - discrepancy / 20.0

        if not product.name or len(product.name) < 3:
            score *= 0.7

        if not product.raw_code:
            score *= 0.95

        if product.quantity > 1000 or product.quantity < 0.01:
            score *= 0.8

        if product.unit_price > 100000 or product.unit_price < 0.01:
            score *= 0.8

        return max(0.0, min(1.0, score))

    def _validate_product_math(self, product: Product) -> Tuple[bool, float]:
        """
        Validate product line math.

        Args:
            product: Product to validate

        Returns:
            (is_valid, discrepancy_percentage)
        """
        calculated_total = product.quantity * product.unit_price

        if calculated_total == 0:
            return False, 100.0

        discrepancy = abs(calculated_total - product.total_price)
        discrepancy_pct = (discrepancy / calculated_total) * 100

        is_valid = discrepancy_pct <= 5.0
        return is_valid, discrepancy_pct

    def _calculate_overall_confidence(self, data: InvoiceData) -> float:
        """
        Calculate overall invoice confidence.

        Args:
            data: InvoiceData with validated products

        Returns:
            Overall confidence score 0.0-1.0
        """
        if not data.products:
            return 1.0

        avg_product_conf = sum(p.confidence_score for p in data.products) / len(
            data.products
        )

        completeness_factor = 1.0
        if not data.supplier:
            completeness_factor *= 0.95
        if not data.invoice_number:
            completeness_factor *= 0.95
        if not data.date:
            completeness_factor *= 0.90

        return avg_product_conf * completeness_factor
