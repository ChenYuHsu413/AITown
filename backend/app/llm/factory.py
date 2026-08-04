"""Build the LLM router from environment. Shared by scripts and the server.

Mock by default; real tiers activate when AI_TOWN_LIVE=1 and at least one
provider key exists. Chains are assembled *dynamically* from whichever keys are
present, so a Groq-only, Gemini-only, or dual setup all work with no code change:

    cheap : Groq-8b   -> Gemini-flash -> (OpenAI-nano) -> mock
    normal: Gemini    -> Groq-8b      -> (OpenAI-nano) -> mock
    smart : Groq-70b  -> Gemini-flash -> (OpenAI-mini) -> mock

Two free-tier providers on different tiers means both actually carry traffic
(so /api/usage `by_model` shows Groq *and* Gemini), and either one 429-ing just
falls through the chain. The mock provider is always last -> the sim never dies
and the budget guard always has a free floor to collapse to.

Env: AI_TOWN_LIVE, GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY,
     AI_TOWN_BUDGET_USD (default 1.0; <=0 disables the cap).
"""

from __future__ import annotations

import os
import sys

from .env import load_env
from .prompts import builders
from .providers.base import LLMProvider
from .providers.mock import MockProvider
from .router import LLMRouter


def _make_console_safe() -> None:
    """Stop logs from crashing the process on a non-UTF-8 console -- notably
    Windows' cp950, where an emoji or a stray symbol raises UnicodeEncodeError
    mid-print. We only relax the *error handler*; the console keeps its own
    encoding, so Big5-encodable CJK still prints correctly and only characters it
    genuinely can't represent degrade to '?'. Idempotent and safe to call when
    stdout is redirected/captured (then it's simply a no-op)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # not a reconfigurable TextIO -- nothing to harden


def _budget_from_env() -> float | None:
    raw = os.environ.get("AI_TOWN_BUDGET_USD", "1.0").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return None if value <= 0 else value


def build_router(live: bool | None = None) -> LLMRouter:
    _make_console_safe()  # before the first status print, harden the console (cp950 etc.)
    load_env()  # fill missing keys from repo-root .env (real env still wins)
    if live is None:
        live = os.environ.get("AI_TOWN_LIVE") == "1"
    budget = _budget_from_env()
    mock = MockProvider()

    if not live:
        print(f"[live] mock-only (AI_TOWN_LIVE={os.environ.get('AI_TOWN_LIVE')!r}) | "
              f"lang={builders.lang_code()}", flush=True)
        return LLMRouter(
            tiers={"cheap": [mock], "normal": [mock], "smart": [mock]},
            budget_usd=budget,
        )

    from .providers.gemini_provider import GeminiProvider
    from .providers.groq_provider import GroqProvider
    from .providers.openai_provider import OpenAIProvider

    small = large = gem = nano = mini = None
    if os.environ.get("GROQ_API_KEY"):
        small = GroqProvider(model="llama-3.1-8b-instant",
                             input_price_per_m=0.05, output_price_per_m=0.08)
        large = GroqProvider(model="llama-3.3-70b-versatile",
                             input_price_per_m=0.59, output_price_per_m=0.79)
    gem_lite = None
    if os.environ.get("GEMINI_API_KEY"):
        # 2.5-flash-lite was retired for new keys; 2.5-flash is current and,
        # with thinking disabled (see GeminiProvider), emits JSON reliably.
        gem = GeminiProvider(model="gemini-2.5-flash",
                             input_price_per_m=0.30, output_price_per_m=2.50)
        # Second Gemini as a same-language backup: if 2.5-flash 429s on the free
        # tier, 2.0-flash (also fluent in zh) catches the zh dialogue/reflection
        # calls before they'd drop to the English mock floor.
        gem_lite = GeminiProvider(model="gemini-2.0-flash",
                                  input_price_per_m=0.10, output_price_per_m=0.40)
    if os.environ.get("OPENAI_API_KEY"):
        nano = OpenAIProvider(model="gpt-5-nano",
                              input_price_per_m=0.05, output_price_per_m=0.40)
        mini = OpenAIProvider(model="gpt-5-mini",
                              input_price_per_m=0.25, output_price_per_m=2.00)

    openrouter = None
    if os.environ.get("OPENROUTER_API_KEY"):
        from .providers.openrouter_provider import OpenRouterProvider
        openrouter = OpenRouterProvider()  # third free provider; English/structured only

    if not any((small, gem, nano, openrouter)):
        print("[llm] AI_TOWN_LIVE=1 but no provider key found "
              "(GROQ_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY) -- staying on mock")

    def chain(*providers: LLMProvider | None, or_tail: bool = True) -> list[LLMProvider]:
        out = [p for p in providers if p is not None]
        if or_tail and openrouter is not None:
            out.append(openrouter)  # last real fallback for English + structured tasks
        out.append(mock)  # free floor: never let a tier be all-live
        return out

    tiers = {
        "cheap": chain(small, gem, nano),
        "normal": chain(gem, small, nano),
        "smart": chain(large, gem, mini),
    }

    # ---- language-aware routing ---------------------------------------
    # Free-text tasks (dialogue, reflection) are the ones the reader sees. In a
    # non-English language, route them only through models that are reliable in
    # that language. Groq's llama-3.1-8b (the cheap/normal workhorse) garbles
    # Chinese into character soup, so it's dropped from these chains -- but
    # llama-3.3-70b IS fluent in zh, so it stays as a high-free-tier backstop
    # after Gemini (whose free tier is a hard 20 requests/day/model). Order:
    # Gemini (best) -> Gemini-lite -> llama-3.3-70b (fluent, high quota) -> mock.
    # English keeps the full dual chain (8b is fine there).
    task_chains: dict[str, list[LLMProvider]] = {}
    if builders.lang_is_zh() and (gem or gem_lite or large):
        # or_tail=False: OpenRouter's free model isn't trusted for zh free text.
        zh_reliable = chain(gem, gem_lite, large, or_tail=False)  # no 8b/openrouter; mock floor last
        task_chains["dialogue"] = zh_reliable
        task_chains["reflection"] = zh_reliable

    # Startup live-status summary: make "am I actually on real models?" a one-line,
    # unmissable fact (a server restarted without .env in scope would show mock-only).
    tags = []
    if small: tags.append("groq [ok]")
    if gem: tags.append("gemini [ok]")
    if nano: tags.append("openai [ok]")
    if openrouter: tags.append("openrouter [ok]")
    dchain = " -> ".join(f"{p.name}/{getattr(p, 'model', '')}"
                         for p in task_chains.get("dialogue", tiers["normal"]))
    print(f"[live] providers: {' '.join(tags) if tags else 'mock-only'} | "
          f"lang={builders.lang_code()} | dialogue: {dchain}", flush=True)

    return LLMRouter(tiers=tiers, task_chains=task_chains, budget_usd=budget)
