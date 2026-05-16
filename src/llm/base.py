"""Abstract LLM interface"""
from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Generate text from the given prompts. Returns the response text."""
        ...


def get_client(provider: Optional[str] = None) -> LLMClient:
    """Factory - returns the configured LLM client."""
    from src import config

    provider = (provider or config.LLM_PROVIDER).lower()

    if provider == "anthropic":
        from src.llm.anthropic_client import AnthropicClient
        return AnthropicClient()
    elif provider == "openai":
        from src.llm.openai_client import OpenAIClient
        return OpenAIClient()
    elif provider == "gemini":
        from src.llm.gemini_client import GeminiClient
        return GeminiClient()
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
