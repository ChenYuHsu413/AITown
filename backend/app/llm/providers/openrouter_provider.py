"""OpenRouter provider.

OpenRouter exposes an OpenAI-compatible chat-completions endpoint, so this is
``OpenAIProvider`` pointed at OpenRouter's URL and reading ``OPENROUTER_API_KEY``.
Used only as a last-resort tail on the English / structured-task chains (its free
models are reliable in English + JSON but not trusted for zh free text), giving a
third free provider when both Groq and Gemini are rate-limited.

Model defaults to ``openrouter/free`` -- OpenRouter's Free Models Router, which
picks a currently-available free model itself, so a single free slug getting
delisted no longer breaks us (individual ``*:free`` slugs churn often). Override
with ``OPENROUTER_MODEL``. Free models bill at $0, so the cost strip stays honest.
"""

from __future__ import annotations

import os

from .openai_provider import OpenAIProvider

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openrouter/free"  # Free Models Router: auto-picks a live free model


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(
        self,
        model: str | None = None,
        input_price_per_m: float = 0.0,
        output_price_per_m: float = 0.0,
        api_key: str | None = None,
    ):
        super().__init__(
            model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL),
            input_price_per_m=input_price_per_m,
            output_price_per_m=output_price_per_m,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=OPENROUTER_URL,
        )
