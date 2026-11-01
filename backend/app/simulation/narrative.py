"""The narrative data layer: what each day *meant*, recorded as data.

Three things, all rule-layer (no LLM -- a daily digest is the daily path, and the
daily path never gains a model call), all starting at Day 1 of the run that has
them (nothing is backfilled):

  * **Event tiers.** Every published event is stamped ``major`` / ``notable`` /
    ``ambient`` from its verb (plus one fact the verb can't carry: whether a talk
    is the pair's first). See ``MAJOR`` / ``NOTABLE`` for the table.
  * **Daily digest.** At each daily settlement the day's major/notable beats become
    one sentence, written in the town's language from templates (``_PHRASES``); a
    day with nothing in it still gets a plain line. Names stay ``{agent:id}``
    placeholders, as in memories, for the display layer to resolve. Private things
    stay private: a wish is "something new", never its words.
  * **Relationship deltas.** At the same settlement every directed pair whose
    friendship / trust / conflict moved records where it now stands and by how much
    -- the history a relationship curve is drawn from.

The day's tiered beats, the relationship baseline and the pairs that have already
talked ride in the snapshot, so a restart mid-day neither loses nor repeats any.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..agents import texts

if TYPE_CHECKING:
    from .engine import Event, SimulationEngine

# ---- tiers -----------------------------------------------------------------------
# major: a wish born or ended, a chapter turning, a confrontation, a confession.
MAJOR = frozenset({
    "wish_born", "wish_seeded", "wish_closed",
    "chapter_closed", "chapter_started",
    "confronted",
    "romance_dating", "romance_rejected",
})
# notable: a setback, a relationship changing stage, a secret told, a first
# conversation -- plus the two world-sized beats the spec's list leaves unnamed (a
# life change, a finished landmark), which belong in a day's story more than in its
# background.
NOTABLE = frozenset({
    "wish_frustrated",
    "milestone", "romance_partners",
    "confide", "leak",
    "transition", "landmark_done",
    "stormed_off", "promise_made", "promise_missed", "skipped_work",
})
TALK_FIRST = "talk_start"           # notable only on a pair's first conversation of the run

REL_AXES = ("friendship", "trust", "conflict")
REL_DEFAULT = (30.0, 30.0, 0.0)     # a fresh Relationship (see agents/agent.py)
REL_MIN_DELTA = 0.5                 # smaller moves are rounding, not history
DIGEST_MAX_BEATS = 4                # a digest is one sentence, not a log


def _pair(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


# ---- digest phrases --------------------------------------------------------------
# (English, Traditional Chinese); fields a / b are {agent:id} placeholders.
_PHRASES: dict[str, tuple[str, str]] = {
    "wish_born": ("{a} quietly came to want something new", "{a}心裡悄悄有了一個新的願望"),
    "wish_seeded": ("{a} quietly came to want something new", "{a}心裡悄悄有了一個新的願望"),
    "wish_closed:completed": ("{a} saw something they had set their heart on through",
                              "{a}完成了一件放在心上的事"),
    "wish_closed:failed": ("something {a} had hoped for did not come to pass", "{a}的一個心願沒能實現"),
    "wish_closed:abandoned": ("{a} let go of something they had wanted", "{a}放下了一個心願"),
    "chapter_closed": ("a chapter of {a}'s life came to a close", "{a}的人生翻過了一頁"),
    "chapter_started": ("{a} began a new chapter", "{a}開始了新的篇章"),
    "confronted": ("{a} confronted {b} face to face", "{a}當面質問了{b}"),
    "romance_dating": ("{a} confessed to {b}, and they are together now", "{a}向{b}告白,兩人在一起了"),
    "romance_rejected": ("{a} confessed to {b} and was gently turned down", "{a}向{b}告白,卻被婉拒了"),
    "romance_partners": ("{a} and {b} decided to make it permanent", "{a}和{b}決定長長久久"),
    "milestone:up": ("{a} and {b} grew closer", "{a}和{b}的關係更近了一步"),
    "milestone:distant": ("{a} and {b} drifted apart", "{a}和{b}漸漸疏遠了"),
    "confide": ("{a} opened up to {b}", "{a}向{b}吐露了心事"),
    "leak": ("{a} let a secret about {b} slip", "{a}把{b}的秘密說了出去"),
    "talk_start:first": ("{a} and {b} talked for the first time", "{a}和{b}第一次聊天"),
    "wish_frustrated": ("something {a} has been quietly working toward stalled again",
                        "{a}默默努力的事又卡住了"),
    "transition": ("{a} made a big change in their life", "{a}做了人生中的重大改變"),
    "landmark_done": ("{a} finished their work at {loc}", "{a}完成了{loc}的作品"),
    "stormed_off": ("{a} walked away from {b} mid-conversation", "{a}和{b}不歡而散"),
    "promise_made": ("{a} and {b} promised to meet", "{a}和{b}約好了要見面"),
    "promise_missed": ("{a} and {b} had promised to meet, but it never happened", "{a}和{b}說好的見面落空了"),
    "skipped_work": ("{a} didn't go to work", "{a}今天沒去上班"),
}
_QUIET = (
    ("A quiet day; the town went about its usual rhythm.", "平靜的一天,小鎮照著平常的步調過。"),
    ("Nothing much happened today.", "今天沒有什麼大事發生。"),
    ("The day passed peacefully.", "日子安安靜靜地過去了。"),
)


def _say(pair: tuple[str, str], **kw) -> str:
    return (pair[1] if texts.zh_native() else pair[0]).format(**kw)


class Narrative:
    """Owned by the engine. ``tier_for`` stamps an event as it is published,
    ``observe`` keeps the day's tiered beats, ``settle`` closes the day."""

    def __init__(self, engine: "SimulationEngine"):
        self.engine = engine
        self.day_beats: list[dict] = []          # today's major/notable beats
        self.talked: set[str] = set()            # pairs that have had a conversation this run
        self.rel_baseline: dict[str, list[float]] = {}
        self.digests: list[dict] = []            # every closed day (also persisted)
        self.recent_deltas: list[dict] = []      # the last settlement's deltas (in-memory view)
        self.on_digest: Callable[[dict], None] | None = None
        self.on_rel_deltas: Callable[[list], None] | None = None
        self.rebaseline()
        # Pairs who already know each other when the run opens (a seeded friendship)
        # are not strangers: their first talk of the run is not a "first meeting".
        self.talked = {_pair(a.id, oid) for a in engine.world.agents.values() for oid in a.relationships}

    # ---- relationships -----------------------------------------------------
    def _rel_values(self) -> dict[str, list[float]]:
        out = {}
        for a in self.engine.world.agents.values():
            for oid, r in a.relationships.items():
                out[f"{a.id}>{oid}"] = [round(getattr(r, k), 1) for k in REL_AXES]
        return out

    def rebaseline(self) -> None:
        """Measure from where things stand now (a fresh run, or a snapshot that
        predates this layer)."""
        self.rel_baseline = self._rel_values()

    # ---- tiers -------------------------------------------------------------
    def tier_for(self, ev: "Event") -> str:
        if ev.verb in MAJOR:
            return "major"
        if ev.verb in NOTABLE:
            return "notable"
        if ev.verb == TALK_FIRST and ev.actor and ev.target:
            key = _pair(ev.actor, ev.target)
            if key not in self.talked:
                self.talked.add(key)
                return "notable"
        return "ambient"

    def observe(self, ev: "Event") -> None:
        if ev.tier == "ambient":
            return
        self.day_beats.append({"minute": ev.minute, "verb": ev.verb, "tier": ev.tier,
                               "actor": ev.actor, "target": ev.target,
                               "location": ev.location, "text": ev.text,
                               "first": ev.verb == TALK_FIRST})

    # ---- the day's close ---------------------------------------------------
    def _emit_frustrations(self, day: int) -> None:
        """A wish's setback is written as a private memory (wishes.note_blocked);
        surface each one of today's as a content-free beat, at its own minute."""
        lo, hi = (day - 1) * 1440, day * 1440
        marks = {texts._CATALOGUE["wish_frustration"][0], texts._CATALOGUE["wish_frustration"][1]}
        for a in self.engine.world.agents.values():
            for m in reversed(a.memory.items):
                if m.minute < lo:
                    break
                if m.minute < hi and m.text in marks and any(t.startswith("goal:") for t in (m.tags or [])):
                    self.engine._publish("system", "wish_frustrated", actor=a,
                                         location_id=a.state.location, minute=m.minute)

    def _phrase(self, b: dict) -> str | None:
        a = f"{{agent:{b['actor']}}}" if b["actor"] else ""
        t = f"{{agent:{b['target']}}}" if b["target"] else ""
        verb = b["verb"]
        if verb == "wish_closed":
            key = f"wish_closed:{b['text'] if b['text'] in ('failed', 'abandoned') else 'completed'}"
        elif verb == "milestone":
            key = "milestone:distant" if b["text"] == "distant" else "milestone:up"
        elif verb == TALK_FIRST:
            key = "talk_start:first"
        else:
            key = verb
        pair = _PHRASES.get(key)
        if pair is None or (("{b}" in pair[1]) and not t):
            return None
        return _say(pair, a=a, b=t, loc=f"{{loc:{b['location']}}}")

    def digest_text(self, day: int, beats: list[dict]) -> str:
        # A chapter that closes opens its interlude in the same breath: one turn of
        # the page, told once.
        closed = {b["actor"] for b in beats if b["verb"] == "chapter_closed"}
        picked = [b for b in beats if not (b["verb"] == "chapter_started" and b["actor"] in closed)]
        picked.sort(key=lambda b: (b["tier"] != "major", b["minute"]))
        phrases, seen = [], set()
        for b in picked:
            p = self._phrase(b)
            if p and p not in seen:
                seen.add(p)
                phrases.append(p)
            if len(phrases) >= DIGEST_MAX_BEATS:
                break
        if not phrases:
            return _say(_QUIET[day % len(_QUIET)])
        if texts.zh_native():
            return ";".join(phrases) + "。"
        s = "; ".join(phrases)
        return s[:1].upper() + s[1:] + "."

    def settle(self, day: int) -> dict:
        """Close ``day`` (1-based): its digest and its relationship deltas."""
        self._emit_frustrations(day)
        hi = day * 1440                        # a beat stamped at/after midnight is tomorrow's
        beats = sorted((b for b in self.day_beats if b["minute"] < hi), key=lambda b: b["minute"])
        digest = {"day": day, "text": self.digest_text(day, beats),
                  "refs": [{k: b[k] for k in ("minute", "verb", "tier", "actor", "target")}
                           for b in beats]}
        self.digests.append(digest)
        self.day_beats = [b for b in self.day_beats if b["minute"] >= hi]

        now = self._rel_values()
        deltas = []
        for key, vals in now.items():
            before = self.rel_baseline.get(key, list(REL_DEFAULT))
            d = [round(v - b, 1) for v, b in zip(vals, before)]
            if any(abs(x) >= REL_MIN_DELTA for x in d):
                a, b = key.split(">", 1)
                deltas.append({"day": day, "a": a, "b": b,
                               **dict(zip(REL_AXES, vals)),
                               **{f"d_{k}": x for k, x in zip(REL_AXES, d)}})
        self.rel_baseline = now
        self.recent_deltas = deltas
        for hook, arg in ((self.on_digest, digest), (self.on_rel_deltas, deltas)):
            if hook is not None:
                try:
                    hook(arg)
                except Exception as err:   # the record must never break the day's close
                    print(f"[narrative] write failed: {err!r}", flush=True)
        return digest

    # ---- snapshot ----------------------------------------------------------
    def capture(self) -> dict:
        return {"day_beats": [dict(b) for b in self.day_beats], "talked": sorted(self.talked),
                "rel_baseline": {k: list(v) for k, v in self.rel_baseline.items()},
                "digests": [dict(d) for d in self.digests[-60:]]}

    def restore(self, data: dict) -> None:
        self.day_beats = [dict(b) for b in data.get("day_beats") or [] if isinstance(b, dict)]
        self.talked = {str(x) for x in data.get("talked") or []}
        base = data.get("rel_baseline")
        if isinstance(base, dict):
            self.rel_baseline = {str(k): [float(x) for x in v] for k, v in base.items()}
        else:
            self.rebaseline()
        self.digests = [dict(d) for d in data.get("digests") or [] if isinstance(d, dict)]
