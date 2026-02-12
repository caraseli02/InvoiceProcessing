"""Regression tests for extraction prompt guidance."""

from invproc.config import InvoiceConfig
from invproc.llm_extractor import LLMExtractor


def test_prompt_is_vat_aware_for_total_column() -> None:
    extractor = LLMExtractor(InvoiceConfig(mock=True))
    prompt = extractor._get_system_prompt()

    assert "total_price` MUST come from" in prompt
    assert "Valoare incl.TVA" in prompt
    assert "Never alter quantity or total_price just to make math match." in prompt
    assert 'Do not map quantity from "Unit" or "Mod".' in prompt

    # Category suggestion contract: enum-only, null if unsure, never guess General.
    assert "CATEGORY SUGGESTION" in prompt
    assert "category_suggestion" in prompt
    assert (
        "General, Produce, Dairy, Meat, Pantry, Snacks, Beverages, Household, Conserve, Cereale"
        in prompt
    )
    assert "If unsure, set `category_suggestion` to null." in prompt
    assert 'Do NOT guess "General" as a default.' in prompt
