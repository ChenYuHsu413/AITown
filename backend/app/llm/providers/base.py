"""Provider abstraction.

Agents never talk to a concrete vendor SDK. They only know:

    result = await provider.generate(messages, schema=...)

Swapping Gemini <-> OpenAI <-> local <-> mock is a config change,
not a code change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResult:
    """Normalized result returned by every provider."""

    text: str
    parsed: Any = None            # JSON-parsed payload when schema was requested
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    raw: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Every provider (real or mock) implements this."""

    name: str = "base"
    model: str = ""

    # USD per 1M tokens -- used for cost estimation in the usage tracker.
    input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0

    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> LLMResult:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]

        If ``schema`` is given, the provider must return JSON conforming to it
        and populate ``LLMResult.parsed``.
        """
        raise NotImplementedError

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_price_per_m
            + output_tokens * self.output_price_per_m
        ) / 1_000_000
