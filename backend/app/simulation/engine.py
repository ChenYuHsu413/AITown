"""Simulation Engine -- the heart of the system. No LLM dependency here.

Key design: EVENT-DRIVEN scheduling. We never loop "every 10s, every agent
thinks". Instead each agent has a `next_decision_at`; the engine pops the
earliest one from a priority queue and jumps the clock straight there.
Sleeping agents cost zero compute for eight sim-hours.

Interrupts: when an agent arrives somewhere, co-located agents get an
immediate decision opportunity -- that's how spontaneous conversations
happen without polling.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import os
import random
from dataclasses import dataclass, field
from typing import Callable

from ..agents import chapters as chapters_mod
from ..agents import wishes as wishes_mod
from ..agents.agent import Agent
from ..agents.core import MemoryItem
from ..agents.decision import ConvPlan, DecisionEngine
from ..llm.prompts import builders
from ..llm.router import ProvidersExhausted
from ..world.world import World

DAY_MIN = 24 * 60

# ---- non-blocking dialogue / reflection ------------------------------------
# A conversation's slow LLM generation runs in a background task so the town
# keeps moving; only the two participants freeze. These bound that.
DIALOGUE_MAX_CONCURRENT = 3   # in-flight dialogue generations (the design's cap); excess queue
REFLECT_MAX_CONCURRENT = 4    # in-flight reflections (smart-tier); a soft burst limit
# Outer BACKSTOP on the whole dialogue generation. The router now bounds each
# provider individually (DIALOGUE_PROVIDER_TIMEOUT_S) and falls through to the fast
# fallback, so this only fires if the entire chain is pathologically slow -- keep it
# generous so it never pre-empts a fallback that would have answered (the too-tight
# 20s value here used to guillotine DeepSeek's normal tail straight to the mock floor).
try:
    DIALOGUE_TIMEOUT_S = float(os.environ.get("AI_TOWN_DIALOGUE_TIMEOUT", "90") or "90")
except ValueError:
    DIALOGUE_TIMEOUT_S = 90.0
# Patient retry (the "rather slow than canned" policy). When the whole live chain
# (deepseek -> gemini -> gemma) fails the zh gate, we do NOT drop to the canned mock:
# the two speakers stay "brewing" (busy) for a short pause, then the whole chain is
# re-run. With 2 extra rounds that is 3 whole-chain attempts (9 provider tries) before
# the mock floor is the last resort. The non-blocking engine means only these two
# freeze; the town keeps moving.
#
# Brew = 30s (not 20s): the only floors seen in acceptance were 429-saturation bursts
# where the whole chain was cooling at once. A provider's 429 cooldown is >= 30s
# (Retry-After, clamped to [10, 90]); a brew shorter than that re-runs into the same
# cooldown and can't recover. 30s lets a typical cooldown lapse so the retry actually
# rescues the turn -- which is the whole point. Env-overridable.
try:
    DIALOGUE_RETRY_ROUNDS = int(os.environ.get("AI_TOWN_DIALOGUE_RETRY_ROUNDS", "2"))
except ValueError:
    DIALOGUE_RETRY_ROUNDS = 2
try:
    DIALOGUE_RETRY_WAIT_S = float(os.environ.get("AI_TOWN_DIALOGUE_RETRY_WAIT", "30") or "30")
except ValueError:
    DIALOGUE_RETRY_WAIT_S = 30.0
REFLECT_TIMEOUT_S = 45.0       # wall-clock: a reflection past this is abandoned (retried, see below)
# Reflection patient retry (task 3, mirrors dialogue): belief/secret/life_decision
# quality must never be a canned mock line, so a whole-chain failure brews a pause and
# re-runs the chain. If every round is still exhausted, the reflection is simply SKIPPED
# this round (no mock floor) -- unlike a dialogue, a missed reflection just happens again
# once weight re-accumulates, so "no reflection" beats "a canned one".
try:
    REFLECT_RETRY_ROUNDS = int(os.environ.get("AI_TOWN_REFLECT_RETRY_ROUNDS", "2"))
except ValueError:
    REFLECT_RETRY_ROUNDS = 2
try:
    REFLECT_RETRY_WAIT_S = float(os.environ.get("AI_TOWN_REFLECT_RETRY_WAIT", "20") or "20")
except ValueError:
    REFLECT_RETRY_WAIT_S = 20.0
# Sim-minutes the two parties are held while the exchange generates. Also the
# self-heal window on resume: a snapshot taken mid-conversation restores the pair
# as busy_until = init + this, so they free themselves within it after a restart
# (the in-flight task is gone; see the design's snapshot/resume note).
DIALOGUE_LOCK_MIN = 30
# In-process backstop only: _finish_dialogue always unlocks (it runs in a finally),
# so this fires solely if that machinery itself wedges. Must exceed the max sim-time
# a legitimate generation can span at the TOP speed, or it would guillotine a still-
# generating conversation: the outer backstop is 90s wall, and at 20x that is 1800
# sim-min, so keep this comfortably above that. (The finally is the real guarantee;
# this is pure paranoia for the case where it somehow doesn't run.)
WATCHDOG_STUCK_MIN = 3000

# Day 1 = Monday. day_of_week 0=Mon .. 6=Sun.
_DOW_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def fmt_time(minute: int) -> str:
    day = minute // DAY_MIN + 1
    dow = (day - 1) % 7
    m = minute % DAY_MIN
    names = _DOW_ZH if builders.lang_is_zh() else _DOW_EN
    return f"Day {day} · {names[dow]} {m // 60:02d}:{m % 60:02d}"


@dataclass
class Event:
    """Structured simulation event.

    Machine-readable fields (verb/actor/target/location) are the source of
    truth -- the DB and every UI language render from these. ``text`` is a
    prerendered English fallback kept for backward compatibility with older
    frontends; treat it as deprecated.
    """

    minute: int
    kind: str                 # action | dialogue | reflection | system
    verb: str                 # sleep|eat|work|rest|idle|arrive|talk_start|say|insight
    actor: str = ""           # agent_id
    actor_name: str = ""      # denormalized for convenience
    target: str = ""          # agent_id (e.g. talk partner)
    target_name: str = ""
    location: str = ""        # location_id
    location_name: str = ""
    text: str = ""            # generated free text ONLY (dialogue line / insight)
    text_en: str = ""         # deprecated: prerendered English sentence
    detail: str = ""          # optional richer context for the chronicle (secret text, rumor wording, ...)

    def render(self) -> str:
        return f"[{fmt_time(self.minute)}] {self.text_en}"


_EN_TEMPLATES = {
    "sleep": "{actor} went to sleep at {loc}",
    "eat": "{actor} is eating at {loc}",
    "work": "{actor} is working at {loc}",
    "rest": "{actor} is resting at {loc}",
    "idle": "{actor} is idling at {loc}",
    "move": "{actor} is heading out",
    "arrive": "{actor} → {loc}",
    "talk_start": "{actor} started talking with {target} at {loc}",
    "share_rumor": "{actor} shared a rumor with {target}: {text}",
    "seek_out": "{actor} went looking for {target}",
    "landmark_done": "{actor} finished {text} at {loc}",
    "confronted": "{actor} confronted {target} about the rumor — they {text}",
    "confide": "{actor} confided something personal to {target}",
    "belief": "{actor} formed an impression of {target}: {text}",
    "day_summary": "{actor}'s day closed — {text}",
    "broke": "{actor} couldn't afford a meal at {loc}",
    "rain_start": "It started raining",
    "rain_end": "The rain stopped",
    "festival_start": "A festival began at {loc}",
    "festival_end": "The festival at {loc} ended",
    "say": "💬 {actor}: {text}",
    "insight": "💭 {actor}: {text}",
    "leak": "{actor} let slip a secret about {target}",
    "secret_born": "{actor} started quietly keeping something to themselves",
    "week_close": "The week's books were settled",
    "transition": "{actor}'s life took a turn: {text}",
    "milestone": "{actor} and {target} — {text}",
    "romance_dating": "{actor} and {target} are together now",
    "romance_rejected": "{actor} confessed to {target}, but it wasn't mutual",
    "romance_partners": "{actor} and {target} made it official",
    "breakdown": "Something broke down at {loc}",
    "repaired": "{actor} repaired the equipment at {loc}",
    "meetup_arranged": "{actor} and {target} arranged to meet at {loc}",
    "meetup_declined": "{actor} asked {target} to meet, but they passed",
    "met_up": "{actor} and {target} met up as planned at {loc}",
    "chapter_closed": "{actor} closed a chapter of their life — {text}",
    "chapter_started": "{actor} began a new chapter: {text}",
    # Wish beats are deliberately content-free: the intention itself is private.
    "wish_seeded": "{actor} quietly set their mind on something",
    "wish_born": "{actor} found something they want",
    "wish_closed": "{actor} settled something they had been carrying — {text}",
}

# The town's living history: the notable beats worth remembering (see Sim.chronicle
# / the frontend Chronicle). Confide/confront/landmark/belief/broke/world-events are
# ordinary bus events; leak/secret_born/week_close are published just for this.
CHRONICLE_VERBS = {
    "confide", "confronted", "landmark_done", "belief", "broke",
    "rain_start", "rain_end", "festival_start", "festival_end",
    "leak", "secret_born", "week_close", "transition", "milestone",
    "romance_dating", "romance_rejected", "romance_partners", "breakdown", "repaired",
    "met_up", "chapter_closed", "chapter_started",
}
CHRONICLE_ICONS = {
    "confide": "🤫", "confronted": "⚖️", "landmark_done": "🎨", "belief": "💭",
    "broke": "💸", "rain_start": "🌧️", "rain_end": "🌤️", "festival_start": "🎉",
    "festival_end": "🎏", "leak": "🕳️", "secret_born": "🔒", "week_close": "📅",
    "transition": "🔀", "milestone": "🤝",
    "romance_dating": "💕", "romance_rejected": "💔", "romance_partners": "💍",
    "breakdown": "🛠️", "repaired": "🔧", "met_up": "🫂",
    "chapter_closed": "📖", "chapter_started": "📗",
}

# Chapter closure: wall-clock bound on the one smart-tier call; past it (or on a
# whole-chain failure) the rule-layer template line is used -- closure never blocks.
CLOSURE_TIMEOUT_S = 60.0

# Public stand-in for a wish-linked chapter's private title (see agents/wishes.py).
PRIVATE_CHAPTER_TITLE = "a private chapter"


def render_en(verb: str, actor: str, target: str, loc: str, text: str) -> str:
    tpl = _EN_TEMPLATES.get(verb, "{actor} {verb} at {loc}")
    return tpl.format(actor=actor, target=target, loc=loc, text=text, verb=verb)


class EventBus:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.subscribers: list[Callable[[Event], None]] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)
        for fn in self.subscribers:
            fn(event)


@dataclass(order=True)
class _ScheduledDecision:
    minute: int
    seq: int
    agent_id: str = field(compare=False)


class Scheduler:
    """Priority queue of (next_decision_at, agent)."""

    def __init__(self) -> None:
        self._heap: list[_ScheduledDecision] = []
        self._seq = 0
        self._pending: dict[str, int] = {}  # agent -> earliest scheduled minute

    def schedule(self, agent_id: str, minute: int) -> None:
        # Skip if an equal-or-earlier decision is already queued.
        if self._pending.get(agent_id, 10**12) <= minute:
            return
        self._seq += 1
        heapq.heappush(self._heap, _ScheduledDecision(minute, self._seq, agent_id))
        self._pending[agent_id] = minute

    def pop_next(self) -> _ScheduledDecision | None:
        while self._heap:
            item = heapq.heappop(self._heap)
            # Drop stale entries (agent was rescheduled earlier).
            if self._pending.get(item.agent_id) == item.minute:
                self._pending.pop(item.agent_id, None)
                return item
        return None

    def peek_minute(self) -> int | None:
        return self._heap[0].minute if self._heap else None


class SimulationEngine:
    def __init__(self, world: World, decision_engine: DecisionEngine):
        self.world = world
        self.decisions = decision_engine
        # Publish the cast to the prompt builders so every free-text prompt can
        # forbid inventing names (Lengyue must never become a hallucinated name).
        from ..llm.prompts import builders
        builders.set_roster([(a.id.capitalize(), a.name, a.profile.gender) for a in world.agents.values()])
        # Places too, so zh dialogue speaks "潮汐咖啡館" rather than "Tide Cafe".
        builders.set_places([(l.name, l.name_zh) for l in world.locations.values() if l.name_zh])
        self.scheduler = Scheduler()
        self.bus = EventBus()
        # The town's rolling chronicle of notable beats (last 200), fed straight off
        # the bus. Persisted in the snapshot; served at /api/chronicle.
        self.chronicle: list[dict] = []
        self.bus.subscribers.append(self._chronicle_add)
        self.now = 0
        self._last_decision_at: dict[str, int] = {}
        self._last_day = 0
        # Fired (zero-arg) after each daily settlement so the host can persist a
        # snapshot; stays None -- and thus a no-op -- for headless/no-DB runs.
        self.on_snapshot: Callable[[], None] | None = None
        # ---- background dialogue / reflection state ----------------------
        # A conversation's slow generation runs in a background task; the two
        # participants are locked in ``_in_dialogue`` (agent_id -> initiation minute,
        # the authoritative lock + settlement token) until it settles.
        self._in_dialogue: dict[str, int] = {}
        self._reflecting: set[str] = set()          # agents with a reflection in flight
        self._tasks: set[asyncio.Task] = set()      # live background tasks (kept referenced)
        self._dialogue_sem = asyncio.Semaphore(DIALOGUE_MAX_CONCURRENT)
        self._reflect_sem = asyncio.Semaphore(REFLECT_MAX_CONCURRENT)
        # Dialogue patient-retry observability (surfaced at /api/usage as proof the
        # retry is doing its job). ``retried`` = conversations that failed the first
        # whole-chain pass and had to brew+retry; ``recovered`` = those a retry then
        # rescued to a real model; ``exhausted`` = those that spent every round and
        # still fell to the mock floor. rounds_extra = total extra chain re-runs.
        self.dialogue_retry_stats: dict[str, int] = {
            "retried": 0, "recovered": 0, "exhausted": 0, "rounds_extra": 0,
        }
        # ---- life chapters (see agents/chapters.py) ------------------------
        self._closing: set[str] = set()             # agents with a closure in flight
        self.chapter_stats: dict[str, int] = {"closed": 0, "llm": 0, "template": 0}
        # Fired with a chapter-ledger row (dict) when a chapter starts or closes, so
        # the host can persist it; None for headless runs.
        self.on_chapter_record: Callable[[dict], None] | None = None
        # The decision layer raises the flag (a resolved secret ending a pursuit);
        # the engine owns the pipeline.
        self.decisions.on_chapter_signal = self.request_chapter_close
        # ---- wishes (see agents/wishes.py) ---------------------------------
        # Progress is driven purely by published events; terminals are drained at
        # a safe point (never re-entrantly inside a publish).
        self._wish_terminals: list[tuple[str, str]] = []   # (agent_id, wish_id)
        self._growing: set[str] = set()                    # agents with a generation in flight
        self.wish_stats: dict[str, int] = {
            "seeded": 0, "grown": 0, "completed": 0, "failed": 0, "abandoned": 0,
            "gen_ok": 0, "gen_declined": 0, "gen_gates": 0, "gen_failed": 0,
        }
        # Fired with a wish-ledger row when a wish is seeded or ends; None headless.
        self.on_wish_record: Callable[[dict], None] | None = None
        self.bus.subscribers.append(self._progress_wishes)
        # Stable across restarts: the drive's dice are seeded from the run id
        # (set by the host when persistence attaches), the sim day, agent and wish.
        self.run_seed: str = ""
        self.decisions.wish_run_seed = ""

    def bootstrap(self, start_minute: int) -> None:
        # Fresh scheduler each call so re-bootstrapping onto a restored world
        # (resume) starts from a clean queue instead of stale START_MINUTE entries.
        self.now = start_minute
        self._last_day = start_minute // DAY_MIN
        self.scheduler = Scheduler()
        self._last_decision_at.clear()
        for agent in self.world.agents.values():
            self.scheduler.schedule(agent.id, start_minute)
            self._last_decision_at[agent.id] = start_minute - 1

    async def run_until(self, end_minute: int) -> None:
        while True:
            nxt = self.scheduler.peek_minute()
            if nxt is None or nxt >= end_minute:
                break
            await self.tick()
            # Yield to the event loop while a dialogue/reflection is in flight so its
            # background task actually gets to run (and settle) between ticks. Without
            # this, an instant provider (mock) never suspends, so the loop would race
            # ahead with the two talkers locked and nothing settling. Only when tasks
            # are pending -- a pure-rules stretch keeps running at full tilt.
            if self._tasks:
                await asyncio.sleep(0)

    def _publish(
        self,
        kind: str,
        verb: str,
        actor: Agent | None = None,
        target: Agent | None = None,
        location_id: str = "",
        text: str = "",
        target_name: str | None = None,
        detail: str = "",
        minute: int | None = None,
    ) -> None:
        # ``minute`` lets a background settlement stamp its events at the
        # conversation's initiation time (the exchange narratively happened then,
        # even though generation finished a few seconds -- and some sim-minutes --
        # later). Defaults to the live clock.
        loc = self.world.locations.get(location_id)
        ev = Event(
            minute=self.now if minute is None else minute,
            kind=kind,
            verb=verb,
            actor=actor.id if actor else "",
            actor_name=actor.name if actor else "",
            target=target.id if target else "",
            # target_name override lets non-agent subjects (a place, "self") still
            # render a name when there's no Agent to derive it from.
            target_name=(target_name if target_name is not None else (target.name if target else "")),
            location=location_id,
            location_name=loc.name if loc else "",
        )
        ev.text = text
        ev.detail = detail
        ev.text_en = render_en(
            verb, ev.actor_name, ev.target_name, ev.location_name, text
        )
        self.bus.publish(ev)

    # Conversation-borne beats (confide/confront) carry a time window so the UI can
    # pull the actual dialogue lines said around them from /api/history.
    _CONV_VERBS = {"confide", "confronted"}

    def _chronicle_add(self, ev: Event) -> None:
        """Bus subscriber: keep the notable beats (last 200) as the town's history.
        Structured fields mirror the event so the UI renders/translates them like
        any other event and can jump replay to the minute. ``detail`` carries the
        expandable specifics; conversation beats also carry their dialogue window."""
        if ev.verb not in CHRONICLE_VERBS:
            return
        entry = {
            "minute": ev.minute, "kind": ev.kind, "verb": ev.verb,
            "icon_hint": CHRONICLE_ICONS.get(ev.verb, "•"),
            "actor": ev.actor, "actor_name": ev.actor_name,
            "target": ev.target, "target_name": ev.target_name,
            "location": ev.location, "location_name": ev.location_name,
            "speech": ev.text, "detail": ev.detail,
        }
        if ev.verb in self._CONV_VERBS:  # the surrounding exchange spans a few sim-minutes
            entry["conversation_minutes"] = [ev.minute, ev.minute + 20]
        self.chronicle.append(entry)
        if len(self.chronicle) > 200:
            del self.chronicle[:-200]

    # ---- world effects (rain / festival, Level 0) --------------------

    def trigger_world_effect(self, etype: str, location: str, duration: int) -> dict | None:
        """God Mode: start (or extend) a world effect. Publishes a system
        start event only on a fresh activation; a repeat just extends the
        window. Returns the live effect."""
        until = self.now + max(1, duration)
        newly = self.world.set_effect(etype, location, until)
        if newly:
            self._publish(
                "system", "rain_start" if etype == "rain" else "festival_start",
                location_id=location or "",
            )
        return self.world.effect_active(etype)

    def expire_world_effects(self) -> None:
        """Drop any effects whose window has closed and announce their end.
        Safe to call every tick and from the server loop -- a no-op when idle."""
        for eff in self.world.expire_effects(self.now):
            self._publish(
                "system", "rain_end" if eff["type"] == "rain" else "festival_end",
                location_id=eff.get("location", ""),
            )

    # ---- daily economy settlement (Level 0, no LLM) ------------------

    def _settle_days_through(self) -> None:
        """Run one settlement per midnight crossed since the last check.
        Handles multi-day jumps (e.g. an agent sleeping across a boundary)."""
        while self._last_day < self.now // DAY_MIN:
            self._last_day += 1
            self._daily_settlement()
            if self.on_snapshot is not None:
                self.on_snapshot()   # persist the freshly-closed day

    def _daily_settlement(self) -> None:
        """Close each shop's books (record + reset today's revenue, nudge the
        owner's mood/memory) and pay everyone their daily wage. When the day that
        just closed was a Sunday, also run the weekly books."""
        closed_dow = (self._last_day - 1) % 7        # dow of the day just closed (Day 1 = Mon)
        week_close = closed_dow == 6                  # Sunday -> weekly settlement too
        week_rev: dict[str, float] = {}              # shop id -> weekly takings (captured before reset)
        for loc in self.world.locations.values():
            if not (loc.owner and loc.price > 0):
                continue
            owner = self.world.agents.get(loc.owner)
            x = loc.revenue_today
            if owner is not None:
                if x < 15:
                    imp, mood = 6, "worried"
                elif x >= 40:
                    imp, mood = 4, "happy"
                else:
                    imp, mood = 2, ""
                owner.memory.add(MemoryItem(
                    minute=self.now, text=f"{{loc:{loc.id}}} made ${x:.0f} today.", importance=imp))
                if mood:
                    owner.state.mood = mood
                self._publish(
                    "action", "day_summary", actor=owner,
                    location_id=loc.id, text=f"cafe revenue ${x:.0f}",
                )
                if week_close:
                    xw = loc.revenue_week
                    if xw < 100:
                        wimp, wmood = 6, "worried"
                    elif xw >= 250:
                        wimp, wmood = 4, "happy"
                    else:
                        wimp, wmood = 3, ""
                    owner.memory.add(MemoryItem(
                        minute=self.now, text=f"This week {{loc:{loc.id}}} made ${xw:.0f}.", importance=wimp))
                    if wmood:
                        owner.state.mood = wmood
            loc.revenue_today = 0.0
            if week_close:
                week_rev[loc.id] = loc.revenue_week
                loc.revenue_week = 0.0
        for ag in self.world.agents.values():
            ag.state.money += ag.profile.daily_wage
            # Shop staff are paid BY their employer -- a real transfer out of the
            # owner's wallet, so hiring actually costs (can Jiji afford it?).
            if ag.state.employer and ag.profile.daily_wage > 0:
                owner = self.world.agents.get(ag.state.employer)
                if owner is not None:
                    owner.state.money -= ag.profile.daily_wage
            ag.semantic.decay()   # unreinforced impressions fade a little each day
        if week_close:            # a chronicle beat: the week's books closed, with the shop-vs-shop takings
            detail = " vs ".join(f"{{loc:{lid}}} ${rev:.0f}" for lid, rev in week_rev.items())
            self._publish("system", "week_close", detail=detail)
        # Staged life changes take effect now, on the clean day boundary.
        self._apply_pending_transitions()
        # Interludes that have run their course lapse into ordinary days.
        self._advance_chapters()
        # Wishes: sample the state-derived requirements, then judge outcomes and
        # abandonment (pure rules, once a day).
        self._settle_wishes()
        # ...and, more slowly, give an ordinary life a chance to want something.
        self._roll_wish_generation()
        # Romance spark judged once per day per resident (see decision._maybe_ignite).
        for agent in self.world.agents.values():
            self.decisions._maybe_ignite(agent, self.world, self.now)
        self._maybe_break_equipment()   # a place or two may fault today -> work for Long
        self._roll_meetups()            # social initiative: friends arrange to meet today

    def _roll_meetups(self) -> None:
        """Once a day, residents may arrange to meet a friend later today (live only; see
        decision.maybe_arrange_meetup). An arranged pair is scheduled to the appointed
        minute so both drift to the venue; a decline is a quiet beat."""
        for agent in list(self.world.agents.values()):
            outcome = self.decisions.maybe_arrange_meetup(agent, self.world, self.now)
            if outcome is None:
                continue
            a, b = self.world.agents[outcome["a"]], self.world.agents[outcome["b"]]
            if outcome["verb"] == "meetup_arranged":
                self.scheduler.schedule(a.id, outcome["minute"])   # both get a tick at the meetup time
                self.scheduler.schedule(b.id, outcome["minute"])
                self._publish("action", "meetup_arranged", actor=a, target=b,
                              location_id=outcome["location"])
            else:
                self._publish("action", "meetup_declined", actor=a, target=b)

    _BREAKDOWN_DAILY_P = 0.08
    _MAX_BROKEN = 2

    def _maybe_break_equipment(self) -> None:
        """Once a day, non-home places have a small chance of an equipment fault, up
        to two broken at once. A fault bites the shop's takings (0.6x, see
        World.execute) until Long repairs it."""
        day = self.now // DAY_MIN
        broken = [l for l in self.world.locations.values() if l.broken]
        for loc in self.world.locations.values():
            if len(broken) >= self._MAX_BROKEN:
                break
            if loc.kind == "home" or loc.broken:
                continue
            seed = int(hashlib.sha256(f"break|{loc.id}|{day}".encode()).hexdigest()[:8], 16)
            if random.Random(seed).random() < self._BREAKDOWN_DAILY_P:
                loc.broken = True
                broken.append(loc)
                self._publish("system", "breakdown", location_id=loc.id)

    def _do_repair(self, tech: Agent, loc_id: str) -> None:
        """Long fixes a broken place: fee $15 out of the owner's wallet into his (an
        ownerless public place is a freebie -- good neighbourliness), a public beat,
        and a memory each (the owner's carries a grudging thanks)."""
        loc = self.world.locations.get(loc_id)
        if loc is None or not loc.broken:
            return
        loc.broken = False
        fee = 15.0
        owner = self.world.agents.get(loc.owner) if loc.owner else None
        if owner is not None:
            owner.state.money -= fee
            tech.state.money += fee
            owner.memory.add(MemoryItem(
                minute=self.now, importance=3, kind="conversation",
                text=f"{{agent:{tech.id}}} fixed the equipment at {{loc:{loc_id}}}. Cost me $15, but at least it works again."))
        tech.memory.add(MemoryItem(
            minute=self.now, importance=3, kind="conversation",
            text=f"Fixed the equipment at {{loc:{loc_id}}} -- whoever installed it did a sloppy job."))
        self._publish("action", "repaired", actor=tech, location_id=loc_id)

    _TRANSITION_RIPPLE = {                        # third person, for friends' memories
        "quit_job": "quit their job",
        "take_job_cafe": "started working at the cafe",
        "take_job_bakery": "started working at the bakery",
        "freelance_from_home": "started freelancing from home",
    }
    _TRANSITION_SELF = {                          # first person, for the owner's own memory
        "quit_job": "quit my job to find a new direction",
        "take_job_cafe": "started working at the cafe",
        "take_job_bakery": "started working at the bakery",
        "freelance_from_home": "started freelancing from home",
    }

    def _apply_pending_transitions(self) -> None:
        """Apply every staged life change (decided by yesterday's reflection). Each:
        re-verify the precondition, run the template's effects (occupation / wage /
        routine / mood), rewrite goals, resolve any secret the change settles, then
        publish a chronicle beat, an importance-9 self memory, and a light ripple to
        close friends. Runs once per day boundary, so routines swap cleanly."""
        from ..agents import transitions as transitions_mod
        for agent in self.world.agents.values():
            tid = agent.state.pending_transition
            if not tid:
                continue
            reason = agent.state.pending_transition_reason
            agent.state.pending_transition = ""
            agent.state.pending_transition_reason = ""
            tmpl = transitions_mod.REGISTRY.get(tid)
            if tmpl is None or not tmpl.precondition(agent, self.world):
                continue                                  # conditions changed overnight -> drop it
            tmpl.effects(agent, self.world)
            if tid == "quit_job":   # a fresh start opens them up a little (romance hook)
                agent.profile.romantic_inclination = max(agent.profile.romantic_inclination, 0.55)
            # Goal rewrite: drop the goals this change makes moot, install the new one.
            agent.profile.goals = [
                g for g in agent.profile.goals
                if not any(s in str(g.get("goal", "")).lower() for s in tmpl.clears_goal)
            ]
            agent.profile.goals.insert(0, {"goal": tmpl.goal, "priority": 0.85})
            # Resolve the secret this change acts on (Xue's interview secret on quit).
            for s in self.decisions.secrets.active_secrets_of(agent.id):
                if tmpl.resolves_secret_kw and any(kw in s.text.lower() for kw in tmpl.resolves_secret_kw):
                    self.decisions._resolve_secret(
                        agent, s, self.now,
                        f"{agent.id.capitalize()} acted on it and moved forward.")
            agent.state.last_transition_day = self.now // DAY_MIN
            agent.memory.add(MemoryItem(
                minute=self.now, importance=9, kind="reflection",
                text=f"I made a real change today: I {self._TRANSITION_SELF.get(tid, 'made a big change')}."
                     + (f" ({reason})" if reason else "")))
            self._publish("system", "transition", actor=agent,
                          location_id=agent.state.location, text=tid, detail=reason or tmpl.label)
            # A life change that makes the current pursuit moot (a job-themed chapter
            # vs quit/take-job/freelance) closes that chapter as completed.
            if chapters_mod.goal_matches_chapter(agent.chapter, *tmpl.clears_goal):
                self.request_chapter_close(agent, "completed", "transition", tmpl.label)
            # Friends hear about it.
            note = self._TRANSITION_RIPPLE.get(tid, "made a big change")
            for other in self.world.agents.values():
                rel = other.relationships.get(agent.id)
                if other.id != agent.id and rel is not None and rel.friendship >= 55:
                    other.memory.add(MemoryItem(
                        minute=self.now, importance=4, kind="reflection",
                        text=f"Heard {{agent:{agent.id}}} {note}."))

    async def tick(self) -> None:
        item = self.scheduler.pop_next()
        if item is None:
            return
        self.now = max(self.now, item.minute)
        self._settle_days_through()   # pay wages + close the cafe's books at each midnight
        self.expire_world_effects()   # end rain/festival whose window has closed
        agent = self.world.agents[item.agent_id]

        # Locked mid-conversation (its dialogue is generating in the background):
        # keep the participant frozen -- reschedule shortly and skip. These small
        # hops also keep the clock advancing even when only the two talkers have
        # near-term work, so the town never stalls waiting on a conversation.
        locked = self._in_dialogue.get(agent.id)
        if locked is not None:
            if self.now - locked > WATCHDOG_STUCK_MIN:   # backstop: settlement machinery wedged
                self._in_dialogue.pop(agent.id, None)
                agent.state.busy_until = self.now
                print(f"[watchdog] {agent.id} stuck in dialogue {self.now - locked} sim-min "
                      f"-> force unlock", flush=True)
            else:
                self.scheduler.schedule(agent.id, self.now + 5)
                return

        # Busy agents (resting/asleep, or a conversation whose lock hasn't lifted)
        # get pushed to when they free up.
        if agent.state.busy_until > self.now:
            self.scheduler.schedule(agent.id, agent.state.busy_until)
            return

        since = self._last_decision_at.get(agent.id, self.now - 1)
        obs = self.world.observe(agent, since_minute=since, now=self.now)
        self._last_decision_at[agent.id] = self.now
        self._accrue_copresence(agent, self.now - since)   # romance-spark fuel

        decision = await self.decisions.decide(agent, self.world, obs, self.now)

        if decision.action == "talk" and decision.talk_partner:
            await self._initiate_conversation(
                agent, decision.talk_partner, decision.confront_text, decision.confront_rumor_id,
                is_meetup=decision.is_meetup,
            )
        else:
            if decision.narrative_verb == "seek_out":
                self._publish(
                    "action", "seek_out", actor=agent,
                    target=self.world.agents.get(decision.narrative_target),
                    location_id=agent.state.location,
                )
            result = self.world.execute(
                agent, decision.action, decision.target_location, self.now, decision.duration
            )
            if result:
                self._publish(
                    "action", result["verb"], actor=agent, location_id=result["location"]
                )
            if decision.narrative_verb == "repair" and decision.narrative_target:
                self._do_repair(agent, decision.narrative_target)
            # Working the creator's own landmark nudges it along (pure rules); a
            # completion rings out as its own event beat.
            if decision.action == "work":
                done = self.world.advance_landmark(agent, decision.duration, self.now)
                if done:
                    self._publish(
                        "action", "landmark_done", actor=agent,
                        location_id=done["location"], text=done["text"],
                    )
                    # Finishing the installation opens Aisi up a little (romance hook).
                    agent.profile.romantic_inclination = max(agent.profile.romantic_inclination, 0.50)
                    # The finished piece closes the creator's pursuit chapter (this must
                    # come first: the worry resolution below would otherwise race it
                    # through the secret_resolved signal -- both are guarded anyway).
                    if agent.chapter is not None and agent.chapter.related_landmark_id == done.get("id", ""):
                        self.request_chapter_close(agent, "completed", "landmark", done.get("text", ""))
                    # The world fact settles the "will I ever finish it?" worry.
                    self._resolve_landmark_worries(agent, done.get("text", ""))
            if decision.action == "move":
                self._interrupt_colocated(agent)

        # Reflection (Level 3) fires on accumulated importance, then runs in the
        # BACKGROUND -- it's a slow smart-tier call and a pure psychological beat, so
        # the agent doesn't lock (it keeps acting; any life-decision it stages waits
        # for the next day boundary anyway). Reset the counter now so a second
        # reflection can't pile up while this one generates.
        if self.decisions.should_reflect(agent) and agent.id not in self._reflecting:
            agent.memory.importance_since_reflection = 0
            self._reflecting.add(agent.id)
            self._spawn(self._reflect_task(agent, self.now))

        # A wish whose last requirement was met by this tick's events settles here,
        # outside the publish path.
        self.drain_wish_terminals()

        self.scheduler.schedule(agent.id, self.now + decision.duration)

    # ---- background dialogue / reflection --------------------------------

    def _spawn(self, coro) -> None:
        """Launch a background task, keep it referenced (so it isn't GC'd mid-flight),
        and drop it from the set when done."""
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _initiate_conversation(
        self, a: Agent, partner_id: str, confront_text: str = "", confront_rumor_id: str = "",
        is_meetup: bool = False,
    ) -> None:
        """Initiation (synchronous in the tick): if the partner is free, build the
        conversation plan (rumor/leak/confide effects apply now), LOCK both parties,
        publish the opening beats immediately (talk_start + any shared_rumor/leak/
        confide), and hand the slow dialogue generation to a background task. The
        world keeps advancing; only these two freeze until it settles."""
        b = self.world.agents[partner_id]
        if (b.state.busy_until > self.now or b.state.current_action == "sleep"
                or b.id in self._in_dialogue or a.id in self._in_dialogue):
            # Partner (or self) got occupied since the decision; retry shortly.
            self.scheduler.schedule(a.id, self.now + 5)
            return
        plan = await self.decisions.start_conversation(
            a, b, self.world, self.now,
            confront_text=confront_text or None, confront_rumor_id=confront_rumor_id,
            count_against_cap=not is_meetup,   # a kept meetup is exempt from the daily cap
        )
        init = self.now
        if is_meetup:   # both kept the appointment -> clear the mirror, mark the beat
            a.state.pending_meetup = None
            b.state.pending_meetup = None
            self._publish("action", "met_up", actor=a, target=b, location_id=a.state.location)
        # Lock both. ``_in_dialogue`` is the authoritative lock + settlement token;
        # busy_until mirrors it for the UI ("talk" bubble) and third-party free checks,
        # and is the resume self-heal window (see DIALOGUE_LOCK_MIN).
        self._in_dialogue[a.id] = init
        self._in_dialogue[b.id] = init
        a.state.current_action = "talk"
        b.state.current_action = "talk"
        a.state.busy_until = init + DIALOGUE_LOCK_MIN
        b.state.busy_until = init + DIALOGUE_LOCK_MIN
        self._publish("action", "talk_start", actor=a, target=b, location_id=a.state.location)
        for sr in plan.shared_rumors:  # one event per direction actually shared
            self._publish(
                "action", "share_rumor",
                actor=self.world.agents.get(sr["from"]),
                target=self.world.agents.get(sr["to"]),
                location_id=a.state.location, text=sr["text"],
            )
            if sr.get("leak"):  # a confided secret just became gossip -> a chronicle beat
                secret_text = sr.get("secret_text", "")
                self._publish(
                    "action", "leak",
                    actor=self.world.agents.get(sr["from"]),
                    target=self.world.agents.get(sr.get("subject", "")),
                    location_id=a.state.location, text=sr["text"],
                    detail=(f'Secret: "{secret_text}"  ->  now spreading as: "{sr["text"]}"'
                            if secret_text else f'Now spreading as: "{sr["text"]}"'),
                )
        for cf in plan.confided:  # content-free to the world; the chronicle (god's-eye) keeps the detail
            self._publish(
                "action", "confide",
                actor=self.world.agents.get(cf["from"]),
                target=self.world.agents.get(cf["to"]),
                location_id=a.state.location,
                detail=cf.get("text", ""),   # the confided secret's text
            )
        self._spawn(self._dialogue_task(plan))

    async def _dialogue_task(self, plan: ConvPlan) -> None:
        """Background: generate the exchange, then settle. Patient retry (the "rather
        slow than canned" policy): a whole-chain failure -- every live provider fails
        the zh gate (``ProvidersExhausted``) or the chain overruns the backstop --
        does NOT drop to the canned mock. Instead the two speakers keep "brewing"
        (they stay locked) for ``DIALOGUE_RETRY_WAIT_S``, then the whole chain re-runs,
        up to ``DIALOGUE_RETRY_ROUNDS`` extra rounds. Only once every round is spent is
        the mock floor served. The settlement runs in a ``finally`` so the participants
        ALWAYS unlock, even on cancellation or error -- no one gets stuck in ``talk``.

        The semaphore is re-acquired per round (released during the brew pause) so a
        brewing pair never holds a generation slot away from other conversations."""
        res: object | None = None
        reason: str | None = None   # non-None only when we fell to the mock floor
        rounds = 1 + max(0, DIALOGUE_RETRY_ROUNDS)
        retried = False
        try:
            for attempt in range(rounds):
                try:
                    async with self._dialogue_sem:
                        res = await asyncio.wait_for(
                            self.decisions.generate_conversation(plan), timeout=DIALOGUE_TIMEOUT_S)
                    if retried:
                        self.dialogue_retry_stats["recovered"] += 1  # a retry rescued this from mock
                    break  # a real, gate-passing exchange
                except (ProvidersExhausted, asyncio.TimeoutError) as err:
                    # "exhausted" = every real provider failed (429-cooled or gate-rejected);
                    # "timeout" = the whole chain overran the backstop.
                    cause = "timeout" if isinstance(err, asyncio.TimeoutError) else "exhausted"
                    if attempt < rounds - 1:
                        if not retried:
                            retried = True
                            self.dialogue_retry_stats["retried"] += 1
                        self.dialogue_retry_stats["rounds_extra"] += 1
                        print(f"[dialogue] chain exhausted ({cause}, round {attempt + 1}/{rounds}); "
                              f"brewing {DIALOGUE_RETRY_WAIT_S:.0f}s then retrying whole chain "
                              f"({plan.a.id}/{plan.b.id} @ min {plan.init_minute})", flush=True)
                        await asyncio.sleep(DIALOGUE_RETRY_WAIT_S)
                        continue
                    # Every round spent -> the mock floor is the last resort.
                    reason = "exhausted"
                    self.dialogue_retry_stats["exhausted"] += 1
                    print(f"[dialogue] floor: all {rounds} chain rounds exhausted ({cause}), "
                          f"degrading to mock ({plan.a.id}/{plan.b.id} @ min {plan.init_minute})", flush=True)
                    try:
                        res = await self.decisions.mock_dialogue(plan)
                    except Exception:
                        res = None
        except Exception as err:
            reason = "error"
            print(f"[dialogue] floor: generation errored ({err!r}), degrading to mock "
                  f"({plan.a.id}/{plan.b.id} @ min {plan.init_minute})", flush=True)
            try:
                res = await self.decisions.mock_dialogue(plan)
            except Exception:
                res = None
        finally:
            self._finish_dialogue(plan, res, reason)

    def _finish_dialogue(self, plan: ConvPlan, res: object | None, reason: str | None = None) -> None:
        """Settlement (synchronous): apply the aftermath and publish the exchange
        (say lines + confronted/milestone/romance beats), stamped at the initiation
        minute. Guarded by the ``_in_dialogue`` token so a late task can't stomp a
        conversation the watchdog already cleared."""
        a, b, init = plan.a, plan.b, plan.init_minute
        if self._in_dialogue.get(a.id) != init or self._in_dialogue.get(b.id) != init:
            return   # superseded / watchdog-cleared -> these two already moved on
        self._in_dialogue.pop(a.id, None)
        self._in_dialogue.pop(b.id, None)
        # Normal-path chain floor: the router exhausted every live provider and
        # returned the mock floor (already recorded). One clear line (the outer
        # backstop paths logged their own already, so only log here when reason=None).
        if reason is None and getattr(res, "provider", "") == "mock":
            print(f"[dialogue] floor: all live providers failed the gate, served mock "
                  f"({a.id}/{b.id} @ min {init})", flush=True)
        duration = 6
        if res is not None:
            turns, _signals, confrontation, milestone, romance_events = \
                self.decisions.settle_conversation(plan, self.world, res)
            duration = max(6, len(turns) * 2)
            by_name = {a.name.lower(): a, b.name.lower(): b}
            for i, turn in enumerate(turns):
                speaker = by_name.get(
                    str(turn.get("speaker", "")).lower(),
                    a if i % 2 == 0 else b,  # fallback: alternate speakers
                )
                self._publish(
                    "dialogue", "say", actor=speaker,
                    target=b if speaker is a else a,
                    location_id=speaker.state.location,
                    text=str(turn.get("text", "")), minute=init + i,  # stagger, stamped at initiation
                )
            if confrontation is not None:  # the rumor's endpoint: publish the verdict
                outcome = "admitted it" if confrontation["outcome"] == "admitted" else "denied it"
                rumor = self.decisions.rumors.rumors.get(confrontation.get("rumor_id", ""))
                rumor_text = rumor.versions[-1].text if rumor and rumor.versions else ""
                self._publish(
                    "action", "confronted", actor=a, target=b,
                    location_id=a.state.location, text=outcome, minute=init,
                    detail=(f'About the rumor "{rumor_text}" -> {b.id.capitalize()} {outcome}'
                            if rumor_text else f"{b.id.capitalize()} {outcome}"),
                )
            if milestone is not None:  # a friendship crossed a stage boundary -> a visible beat
                self._publish_milestone(a, b, milestone, minute=init)
            for ev in romance_events:  # a confession that just resolved (accepted -> dating, or turned down)
                self._publish_romance(ev, minute=init)
        # Narrative duration: the exchange occupies ``init .. init+duration`` in sim
        # time regardless of how long generation took. Unlock and reschedule both;
        # if that window is already in the past, they decide again immediately.
        a.state.busy_until = init + duration
        b.state.busy_until = init + duration
        self.scheduler.schedule(a.id, max(self.now, a.state.busy_until))
        self.scheduler.schedule(b.id, max(self.now, b.state.busy_until))

    async def _reflect_task(self, agent: Agent, at_minute: int) -> None:
        """Background reflection: generate insights/beliefs/new-secret/life-decision,
        then publish them (stamped at ``at_minute``). Patient retry (task 3): reflection
        runs no_floor, so a whole-chain failure raises ``ProvidersExhausted``; rather
        than accept a canned mock, brew ``REFLECT_RETRY_WAIT_S`` and re-run the chain,
        up to ``REFLECT_RETRY_ROUNDS`` extra rounds. If every round is still exhausted,
        the reflection is SKIPPED this round -- no mock floor -- since a missed
        reflection simply recurs once weight re-accumulates. Always clears the per-agent
        in-flight guard."""
        insights: list[str] = []
        beliefs: list[dict] = []
        secret_born = False
        romance_events: list[dict] = []
        rounds = 1 + max(0, REFLECT_RETRY_ROUNDS)
        try:
            for attempt in range(rounds):
                try:
                    async with self._reflect_sem:
                        insights, beliefs, secret_born, romance_events = await asyncio.wait_for(
                            self.decisions.reflect(agent, self.world, at_minute), timeout=REFLECT_TIMEOUT_S)
                    break  # a real, gate-passing reflection
                except (ProvidersExhausted, asyncio.TimeoutError):
                    if attempt < rounds - 1:
                        await asyncio.sleep(REFLECT_RETRY_WAIT_S)   # brew, then re-run the chain
                        continue
                    print(f"[reflect] chain exhausted after {rounds} rounds; skipping "
                          f"{agent.id}'s reflection this round (no canned floor)", flush=True)
                except builders.RosterNotLoadedError:
                    raise      # a bare gender gate is a programming error -- let it out
                except Exception:
                    break  # unexpected error -> no reflection this round, don't spin
        finally:
            self._reflecting.discard(agent.id)
        for ev in romance_events:      # a proposal that just settled into partnership
            self._publish_romance(ev, minute=at_minute)
        if secret_born:  # content-free beat: the town notes they're holding something back
            self._publish("reflection", "secret_born", actor=agent,
                          location_id=agent.state.location, minute=at_minute)
        for ins in insights:
            self._publish("reflection", "insight", actor=agent,
                          location_id=agent.state.location, text=ins, minute=at_minute)
        for be in beliefs:
            conf = be.get("confidence")
            detail = be["text"] + (f"  (confidence {int(conf * 100)}%)" if conf is not None else "")
            self._publish(
                "reflection", "belief", actor=agent,
                target=self.world.agents.get(be["subject_id"]),
                target_name=be["subject_name"],
                location_id=agent.state.location, text=be["text"], detail=detail, minute=at_minute,
            )

    # ---- wishes (see agents/wishes.py) --------------------------------------

    def set_run_seed(self, seed: str) -> None:
        """Bind the wish drive's deterministic dice to this run."""
        self.run_seed = seed or ""
        self.decisions.wish_run_seed = self.run_seed

    def _emit_wish_record(self, wish) -> None:
        if self.on_wish_record is None:
            return
        try:
            self.on_wish_record({
                "wish_id": wish.id, "owner": wish.owner, "scale": wish.scale,
                "status": wish.status, "title": wish.title, "statement": wish.statement,
                "motivation": wish.motivation, "chapter_id": wish.chapter_id,
                "created_on": wish.created_on, "ended_on": wish.ended_on,
                "expires_on": wish.expires_on or 0, "outcome_reason": wish.outcome_reason,
                "frustration_count": wish.frustration_count,
                "requirements": [r.to_dict() for r in wish.requirements],
                "provenance": list(wish.provenance),
            })
        except Exception as err:
            print(f"[wish] ledger hook failed: {err}", flush=True)

    def _linked_wish(self, agent: Agent, chapter_id: str):
        """The wish (any status) whose pursuit chapter this is -- the marker that the
        chapter's title/goal are private text."""
        if not chapter_id:
            return None
        return next((w for w in agent.wishes if w.chapter_id == chapter_id), None)

    def _progress_wishes(self, ev: Event) -> None:
        """Bus subscriber: a real event is the ONLY thing that advances an
        event-kind requirement. Terminals are queued, never handled in-line."""
        for agent in self.world.agents.values():
            for wish in agent.wishes:
                if wish.status != "active":
                    continue
                if wishes_mod.update_from_event(wish, agent, ev):
                    if wishes_mod.outcome(wish, ev.minute // DAY_MIN + 1) is not None:
                        self._queue_wish_terminal(agent, wish)

    def _queue_wish_terminal(self, agent: Agent, wish) -> None:
        key = (agent.id, wish.id)
        if key not in self._wish_terminals:
            self._wish_terminals.append(key)

    def drain_wish_terminals(self) -> None:
        """Settle every wish that reached an outcome, outside the publish path."""
        while self._wish_terminals:
            aid, wid = self._wish_terminals.pop(0)
            agent = self.world.agents.get(aid)
            wish = next((w for w in agent.wishes if w.id == wid), None) if agent else None
            if wish is None or wish.status != "active":
                continue
            result = wishes_mod.outcome(wish, self.now // DAY_MIN + 1)
            if result is None:
                continue
            self._close_wish(agent, wish, *result)

    def _settle_wishes(self) -> None:
        """Daily: sample state-derived requirements, judge completion/expiry, then
        weigh abandonment. All pure rules -- no LLM call originates here."""
        day = self._last_day + 1
        for agent in self.world.agents.values():
            for wish in list(agent.wishes):
                if wish.status != "active":
                    continue
                wishes_mod.update_from_state(wish, agent)
                result = wishes_mod.outcome(wish, day)      # completion beats expiry
                if result is not None:
                    self._close_wish(agent, wish, *result, day=day)
                elif wishes_mod.should_abandon(agent, wish, day):
                    self._close_wish(agent, wish, "abandoned",
                                     "the repeated obstacles outweighed how much it was still worth",
                                     day=day)

    def _close_wish(self, agent: Agent, wish, status: str, reason: str, day: int | None = None) -> None:
        """A wish ends. A *major* wish runs the phase-1 closure pipeline (biography,
        down-weighting, interlude) for all three outcomes -- this is the first
        automatic source of a failed/abandoned closure. A *minor* wish is a smaller
        beat: one event and one ordinary memory, no biography, no chapter change."""
        day = self.now // DAY_MIN + 1 if day is None else day
        if not wishes_mod.finish(wish, status, day, reason):
            return
        self.wish_stats[status] = self.wish_stats.get(status, 0) + 1
        self._emit_wish_record(wish)
        # Content-free public beat: the outcome is visible, the intention is not.
        self._publish("system", "wish_closed", actor=agent,
                      location_id=agent.state.location, text=status)
        if wish.scale != "major":
            agent.memory.add(MemoryItem(
                minute=self.now, importance=3, kind="reflection",
                text=wishes_mod.MINOR_CLOSE_TEXT.get(status, wishes_mod.MINOR_CLOSE_TEXT["completed"]),
                tags=[f"goal:{wish.id}"]))
            return
        if agent.chapter is not None and agent.chapter.chapter_type == "pursuit" \
                and agent.chapter.id == wish.chapter_id:
            self.request_chapter_close(agent, status, "wish", reason)

    def seed_wish(self, agent: Agent, clean: dict, day: int | None = None, born: str = "seeded"):
        """Install a validated wish. A major wish opens its pursuit chapter through
        the ordinary phase-1 machinery. Shared by God Mode (``born="seeded"``) and
        phase 2b generation (``born="grown"``) so both take exactly one path in."""
        day = self.now // DAY_MIN + 1 if day is None else day
        wish = wishes_mod.install(agent, clean, day)
        wish.born = born
        if wish.scale == "major":
            chapter = chapters_mod.make_pursuit(wish.statement, wish.title, clean["narrative"], day)
            chapter.related_goal_id = wish.id
            chapters_mod.start_pursuit(agent, chapter)
            wish.chapter_id = chapter.id
            self._emit_chapter_record(agent, chapter=chapter)
            # Neutral text: the chapter's own title is the wish's private wording.
            self._publish("system", "chapter_started", actor=agent,
                          location_id=agent.state.location, text=PRIVATE_CHAPTER_TITLE)
        if born == "seeded":
            self.wish_stats["seeded"] += 1
            self._emit_wish_record(wish)
            self._publish("system", "wish_seeded", actor=agent, location_id=agent.state.location,
                          text=wish.scale)   # scale only -- never the wish's own words
        return wish

    # ---- life chapters: the closure pipeline (see agents/chapters.py) --------

    def request_chapter_close(self, agent: Agent, outcome: str, trigger: str, reason: str = "") -> bool:
        """Signal entry point (landmark done / transition / secret resolved / God Mode):
        if the agent is in a pursuit and no closure is in flight, launch the pipeline
        in the background. Returns True if a closure was launched."""
        if agent.chapter is None or agent.chapter.chapter_type != "pursuit" or agent.id in self._closing:
            return False
        self._closing.add(agent.id)
        self._spawn(self._closure_task(agent, outcome, trigger, reason))
        return True

    async def _closure_task(self, agent: Agent, outcome: str, trigger: str, reason: str) -> None:
        try:
            await self._close_chapter_locked(agent, outcome, trigger, reason)
        finally:
            self._closing.discard(agent.id)

    async def close_chapter(self, agent: Agent, outcome: str, trigger: str = "manual",
                            reason: str = "", ended_minute: int | None = None,
                            aftermath_window: tuple[int, int] | None = None,
                            forbid_terms: tuple[str, ...] = (),
                            ) -> chapters_mod.ChapterRecord | None:
        """Awaitable form (God Mode endpoint, backfill, tests): run the whole pipeline
        now and return the history record (None if there was no pursuit to close or
        one is already closing). ``ended_minute``: when the matter really ended, for a
        retroactive closure (the model is told the true span, never the stale one);
        ``aftermath_window`` / ``forbid_terms``: see chapters.closure_material."""
        if agent.chapter is None or agent.chapter.chapter_type != "pursuit" or agent.id in self._closing:
            return None
        self._closing.add(agent.id)
        try:
            return await self._close_chapter_locked(agent, outcome, trigger, reason, ended_minute,
                                                    aftermath_window, forbid_terms)
        finally:
            self._closing.discard(agent.id)

    async def _close_chapter_locked(self, agent: Agent, outcome: str, trigger: str,
                                    reason: str, ended_minute: int | None = None,
                                    aftermath_window: tuple[int, int] | None = None,
                                    forbid_terms: tuple[str, ...] = (),
                                    ) -> chapters_mod.ChapterRecord | None:
        """The pipeline: rule-assembled material -> ONE smart-tier reflection (bounded;
        any failure falls back to the template line) -> the atomic rule-layer state
        change -> the ``chapter_closed`` beat. The apply step runs in a ``finally`` so
        a wedged or failed model can never leave the chapter half-closed."""
        at = self.now
        chapter = agent.chapter
        material = chapters_mod.closure_material(agent, self.world, chapter, at, ended_minute,
                                                 aftermath_window, forbid_terms)
        out: dict | None = None
        bare_roster = False
        try:
            out = await asyncio.wait_for(
                self.decisions.closure_reflection(agent, self.world, material, outcome),
                timeout=CLOSURE_TIMEOUT_S)
        except builders.RosterNotLoadedError:
            # A bare gender gate is a programming error, not a slow model: let it out,
            # and do NOT let the finally close the chapter on a template line first --
            # that would bury the bug under a completed closure.
            bare_roster = True
            raise
        except Exception as err:
            print(f"[chapter] closure reflection unavailable for {agent.id} ({err!r}); template line", flush=True)
            out = None
        finally:
            if bare_roster:
                pass       # leave the chapter untouched; the exception is propagating
            elif out is not None:
                line, residue, refs, source = (out["biography_line"], out["emotional_residue"],
                                               out["memory_refs"], "llm")
            else:
                line, residue, refs, source = (chapters_mod.template_biography(agent, chapter, outcome),
                                               "", [], "template")
            if not bare_roster:
                record = chapters_mod.apply_closure(
                    agent, self.world, outcome, line, residue, refs, at,
                    trigger=trigger, biography_source=source)
        if record is None:
            return None
        self.chapter_stats["closed"] += 1
        self.chapter_stats[source] += 1
        # A wish-linked chapter carries the owner's PRIVATE wording in its title and
        # goal, so the public beat names neither -- only the outcome. The biography
        # line itself stays public: it is the owner's own account (phase-1 rule).
        linked = self._linked_wish(agent, chapter.id)
        title = record.chapter.get("title", "")
        detail = (f"{record.outcome} · a private chapter" if linked is not None
                  else f"{record.outcome} · {title}" + (f" · {reason}" if reason else ""))
        self._publish(
            "system", "chapter_closed", actor=agent, location_id=agent.state.location,
            text=record.biography_line, minute=at, detail=detail,
        )
        # The chapter may have been closed by something other than the wish itself
        # (a landmark, a transition, God Mode). The linked wish settles with it --
        # ``finish`` first, so this can never bounce back into a second closure.
        if linked is not None and linked.status == "active":
            wishes_mod.finish(linked, record.outcome, record.ended_on,
                              reason or f"the linked chapter closed via {trigger}")
            self.wish_stats[record.outcome] = self.wish_stats.get(record.outcome, 0) + 1
            self._emit_wish_record(linked)
            self._publish("system", "wish_closed", actor=agent,
                          location_id=agent.state.location, text=record.outcome, minute=at)
        self._emit_chapter_record(agent, record=record)
        self._emit_chapter_record(agent, chapter=agent.chapter)   # the interlude, ledger only
        return record

    def _advance_chapters(self) -> None:
        """Daily: a spent interlude gives way. Phase 2b asks first whether a wish
        grows out of this resident's own material -- that attempt runs in the
        background and, if it succeeds, opens a pursuit instead. The interlude is
        held (not lapsed) while the attempt is in flight, so the resident is never in
        an undefined state and the attempt cannot fire twice."""
        day = self._last_day + 1                    # the sim day that just began (1-based)
        for agent in self.world.agents.values():
            if not chapters_mod.interlude_lapsed(agent, day):
                continue
            if agent.id not in self._growing and self._may_attempt_generation(agent, day):
                residue = agent.chapter.emotional_residue if agent.chapter else ""
                self._growing.add(agent.id)
                agent.wish_last_attempt_day = day
                self._spawn(self._grow_wish_task(agent, day, residue, lapse_on_failure=True))
                continue
            self._lapse_interlude(agent, day)

    def _lapse_interlude(self, agent: Agent, day: int) -> None:
        new = chapters_mod.end_interlude(agent, day)
        if new is not None:
            self._publish("system", "chapter_started", actor=agent,
                          location_id=agent.state.location, text=new.title, detail=new.narrative)
            self._emit_chapter_record(agent, chapter=new)

    # ---- wish generation (phase 2b) -----------------------------------------

    def _active_majors_town_wide(self) -> int:
        return sum(len(wishes_mod.active_wishes(a, "major")) for a in self.world.agents.values())

    def _may_attempt_generation(self, agent: Agent, day: int) -> bool:
        """The environment's pressure valve: the more major wishes the town already
        carries, the less likely anyone starts another. Deterministic per
        (run, agent, day) so a restart replays the same decision."""
        if wishes_mod.capacity_left(agent, "major") <= 0 and wishes_mod.capacity_left(agent, "minor") <= 0:
            return False
        p = wishes_mod.generation_probability(self._active_majors_town_wide())
        seed = hashlib.sha256(f"wish-gen|{self.run_seed}|{agent.id}|{day}".encode()).hexdigest()
        return int(seed[:8], 16) / 0xFFFFFFFF < p

    def _roll_wish_generation(self) -> None:
        """The slower path: a resident living ordinary days gets an occasional chance
        for something to surface, no more often than every GENERATION_ORDINARY_EVERY
        days and subject to the same valve."""
        day = self._last_day + 1
        for agent in self.world.agents.values():
            if agent.id in self._growing or chapters_mod.chapter_type(agent) != "ordinary":
                continue
            if day - agent.wish_last_attempt_day < wishes_mod.GENERATION_ORDINARY_EVERY:
                continue
            if not self._may_attempt_generation(agent, day):
                continue
            agent.wish_last_attempt_day = day
            self._growing.add(agent.id)
            self._spawn(self._grow_wish_task(agent, day, "", lapse_on_failure=False))

    async def _grow_wish_task(self, agent: Agent, day: int, residue: str,
                              lapse_on_failure: bool) -> None:
        """Run the generation reflection and, if a wish survives all three gates,
        let it into the world immediately -- no queue, no confirmation."""
        try:
            clean, outcome = await self.decisions.grow_wish(agent, self.world, day, residue)
            self.wish_stats[f"gen_{outcome}"] = self.wish_stats.get(f"gen_{outcome}", 0) + 1
            if clean is not None:
                self._install_grown_wish(agent, clean, day)
                return
        except Exception as err:
            self.wish_stats["gen_failed"] = self.wish_stats.get("gen_failed", 0) + 1
            print(f"[wish] generation errored for {agent.id}: {err!r}", flush=True)
        finally:
            self._growing.discard(agent.id)
        if lapse_on_failure:
            self._lapse_interlude(agent, day)      # nothing grew -> plain ordinary days

    def _install_grown_wish(self, agent: Agent, clean: dict, day: int) -> None:
        wish = self.seed_wish(agent, clean, day, born="grown")
        wish.embedding = list(clean.get("embedding") or [])
        self.wish_stats["grown"] = self.wish_stats.get("grown", 0) + 1
        self._emit_wish_record(wish)
        self._publish("system", "wish_born", actor=agent, location_id=agent.state.location,
                      text=wish.scale)          # scale only -- the wish itself stays private
        print(f"[wish] {agent.id} grew a {wish.scale} wish: \"{wish.title}\" "
              f"({len(wish.provenance)} memories cited)", flush=True)

    def _emit_chapter_record(self, agent: Agent, chapter: chapters_mod.Chapter | None = None,
                             record: chapters_mod.ChapterRecord | None = None) -> None:
        """Hand a chapter-ledger row to the host (no-op headless)."""
        if self.on_chapter_record is None:
            return
        if record is not None:
            ch = record.chapter
            row = {**{k: ch.get(k, "") for k in ("title", "narrative", "goal", "related_landmark_id")},
                   "chapter_id": ch.get("id", ""), "agent_id": agent.id,
                   "chapter_type": ch.get("chapter_type", "pursuit"),
                   "related_goal_id": ch.get("related_goal_id") or "",
                   "started_on": int(ch.get("started_on", 0)), "ended_on": record.ended_on,
                   "outcome": record.outcome, "biography_line": record.biography_line,
                   "emotional_residue": record.emotional_residue, "trigger": record.trigger,
                   "memory_refs": list(record.memory_refs)}
        elif chapter is not None:
            row = {"chapter_id": chapter.id, "agent_id": agent.id, "chapter_type": chapter.chapter_type,
                   "title": chapter.title, "narrative": chapter.narrative, "goal": chapter.goal,
                   "related_goal_id": chapter.related_goal_id or "",
                   "related_landmark_id": chapter.related_landmark_id,
                   "started_on": chapter.started_on, "ended_on": 0, "outcome": "",
                   "biography_line": "", "emotional_residue": chapter.emotional_residue,
                   "trigger": "", "memory_refs": []}
        else:
            return
        try:
            self.on_chapter_record(row)
        except Exception as err:
            print(f"[chapter] ledger hook failed: {err}", flush=True)

    async def drain(self, end_minute: int) -> None:
        """Headless helper (run_day): settle every in-flight dialogue/reflection and
        process any decisions they reschedule, until nothing is pending and the
        scheduler is exhausted to ``end_minute``. Lets a day's conversations fully
        play out even though generation is now backgrounded (the server never calls
        this -- it runs continuously and lets settlements arrive on their own)."""
        guard = 0
        while self._tasks or (
            self.scheduler.peek_minute() is not None and self.scheduler.peek_minute() < end_minute
        ):
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await self.run_until(end_minute)
            guard += 1
            if guard > 10000:   # backstop against a pathological reschedule loop
                break

    def _accrue_copresence(self, agent: Agent, elapsed: int) -> None:
        """Credit the time this agent just spent co-located with other awake residents
        toward the romance spark (see decision._maybe_ignite). A soft proxy: cheap and
        good enough to surface pairs who are always around each other."""
        if elapsed <= 0 or agent.state.current_action == "sleep":
            return
        for other in self.world.agents.values():
            if (other.id != agent.id and other.state.location == agent.state.location
                    and other.state.current_action != "sleep"):
                cp = agent.state.copresence
                cp[other.id] = cp.get(other.id, 0) + elapsed

    _LANDMARK_WORDS = ("installation", "mural", "artwork", "project", "sculpture")

    def _resolve_landmark_worries(self, creator: Agent, landmark_text: str) -> int:
        """A finished landmark lays to rest the creator's worry about finishing it --
        a generic world-fact resolution (not an Aisi special-case). Matches an active
        secret that names the piece (installation/mural/...). Returns how many settled."""
        n = 0
        for s in self.decisions.secrets.active_secrets_of(creator.id):
            words = {w.strip(".,;:!?'\"").lower() for w in s.text.split()}
            if any(k in words for k in self._LANDMARK_WORDS):
                if self.decisions._resolve_secret(
                        creator, s, self.now,
                        f"{creator.id.capitalize()} finished it -- the worry never came true."):
                    n += 1
        return n

    def _publish_romance(self, ev: dict, minute: int | None = None) -> None:
        """A public romance beat: dating / rejection / partnership. (A crush stays
        private -- it plants a secret, never a chronicle line.)"""
        a = self.world.agents.get(ev["a"])
        b = self.world.agents.get(ev["b"])
        self._publish("system", ev["verb"], actor=a, target=b,
                      location_id=a.state.location if a else "", minute=minute)

    def _publish_milestone(self, a: Agent, b: Agent, m: dict, minute: int | None = None) -> None:
        """A relationship stage-change: one chronicle beat + a memory for each side.
        ``text`` is the stage reached (up) or 'distant' (a conflict-driven drop); the
        frontend renders both languages. ``detail`` carries the current friendship
        both ways so the expanded row shows where they stand."""
        m_at = self.now if minute is None else minute
        stage = m["stage"] if m["up"] else "distant"
        detail = f"{{agent:{a.id}}} ↔ {{agent:{b.id}}} · {m['fa']} / {m['fb']}"
        self._publish("system", "milestone", actor=a, target=b,
                      location_id=a.state.location, text=stage, detail=detail, minute=minute)
        note = ("grew closer to" if m["up"] else "drifted apart from")
        a.memory.add(MemoryItem(minute=m_at, importance=4, kind="reflection",
                                text=f"I {note} {{agent:{b.id}}}."))
        b.memory.add(MemoryItem(minute=m_at, importance=4, kind="reflection",
                                text=f"I {note} {{agent:{a.id}}}."))

    def _interrupt_colocated(self, mover: Agent) -> None:
        """Arrival interrupt: others at the destination may react now."""
        for other in self.world.agents.values():
            if (
                other.id != mover.id
                and other.state.location == mover.state.location
                and other.state.current_action not in ("sleep",)
                and other.state.busy_until <= self.now
            ):
                self.scheduler.schedule(other.id, self.now + 1)
