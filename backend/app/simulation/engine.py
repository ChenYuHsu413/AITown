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
    minute: int
    kind: str          # action | dialogue | reflection | system
    text: str
    agent_id: str = ""

    def render(self) -> str:
        return f"[{fmt_time(self.minute)}] {self.text}"


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
            await self._handle_conversation(agent, decision.talk_partner)
        else:
            text = self.world.execute(
                agent, decision.action, decision.target_location, self.now, decision.duration
            )
            if text:
                self.bus.publish(Event(self.now, "action", text, agent.id))
            if decision.action == "move":
                self._interrupt_colocated(agent)

        # Reflection check (Level 3) fires only on accumulated importance.
        insights = await self.decisions.maybe_reflect(agent, self.now)
        for ins in insights:
            self.bus.publish(Event(self.now, "reflection", f"💭 {agent.name}: {ins}", agent.id))

        self.scheduler.schedule(agent.id, self.now + decision.duration)

    async def _handle_conversation(self, a: Agent, partner_id: str) -> None:
        b = self.world.agents[partner_id]
        if b.state.busy_until > self.now or b.state.current_action == "sleep":
            # Partner got occupied since the decision; retry shortly.
            self.scheduler.schedule(a.id, self.now + 5)
            return
        turns, signals = await self.decisions.run_conversation(a, b, self.world, self.now)
        a.state.current_action = "talk"
        b.state.current_action = "talk"
        duration = max(6, len(turns) * 2)
        a.state.busy_until = self.now + duration
        b.state.busy_until = self.now + duration
        loc = self.world.locations[a.state.location].name
        self.bus.publish(
            Event(self.now, "action", f"{a.name} started talking with {b.name} at {loc}", a.id)
        )
        for i, turn in enumerate(turns):
            self.bus.publish(
                Event(self.now + i, "dialogue", f"💬 {turn.get('speaker', '?')}: {turn.get('text', '')}")
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
