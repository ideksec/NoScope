"""LLM provider abstraction and factory."""

from noscope.config.settings import NoscopeSettings
from noscope.llm.base import LLMProvider
from noscope.llm.providers.anthropic import AnthropicProvider
from noscope.llm.providers.openai import OpenAIProvider


def create_provider(settings: NoscopeSettings) -> LLMProvider:
    """Create an LLM provider based on settings."""
    provider_name = settings.default_provider

    if provider_name is None:
        if settings.anthropic_api_key and not settings.openai_api_key:
            provider_name = "anthropic"
        elif settings.openai_api_key and not settings.anthropic_api_key:
            provider_name = "openai"
        else:
            provider_name = "anthropic"

    if provider_name == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("Anthropic API key required but not set (NOSCOPE_ANTHROPIC_API_KEY)")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.default_model,
        )
    elif provider_name == "openai":
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key required but not set (NOSCOPE_OPENAI_API_KEY)")
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.default_model,
        )
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
