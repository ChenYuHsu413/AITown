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

from .base import LLMProvider, LLMResult


def _seed_from(messages: list[dict]) -> int:
    blob = "|".join(m.get("content", "") for m in messages)
    return int(hashlib.sha256(blob.encode()).hexdigest()[:8], 16)


_SMALL_TALK = [
    ("How has your day been so far?", "Pretty good, just the usual. And you?"),
    ("Did you hear about the new mural near the park?", "No! I should go take a look."),
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
    ("你聽說公園那幅新壁畫了嗎？", "還沒！我該去看看。"),
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
        if "appraise" in prompt:
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
            if is_confront:
                admit = rng.random() < 0.6                    # ~60% own up, deterministic per seed
                if zh:
                    verdict = "…對，那句話是我說的，對不起。" if admit else "那不是我，我發誓。"
                    opener = "我聽說有人在傳我的閒話，是你說的嗎？"
                else:
                    verdict = (
                        "...Yes, I did say that. I'm sorry."
                        if admit else "That wasn't me, I swear."
                    )
                    opener = mention or "I heard people are saying something. Did this come from you?"
                turns = [
                    {"speaker": a, "text": opener},
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
                small_talk, concerns, closer = (
                    (_SMALL_TALK_ZH, _CONCERNS_ZH, _CLOSER_ZH) if zh
                    else (_SMALL_TALK, _CONCERNS, "I'm here if you want to talk about it.")
                )
                opener, reply = rng.choice(small_talk)
                turns = [
                    {"speaker": a, "text": opener},
                    {"speaker": b, "text": reply},
                ]
                if rng.random() < 0.5:
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
