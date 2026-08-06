"""LLM provider abstraction and factory."""

from noscope.config.settings import NoscopeSettings
from noscope.llm.base import LLMProvider
from noscope.llm.providers.anthropic import AnthropicProvider
from noscope.llm.providers.openai import OpenAIProvider


def resolve_provider_name(settings: NoscopeSettings) -> str:
    """Resolve which provider a run will use, honoring auto-selection from API keys."""
    if settings.default_provider is not None:
        return settings.default_provider
    if settings.openai_api_key and not settings.anthropic_api_key:
        return "openai"
    return "anthropic"


def default_model_for(provider_name: str) -> str:
    """The default model a provider uses when no override is given."""
    from noscope.llm.providers.anthropic import DEFAULT_MODEL as ANTHROPIC_DEFAULT
    from noscope.llm.providers.openai import DEFAULT_MODEL as OPENAI_DEFAULT

    return OPENAI_DEFAULT if provider_name == "openai" else ANTHROPIC_DEFAULT


def create_provider(settings: NoscopeSettings) -> LLMProvider:
    """Create an LLM provider based on settings."""
    provider_name = resolve_provider_name(settings)

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
