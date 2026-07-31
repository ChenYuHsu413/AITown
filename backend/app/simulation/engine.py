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
from ..agents.decision import DecisionEngine
from ..world.world import World

DAY_MIN = 24 * 60


def fmt_time(minute: int) -> str:
    day = minute // DAY_MIN + 1
    m = minute % DAY_MIN
    return f"Day {day} {m // 60:02d}:{m % 60:02d}"


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
    "confronted": "{actor} confronted {target} about the rumor — they {text}",
    "say": "💬 {actor}: {text}",
    "insight": "💭 {actor}: {text}",
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
        self.scheduler = Scheduler()
        self.bus = EventBus()
        self.now = 0
        self._last_decision_at: dict[str, int] = {}

    def bootstrap(self, start_minute: int) -> None:
        self.now = start_minute
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
    ) -> None:
        loc = self.world.locations.get(location_id)
        ev = Event(
            minute=self.now,
            kind=kind,
            verb=verb,
            actor=actor.id if actor else "",
            actor_name=actor.name if actor else "",
            target=target.id if target else "",
            target_name=target.name if target else "",
            location=location_id,
            location_name=loc.name if loc else "",
        )
        ev.text = text
        ev.text_en = render_en(
            verb, ev.actor_name, ev.target_name, ev.location_name, text
        )
        self.bus.publish(ev)

    async def tick(self) -> None:
        item = self.scheduler.pop_next()
        if item is None:
            return
        self.now = max(self.now, item.minute)
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
            if decision.action == "move":
                self._interrupt_colocated(agent)

        # Reflection check (Level 3) fires only on accumulated importance.
        insights = await self.decisions.maybe_reflect(agent, self.now)
        for ins in insights:
            self._publish(
                "reflection", "insight", actor=agent,
                location_id=agent.state.location, text=ins,
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
        turns, signals, shared_rumors, confrontation = await self.decisions.run_conversation(
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
            self._publish(
                "action", "confronted", actor=a, target=b,
                location_id=a.state.location,
                text="admitted it" if confrontation["outcome"] == "admitted" else "denied it",
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
