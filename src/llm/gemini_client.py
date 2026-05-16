"""Google Gemini client."""
from google import genai  # type: ignore
from google.genai import types  # type: ignore

from src import config
from src.llm.base import LLMClient


class GeminiClient(LLMClient):
    def __init__(self):
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = config.GEMINI_MODEL

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Content(
                    parts=[types.Part.from_text(text=user_prompt)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return response.text or ""