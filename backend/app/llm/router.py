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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .providers.base import LLMProvider, LLMResult
from .usage import LLMCall, UsageTracker

SOFT_RETRIES = 1  # extra attempts on the same provider after an empty/garbled result

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
    # Per-task chain overrides (task -> chain), checked *before* the tier chain.
    # Used for language-aware routing: e.g. zh-TW dialogue -> Gemini-only, so a
    # weak-at-Chinese model never garbles the feed.
    task_chains: dict[str, list[LLMProvider]] = field(default_factory=dict)
    usage: UsageTracker = field(default_factory=UsageTracker)
    budget_usd: float | None = None       # hard spend cap; None = unlimited
    # In-flight hooks: fired immediately before/after each real provider call
    # (never for cache hits or cooled-down skips), so a host can surface "waiting
    # for the LLM" state while a slow call blocks the tick. Paired 1:1.
    on_call_start: Callable[[str, str], None] | None = None   # (provider_name, task)
    on_call_end: Callable[[], None] | None = None
    _cache: dict[str, LLMResult] = field(default_factory=dict)
    _budget_logged: bool = False
    _cooldown: dict[int, float] = field(default_factory=dict)  # id(provider) -> monotonic deadline

    # ---- rate-limit cooldown ---------------------------------------
    # A provider that just returned 429 is skipped for a cooldown window instead
    # of being hammered on every call -- that both stops wasting calls and lets
    # its per-minute bucket recover. The window comes from the response's
    # Retry-After / "retry in Ns" hint, clamped to a sane range.
    def _cooling(self, provider: LLMProvider) -> bool:
        return self._cooldown.get(id(provider), 0.0) > time.monotonic()

    def _start_cooldown(self, provider: LLMProvider, err: Exception) -> float:
        secs = 30.0
        resp = getattr(err, "response", None)
        if resp is not None:
            ra = getattr(resp, "headers", {}).get("retry-after") if hasattr(resp, "headers") else None
            if ra:
                try:
                    secs = float(ra)
                except ValueError:
                    pass
            else:
                m = re.search(r"(?:retry|try again) in ([\d.]+)s", getattr(resp, "text", "") or "")
                if m:
                    secs = float(m.group(1))
        # TPM/RPM buckets reset every minute, so keep the skip short -- a long
        # cooldown from one transient 429 would otherwise sideline a healthy
        # provider for the whole run.
        secs = max(10.0, min(secs, 90.0))
        self._cooldown[id(provider)] = time.monotonic() + secs
        print(f"[cooldown] {provider.name}/{getattr(provider, 'model', '')} "
              f"429 -> skipping for {secs:.0f}s", flush=True)
        return secs

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
        validate: Callable[[LLMResult], bool] | None = None,
    ) -> LLMResult:
        """``validate`` is an optional quality gate run on each provider's
        output. A result that fails it (or, when ``schema`` was requested,
        parses to None -- i.e. truncated/malformed JSON) is treated as a
        provider failure and the chain falls through to the next provider,
        so broken content never reaches the caller."""
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
        # Task override (language-aware routing) wins over the tier chain.
        chain = self.task_chains.get(task) or self.tiers.get(tier) or self.tiers["normal"]

        # ---- budget guard ---------------------------------------
        # Once cumulative spend hits the cap, collapse to the free fallback
        # (factory guarantees the mock provider is always last in every chain).
        # Cache hits above already returned for free; only real calls are gated.
        if self.budget_usd is not None and self.usage.total_cost >= self.budget_usd:
            if not self._budget_logged:
                print(
                    f"[budget] cap ${self.budget_usd:.2f} reached "
                    f"(spent ${self.usage.total_cost:.4f}) -- routing to free mock provider"
                )
                self._budget_logged = True
            chain = chain[-1:]

        last_err: Exception | None = None
        floor: tuple[LLMProvider, LLMResult] | None = None  # best-effort output if all gates fail
        for provider in chain:
            if self._cooling(provider):
                continue  # 429'd recently -> skip until its cooldown lapses
            result: LLMResult | None = None
            reason: str | None = None
            # Soft failures (empty/garbled/truncated JSON) are often intermittent
            # -- e.g. Groq's 70b returns valid-but-empty "text" ~10% of the time --
            # so retry the SAME provider once before moving on. Hard errors (429,
            # timeout) won't fix on retry, so those break to the next provider.
            for _ in range(1 + SOFT_RETRIES):
                if self.on_call_start is not None:
                    self.on_call_start(provider.name, task)
                try:
                    result = await provider.generate(
                        messages, schema=schema, max_tokens=max_tokens
                    )
                except Exception as err:  # 429, timeout, HTTP error...
                    last_err = err
                    result = None
                    if getattr(getattr(err, "response", None), "status_code", None) == 429:
                        self._start_cooldown(provider, err)   # back off, don't hammer
                    break
                finally:
                    if self.on_call_end is not None:
                        self.on_call_end()
                reason = self._reject_reason(result, schema, validate)
                if reason is None:
                    break  # good output

            if result is not None and reason is None:
                self._record(provider, result, task=task, agent_id=agent_id, sim_minute=sim_minute)
                if full_key is not None:
                    self._cache[full_key] = result
                return result
            if result is not None:  # soft-failed every attempt -> remember, try next provider
                last_err = RuntimeError(f"{provider.name}/{result.model}: {reason}")
                if floor is None:
                    floor = (provider, result)

        # Nothing cleared the gates. Rather than hard-fail the tick, fall back to
        # the first output we did get (typically the free mock floor).
        if floor is not None:
            provider, result = floor
            self._record(provider, result, task=task, agent_id=agent_id, sim_minute=sim_minute)
            return result

        raise RuntimeError(f"All providers failed for task '{task}' (tier '{tier}'): {last_err}")

    @staticmethod
    def _reject_reason(
        result: LLMResult, schema: dict | None, validate: Callable[[LLMResult], bool] | None
    ) -> str | None:
        """None if the result is acceptable, else a short reason it should be
        rejected (so the chain retries / falls through)."""
        if schema is not None and result.parsed is None:
            return "invalid/truncated JSON"
        if validate is not None and not validate(result):
            return "failed sanity check"
        return None

    def _record(self, provider: LLMProvider, result: LLMResult, *,
                task: str, agent_id: str, sim_minute: int) -> None:
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
