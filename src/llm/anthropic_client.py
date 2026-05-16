"""Anthropic Claude client."""
from anthropic import Anthropic
from anthropic.types import TextBlock

from src import config
from src.llm.base import LLMClient


class AnthropicClient(LLMClient):
    def __init__(self):
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.ANTHROPIC_MODEL

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # Concatenate any text blocks in the response
        return "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
        )
