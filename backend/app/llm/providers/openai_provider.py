"""OpenAI provider (chat completions, JSON mode).

Only imported/used when configured; the sim runs fine on MockProvider.
Requires: pip install httpx, and OPENAI_API_KEY in the environment.
"""

from __future__ import annotations

import json
import os
import time

from .base import LLMProvider, LLMResult


class OpenAIProvider(LLMProvider):
    name = "openai"

    # Chat-completions endpoint. Overridable so OpenAI-compatible vendors
    # (Groq, Together, local vLLM, ...) can reuse this exact client.
    base_url: str = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        model: str = "gpt-5-nano",
        input_price_per_m: float = 0.05,
        output_price_per_m: float = 0.40,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model
        self.input_price_per_m = input_price_per_m
        self.output_price_per_m = output_price_per_m
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if base_url is not None:
            self.base_url = base_url

    async def generate(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> LLMResult:
        import httpx  # local import: optional dependency

        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if schema is not None:
            body["response_format"] = {"type": "json_object"}

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
        resp.raise_for_status()
        data = resp.json()
        latency = int((time.perf_counter() - t0) * 1000)

        text = data["choices"][0]["message"]["content"] or ""
        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(text.replace("```json", "").replace("```", "").strip())
            except json.JSONDecodeError:
                parsed = None

        usage = data.get("usage", {})
        return LLMResult(
            text=text,
            parsed=parsed,
            model=self.model,
            provider=self.name,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
            raw=data,
        )
