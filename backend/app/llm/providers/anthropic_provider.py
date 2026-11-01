"""Anthropic (Claude) provider, via the official ``anthropic`` SDK.

Only imported/used when ANTHROPIC_API_KEY is set; the sim runs fine on MockProvider.
Sits at the head of every chain when configured (see factory.build_router):
cheap/normal -> Claude Haiku, smart -> Claude Sonnet.

Request shape, per model:
  * no ``temperature`` -- current API versions reject sampling parameters;
  * Haiku: thinking disabled (allowed at its default effort) -- these are short,
    high-volume calls whose ``max_tokens`` must go to the answer, not to reasoning;
  * Sonnet: ``between_tools`` thinking (no reasoning without tools -- Sonnet can't
    disable it outright) at ``high`` effort, plus the server-side refusal fallback,
    which re-runs a declined request on another Claude model inside the same call.
A refusal that survives is raised, so the router falls through to the next provider.
SDK retries are off: the router owns fallback and the 429 cooldown.
"""

from __future__ import annotations

import json
import os
import re
import time

from .base import LLMProvider, LLMResult

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _parse_json(text: str):
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")      # a stray sentence around the object
    if 0 <= a < b:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            return None
    return None


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, input_price_per_m: float, output_price_per_m: float,
                 api_key: str | None = None):
        import anthropic  # local import: optional dependency

        self.model = model
        self.input_price_per_m = input_price_per_m
        self.output_price_per_m = output_price_per_m
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            max_retries=0, timeout=60.0)
        self._sonnet = "sonnet" in model

    async def generate(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,   # accepted for the interface; never sent
    ) -> LLMResult:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
        if schema is not None:
            system = (system + "\n\n" if system else "") + \
                "Reply with one JSON object only -- no prose, no code fences."
        kw: dict = {"model": self.model, "max_tokens": max_tokens, "messages": convo}
        if system:
            kw["system"] = system

        t0 = time.perf_counter()
        if self._sonnet:
            resp = await self._client.beta.messages.create(
                **kw, thinking={"type": "between_tools"}, output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            resp = await self._client.messages.create(**kw, thinking={"type": "disabled"})
        latency = int((time.perf_counter() - t0) * 1000)

        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise RuntimeError(f"anthropic/{self.model}: refusal ({cat})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMResult(
            text=text,
            parsed=_parse_json(text) if schema is not None else None,
            model=resp.model or self.model,
            provider=self.name,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            latency_ms=latency,
        )
