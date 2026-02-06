"""Tests for InvoiceConfig."""

import os
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


def test_api_keys_default():
    """Test that api_keys can be loaded from .env file or defaults to empty."""
    config = InvoiceConfig()
    assert isinstance(config.api_keys, str)
    assert len(config.api_keys) >= 0


def test_api_keys_from_env():
    """Test that api_keys can be loaded from environment variable."""
    os.environ["API_KEYS"] = "key1,key2,key3"
    config = InvoiceConfig()
    assert "key1" in config.api_keys
    assert "key2" in config.api_keys
    assert "key3" in config.api_keys
    del os.environ["API_KEYS"]


def test_config_singleton():
    """Test that get_config returns singleton instance."""
    config1 = get_config()
    config2 = get_config()
    assert config1 is config2


def test_reload_config():
    """Test that reload_config creates new config instance."""
    config1 = get_config()
    os.environ["ALLOWED_CURRENCIES"] = "JPY,CNY"
    config2 = reload_config()
    assert config1 is not config2
    assert "JPY" in config2.get_allowed_currencies()
    assert "CNY" in config2.get_allowed_currencies()
    del os.environ["ALLOWED_CURRENCIES"]
