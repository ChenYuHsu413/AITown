"""LLM Router.

Maps a *task type* to a model *tier* (cheap / normal / smart), with:
  - per-tier fallback chain (e.g. Gemini 429 -> OpenAI nano)
  - an exact-match decision cache (state fingerprint -> cached answer)
  - full usage tracking

Task -> tier mapping (mirrors the plan):
  cheap : should_talk, importance, mood, summary
  normal: decision, dialogue
  smart : reflection, life_goal_update
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .providers.base import LLMProvider, LLMResult
from .usage import LLMCall, UsageTracker

TASK_TIERS: dict[str, str] = {
    "should_talk": "cheap",
    "importance": "cheap",
    "mood": "cheap",
    "summary": "cheap",
    "distort": "cheap",
    "appraise": "cheap",
    "decision": "normal",
    "dialogue": "normal",
    "reflection": "smart",
    "life_goal_update": "smart",
}


@dataclass
class LLMRouter:
    tiers: dict[str, list[LLMProvider]]  # tier -> fallback chain
    usage: UsageTracker = field(default_factory=UsageTracker)
    _cache: dict[str, LLMResult] = field(default_factory=dict)

    async def generate(
        self,
        *,
        task: str,
        messages: list[dict],
        agent_id: str = "-",
        sim_minute: int = 0,
        schema: dict | None = None,
        cache_key: str | None = None,
        max_tokens: int = 512,
    ) -> LLMResult:
        tier = TASK_TIERS.get(task, "normal")

        # ---- decision cache -------------------------------------
        full_key = None
        if cache_key is not None:
            full_key = hashlib.sha256(f"{task}|{cache_key}".encode()).hexdigest()
            hit = self._cache.get(full_key)
            if hit is not None:
                self.usage.record(
                    LLMCall(
                        sim_minute=sim_minute,
                        agent_id=agent_id,
                        task_type=task,
                        provider=hit.provider,
                        model=hit.model,
                        input_tokens=0,
                        output_tokens=0,
                        latency_ms=0,
                        estimated_cost=0.0,
                        cache_hit=True,
                    )
                )
                return hit

        # ---- provider chain with fallback -----------------------
        chain = self.tiers.get(tier) or self.tiers["normal"]
        last_err: Exception | None = None
        for provider in chain:
            try:
                result = await provider.generate(
                    messages, schema=schema, max_tokens=max_tokens
                )
            except Exception as err:  # 429, timeout, parse failure...
                last_err = err
                continue

            self.usage.record(
                LLMCall(
                    sim_minute=sim_minute,
                    agent_id=agent_id,
                    task_type=task,
                    provider=result.provider,
                    model=result.model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    latency_ms=result.latency_ms,
                    estimated_cost=provider.estimate_cost(
                        result.input_tokens, result.output_tokens
                    ),
                )
            )
            if full_key is not None:
                self._cache[full_key] = result
            return result

        raise RuntimeError(f"All providers failed for tier '{tier}': {last_err}")
