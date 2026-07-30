"""MockProvider -- deterministic, zero-cost stand-in for a real LLM.

Lets the whole simulation run end-to-end with no API key.
Responses are seeded by the prompt content so runs are reproducible
but not completely uniform.
"""

from __future__ import annotations

import hashlib
import json
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
        elif "should_talk" in prompt:
            parsed = {"talk": rng.random() < 0.6, "reason": "mock heuristic"}
        elif "importance" in prompt:
            parsed = {"importance": rng.randint(1, 6)}
        elif "conversation" in prompt or "dialogue" in prompt:
            a, b = self._extract_names(prompt)
            opener, reply = rng.choice(_SMALL_TALK)
            turns = [
                {"speaker": a, "text": opener},
                {"speaker": b, "text": reply},
            ]
            if rng.random() < 0.5:
                turns.append({"speaker": b, "text": rng.choice(_CONCERNS)})
                turns.append({"speaker": a, "text": "I'm here if you want to talk about it."})
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
