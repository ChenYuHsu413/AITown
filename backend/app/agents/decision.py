"""Decision System -- the 4-level funnel.

    Simulation tick
          ↓
    Need decision?
     ┌────┴─────┐
     NO         YES
     │           │
    Rules      LLM Router (cheap → normal → smart)

Level 0 (rules)  : follow routine, low-energy rest, sleep, movement
Level 1 (cheap)  : "should I talk to this person?", memory importance
Level 2 (normal) : dialogue generation, off-routine action choice
Level 3 (smart)  : end-of-day reflection

Every decision also returns a DecisionTrace so nothing is a black box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..llm.prompts import builders
from ..llm.router import LLMRouter
from ..world.world import Observation, World
from .agent import Agent
from .core import MemoryItem

TALK_COOLDOWN_MIN = 90        # don't re-approach the same person within 90 sim-minutes
TALK_DURATION_MIN = 10
LOW_ENERGY = 20


@dataclass
class Decision:
    action: str                        # move | work | rest | eat | sleep | talk | idle
    target_location: str | None = None
    talk_partner: str | None = None
    duration: int = 15                 # minutes until next natural decision
    level: int = 0                     # 0 rules / 1 cheap / 2 normal / 3 smart
    reason: str = ""


@dataclass
class DecisionTrace:
    minute: int
    agent_id: str
    observation: str
    retrieved_memories: list[str]
    decision: Decision
    model: str = "rules"

    def render(self) -> str:
        mems = "\n".join(f"    {i+1}. {m}" for i, m in enumerate(self.retrieved_memories)) or "    (none)"
        return (
            f"  Agent: {self.agent_id}\n"
            f"  Observation: {self.observation}\n"
            f"  Memories:\n{mems}\n"
            f"  Decision: {self.decision.action}"
            + (f" -> {self.decision.talk_partner}" if self.decision.talk_partner else "")
            + f"  (L{self.decision.level}, {self.model})\n"
            f"  Reason: {self.decision.reason}"
        )


@dataclass
class DecisionEngine:
    router: LLMRouter
    traces: list[DecisionTrace] = field(default_factory=list)

    async def decide(
        self, agent: Agent, world: World, obs: Observation, now: int
    ) -> Decision:
        minute_of_day = now % (24 * 60)
        entry = agent.routine.current(minute_of_day)
        obs_text = obs.describe(world)
        memories: list[str] = []
        model_used = "rules"

        decision: Decision | None = None

        # ---- Level 0: hard rules --------------------------------------
        if agent.state.current_action == "sleep" and entry.action == "sleep":
            decision = Decision("sleep", duration=agent.routine.next_boundary(minute_of_day) - minute_of_day,
                                reason="asleep per routine")
        elif agent.state.energy <= LOW_ENERGY and entry.action not in ("sleep", "rest"):
            decision = Decision("rest", duration=30, reason=f"energy {agent.state.energy} <= {LOW_ENERGY}")

        # ---- Level 1: social trigger (cheap LLM) ----------------------
        if decision is None and obs.arrivals:
            partner_id = obs.arrivals[0]
            partner = world.agents[partner_id]
            last = agent.state.last_talk_minute.get(partner_id, -10**9)
            partner_free = (
                partner.state.busy_until <= now
                and partner.state.current_action != "sleep"
            )
            if partner_free and now - last >= TALK_COOLDOWN_MIN and agent.state.current_action != "sleep":
                memories = agent.memory.retrieve(f"{partner.name} {obs.location}", k=5)
                res = await self.router.generate(
                    task="should_talk",
                    messages=builders.should_talk_prompt(agent, partner.name, memories),
                    agent_id=agent.id,
                    sim_minute=now,
                    schema={"type": "object"},
                    cache_key=f"{agent.id}|{partner_id}|{agent.state.mood}|{obs.location}",
                    max_tokens=60,
                )
                model_used = f"{res.provider}/{res.model}"
                if isinstance(res.parsed, dict) and res.parsed.get("talk"):
                    decision = Decision(
                        "talk",
                        talk_partner=partner_id,
                        duration=TALK_DURATION_MIN,
                        level=1,
                        reason=str(res.parsed.get("reason", "wants to chat")),
                    )

        # ---- Level 0 default: follow the routine ----------------------
        if decision is None:
            until_next = agent.routine.next_boundary(minute_of_day) - minute_of_day
            if agent.state.location != entry.location:
                decision = Decision(
                    "move", target_location=entry.location,
                    duration=10, reason=f"routine: head to {entry.location}",
                )
            else:
                decision = Decision(
                    entry.action, duration=max(15, min(until_next, 120)),
                    reason=f"routine: {entry.action}",
                )
            model_used = "rules" if decision.level == 0 else model_used

        trace = DecisionTrace(
            minute=now,
            agent_id=agent.id,
            observation=obs_text,
            retrieved_memories=memories,
            decision=decision,
            model=model_used,
        )
        self.traces.append(trace)
        return decision

    # ---- Level 2: one-call conversation ------------------------------

    async def run_conversation(
        self, a: Agent, b: Agent, world: World, now: int
    ) -> tuple[list[dict], dict]:
        """One LLM call produces the whole exchange (played back turn by
        turn in the UI later) + numeric relationship signals."""
        a_mem = a.memory.retrieve(b.name, k=3)
        b_mem = b.memory.retrieve(a.name, k=3)
        res = await self.router.generate(
            task="dialogue",
            messages=builders.dialogue_prompt(a, b, a_mem, b_mem),
            agent_id=a.id,
            sim_minute=now,
            schema={"type": "object"},
            max_tokens=400,
        )
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        turns = parsed.get("turns", [])
        signals = {
            "sentiment": float(parsed.get("sentiment", 0.5)),
            "trust_signal": float(parsed.get("trust_signal", 0.0)),
            "conflict_signal": float(parsed.get("conflict_signal", 0.0)),
        }

        # Both sides remember the conversation (importance via cheap tier
        # is skipped here: conversation gets a flat importance; a real
        # importance call is wired for notable events in the engine).
        summary = f"Talked with {b.name} at {world.locations[a.state.location].name}."
        a.memory.add(MemoryItem(minute=now, text=summary, importance=3, kind="conversation"))
        b.memory.add(
            MemoryItem(
                minute=now,
                text=f"Talked with {a.name} at {world.locations[b.state.location].name}.",
                importance=3,
                kind="conversation",
            )
        )
        a.apply_conversation_signals(b.id, **signals)
        b.apply_conversation_signals(a.id, **signals)
        a.state.last_talk_minute[b.id] = now
        b.state.last_talk_minute[a.id] = now
        return turns, signals

    # ---- Level 3: reflection -----------------------------------------

    async def maybe_reflect(self, agent: Agent, now: int, threshold: int = 25) -> list[str]:
        if agent.memory.importance_since_reflection < threshold:
            return []
        day_start = now - (now % (24 * 60))
        events = agent.memory.today(day_start)
        res = await self.router.generate(
            task="reflection",
            messages=builders.reflection_prompt(agent, events),
            agent_id=agent.id,
            sim_minute=now,
            schema={"type": "object"},
            max_tokens=200,
        )
        insights = []
        if isinstance(res.parsed, dict):
            insights = [str(x) for x in res.parsed.get("insights", [])]
        for ins in insights:
            agent.memory.add(MemoryItem(minute=now, text=ins, importance=5, kind="reflection"))
        agent.memory.importance_since_reflection = 0
        return insights
