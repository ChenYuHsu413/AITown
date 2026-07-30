"""Build the LLM router from environment. Shared by scripts and the server.

Mock by default; real tiers activate when AI_TOWN_LIVE=1 and keys exist.
"""

from __future__ import annotations

import os

from .providers.mock import MockProvider
from .router import LLMRouter


def build_router(live: bool | None = None) -> LLMRouter:
    if live is None:
        live = os.environ.get("AI_TOWN_LIVE") == "1"
    has_keys = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    mock = MockProvider()

    if live and has_keys:
        from .providers.gemini_provider import GeminiProvider
        from .providers.openai_provider import OpenAIProvider

        nano = OpenAIProvider(model="gpt-5-nano", input_price_per_m=0.05, output_price_per_m=0.40)
        flash = GeminiProvider(model="gemini-2.5-flash-lite", input_price_per_m=0.10, output_price_per_m=0.40)
        mini = OpenAIProvider(model="gpt-5-mini", input_price_per_m=0.25, output_price_per_m=2.00)
        return LLMRouter(tiers={
            "cheap": [nano, flash, mock],
            "normal": [flash, nano, mock],
            "smart": [mini, flash, mock],
        })

    return LLMRouter(tiers={"cheap": [mock], "normal": [mock], "smart": [mock]})
