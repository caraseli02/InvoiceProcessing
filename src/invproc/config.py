"""Configuration management for invoice processing CLI."""

import logging
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import pycountry

logger = logging.getLogger(__name__)

_DEFAULT_DEV_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://lavio.vercel.app",
]


class ColumnHeadersConfig(BaseModel):
    """Configurable column header names for invoice format detection."""

    quantity: str = Field(default="Cant.", description="Quantity column header")
    unit_price: str = Field(
        default="Pret unitar", description="Unit price column header"
    )
    total_price: str = Field(
        default="Valoare incl.TVA", description="Total price column header"
    )

    model_config = {"extra": "ignore"}


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

    app_env: Literal["local", "production"] = Field(
        default="local",
        description="Application environment mode (local|production). Production enables strict security validation.",
    )

    allowed_origins: Optional[str] = Field(
        default=None,
        description="CORS allowlist (comma-separated origins). Required when APP_ENV=production.",
    )

    allow_api_key_auth: bool = Field(
        default=False,
        description="Allow API key auth bypass (dev-only). Must be false in production.",
    )

    extract_cache_debug_headers: bool = Field(
        default=False,
        description="Enable debug headers for extract/cache (dev-only by default).",
    )

    extract_observability_headers: bool = Field(
        default=False,
        description="Enable observability headers (instance/process identifiers).",
    )

    allow_prod_debug_headers: bool = Field(
        default=False,
        description="Allow debug/observability headers in production when explicitly enabled.",
    )

    model: str = Field(default="gpt-4o-mini", description="Default OpenAI model to use")

    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=128000,
        description="Maximum tokens for API responses",
    )

    openai_timeout_sec: float = Field(
        default=180.0,
        ge=10.0,
        le=600.0,
        description="OpenAI API request timeout in seconds",
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
        default=150,
        ge=150,
        le=600,
        description="OCR resolution in DPI",
    )

    max_pdf_size_mb: int = Field(
        default=2,
        ge=1,
        le=50,
        description="Maximum PDF upload size in megabytes for API extraction",
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

    column_headers: ColumnHeadersConfig = Field(
        default_factory=ColumnHeadersConfig,
        description="Column header names for invoice format detection",
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

    supabase_url: Optional[str] = Field(
        default=None,
        description="Supabase project URL for JWT validation",
    )

    supabase_service_role_key: Optional[str] = Field(
        default=None,
        description="Supabase service role key for server-side token verification",
    )

    fx_lei_to_eur: float = Field(
        default=19.5,
        gt=0,
        description="Fixed FX rate used for invoice pricing parity",
    )

    transport_rate_per_kg: float = Field(
        default=1.5,
        gt=0,
        description="Transport surcharge in EUR per kilogram",
    )

    extract_cache_enabled: bool = Field(
        default=False,
        description="Enable in-memory extraction cache for repeated identical PDFs",
    )

    extract_cache_ttl_sec: int = Field(
        default=86400,
        ge=1,
        le=604800,
        description="Extraction cache TTL in seconds",
    )

    extract_cache_max_entries: int = Field(
        default=256,
        ge=1,
        le=10000,
        description="Maximum number of cached extraction entries",
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

    def cors_allowed_origins(self) -> list[str]:
        """Return the CORS allowlist for the current environment."""
        if self.allowed_origins is None:
            if self.app_env == "production":
                return []
            return list(_DEFAULT_DEV_ALLOWED_ORIGINS)

        origins = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        return origins

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

        if self.max_pdf_size_mb < 1 or self.max_pdf_size_mb > 50:
            errors.append("MAX_PDF_SIZE_MB must be between 1 and 50")

        if self.openai_timeout_sec < 10 or self.openai_timeout_sec > 600:
            errors.append("OPENAI_TIMEOUT_SEC must be between 10 and 600")

        if self.extract_cache_ttl_sec < 1:
            errors.append("EXTRACT_CACHE_TTL_SEC must be >= 1")

        if self.extract_cache_max_entries < 1:
            errors.append("EXTRACT_CACHE_MAX_ENTRIES must be >= 1")

        if self.app_env == "production":
            if not self.allowed_origins or not self.allowed_origins.strip():
                errors.append(
                    "ALLOWED_ORIGINS is required when APP_ENV=production (no fallback)"
                )
            else:
                origins = self.cors_allowed_origins()
                if not origins:
                    errors.append(
                        "ALLOWED_ORIGINS is required when APP_ENV=production (no fallback)"
                    )
                if any(o.strip() == "*" for o in origins):
                    errors.append("ALLOWED_ORIGINS must not include '*' in production")

            if self.allow_api_key_auth:
                errors.append("ALLOW_API_KEY_AUTH must be false in production")

            debug_enabled = (
                self.extract_cache_debug_headers or self.extract_observability_headers
            )
            if debug_enabled and not self.allow_prod_debug_headers:
                errors.append(
                    "EXTRACT_CACHE_DEBUG_HEADERS / EXTRACT_OBSERVABILITY_HEADERS are not allowed in production "
                    "unless ALLOW_PROD_DEBUG_HEADERS=true"
                )

        # Raise error if any validation failed
        if errors:
            raise ValueError(
                "Configuration validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )


_config_instance = None


def build_config(*, validate: bool = True) -> InvoiceConfig:
    """Create a new configuration instance for an explicit lifecycle owner."""
    config = InvoiceConfig()
    if validate:
        config.validate_config()
        logger.info("Configuration validated successfully")
    return config


def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = build_config()
    return _config_instance


def get_config_unvalidated() -> InvoiceConfig:
    """Get or create global configuration instance without validation."""
    global _config_instance
    if _config_instance is None:
        _config_instance = build_config(validate=False)
    return _config_instance


def reload_config() -> InvoiceConfig:
    """Reload configuration (useful for testing)."""
    global _config_instance
    _config_instance = InvoiceConfig()
    return _config_instance
