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

# A specific Chinese-capable free model for the zh dialogue/reflection tail. The
# Free Models Router (openrouter/free) can land on English-weak models, so zh
# free text pins a known-multilingual slug instead. Picked by live-querying
# GET /api/v1/models for a free model from the strong-zh families (Qwen ->
# DeepSeek -> GLM -> Gemma); at time of writing only Gemma 4 was free. Both the
# 31B and 26B variants speak fluent zh-TW, but the 31B was persistently
# upstream-rate-limited (Google AI Studio) while the 26B served reliably, so the
# default is Gemma 4 26B (262k ctx). Override with OPENROUTER_MODEL_ZH (e.g. the
# 31B slug); the free list churns, so re-check when the town's zh output degrades.
DEFAULT_MODEL_ZH = "google/gemma-4-26b-a4b-it:free"


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
            # `... or DEFAULT_MODEL` (not a get-default): an empty OPENROUTER_MODEL=
            # placeholder in .env would otherwise resolve to "" and break the call.
            model=model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL,
            input_price_per_m=input_price_per_m,
            output_price_per_m=output_price_per_m,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=OPENROUTER_URL,
        )
