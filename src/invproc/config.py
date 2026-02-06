"""Configuration management for invoice processing CLI."""

import logging
from pathlib import Path
from typing import Optional
import pycountry
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class InvoiceConfig(BaseSettings):
    """Configuration for invoice processing."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key (can use env var: OPENAI_API_KEY or INVPROC_OPENAI_API_KEY)",
    )

    mock: bool = Field(
        default=False,
        description="Use mock data instead of calling OpenAI API (for testing without API key)",
    )

    model: str = Field(default="gpt-4o-mini", description="Default OpenAI model to use")

    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=128000,
        description="Maximum tokens for API responses",
    )

    scale_factor: float = Field(
        default=0.2,
        ge=0.1,
        le=0.5,
        description="Horizontal compression factor for text grid (0.1-0.5)",
    )

    tolerance: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Vertical grouping tolerance in pixels",
    )

    ocr_dpi: int = Field(
        default=300,
        ge=150,
        le=600,
        description="OCR resolution in DPI",
    )

    ocr_languages: str = Field(
        default="ron+eng+rus",
        description="OCR language codes (e.g., 'ron+eng+rus')",
    )

    ocr_config: str = Field(
        default="--oem 1 --psm 6",
        description="Tesseract OCR configuration",
    )

    allowed_currencies: str = Field(
        default="EUR,USD,MDL,RUB,RON",
        description="Comma-separated list of allowed currency codes",
    )

    @field_validator("allowed_currencies")
    @classmethod
    def validate_allowed_currencies_format(cls, v: str) -> str:
        """Validate allowed currencies are valid ISO 4217 format."""
        if not v:
            raise ValueError("ALLOWED_CURRENCIES cannot be empty")

        currencies = [c.strip().upper() for c in v.split(",") if c.strip()]

        if not currencies:
            raise ValueError("ALLOWED_CURRENCIES cannot be empty")

        for currency in currencies:
            if len(currency) != 3 or not currency.isalpha():
                raise ValueError(
                    f"Invalid currency code format: '{currency}'. "
                    f"Must be 3-letter ISO 4217 codes (e.g., USD, EUR)."
                )

        valid_iso_codes = {c.alpha_3 for c in pycountry.currencies}
        invalid = set(currencies) - valid_iso_codes
        if invalid:
            raise ValueError(
                f"Invalid ISO 4217 codes: {', '.join(sorted(invalid))}. "
                f"See https://en.wikipedia.org/wiki/ISO_4217"
            )

        return v

    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM temperature (0 = deterministic)",
    )

    output_dir: Path = Field(
        default=Path.cwd() / "output",
        description="Default output directory",
    )

    api_host: str = Field(
        default="0.0.0.0",
        description="API host address",
    )

    api_port: int = Field(
        default=8000,
        description="API port",
    )

    api_keys: str = Field(
        default="",
        description="Comma-separated API keys for authentication",
    )

    def create_output_dirs(self) -> Path:
        """Ensure output directories exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "grids").mkdir(exist_ok=True)
        (self.output_dir / "ocr_debug").mkdir(exist_ok=True)
        (self.output_dir / "results").mkdir(exist_ok=True)
        return self.output_dir

    def get_allowed_currencies(self) -> set[str]:
        """Parse allowed currencies from comma-separated string."""
        currencies = {
            c.strip().upper() for c in self.allowed_currencies.split(",") if c.strip()
        }

        if not currencies:
            logger.warning(
                "ALLOWED_CURRENCIES produced empty set (value: '%s'). "
                "Using safe default.",
                self.allowed_currencies,
            )
            return {"EUR", "USD", "MDL", "RUB", "RON"}

        return currencies

    def validate_config(self) -> None:
        """Validate configuration at startup. Raises ValueError if invalid."""
        errors = []

        # Validate ALLOWED_CURRENCIES
        currencies = self.get_allowed_currencies()
        if not currencies:
            errors.append("ALLOWED_CURRENCIES cannot be empty")

        # Validate OpenAI API key (if not using mock mode)
        if not self.mock and not self.openai_api_key:
            errors.append("OPENAI_API_KEY required when mock mode is disabled")

        # Validate OCR config (basic check)
        if self.ocr_config:
            if ";" in self.ocr_config or "&" in self.ocr_config:
                errors.append("OCR_CONFIG contains suspicious characters (; or &)")

        # Validate numeric ranges
        if self.temperature < 0 or self.temperature > 2:
            errors.append("TEMPERATURE must be between 0 and 2")

        if self.scale_factor <= 0:
            errors.append("SCALE_FACTOR must be positive")

        if self.ocr_dpi < 72 or self.ocr_dpi > 600:
            errors.append("OCR_DPI must be between 72 and 600")

        # Raise error if any validation failed
        if errors:
            raise ValueError(
                "Configuration validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )


_config_instance = None


def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
        _config_instance.validate_config()
        logger.info("Configuration validated successfully")
    return _config_instance


def get_config_unvalidated() -> InvoiceConfig:
    """Get or create global configuration instance without validation."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
    return _config_instance


def reload_config() -> InvoiceConfig:
    """Reload configuration (useful for testing)."""
    global _config_instance
    _config_instance = InvoiceConfig()
    return _config_instance
