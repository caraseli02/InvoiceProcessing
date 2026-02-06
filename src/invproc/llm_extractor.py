"""LLM integration for invoice data extraction."""

import logging
from typing import Optional

from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError

from .config import InvoiceConfig
from .models import InvoiceData, Product

logger = logging.getLogger(__name__)


class LLMExtractor:
    """Extract structured invoice data using OpenAI LLM."""

    def __init__(self, config: InvoiceConfig) -> None:
        self.config = config
        self.mock = config.mock
        self.client: Optional[OpenAI] = None
        if not self.mock:
            if config.openai_api_key:
                self.client = OpenAI(api_key=config.openai_api_key)

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
                ),
                Product(
                    raw_code="4840167002500",
                    name="CIOCOLATA ALBA 70% 200G",
                    quantity=4.0,
                    unit_price=41.58,
                    total_price=166.32,
                    confidence_score=0.95,
                ),
            ],
        )

    def _get_system_prompt(self) -> str:
        """
        Get system prompt with column identification rules.

        Emphasizes column headers, math validation, and hallucination prevention.
        """
        return """You are a precise invoice data extraction assistant specialized in processing METRO Cash & Carry invoices.

INPUT FORMAT:
You will receive a text representation of an invoice where the table layout is preserved through spatial alignment (columns are visually aligned using spaces).

EXTRACTION RULES:
1. Extract these fields:
   - Supplier name (e.g., "METRO CASH & CARRY MOLDOVA")
   - Invoice number (e.g., "94")
   - Date (format: DD-MM-YYYY)
   - Total amount (final "Total de plata" value)
   - Currency (MDL, EUR, USD, etc.)
   - List of products with: code, name, quantity, unit_price, total_price

2. CRITICAL - Column Identification:
   - Look for column headers: "Cod articol", "Denumire articol", "Cant.", "Pret unitar", "Valoare incl.TVA"
   - "Cant." = Quantity (usually integers: 1, 2, 5, 10, 24)
   - "Pret unitar" = Unit Price (usually decimals with 2 places)
   - "Pret colet" = Package Price (may equal unit price)
   - "Valoare incl.TVA" = Total Price (rightmost column)
   - Use VERTICAL ALIGNMENT under headers to identify which number belongs to which column

3. MATH VALIDATION REQUIRED:
   - For each product: quantity × unit_price ≈ total_price (allow ±5% for rounding/discounts)
   - If math doesn't match, set confidence_score = 0.3 and flag it

4. HALLUCINATION PREVENTION:
   - Product codes: If you don't see a numeric code in leftmost column, return null for raw_code
   - DO NOT generate/invent barcodes or EAN codes
   - DO NOT infer product codes from product names
   - If a product name is unclear, use the text as-is (don't "clean it up")

5. MULTI-PAGE HANDLING:
   - You may receive multiple pages concatenated
   - Look for "Total pagina" / "Total ultima pagina" markers
   - Extract ALL products from ALL pages
   - Use the final "Total de plata" value (last page)

6. DISCOUNT LINES:
   - Lines with only numeric codes (e.g., "250075360  2,49-  20%  0,50-  2,99-") are discount details
   - Skip these - don't treat as products

OUTPUT FORMAT:
Return a JSON object with this exact structure:
{
  "supplier": "string or null",
  "invoice_number": "string or null",
  "date": "DD-MM-YYYY or null",
  "total_amount": float,
  "currency": "string (e.g., MDL, EUR)",
  "products": [
    {
      "raw_code": "string or null",
      "name": "string",
      "quantity": float,
      "unit_price": float,
      "total_price": float,
      "confidence_score": float (0.0-1.0)
    }
  ]
}
"""
