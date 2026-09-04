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
    raw = os.environ.get("AI_TOWN_BUDGET_USD", "5.0").strip()
    try:
        value = float(raw)
    except ValueError:
        return 5.0
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
    openrouter_zh = None
    if os.environ.get("OPENROUTER_API_KEY"):
        from .providers.openrouter_provider import DEFAULT_MODEL_ZH, OpenRouterProvider
        openrouter = OpenRouterProvider()  # third free provider; English/structured only
        # A pinned Chinese-capable free model as a zh dialogue/reflection tail, so the
        # town doesn't fall all the way to the English mock once Gemini's free quota is
        # spent. Defaults to the best free zh model found on OpenRouter; blank
        # OPENROUTER_MODEL_ZH to disable.
        zh_model = os.environ.get("OPENROUTER_MODEL_ZH", DEFAULT_MODEL_ZH).strip()
        if zh_model:
            openrouter_zh = OpenRouterProvider(model=zh_model)

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
    if builders.lang_is_zh() and (gem or gem_lite or large or openrouter_zh):
        # or_tail=False keeps the English Free-Models-Router out of zh; the pinned
        # Chinese-capable openrouter_zh goes on the tail (before mock) instead, and a
        # gibberish gate (see decision._zh_text_ok) rejects any character-soup it emits.
        zh_reliable = chain(gem, gem_lite, large, openrouter_zh, or_tail=False)  # mock floor last
        # dialogue = live zh performance; translate = English knowledge -> zh for display.
        # (reflection/distort/leak are now English-canonical, so they use the normal
        # English chains -- no zh routing.)
        task_chains["dialogue"] = zh_reliable
        task_chains["translate"] = zh_reliable

    # ---- paid-first config (AI_TOWN_PAID=1) ---------------------------
    # Put a cheap-but-strong paid model at the FRONT of the reader-facing free-text
    # chains (dialogue/reflection), a fast paid translator at the front of translate,
    # and the paid model only as a TAIL on cheap structured tasks (they stay free in
    # the common case). Everything still falls through to the existing free chain +
    # mock floor, so a paid 429/outage degrades exactly as before.
    paid = os.environ.get("AI_TOWN_PAID") == "1"
    if paid and openrouter is not None:
        from .providers.openrouter_provider import OpenRouterProvider
        deepseek = OpenRouterProvider(model="deepseek/deepseek-v4-flash",
                                      input_price_per_m=0.14, output_price_per_m=0.28)
        # Translate primary: DeepSeek too (qwen's bare-number output was retired). A
        # SEPARATE instance from the dialogue deepseek so a translate 429 cools only
        # translate, never sidelining dialogue's quality anchor. Translation volume is
        # small, so the paid cost is negligible and the stability is worth it.
        tr_slug = os.environ.get("AI_TOWN_TRANSLATE_MODEL", "deepseek/deepseek-v4-flash")
        translator = OpenRouterProvider(model=tr_slug,
                                        input_price_per_m=0.14, output_price_per_m=0.28)
        # Paid SECOND string for the reader-facing zh chains (dialogue/reflection/
        # translate). A paid user's first fallback must not depend on the free tier's
        # 20-requests/day quota, so a second paid model sits ahead of the free layer.
        # Kimi K2.6 wins on zh-TW feel (measured: the most idiomatic Taiwanese
        # colloquial of the candidates); it is a *thinking* model, so reasoning is
        # disabled or it burns our token budget on hidden reasoning and truncates the
        # JSON. Override the slug with AI_TOWN_ZH_SECOND_MODEL (e.g. z-ai/glm-4.6).
        zh2_slug = os.environ.get("AI_TOWN_ZH_SECOND_MODEL", "moonshotai/kimi-k2.6")
        zh_second = OpenRouterProvider(model=zh2_slug,
                                       input_price_per_m=0.589, output_price_per_m=2.48,
                                       extra_body={"reasoning": {"enabled": False}})

        def existing(task: str, tier: str) -> list[LLMProvider]:
            return list(task_chains.get(task) or tiers[tier])

        def tail(base: list[LLMProvider]) -> list[LLMProvider]:
            # deepseek as a last real provider, kept just before the mock floor.
            return base[:-1] + [deepseek, base[-1]] if base and base[-1] is mock else base + [deepseek]

        # deepseek primary -> zh_second (paid) -> free layer -> mock. The two paid
        # providers carry a paid user before the free 20/day quota is ever touched.
        task_chains["dialogue"] = [deepseek, zh_second] + existing("dialogue", "normal")
        task_chains["reflection"] = [deepseek, zh_second] + existing("reflection", "smart")
        # Chapter closure and wish generation are rare smart-tier reflections; same
        # chain as reflection.
        task_chains["chapter_closure"] = [deepseek, zh_second] + existing("chapter_closure", "smart")
        task_chains["wish_generation"] = [deepseek, zh_second] + existing("wish_generation", "smart")
        task_chains["translate"] = [translator, zh_second] + existing("translate", "cheap")
        for t in ("should_talk", "importance", "mood", "summary", "appraise", "distort"):
            task_chains[t] = tail(existing(t, "cheap"))
        task_chains["decision"] = tail(existing("decision", "normal"))
        print(f"[live] paid-first: dialogue/reflection -> deepseek-v4-flash -> {zh2_slug} | "
              f"translate -> {tr_slug} -> {zh2_slug} | cheap tasks free-first (deepseek tail)", flush=True)

    # Startup live-status summary: make "am I actually on real models?" a one-line,
    # unmissable fact (a server restarted without .env in scope would show mock-only).
    tags = []
    if small: tags.append("groq [ok]")
    if gem: tags.append("gemini [ok]")
    if nano: tags.append("openai [ok]")
    if openrouter: tags.append("openrouter [ok]")
    if openrouter_zh: tags.append("or-zh [ok]")
    dchain = " -> ".join(f"{p.name}/{getattr(p, 'model', '')}"
                         for p in task_chains.get("dialogue", tiers["normal"]))
    print(f"[live] providers: {' '.join(tags) if tags else 'mock-only'} | "
          f"lang={builders.lang_code()} | dialogue: {dchain}", flush=True)

    # Loud, unmissable mode banner: the #1 confusion is "is paid actually on?".
    # AI_TOWN_PAID=1 only takes effect with an OpenRouter key -- if it's set but the
    # key is missing, say so here rather than silently running the free config.
    if paid and openrouter is not None:
        mode = "PAID  (DeepSeek-first dialogue/reflection; live speed uncapped)"
    elif paid:
        mode = "FREE-QUOTA  [!] AI_TOWN_PAID=1 but no OPENROUTER_API_KEY -> paid config NOT active"
    else:
        mode = "FREE-QUOTA  (free providers; live speed capped at 5x)"
    bar = "=" * 72
    print(f"{bar}\n  AI TOWN MODE: {mode}\n{bar}", flush=True)

    return LLMRouter(tiers=tiers, task_chains=task_chains, budget_usd=budget)
