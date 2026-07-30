"""Gemini provider (generateContent, JSON mode).

Only used when configured. Requires httpx and GEMINI_API_KEY.
"""

from __future__ import annotations

import json
import os
import time

from .base import LLMProvider, LLMResult


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        model: str = "gemini-2.5-flash-lite",
        input_price_per_m: float = 0.10,
        output_price_per_m: float = 0.40,
        api_key: str | None = None,
    ):
        self.model = model
        self.input_price_per_m = input_price_per_m
        self.output_price_per_m = output_price_per_m
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    async def generate(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> LLMResult:
        import httpx  # optional dependency

        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if schema is not None:
            body["generationConfig"]["responseMimeType"] = "application/json"

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self._api_key}"
        )

        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        latency = int((time.perf_counter() - t0) * 1000)

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None

        usage = data.get("usageMetadata", {})
        return LLMResult(
            text=text,
            parsed=parsed,
            model=self.model,
            provider=self.name,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            latency_ms=latency,
            raw=data,
        )
