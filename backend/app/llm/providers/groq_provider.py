"""Groq provider.

Groq exposes an OpenAI-compatible chat-completions endpoint, so this is just
``OpenAIProvider`` pointed at Groq's URL and reading ``GROQ_API_KEY``. JSON mode
(``response_format={"type": "json_object"}``) is supported by the llama models
below, which is all the sim asks for.

Free-tier friendly defaults:
  * ``llama-3.1-8b-instant``   -- cheap/normal tiers (fast, plenty for should_talk + chat)
  * ``llama-3.3-70b-versatile`` -- smart tier (reflection)

Prices are Groq's published per-1M-token rates, used only for the cost strip.
"""

from __future__ import annotations

import os

from .openai_provider import OpenAIProvider

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(OpenAIProvider):
    name = "groq"

    def __init__(
        self,
        model: str = "llama-3.1-8b-instant",
        input_price_per_m: float = 0.05,
        output_price_per_m: float = 0.08,
        api_key: str | None = None,
    ):
        super().__init__(
            model=model,
            input_price_per_m=input_price_per_m,
            output_price_per_m=output_price_per_m,
            api_key=api_key or os.environ.get("GROQ_API_KEY", ""),
            base_url=GROQ_URL,
        )
