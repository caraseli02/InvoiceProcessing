"""LLM integration for invoice data extraction."""

import logging
from typing import Any, Optional

from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError

from .config import InvoiceConfig
from .models import InvoiceData, Product

logger = logging.getLogger(__name__)


class LLMOutputIntegrityError(ValueError):
    """Raised when LLM output is structurally incomplete for safe extraction."""


class LLMExtractor:
    """Extract structured invoice data using OpenAI LLM."""

    def __init__(self, config: InvoiceConfig) -> None:
        self.config = config
        self.mock = config.mock
        self.client: Optional[OpenAI] = None
        if not self.mock:
            if config.openai_api_key:
                self.client = OpenAI(
                    api_key=config.openai_api_key, timeout=config.openai_timeout_sec
                )

    def parse_with_llm(self, text_grid: str) -> InvoiceData:
        """
        Send text grid to GPT-4o-mini for parsing.

        Uses client.chat.completions.parse() with Pydantic schema.
        Temperature=0 for consistency.

        Args:
            text_grid: Text grid representation of invoice

        Returns:
            Structured InvoiceData object
        """
        if self.mock:
            logger.info("Using mock data (no API call)")
            return self._get_mock_data()

        if not self.client:
            raise ValueError("OpenAI client not initialized (missing API key)")

        try:
            system_prompt = self._get_system_prompt()
            user_prompt = f"""Here is invoice text with preserved spatial layout:

{text_grid}

Extract all invoice data following the rules in the system prompt.
Pay special attention to the column headers to correctly identify quantity vs price columns.
"""

            completion = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            import json

            content = completion.choices[0].message.content
            if content is None:
                raise ValueError("API returned no content")
            invoice_data_dict = json.loads(content)
            invoice_data_dict = self._normalize_invoice_payload(invoice_data_dict)
            return InvoiceData(**invoice_data_dict)

        except APIConnectionError as e:
            logger.error(f"Connection failed: {e.__cause__}")
            raise
        except RateLimitError as e:
            logger.warning(f"Rate limited: {e}")
            raise
        except APIStatusError as e:
            logger.error(f"API error {e.status_code}: {e.response}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            raise

    def _normalize_invoice_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize LLM JSON payload before strict Pydantic validation."""
        if not isinstance(payload, dict):
            raise ValueError("LLM payload must be a JSON object")

        products_raw = payload.get("products", [])
        cleaned_products: list[dict[str, Any]] = []
        dropped_products = 0

        if not isinstance(products_raw, list):
            products_raw = []

        for product in products_raw:
            if not isinstance(product, dict):
                dropped_products += 1
                continue

            quantity = self._to_float(product.get("quantity"))
            unit_price = self._to_float(product.get("unit_price"))
            total_price = self._to_float(product.get("total_price"))
            name_raw = product.get("name")
            name = name_raw.strip() if isinstance(name_raw, str) else ""

            # Skip malformed product rows instead of failing entire invoice.
            # Quantity and unit price must be strictly positive for valid products.
            if (
                not name
                or quantity is None
                or unit_price is None
                or total_price is None
                or quantity <= 0
                or unit_price <= 0
                or total_price < 0
            ):
                dropped_products += 1
                continue

            confidence_raw = self._to_float(product.get("confidence_score"))
            confidence = confidence_raw if confidence_raw is not None else 0.5
            confidence = max(0.0, min(1.0, confidence))

            raw_code = product.get("raw_code")
            normalized_code = None
            if raw_code is not None:
                code_text = str(raw_code).strip()
                normalized_code = code_text or None

            cleaned_products.append(
                {
                    "raw_code": normalized_code,
                    "name": name,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "confidence_score": confidence,
                }
            )

        if dropped_products:
            logger.warning(
                "Dropped %s malformed product rows from LLM output", dropped_products
            )
            # Keep extraction usable if at least one row is valid.
            if not cleaned_products:
                raise LLMOutputIntegrityError(
                    f"LLM returned {dropped_products} malformed product rows"
                )

        normalized = dict(payload)
        normalized["products"] = cleaned_products

        total_amount = self._to_float(normalized.get("total_amount"))
        if total_amount is not None:
            normalized["total_amount"] = total_amount

        currency = normalized.get("currency")
        if currency is None:
            normalized["currency"] = ""
        else:
            normalized["currency"] = str(currency).strip()

        for key in ("supplier", "invoice_number", "date"):
            value = normalized.get(key)
            if value is not None and not isinstance(value, str):
                normalized[key] = str(value)

        return normalized

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """Convert model output value to float when possible."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(" ", "").replace(",", ".")
            if not cleaned:
                return None
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    def _get_mock_data(self) -> InvoiceData:
        """Generate mock invoice data for testing without API."""
        return InvoiceData(
            supplier="MOCK SUPPLIER",
            invoice_number="MOCK-001",
            date="02-02-2026",
            total_amount=8142.84,
            currency="MDL",
            products=[
                Product(
                    raw_code="4840167001399",
                    name="200G UNT CIOCOLATA JLC",
                    quantity=5.0,
                    unit_price=43.43,
                    total_price=217.15,
                    confidence_score=0.95,
                    row_id=None,
                    weight_kg_candidate=None,
                    size_token=None,
                    parse_confidence=None,
                ),
                Product(
                    raw_code="4840167002500",
                    name="CIOCOLATA ALBA 70% 200G",
                    quantity=4.0,
                    unit_price=41.58,
                    total_price=166.32,
                    confidence_score=0.95,
                    row_id=None,
                    weight_kg_candidate=None,
                    size_token=None,
                    parse_confidence=None,
                ),
            ],
        )

    def _get_system_prompt(self) -> str:
        """
        Get system prompt with column identification rules.

        Emphasizes column headers, math validation, and hallucination prevention.
        """
        headers = self.config.column_headers

        return f"""You are a precise invoice data extraction assistant specialized in processing invoices.

INPUT FORMAT:
You will receive a text representation of an invoice where table layout is preserved through spatial alignment (columns are visually aligned using spaces).

EXTRACTION RULES:
1. Extract these fields:
   - Supplier name (e.g., "METRO CASH & CARRY MOLDOVA")
   - Invoice number (e.g., "94")
   - Date (format: DD-MM-YYYY)
   - Total amount (final total value)
   - Currency (MDL, EUR, USD, etc.)
   - List of products with: code, name, quantity, unit_price, total_price

2. CRITICAL - Column Identification:
   - Look for column headers with these names:
     * Quantity column: "{headers.quantity}"
     * Unit price column: "{headers.unit_price}"
     * Total price column: "{headers.total_price}"
   - "{headers.quantity}" = Quantity (usually integers: 1, 2, 5, 10, 24)
   - "{headers.unit_price}" = Unit Price (usually decimals with 2 places)
   - "{headers.total_price}" = Total Price (rightmost column)
   - Use VERTICAL ALIGNMENT under headers to identify which number belongs to which column

3. COLUMN SEMANTICS (VAT-aware):
   - `quantity` MUST come from "{headers.quantity}" (e.g., "Cant.")
   - `unit_price` MUST come from "{headers.unit_price}" (e.g., "Pret unitar")
   - `total_price` MUST come from "{headers.total_price}" (e.g., "Valoare incl.TVA")
   - IMPORTANT: In many invoices, `quantity × unit_price` matches "Valoare fara TVA", NOT "Valoare incl.TVA"
   - Never alter quantity or total_price just to make math match.

4. HALLUCINATION PREVENTION:
   - Product codes: If you don't see a numeric code in leftmost column, return null for raw_code
   - DO NOT generate/invent barcodes or EAN codes
   - DO NOT infer product codes from product names
   - If a product name is unclear, use text as-is (don't "clean it up")

5. MULTI-PAGE HANDLING:
   - You may receive multiple pages concatenated
   - Look for page total markers
   - Extract ALL products from ALL pages
   - Use final total value (last page)

6. MULTIPLE INTEGER COLUMNS:
   - Some invoices contain nearby integer columns (for example "Unit", "Mod", and "Cant.")
   - Only map quantity from "{headers.quantity}".
   - Do not map quantity from "Unit" or "Mod".

7. DISCOUNT LINES:
   - Lines with only numeric codes (e.g., "250075360  2,49-  20%  0,50-  2,99-") are discount details
   - Skip these - don't treat as products

OUTPUT FORMAT:
Return a JSON object with this exact structure:
{{
  "supplier": "string or null",
  "invoice_number": "string or null",
  "date": "DD-MM-YYYY or null",
  "total_amount": float,
  "currency": "string (e.g., MDL, EUR)",
  "products": [
    {{
      "raw_code": "string or null",
      "name": "string",
      "quantity": float,
      "unit_price": float,
      "total_price": float,
      "confidence_score": float (0.0-1.0)
    }}
  ]
}}
"""
