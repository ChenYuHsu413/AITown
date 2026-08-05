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
import random
from dataclasses import dataclass, field
from typing import Callable

from ..agents.agent import Agent
from ..agents.core import MemoryItem
from ..agents.decision import ConvPlan, DecisionEngine
from ..llm.prompts import builders
from ..world.world import World

DAY_MIN = 24 * 60

# ---- non-blocking dialogue / reflection ------------------------------------
# A conversation's slow LLM generation runs in a background task so the town
# keeps moving; only the two participants freeze. These bound that.
DIALOGUE_MAX_CONCURRENT = 3   # in-flight dialogue generations (the design's cap); excess queue
REFLECT_MAX_CONCURRENT = 4    # in-flight reflections (smart-tier); a soft burst limit
DIALOGUE_TIMEOUT_S = 20.0     # wall-clock: a generation past this drops to the mock floor
REFLECT_TIMEOUT_S = 20.0      # wall-clock: a reflection past this is abandoned (no insight this round)
# Sim-minutes the two parties are held while the exchange generates. Also the
# self-heal window on resume: a snapshot taken mid-conversation restores the pair
# as busy_until = init + this, so they free themselves within it after a restart
# (the in-flight task is gone; see the design's snapshot/resume note).
DIALOGUE_LOCK_MIN = 30
# In-process backstop only: _finish_dialogue always unlocks (it runs in a finally),
# so this fires solely if that machinery itself wedges. Sized well above the max
# sim-time a legitimate generation can span even at 20x (20s * 20x = 400 sim-min),
# so it never force-kills a still-generating conversation.
WATCHDOG_STUCK_MIN = 720

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
}

# The town's living history: the notable beats worth remembering (see Sim.chronicle
# / the frontend Chronicle). Confide/confront/landmark/belief/broke/world-events are
# ordinary bus events; leak/secret_born/week_close are published just for this.
CHRONICLE_VERBS = {
    "confide", "confronted", "landmark_done", "belief", "broke",
    "rain_start", "rain_end", "festival_start", "festival_end",
    "leak", "secret_born", "week_close", "transition", "milestone",
    "romance_dating", "romance_rejected", "romance_partners", "breakdown", "repaired",
}
CHRONICLE_ICONS = {
    "confide": "🤫", "confronted": "⚖️", "landmark_done": "🎨", "belief": "💭",
    "broke": "💸", "rain_start": "🌧️", "rain_end": "🌤️", "festival_start": "🎉",
    "festival_end": "🎏", "leak": "🕳️", "secret_born": "🔒", "week_close": "📅",
    "transition": "🔀", "milestone": "🤝",
    "romance_dating": "💕", "romance_rejected": "💔", "romance_partners": "💍",
    "breakdown": "🛠️", "repaired": "🔧",
}


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
        # Romance spark judged once per day per resident (see decision._maybe_ignite).
        for agent in self.world.agents.values():
            self.decisions._maybe_ignite(agent, self.world, self.now)
        self._maybe_break_equipment()   # a place or two may fault today -> work for Long

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
        )
        init = self.now
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
        """Background: generate the exchange (bounded to DIALOGUE_MAX_CONCURRENT and a
        wall-clock timeout; a timeout or total failure drops to the mock floor), then
        settle. The settlement runs in a ``finally`` so the participants ALWAYS unlock,
        even on cancellation or error -- no one can get stuck in ``talk`` forever."""
        res: object | None = None
        try:
            try:
                async with self._dialogue_sem:
                    res = await asyncio.wait_for(
                        self.decisions.generate_conversation(plan), timeout=DIALOGUE_TIMEOUT_S)
            except Exception:
                res = await self.decisions.mock_dialogue(plan)   # floor: never leave them hanging
        except Exception:
            res = None
        finally:
            self._finish_dialogue(plan, res)

    def _finish_dialogue(self, plan: ConvPlan, res: object | None) -> None:
        """Settlement (synchronous): apply the aftermath and publish the exchange
        (say lines + confronted/milestone/romance beats), stamped at the initiation
        minute. Guarded by the ``_in_dialogue`` token so a late task can't stomp a
        conversation the watchdog already cleared."""
        a, b, init = plan.a, plan.b, plan.init_minute
        if self._in_dialogue.get(a.id) != init or self._in_dialogue.get(b.id) != init:
            return   # superseded / watchdog-cleared -> these two already moved on
        self._in_dialogue.pop(a.id, None)
        self._in_dialogue.pop(b.id, None)
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
        then publish them (stamped at ``at_minute``). Bounded and timeout-guarded;
        always clears the per-agent in-flight guard."""
        insights: list[str] = []
        beliefs: list[dict] = []
        secret_born = False
        romance_events: list[dict] = []
        try:
            async with self._reflect_sem:
                insights, beliefs, secret_born, romance_events = await asyncio.wait_for(
                    self.decisions.reflect(agent, self.world, at_minute), timeout=REFLECT_TIMEOUT_S)
        except Exception:
            pass  # a hiccup/timeout just means no reflection this round
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
