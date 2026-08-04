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

import heapq
from dataclasses import dataclass, field
from typing import Callable

from ..agents.agent import Agent
from ..agents.core import MemoryItem
from ..agents.decision import DecisionEngine
from ..llm.prompts import builders
from ..world.world import World

DAY_MIN = 24 * 60

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
}

# The town's living history: the notable beats worth remembering (see Sim.chronicle
# / the frontend Chronicle). Confide/confront/landmark/belief/broke/world-events are
# ordinary bus events; leak/secret_born/week_close are published just for this.
CHRONICLE_VERBS = {
    "confide", "confronted", "landmark_done", "belief", "broke",
    "rain_start", "rain_end", "festival_start", "festival_end",
    "leak", "secret_born", "week_close",
}
CHRONICLE_ICONS = {
    "confide": "🤫", "confronted": "⚖️", "landmark_done": "🎨", "belief": "💭",
    "broke": "💸", "rain_start": "🌧️", "rain_end": "🌤️", "festival_start": "🎉",
    "festival_end": "🎏", "leak": "🕳️", "secret_born": "🔒", "week_close": "📅",
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
        builders.set_roster([(a.id.capitalize(), a.name) for a in world.agents.values()])
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
    ) -> None:
        loc = self.world.locations.get(location_id)
        ev = Event(
            minute=self.now,
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
            ag.semantic.decay()   # unreinforced impressions fade a little each day
        if week_close:            # a chronicle beat: the week's books closed, with the shop-vs-shop takings
            detail = " vs ".join(f"{{loc:{lid}}} ${rev:.0f}" for lid, rev in week_rev.items())
            self._publish("system", "week_close", detail=detail)

    async def tick(self) -> None:
        item = self.scheduler.pop_next()
        if item is None:
            return
        self.now = max(self.now, item.minute)
        self._settle_days_through()   # pay wages + close the cafe's books at each midnight
        self.expire_world_effects()   # end rain/festival whose window has closed
        agent = self.world.agents[item.agent_id]

        # Busy agents (mid-conversation) get pushed to when they free up.
        if agent.state.busy_until > self.now:
            self.scheduler.schedule(agent.id, agent.state.busy_until)
            return

        since = self._last_decision_at.get(agent.id, self.now - 1)
        obs = self.world.observe(agent, since_minute=since, now=self.now)
        self._last_decision_at[agent.id] = self.now

        decision = await self.decisions.decide(agent, self.world, obs, self.now)

        if decision.action == "talk" and decision.talk_partner:
            await self._handle_conversation(
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
            # Working the creator's own landmark nudges it along (pure rules); a
            # completion rings out as its own event beat.
            if decision.action == "work":
                done = self.world.advance_landmark(agent, decision.duration, self.now)
                if done:
                    self._publish(
                        "action", "landmark_done", actor=agent,
                        location_id=done["location"], text=done["text"],
                    )
            if decision.action == "move":
                self._interrupt_colocated(agent)

        # Reflection check (Level 3) fires only on accumulated importance. It also
        # distills lasting beliefs from repeated experience (semantic memory).
        insights, beliefs, secret_born = await self.decisions.maybe_reflect(agent, self.world, self.now)
        if secret_born:  # content-free beat: the town notes they're holding something back
            self._publish("reflection", "secret_born", actor=agent, location_id=agent.state.location)
        for ins in insights:
            self._publish(
                "reflection", "insight", actor=agent,
                location_id=agent.state.location, text=ins,
            )
        for be in beliefs:
            conf = be.get("confidence")
            detail = be["text"] + (f"  (confidence {int(conf * 100)}%)" if conf is not None else "")
            self._publish(
                "reflection", "belief", actor=agent,
                target=self.world.agents.get(be["subject_id"]),
                target_name=be["subject_name"],
                location_id=agent.state.location, text=be["text"], detail=detail,
            )

        self.scheduler.schedule(agent.id, self.now + decision.duration)

    async def _handle_conversation(
        self, a: Agent, partner_id: str, confront_text: str = "", confront_rumor_id: str = "",
    ) -> None:
        b = self.world.agents[partner_id]
        if b.state.busy_until > self.now or b.state.current_action == "sleep":
            # Partner got occupied since the decision; retry shortly.
            self.scheduler.schedule(a.id, self.now + 5)
            return
        turns, signals, shared_rumors, confrontation, confided = await self.decisions.run_conversation(
            a, b, self.world, self.now,
            confront_text=confront_text or None, confront_rumor_id=confront_rumor_id,
        )
        a.state.current_action = "talk"
        b.state.current_action = "talk"
        duration = max(6, len(turns) * 2)
        a.state.busy_until = self.now + duration
        b.state.busy_until = self.now + duration
        self._publish(
            "action", "talk_start", actor=a, target=b, location_id=a.state.location
        )
        for sr in shared_rumors:  # one event per direction actually shared
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
        for cf in confided:  # content-free to the world; the chronicle (god's-eye) keeps the detail
            self._publish(
                "action", "confide",
                actor=self.world.agents.get(cf["from"]),
                target=self.world.agents.get(cf["to"]),
                location_id=a.state.location,
                detail=cf.get("text", ""),   # the confided secret's text
            )
        by_name = {a.name.lower(): a, b.name.lower(): b}
        for i, turn in enumerate(turns):
            speaker = by_name.get(
                str(turn.get("speaker", "")).lower(),
                a if i % 2 == 0 else b,  # fallback: alternate speakers
            )
            saved_now = self.now
            self.now = saved_now + i  # stagger dialogue lines in sim time
            self._publish(
                "dialogue", "say", actor=speaker,
                target=b if speaker is a else a,
                location_id=speaker.state.location,
                text=str(turn.get("text", "")),
            )
            self.now = saved_now
        if confrontation is not None:  # the rumor's endpoint: publish the verdict
            outcome = "admitted it" if confrontation["outcome"] == "admitted" else "denied it"
            rumor = self.decisions.rumors.rumors.get(confrontation.get("rumor_id", ""))
            rumor_text = rumor.versions[-1].text if rumor and rumor.versions else ""
            self._publish(
                "action", "confronted", actor=a, target=b,
                location_id=a.state.location, text=outcome,
                detail=(f'About the rumor "{rumor_text}" -> {b.id.capitalize()} {outcome}'
                        if rumor_text else f"{b.id.capitalize()} {outcome}"),
            )
        self.scheduler.schedule(b.id, self.now + duration)

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
