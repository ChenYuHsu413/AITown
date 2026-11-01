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

from ...pacing import SPAN_DAYS
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

# Extra lines for a turn-by-turn exchange that runs past the canned four.
_FILLERS = ["Mm, I know what you mean.", "Thanks for hearing me out.", "Let's talk again soon."]
_FILLERS_ZH = ["嗯，我懂你的意思。", "謝謝你願意聽我說。", "改天再好好聊。"]


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
        # A zh-native town gets Chinese knowledge from its floor too. Same branches,
        # same dice -- only the words change -- so a mock run makes the same calls in
        # both languages.
        zh = os.environ.get("AI_TOWN_LANG", "en").strip().lower().startswith("zh")

        parsed: object
        if "task: wish_generation" in prompt:
            # Deterministic stand-in for a grown wish. Checked FIRST, before the
            # dialogue branch, because the wish prompt legitimately contains the word
            # "conversations". Reads the rule-assembled lists back out of the prompt
            # so the proposal is feasible AND deviates -- and offers two shapes, so
            # the novelty gate has something to actually reject.
            raw = " ".join(m.get("content", "") for m in messages)
            def _after(label: str) -> list:
                if label not in raw:
                    return []
                tail = raw.split(label, 1)[1].split("\n", 1)[0]
                return [x.strip() for x in tail.split(",") if x.strip() and "(" not in x]
            unvisited = _after("Places their routine never takes them:")
            strangers = _after("People they never regularly cross paths with:")
            ids = re.findall(r"\[([0-9a-f]{8})\]", raw)
            scales = "major" if "major" in raw.split("may propose a wish of scale:", 1)[-1][:40] else "minor"
            if not ids:
                parsed = {"no_wish": True, "reason": "no material to build on"}
            elif rng.random() < 0.5 and unvisited:
                place = unvisited[0]
                text = ({"title": "一個我從不去的地方",
                         "statement": f"我想開始真正花時間待在{{loc:{place}}},那是我的日子從來不會帶我去的地方。",
                         "motivation": "每週都是同樣幾個地方,開始讓我覺得日子變窄了。",
                         "narrative": "我正悄悄試著讓自己去一個新的地方。"} if zh else
                        {"title": f"Somewhere I never go",
                         "statement": f"I want to start spending real time at {place}, "
                                      f"somewhere my days never take me.",
                         "motivation": "The same rooms every week have started to feel small.",
                         "narrative": "I am quietly trying to put myself somewhere new."})
                parsed = {**text, "scale": scales,
                          "expires_in_days": SPAN_DAYS[scales], "provenance": ids[:2],
                          "requirements": [{"kind": "location_visits", "target": place, "threshold": 3}]}
            elif strangers:
                who = strangers[0].lower()
                text = ({"title": "一個我幾乎不認識的人",
                         "statement": f"我想真正認識{{agent:{who}}},我們不知怎麼的從來沒好好說過話。",
                         "motivation": "我們在同一個小鎮住了這麼久,這件事讓我耿耿於懷。",
                         "narrative": "我正悄悄試著去認識一個我從不交談的人。"} if zh else
                        {"title": "Someone I barely know",
                         "statement": f"I want to actually know {who.capitalize()}, "
                                      f"who I have somehow never really spoken to.",
                         "motivation": "It bothers me that we have shared a town this long.",
                         "narrative": "I am quietly trying to get to know someone I never talk to."})
                parsed = {**text, "scale": scales,
                          "expires_in_days": SPAN_DAYS[scales], "provenance": ids[:2],
                          "requirements": [{"kind": "talk_count", "target": who, "threshold": 3}]}
            else:
                parsed = {"no_wish": True, "reason": "nothing outside the routine to reach for"}
        elif "task: chapter_closure" in prompt:
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
            line, residue = ({
                "completed": (f"我曾下定決心要{re.sub(r'^我(?:想要|想|要|希望)', '', goal).rstrip('。')},這麼多天過去,我真的做到了。", "fulfilled"),
                "failed": (f"我試著{re.sub(r'^我(?:想要|想|要|希望)', '', goal).rstrip('。')},卻還是從指縫間溜走了;我仍在學著接受。", "unmoored"),
                "abandoned": (f"我原本想{re.sub(r'^我(?:想要|想|要|希望)', '', goal).rstrip('。')},但我選擇放下,我不後悔。", "relieved"),
            } if zh else {
                "completed": (f"I set out to {g}, and after all those days I actually did it.", "fulfilled"),
                "failed": (f"I tried to {g} and it slipped through my fingers; I'm still learning to carry that.", "unmoored"),
                "abandoned": (f"I meant to {g}, but I chose to let it go, and I stand by that.", "relieved"),
            })[outcome]
            refs = re.findall(r"\[([0-9a-f]{8})\]", raw)[:2]
            parsed = {"biography_line": line, "emotional_residue": residue, "memory_refs": refs}
        elif "task: day_plan" in prompt:
            # The floor wakes up to an ordinary day: no changes, so a mock run's
            # timetable is exactly the routine (tests drive real plans directly).
            parsed = {"intent": "今天就照平常的步調過。" if zh else "Just an ordinary day, at my usual pace.",
                      "nothing_special": True, "skip_work": 0.0, "slots": []}
        elif "task: dialogue_turn" in prompt:
            parsed = self._dialogue_turn(" ".join(m.get("content", "") for m in messages), rng, zh)
        elif "appraise" in prompt:
            negative = any(w in prompt for w in ("closing", "quit", "trouble", "bad"))
            parsed = {"sentiment": -0.6 if negative else 0.2}
        elif "distort" in prompt:
            raw = " ".join(m.get("content", "") for m in messages)
            src = (raw.split("Retell this:", 1)[-1].strip() if "Retell this:" in raw else "Something happened")
            head = (src[:1].lower() + src[1:]) if src else src
            transforms = [
                lambda s: s.rstrip("。") + ",聽說是這樣。",   # tacked-on hedge
                lambda s: "我聽說" + s,                        # secondhand framing
                lambda s: "大家都說," + s,                     # rumor framing
            ] if zh else [
                lambda s: s.rstrip(".") + ", apparently.",   # tacked-on hedge
                lambda s: "I heard that " + head,             # secondhand framing
                lambda s: "Word is, " + head,                 # rumor framing
            ]
            parsed = {"text": rng.choice(transforms)(src)}
        elif "task: leak" in prompt:
            parsed = {"text": "有人一直悄悄瞞著大家一件事" if zh
                      else "someone has been quietly keeping something from everyone", "sentiment": -0.4}
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
                    "今天照著平常的步調過,有幾段有意義的對話。",
                    "我應該多留意朋友們過得好不好。",
                ] if zh else [
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
            if "people are saying" in prompt or "大家在傳" in prompt or "大家都在傳" in prompt:
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
    def _dialogue_turn(raw: str, rng: random.Random, zh: bool) -> dict:
        """One line of a turn-by-turn conversation. The exchange's shape (length,
        canned pair, a confrontation's verdict) is fixed per PAIR of speakers, so
        the lines across separate calls fit together; only signals vary per line."""
        m = re.search(r"You voice ONE character, (.+?), in a face-to-face conversation with (.+?) in a life", raw)
        me, other = m.groups() if m else ("A", "B")
        ln = re.search(r"This is line (\d+) of at most (\d+)\.", raw)
        n, mx = (int(ln.group(1)), int(ln.group(2))) if ln else (1, 4)
        mn = re.search(r"don't end before line (\d+)", raw)
        min_turns = int(mn.group(1)) if mn else 2
        h = int(hashlib.sha256("|".join(sorted((me, other))).encode()).hexdigest()[:8], 16)
        pair_rng = random.Random(h)
        length = min(mx, max(min_turns, 2 + h % 3))
        small_talk, concerns, closer, fillers = (
            (_SMALL_TALK_ZH, _CONCERNS_ZH, _CLOSER_ZH, _FILLERS_ZH) if zh
            else (_SMALL_TALK, _CONCERNS, "I'm here if you want to talk about it.", _FILLERS))
        opener, reply = pair_rng.choice(small_talk)
        concern = pair_rng.choice(concerns)
        admit = pair_rng.random() < 0.6
        accused = '"admitted": true|false' in raw
        out: dict = {}
        if accused or "You came to ask" in raw:
            if zh:
                seq = ["我聽說有人在背後傳我的閒話，我得當面問你——這話是不是你說出去的？",
                       "你怎麼會這樣想？這種話你是從哪裡聽來的？",
                       "我不想拐彎抹角，我只想聽你親口說清楚。",
                       "…對，那句話是我說的，對不起。" if admit else "那不是我，我發誓。"]
            else:
                seq = ["I've heard people repeating something about me, and I need to ask you to your face — did it come from you?",
                       "Whoa. Where is this even coming from?",
                       "I'm not trying to corner you. I just need to hear it straight.",
                       "...Yes, I did say that. I'm sorry." if admit else "That wasn't me, I swear."]
            if accused:
                out["admitted"] = admit
            conflict = round(rng.uniform(0.3, 0.7), 2)
        elif "You have decided to open up" in raw:
            # Only the confider's own lines (1, 3) carry the marker; the listener's
            # (2, 4) are the same in both sequences, so the exchange stays coherent.
            seq = [concern, reply, opener, closer]
            conflict = 0.0
        else:
            seq = [opener, reply, concern, closer]
            conflict = 0.0
        text = seq[n - 1] if n <= len(seq) else fillers[(n - len(seq) - 1) % len(fillers)]
        out.update({"text": text, "end": n >= length,
                    "sentiment": round(rng.uniform(0.2, 0.9), 2) if not conflict else round(rng.uniform(0.1, 0.4), 2),
                    "trust_signal": round(rng.uniform(0.0, 0.3), 2) if not conflict else 0.0,
                    "conflict_signal": conflict, "tells": "", "plan": None, "storm_off": False})
        return out

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
