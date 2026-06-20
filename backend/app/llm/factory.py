"""Select an LLM provider from configuration."""
from __future__ import annotations

from app.config import settings
from app.llm.base import LLMProvider
from app.llm.mock_provider import MockLLMProvider


def _build(provider: str) -> LLMProvider:
    if provider == "mock":
        if not settings.mock_allowed:
            raise RuntimeError(
                f"The offline mock provider is disabled when APP_ENV={settings.app_env}. "
                "Set LLM_PROVIDER=openai (with OPENAI_API_KEY)."
            )
        return MockLLMProvider()
    if provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()
    raise ValueError(f"Unknown LLM provider '{provider}'")


def get_provider() -> LLMProvider:
    return _build(settings.llm_provider.lower())


def get_cad_provider() -> LLMProvider:
    """Provider for the CAD feature-graph planner. Defaults to the configured
    LLM provider; CAD_LLM_PROVIDER can point the planner at a different backend."""
    return _build((settings.cad_llm_provider or settings.llm_provider).lower())
