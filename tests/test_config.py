"""Tests for InvoiceConfig."""

import pytest
from invproc.config import InvoiceConfig, get_config, reload_config


def test_get_allowed_currencies():
    """Test parsing allowed currencies from comma-separated string."""
    config = InvoiceConfig(allowed_currencies="EUR,USD,GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}


def test_get_allowed_currencies_case_insensitive():
    """Test that currency codes are case-insensitive and normalized to uppercase."""
    config = InvoiceConfig(allowed_currencies="eur,usd,gBp")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}


def test_get_allowed_currencies_with_spaces():
    """Test that whitespace around currencies is trimmed."""
    config = InvoiceConfig(allowed_currencies="EUR , USD , GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}


def test_get_allowed_currencies_empty_items():
    """Test that empty items are filtered out."""
    config = InvoiceConfig(allowed_currencies="EUR,,USD,,GBP")
    assert config.get_allowed_currencies() == {"EUR", "USD", "GBP"}


def test_supabase_settings_default(monkeypatch):
    """Test Supabase settings default to None."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    config = InvoiceConfig(_env_file=None)
    assert config.supabase_url is None
    assert config.supabase_service_role_key is None


def test_supabase_settings_from_env(monkeypatch):
    """Test Supabase auth settings can be loaded from environment."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    config = InvoiceConfig(_env_file=None)
    assert config.supabase_url == "https://example.supabase.co"
    assert config.supabase_service_role_key == "service-role"


def test_config_singleton(monkeypatch):
    """Test that get_config returns singleton instance."""
    monkeypatch.setenv("MOCK", "true")
    try:
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2
    finally:
        monkeypatch.delenv("MOCK", raising=False)
        reload_config()


def test_reload_config(monkeypatch):
    """Test that reload_config creates new config instance."""
    config1 = get_config()
    monkeypatch.setenv("ALLOWED_CURRENCIES", "JPY,CNY")
    config2 = reload_config()
    assert config1 is not config2
    assert "JPY" in config2.get_allowed_currencies()
    assert "CNY" in config2.get_allowed_currencies()
    monkeypatch.delenv("ALLOWED_CURRENCIES", raising=False)

def test_validate_config_empty_currencies():
    """Test that empty ALLOWED_CURRENCIES raises validation error."""
    with pytest.raises(ValueError, match="ALLOWED_CURRENCIES cannot be empty"):
        config = InvoiceConfig(allowed_currencies="")
        config.validate_config()

def test_validate_config_invalid_format():
    """Test that invalid currency format raises validation error."""
    with pytest.raises(ValueError, match="Invalid currency code format"):
        config = InvoiceConfig(allowed_currencies="US,EURRO")
        config.validate_config()

def test_validate_config_invalid_iso_codes():
    """Test that invalid ISO 4217 codes raise validation error."""
    with pytest.raises(ValueError, match="Invalid ISO 4217 codes"):
        config = InvoiceConfig(allowed_currencies="XXX,YYY,ZZZ")
        config.validate_config()

def test_validate_config_missing_api_key():
    """Test that missing API key when not in mock mode raises validation error."""
    with pytest.raises(ValueError, match="OPENAI_API_KEY required"):
        config = InvoiceConfig(mock=False, openai_api_key=None)
        config.validate_config()

def test_validate_config_suspicious_ocr_config():
    """Test that suspicious OCR config characters raise validation error."""
    with pytest.raises(ValueError, match="OCR_CONFIG contains suspicious characters"):
        config = InvoiceConfig(ocr_config="--oem 1; rm -rf /")
        config.validate_config()

def test_validate_config_valid():
    """Test that valid configuration passes validation."""
    config = InvoiceConfig(
        allowed_currencies="EUR,USD",
        mock=True,
        temperature=0.5,
        scale_factor=0.2
    )
    config.validate_config()  # Should not raise


def test_extract_cache_defaults(monkeypatch):
    """Test extraction cache defaults."""
    monkeypatch.delenv("EXTRACT_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("EXTRACT_CACHE_TTL_SEC", raising=False)
    monkeypatch.delenv("EXTRACT_CACHE_MAX_ENTRIES", raising=False)
    config = InvoiceConfig(mock=True, _env_file=None)
    assert config.extract_cache_enabled is False
    assert config.extract_cache_ttl_sec == 86400
    assert config.extract_cache_max_entries == 256


def test_extract_cache_ttl_validation():
    """Test extraction cache TTL validation."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        InvoiceConfig(mock=True, extract_cache_ttl_sec=0)


def test_extract_cache_max_entries_validation():
    """Test extraction cache max entries validation."""
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        InvoiceConfig(mock=True, extract_cache_max_entries=0)
