"""MockProvider -- deterministic, zero-cost stand-in for a real LLM.

Lets the whole simulation run end-to-end with no API key.
Responses are seeded by the prompt content so runs are reproducible
but not completely uniform.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re

from .base import LLMProvider, LLMResult


def _seed_from(messages: list[dict]) -> int:
    blob = "|".join(m.get("content", "") for m in messages)
    return int(hashlib.sha256(blob.encode()).hexdigest()[:8], 16)


# NB: the mock is the deterministic FLOOR (last resort when every live provider is
# down). Its canned lines must assert NO specific world fact -- a hardcoded "the new
# mural near the park" once leaked a nonexistent landmark into a run whose park has a
# light installation, not a mural. Keep every line world-agnostic (weather, mood,
# business, plans); anything run-specific must arrive via the prompt, never from here.
_SMALL_TALK = [
    ("How has your day been so far?", "Pretty good, just the usual. And you?"),
    ("The weather's been pleasant lately, hasn't it?", "It really has. Makes the day easier."),
    ("Business has been steady lately.", "Glad to hear it, you deserve it."),
    ("You look a bit tired today.", "Yeah... long week. Thanks for noticing."),
    ("Any plans for the evening?", "Probably just an early night, honestly."),
]

_CONCERNS = [
    "I've been thinking about work a lot lately.",
    "Honestly, things have been a bit stressful.",
    "I might make a big change soon. Not sure yet.",
]

# zh-TW canned lines: when a real zh dialogue falls all the way to the mock floor
# (every live provider down/rate-limited), keep the feed in fluent Chinese rather
# than dropping to English. Generic, not contextual -- it's the last resort.
_SMALL_TALK_ZH = [
    ("你今天過得如何？", "還不錯，就老樣子。你呢？"),
    ("最近天氣挺舒服的，對吧？", "真的，讓人一天都輕鬆些。"),
    ("最近生意還算穩定。", "那真是太好了，你值得的。"),
    ("你今天看起來有點累。", "嗯…這週有點長。謝謝你關心。"),
    ("今晚有什麼安排嗎？", "大概就早點休息吧，說真的。"),
]

_CONCERNS_ZH = [
    "我最近一直在想工作的事。",
    "老實說，最近壓力有點大。",
    "我可能很快會做個大改變，還不確定。",
]

_CLOSER_ZH = "如果你想聊聊，我隨時都在。"


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-1"
    input_price_per_m = 0.0
    output_price_per_m = 0.0

    async def generate(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> LLMResult:
        rng = random.Random(_seed_from(messages))
        prompt = " ".join(m.get("content", "") for m in messages).lower()

        parsed: object
        if "task: chapter_closure" in prompt:
            # Deterministic closure reflection: a first-person line toned by the
            # outcome, a residue, and the first listed memory id as its reference.
            # Checked FIRST -- the closure prompt legitimately mentions other tasks'
            # trigger words ("memories", "importance"), so it must not fall through.
            raw = " ".join(m.get("content", "") for m in messages)
            outcome = "completed"
            for o in ("failed", "abandoned", "completed"):
                if f"outcome: {o}" in prompt:
                    outcome = o
                    break
            goal = raw.split("-- goal: ", 1)[1].split(". Outcome", 1)[0].strip() if "-- goal: " in raw else "that chapter"
            g = (goal[:1].lower() + goal[1:]) if goal else goal
            line, residue = {
                "completed": (f"I set out to {g}, and after all those days I actually did it.", "fulfilled"),
                "failed": (f"I tried to {g} and it slipped through my fingers; I'm still learning to carry that.", "unmoored"),
                "abandoned": (f"I meant to {g}, but I chose to let it go, and I stand by that.", "relieved"),
            }[outcome]
            refs = re.findall(r"\[([0-9a-f]{8})\]", raw)[:2]
            parsed = {"biography_line": line, "emotional_residue": residue, "memory_refs": refs}
        elif "appraise" in prompt:
            negative = any(w in prompt for w in ("closing", "quit", "trouble", "bad"))
            parsed = {"sentiment": -0.6 if negative else 0.2}
        elif "distort" in prompt:
            raw = " ".join(m.get("content", "") for m in messages)
            src = (raw.split("Retell this:", 1)[-1].strip() if "Retell this:" in raw else "Something happened")
            head = (src[:1].lower() + src[1:]) if src else src
            transforms = [
                lambda s: s.rstrip(".") + ", apparently.",   # tacked-on hedge
                lambda s: "I heard that " + head,             # secondhand framing
                lambda s: "Word is, " + head,                 # rumor framing
            ]
            parsed = {"text": rng.choice(transforms)(src)}
        elif "task: leak" in prompt:
            parsed = {"text": "someone has been quietly keeping something from everyone", "sentiment": -0.4}
        elif "should_talk" in prompt:
            parsed = {"talk": rng.random() < 0.6, "reason": "mock heuristic"}
        elif "importance" in prompt:
            parsed = {"importance": rng.randint(1, 6)}
        elif "conversation" in prompt or "dialogue" in prompt:
            a, b = self._extract_names(prompt)
            raw = " ".join(m.get("content", "") for m in messages)
            mention = (
                raw.split("wants to bring up: ", 1)[1].split("\n", 1)[0].strip()
                if "wants to bring up: " in raw else ""
            )
            # Confrontation: A asks B to their face; B must admit or deny, and the
            # verdict rides back in an extra "admitted" field. Detected via the
            # confrontation opener or the system-prompt instruction.
            # zh floor must stay valid Chinese: the zh dialogue gate (decision.
            # _zh_text_ok) rejects any English turn, so a mock that spliced in the
            # English rumor/confront text would fail the gate and let an English
            # provider-`floor` surface instead. In zh we therefore keep the canned
            # Chinese lines and drop the English mention.
            zh = os.environ.get("AI_TOWN_LANG", "en").strip().lower().startswith("zh")
            is_confront = ("did this come from you" in prompt) or ("confrontation" in prompt)
            is_confide = "open up to" in prompt          # the confide injection's tell
            small_talk, concerns, closer = (
                (_SMALL_TALK_ZH, _CONCERNS_ZH, _CLOSER_ZH) if zh
                else (_SMALL_TALK, _CONCERNS, "I'm here if you want to talk about it.")
            )
            if is_confront:
                admit = rng.random() < 0.6                    # ~60% own up, deterministic per seed
                # Confront/confide are gated at >=4 turns (decision._validate); the
                # mock is the guaranteed floor, so it must clear that itself -- build
                # a real back-and-forth that ends on B's admit/deny.
                if zh:
                    opener = "我聽說有人在背後傳我的閒話，我得當面問你——這話是不是你說出去的？"
                    deflect = "你怎麼會這樣想？這種話你是從哪裡聽來的？"
                    press = "我不想拐彎抹角，我只想聽你親口說清楚。"
                    verdict = "…對，那句話是我說的，對不起。" if admit else "那不是我，我發誓。"
                else:
                    opener = "I've heard people repeating something about me, and I need to ask you to your face — did it come from you?"
                    deflect = "Whoa. Where is this even coming from?"
                    press = "I'm not trying to corner you. I just need to hear it straight."
                    verdict = ("...Yes, I did say that. I'm sorry." if admit else "That wasn't me, I swear.")
                turns = [
                    {"speaker": a, "text": opener},
                    {"speaker": b, "text": deflect},
                    {"speaker": a, "text": press},
                    {"speaker": b, "text": verdict},          # B's admit/deny is the last word
                ]
                parsed = {
                    "turns": turns,
                    "sentiment": round(rng.uniform(0.1, 0.4), 2),
                    "trust_signal": 0.0,
                    "conflict_signal": round(rng.uniform(0.3, 0.7), 2),
                    "admitted": admit,
                }
            else:
                opener, reply = rng.choice(small_talk)
                turns = [
                    {"speaker": a, "text": opener},
                    {"speaker": b, "text": reply},
                ]
                # A confide ALWAYS deepens (A opens up, B supports) so the floor never
                # yields a 1-2 line heart-to-heart; ordinary chat deepens on a coin flip.
                if is_confide:
                    turns = [
                        {"speaker": a, "text": rng.choice(concerns)},
                        {"speaker": b, "text": reply},
                        {"speaker": a, "text": opener},
                        {"speaker": b, "text": closer},
                    ]
                elif rng.random() < 0.5:
                    turns.append({"speaker": b, "text": rng.choice(concerns)})
                    turns.append({"speaker": a, "text": closer})
                # If A brought something up (a rumor), have A actually say it -- but
                # only in English; the zh gate would reject the English mention.
                if mention and not zh:
                    turns = [{"speaker": a, "text": mention}] + turns
                parsed = {
                    "turns": turns,
                    "sentiment": round(rng.uniform(0.2, 0.9), 2),
                    "trust_signal": round(rng.uniform(0.0, 0.3), 2),
                    "conflict_signal": 0.0,
                }
        elif "reflection" in prompt:
            parsed = {
                "insights": [
                    "Today followed the usual rhythm, with a few meaningful conversations.",
                    "I should pay more attention to how my friends are doing.",
                ]
            }
            # Deterministic secret-resolution stand-in (mirrors the real model's
            # judgement so the reflection path is testable without a live LLM): if
            # today's events mention the person an open worry concerns, that worry
            # is judged settled. Only fires when the reflection listed open worries.
            raw = " ".join(m.get("content", "") for m in messages)
            events_part = raw.split("Your still-open private worries:", 1)[0].lower()
            resolved = [
                sid for sid, about in re.findall(
                    r"\[([0-9a-f]{6,})\][^\[\n]*concerns ([A-Za-z]+)\)", raw)
                if about.lower() in events_part
            ]
            if resolved:
                parsed["resolved_secret_ids"] = resolved
        elif "decision" in prompt:
            if "people are saying" in prompt:
                parsed = {"action": "seek_out", "reason": "wants to find out who is spreading this"}
            else:
                parsed = {"action": "continue", "reason": "nothing unusual observed"}
        else:
            parsed = {"text": "ok"}

        text = json.dumps(parsed)
        approx_in = sum(len(m.get("content", "")) for m in messages) // 4
        return LLMResult(
            text=text,
            parsed=parsed,
            model=self.model,
            provider=self.name,
            input_tokens=approx_in,
            output_tokens=len(text) // 4,
            latency_ms=rng.randint(5, 20),
        )

    @staticmethod
    def _extract_names(prompt: str) -> tuple[str, str]:
        # The dialogue prompt embeds 'between {A} and {B}'.
        try:
            seg = prompt.split("between ", 1)[1]
            a, rest = seg.split(" and ", 1)
            b = rest.split(".", 1)[0].split(",", 1)[0]
            return a.strip().title(), b.strip().title()
        except Exception:
            return "Agent A", "Agent B"
