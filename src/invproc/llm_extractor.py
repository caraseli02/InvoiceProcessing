"""LLM integration for invoice data extraction."""

from json import JSONDecodeError
import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError

from .config import InvoiceConfig
from .models import InvoiceData, Product

logger = logging.getLogger(__name__)

_CATEGORY_ENUM: tuple[str, ...] = (
    "General",
    "Produce",
    "Dairy",
    "Meat",
    "Pantry",
    "Snacks",
    "Beverages",
    "Household",
    "Conserve",
    "Cereale",
)
_CATEGORY_SET = set(_CATEGORY_ENUM)
_PAGE_MARKER_PATTERN = re.compile(
    r"(?=^--- Page \d+ \((?:OCR|Native)\) ---$)", re.MULTILINE
)
_MAX_CHUNK_CHARS = 6000


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
                    api_key=config.openai_api_key.get_secret_value(),
                    timeout=config.openai_timeout_sec,
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
            chunks = self._split_text_grid_into_chunks(text_grid)
            if len(chunks) > 1:
                logger.info("Splitting invoice extraction into %s chunks", len(chunks))

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

    def _request_invoice_chunk(
        self,
        *,
        chunk_text: str,
        chunk_index: int,
        chunk_count: int,
    ) -> dict[str, Any]:
        """Request one chunk of invoice text from the model."""
        if not self.client:
            raise ValueError("OpenAI client not initialized (missing API key)")

        system_prompt = self._get_system_prompt()
        user_prompt = self._get_user_prompt(
            text_grid=chunk_text,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )

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

        content = completion.choices[0].message.content
        if content is None:
            raise ValueError("API returned no content")
        try:
            invoice_data_dict = json.loads(content)
        except JSONDecodeError as e:
            finish_reason = completion.choices[0].finish_reason
            logger.error(
                "LLM returned invalid JSON: chunk=%s/%s finish_reason=%s content_length=%s",
                chunk_index,
                chunk_count,
                finish_reason,
                len(content),
            )
            if finish_reason == "length":
                raise LLMOutputIntegrityError(
                    "Model output was truncated before valid JSON was completed. Please retry."
                ) from e
            raise LLMOutputIntegrityError(
                "Model returned invalid JSON for this invoice. Please retry."
            ) from e

        normalized_payload = self._normalize_invoice_payload(invoice_data_dict)
        if not normalized_payload.get("products"):
            logger.warning(
                "Chunk %s/%s produced no valid products", chunk_index, chunk_count
            )
        return normalized_payload

    def _split_text_grid_into_chunks(self, text_grid: str) -> list[str]:
        """Split large invoice grids into smaller chunks for safer JSON output."""
        if len(text_grid) <= _MAX_CHUNK_CHARS:
            return [text_grid]

        page_sections = self._split_page_sections(text_grid)
        chunks: list[str] = []
        current_chunk_sections: list[str] = []
        current_length = 0

        for section in page_sections:
            for bounded_section in self._split_section_by_lines(section):
                projected_length = current_length + len(bounded_section) + 1
                if current_chunk_sections and projected_length > _MAX_CHUNK_CHARS:
                    chunks.append("\n".join(current_chunk_sections))
                    current_chunk_sections = [bounded_section]
                    current_length = len(bounded_section)
                else:
                    current_chunk_sections.append(bounded_section)
                    current_length = projected_length

        if current_chunk_sections:
            chunks.append("\n".join(current_chunk_sections))

        return chunks or [text_grid]

    def _split_page_sections(self, text_grid: str) -> list[str]:
        """Split a multi-page text grid on page markers when present."""
        matches = [match.start() for match in _PAGE_MARKER_PATTERN.finditer(text_grid)]
        if not matches:
            return [text_grid]

        sections: list[str] = []
        for index, start in enumerate(matches):
            end = matches[index + 1] if index + 1 < len(matches) else len(text_grid)
            section = text_grid[start:end].strip()
            if section:
                sections.append(section)
        return sections or [text_grid]

    def _split_section_by_lines(self, section: str) -> list[str]:
        """Split an oversized section on line boundaries while keeping page header."""
        if len(section) <= _MAX_CHUNK_CHARS:
            return [section]

        lines = section.splitlines()
        if not lines:
            return [section]

        header = lines[0]
        body_lines = lines[1:] if len(lines) > 1 else []
        chunks: list[str] = []
        current_lines: list[str] = [header]
        current_length = len(header)

        for line in body_lines:
            line_length = len(line) + 1
            if len(current_lines) > 1 and current_length + line_length > _MAX_CHUNK_CHARS:
                chunks.append("\n".join(current_lines))
                current_lines = [header, line]
                current_length = len(header) + 1 + len(line)
            else:
                current_lines.append(line)
                current_length += line_length

        if current_lines:
            chunks.append("\n".join(current_lines))

        return chunks or [section]

    def _merge_chunk_payloads(
        self, payloads: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Merge chunk-level payloads into one final invoice payload."""
        merged: dict[str, Any] = {
            "supplier": None,
            "invoice_number": None,
            "date": None,
            "total_amount": None,
            "currency": "",
            "products": [],
        }

        for payload in payloads:
            for key in ("supplier", "invoice_number", "date"):
                current_value = merged.get(key)
                if current_value is None and key in payload:
                    merged[key] = payload.get(key)
                    continue

                next_value = payload.get(key)
                if not current_value and next_value:
                    merged[key] = next_value

            total_amount = self._to_float(payload.get("total_amount"))
            if total_amount is not None and total_amount > 0:
                merged["total_amount"] = total_amount

            currency = payload.get("currency")
            if isinstance(currency, str) and currency.strip():
                merged["currency"] = currency.strip()

            products = payload.get("products", [])
            if isinstance(products, list):
                merged["products"].extend(products)

        if self._to_float(merged.get("total_amount")) is None:
            raise LLMOutputIntegrityError(
                "Model did not return a valid invoice total across invoice chunks."
            )

        return merged

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

            category_suggestion = product.get("category_suggestion")
            normalized_category = None
            if category_suggestion is not None:
                category_text = str(category_suggestion).strip()
                normalized_category = (
                    category_text if category_text in _CATEGORY_SET else None
                )

            cleaned_products.append(
                {
                    "raw_code": normalized_code,
                    "name": name,
                    "uom": _normalize_uom(product.get("uom")),
                    "category_suggestion": normalized_category,
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
                    uom=None,
                    category_suggestion=None,
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
                    uom=None,
                    category_suggestion=None,
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
   - Total amount (final total value when visible; null if the provided chunk does not show it)
   - Currency (MDL, EUR, USD, etc.; empty string if the provided chunk does not show it)
   - List of products with: code, name, optional uom, optional category_suggestion, quantity, unit_price, total_price

1b. CATEGORY SUGGESTION (optional, enum-only):
   - For each product, you MAY include `category_suggestion` if you are confident.
   - `category_suggestion` MUST be exactly one of:
     General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale
   - If unsure, set `category_suggestion` to null.
   - Do NOT guess "General" as a default.

2. CRITICAL - Column Identification:
   - Look for column headers with these names:
     * Quantity column: "{headers.quantity}"
     * Unit price column: "{headers.unit_price}"
     * Total price column: "{headers.total_price}"
     * UOM/packaging column: usually "Mod amb" (or similar)
   - "{headers.quantity}" = Quantity (usually integers: 1, 2, 5, 10, 24)
   - "{headers.unit_price}" = Unit Price (usually decimals with 2 places)
   - "{headers.total_price}" = Total Price (rightmost column)
   - Use VERTICAL ALIGNMENT under headers to identify which number belongs to which column

3. COLUMN SEMANTICS (VAT-aware):
   - `quantity` MUST come from "{headers.quantity}" (e.g., "Cant.")
   - `unit_price` MUST come from "{headers.unit_price}" (e.g., "Pret unitar")
   - `total_price` MUST come from "{headers.total_price}" (e.g., "Valoare incl.TVA")
   - `uom` MUST come from the UOM/packaging column (e.g., "Mod amb") when visible
   - NOTE: When `uom` is "KG", `quantity` from "{headers.quantity}" may be a decimal weight (e.g., 0,878). Keep it as-is.
   - IMPORTANT: In many invoices, `quantity × unit_price` matches "Valoare fara TVA", NOT "Valoare incl.TVA"
   - Never alter quantity or total_price just to make math match.

4. HALLUCINATION PREVENTION:
   - Product codes: If you don't see a numeric code in leftmost column, return null for raw_code
   - DO NOT generate/invent barcodes or EAN codes
   - DO NOT infer product codes from product names
   - If a product name is unclear, use text as-is (don't "clean it up")

5. MULTI-PAGE / CHUNK HANDLING:
   - You may receive one chunk from a larger invoice rather than the full document
   - Extract ONLY products that are visible in the provided chunk
   - Do NOT duplicate or invent products from pages/chunks you cannot see
   - If supplier, invoice_number, date, currency, or final total are not visible in this chunk, return null for missing strings and null/empty string for missing totals/currency
   - Use the final total value only when it is actually visible in the provided chunk

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
  "total_amount": "float or null when not visible in this chunk",
  "currency": "string (e.g., MDL, EUR) or empty string when not visible",
  "products": [
    {{
      "raw_code": "string or null",
      "name": "string",
      "uom": "string or null (e.g., KG, BU, CU)",
      "category_suggestion": "one of: General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale (or null if unsure)",
      "quantity": float,
      "unit_price": float,
      "total_price": float,
      "confidence_score": float (0.0-1.0)
    }}
  ]
}}
"""

    def _get_user_prompt(
        self,
        *,
        text_grid: str,
        chunk_index: int,
        chunk_count: int,
    ) -> str:
        """Build the chunk-aware user prompt."""
        return f"""Here is invoice text with preserved spatial layout:

{text_grid}

This is chunk {chunk_index} of {chunk_count} for a single invoice.
Extract only the data visible in this chunk, following the system prompt exactly.
Pay special attention to the column headers to correctly identify quantity vs price columns.
If supplier, invoice number, date, currency, or final total are not visible here, leave them null (or empty string for currency) instead of guessing.
"""


def _normalize_uom(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None
