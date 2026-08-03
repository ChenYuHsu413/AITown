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

        gen_config: dict = {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        }
        # Gemini 2.5 models "think" by default, which can silently eat the whole
        # output budget and return an empty/truncated candidate -> broken JSON.
        # For the sim's short structured tasks we don't want thinking; spend the
        # budget on the answer.
        if self.model.startswith("gemini-2.5"):
            gen_config["thinkingConfig"] = {"thinkingBudget": 0}

        body: dict = {"contents": contents, "generationConfig": gen_config}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if schema is not None:
            gen_config["responseMimeType"] = "application/json"

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

        # Robust extraction: a safety block or a truncated candidate must raise
        # (not KeyError-crash), so the router falls cleanly through to the next
        # provider in the chain instead of the whole tick dying.
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise RuntimeError(f"gemini: response blocked/empty ({reason})")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise RuntimeError(
                f"gemini: empty text (finishReason={cand.get('finishReason', '?')})"
            )
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
