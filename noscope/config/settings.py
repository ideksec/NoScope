"""Application settings via Pydantic BaseSettings."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings


class NoscopeSettings(BaseSettings):
    """NoScope configuration from environment variables and .env files."""

    model_config = {"env_prefix": "NOSCOPE_", "env_file": ".env", "extra": "ignore"}

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    default_provider: Literal["anthropic", "openai"] | None = None
    default_model: str | None = None
    default_timebox: str = "30m"
    danger_mode: bool = False

    @model_validator(mode="after")
    def check_api_keys(self) -> NoscopeSettings:
        if not self.anthropic_api_key and not self.openai_api_key:
            raise ValueError(
                "At least one API key is required: "
                "set NOSCOPE_ANTHROPIC_API_KEY or NOSCOPE_OPENAI_API_KEY"
            )
        return self


def load_settings(**overrides: object) -> NoscopeSettings:
    """Load settings with optional overrides (useful for CLI args)."""
    return NoscopeSettings(**{k: v for k, v in overrides.items() if v is not None})  # type: ignore[arg-type]
