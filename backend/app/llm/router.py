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

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .providers.base import LLMProvider, LLMResult
from .usage import LLMCall, UsageTracker

SOFT_RETRIES = 1  # extra attempts on the same provider after an empty/garbled result


class ProvidersExhausted(RuntimeError):
    """Every real provider failed the quality gate for a ``no_floor`` call. The mock
    floor was deliberately withheld so the caller can *wait and retry the whole chain*
    (the reader-facing "patient retry, never canned" policy) instead of shipping a
    canned mock line. Dialogue and reflection raise this; their engine tasks catch it,
    brew a short pause, and re-run the chain."""

# Universal free-text quality gate (the "no provider ships garbage" rule). Every
# model has its own way of dodging a hard prompt -- the classic is a bare "ok" --
# so this runs at the ROUTER for every call, on top of any task-specific validate,
# and a result that trips it is treated as a provider failure (chain falls through).
# Adding a new provider to any chain inherits this automatically; the gate lives here,
# not scattered across task call-sites.
_GARBAGE = {
    "ok", "okay", "o.k.", "o.k", "k", "none", "n/a", "na", "n a", "good", "fine",
    "bad", "nothing", "idk", "unknown", "yes", "no", "true", "false", "null", "nil",
    "nan", "...", "…", "-", "--",
}


def _is_garbage_text(s: object) -> bool:
    """True if a free-text value is a model's throwaway dodge (a junk word, or too
    short to be a real answer)."""
    t = str(s or "").strip().lower().strip(" .!?\"'…、。").replace("/", " ").strip()
    if not t:
        return True
    if t in _GARBAGE:
        return True
    return len(t) < 2


def _baseline_reject(parsed: object, task: str) -> str | None:
    """Router-level free-text quality gate keyed by task. Returns a short reason to
    reject, or None. Only inspects the free-text field(s) each task produces, so
    structured tasks (should_talk {talk:bool}, importance {importance:int}) are
    untouched. This is what stops a 'ok' dodge from any provider reaching the reader
    (a dialogue turn, a translation, a reflection insight/belief)."""
    if not isinstance(parsed, dict):
        return None  # schema/None handling stays with the caller
    if task in ("translate", "distort", "leak", "summary"):
        if "text" in parsed and _is_garbage_text(parsed.get("text")):
            return "garbage text"
    if task == "dialogue":
        turns = parsed.get("turns")
        if isinstance(turns, list):
            for t in turns:
                if isinstance(t, dict) and _is_garbage_text(t.get("text")):
                    return "garbage dialogue turn"
    if task == "reflection":
        for ins in (parsed.get("insights") or []):
            if _is_garbage_text(ins):
                return "garbage insight"
        for b in (parsed.get("beliefs") or []):
            if isinstance(b, dict) and _is_garbage_text(b.get("text")):
                return "garbage belief"
    if task == "chapter_closure":
        if _is_garbage_text(parsed.get("biography_line")):
            return "garbage biography line"
    return None

TASK_TIERS: dict[str, str] = {
    "should_talk": "cheap",
    "importance": "cheap",
    "mood": "cheap",
    "summary": "cheap",
    "distort": "cheap",
    "appraise": "cheap",
    "translate": "cheap",
    "decision": "normal",
    "dialogue": "normal",
    "reflection": "smart",
    "life_goal_update": "smart",
    "chapter_closure": "smart",   # rare (once per closed chapter); see agents/chapters.py
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
        per_call_timeout: float | None = None,
        no_floor: bool = False,
    ) -> LLMResult:
        """``validate`` is an optional quality gate run on each provider's
        output. A result that fails it (or, when ``schema`` was requested,
        parses to None -- i.e. truncated/malformed JSON) is treated as a
        provider failure and the chain falls through to the next provider,
        so broken content never reaches the caller.

        ``per_call_timeout`` (seconds), when set, bounds EACH provider call: a
        provider slower than this is abandoned like any other failure and the
        chain falls through to the next one (which is recorded normally). This
        keeps a single slow model -- e.g. DeepSeek on a bad tail -- from starving
        the whole call; the fast fallback answers instead of dropping to the mock
        floor. Off (None) preserves the original unbounded per-provider behaviour.

        ``no_floor``: withhold the mock floor and raise ``ProvidersExhausted`` when
        every real provider fails the gate, so the caller can wait and re-run the whole
        chain rather than ship a canned line (the "patient retry, never canned" policy;
        used by dialogue/reflection). Under budget exhaustion the mock still serves --
        we're out of money and it's the only free option."""
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
        budget_hit = self.budget_usd is not None and self.usage.total_cost >= self.budget_usd
        if budget_hit:
            if not self._budget_logged:
                print(
                    f"[budget] cap ${self.budget_usd:.2f} reached "
                    f"(spent ${self.usage.total_cost:.4f}) -- routing to free mock provider"
                )
                self._budget_logged = True
            chain = chain[-1:]

        # ---- no_floor: withhold the mock so failure RAISES ------
        # The caller (dialogue/reflection) wants an exception it can wait-and-retry on,
        # not a canned mock line. Drop the mock provider so a total chain failure falls
        # through to ProvidersExhausted below. Only do this when a REAL provider remains:
        # in mock-only mode (no keys) there is nothing to retry, so serve mock as before
        # -- otherwise every call would spin the caller's retry loop for nothing. Also
        # skipped under budget exhaustion (mock is the only affordable option left).
        withhold_floor = False
        if no_floor and not budget_hit:
            real = [p for p in chain if p.name != "mock"]
            if real:
                chain = real
                withhold_floor = True

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
                    call = provider.generate(messages, schema=schema, max_tokens=max_tokens)
                    if per_call_timeout is not None:
                        result = await asyncio.wait_for(call, timeout=per_call_timeout)
                    else:
                        result = await call
                except Exception as err:  # 429, timeout (incl. per_call_timeout), HTTP error...
                    last_err = err
                    result = None
                    if getattr(getattr(err, "response", None), "status_code", None) == 429:
                        self._start_cooldown(provider, err)   # back off, don't hammer
                    break
                finally:
                    if self.on_call_end is not None:
                        self.on_call_end()
                reason = self._reject_reason(result, schema, validate, task)
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

        # Nothing cleared the gates. A no_floor caller wants to wait and retry the
        # whole chain rather than serve canned filler, so signal exhaustion instead
        # of returning the soft floor (only when we actually withheld a real chain's
        # mock -- never in mock-only mode, which has no floor to withhold).
        if withhold_floor:
            raise ProvidersExhausted(
                f"all real providers failed the gate for task '{task}': {last_err}")

        # Otherwise, rather than hard-fail the tick, fall back to the first output we
        # did get (typically the free mock floor).
        if floor is not None:
            provider, result = floor
            self._record(provider, result, task=task, agent_id=agent_id, sim_minute=sim_minute)
            return result

        raise RuntimeError(f"All providers failed for task '{task}' (tier '{tier}'): {last_err}")

    @staticmethod
    def _reject_reason(
        result: LLMResult, schema: dict | None,
        validate: Callable[[LLMResult], bool] | None, task: str = "",
    ) -> str | None:
        """None if the result is acceptable, else a short reason it should be
        rejected (so the chain retries / falls through). The universal free-text
        gate runs first -- every provider is held to it -- then any task-specific
        validate."""
        if schema is not None and result.parsed is None:
            return "invalid/truncated JSON"
        garbage = _baseline_reject(result.parsed, task)
        if garbage is not None:
            return garbage
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
