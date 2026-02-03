"""Configuration management for invoice processing CLI."""

from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os


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

    def create_output_dirs(self) -> Path:
        """Ensure output directories exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "grids").mkdir(exist_ok=True)
        (self.output_dir / "ocr_debug").mkdir(exist_ok=True)
        (self.output_dir / "results").mkdir(exist_ok=True)
        return self.output_dir


_config_instance = None


def get_config() -> InvoiceConfig:
    """Get or create global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = InvoiceConfig()
    return _config_instance


def reload_config() -> InvoiceConfig:
    """Reload configuration (useful for testing)."""
    global _config_instance
    _config_instance = InvoiceConfig()
    return _config_instance
