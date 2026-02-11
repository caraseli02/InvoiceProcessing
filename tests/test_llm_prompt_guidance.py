"""Regression tests for extraction prompt guidance."""

from invproc.config import InvoiceConfig
from invproc.llm_extractor import LLMExtractor


def test_prompt_is_vat_aware_for_total_column() -> None:
    extractor = LLMExtractor(InvoiceConfig(mock=True))
    prompt = extractor._get_system_prompt()

    assert "total_price` MUST come from" in prompt
    assert "Valoare incl.TVA" in prompt
    assert "Never alter quantity or total_price just to make math match." in prompt
    assert "Do not map quantity from \"Unit\" or \"Mod\"." in prompt
