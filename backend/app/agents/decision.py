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

import hashlib
import json
import os
import random
from dataclasses import dataclass, field

from ..llm.prompts import builders
from ..llm.router import LLMRouter
from ..social.rumors import RumorRegistry
from ..world.world import Observation, World
from .agent import Agent
from .core import MemoryItem

RUMOR_DISTORT_CHANCE = 0.35   # probability a shared rumor mutates in the retelling

TALK_COOLDOWN_MIN = 90        # don't re-approach the same person within 90 sim-minutes
TALK_DURATION_MIN = 10
LOW_ENERGY = 20


def _dialogue_ok(result) -> bool:
    """Structural quality gate for a generated conversation (passed to the router
    so a bad result is retried / falls through instead of landing on-screen):
    real turns, each with non-empty, sensibly-sized text. Catches truncated JSON
    and the empty-"text" JSON that Groq's 70b intermittently emits.

    Deliberately NOT a language check: keeping weak-at-Chinese models off the zh
    chain is handled by language-aware routing in the factory, and a structural-
    only gate lets the English mock serve as a readable last resort (a rare, plain
    fallback) rather than being rejected into an empty bubble."""
    parsed = result.parsed
    if not isinstance(parsed, dict):
        return False
    turns = parsed.get("turns")
    if not isinstance(turns, list) or not turns or len(turns) > 12:
        return False
    for t in turns:
        if not isinstance(t, dict):
            return False
        text = str(t.get("text", "")).strip()
        if not text or len(text) > 500:
            return False
    return True


@dataclass
class Decision:
    action: str                        # move | work | rest | eat | sleep | talk | idle
    target_location: str | None = None
    talk_partner: str | None = None
    duration: int = 15                 # minutes until next natural decision
    level: int = 0                     # 0 rules / 1 cheap / 2 normal / 3 smart
    reason: str = ""
    narrative_verb: str = ""           # non-routine story beat for the engine to publish (e.g. "seek_out")
    narrative_target: str = ""         # agent_id the narrative beat is aimed at
    confront_text: str = ""            # opener injected into the dialogue when confronting over a rumor
    confront_rumor_id: str = ""        # the rumor being confronted (set alongside confront_text)


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
    rumors: RumorRegistry = field(default_factory=RumorRegistry)
    # Per-sim-day dialogue budget (live only) -- keeps free-tier token spend
    # bounded. 0 = unlimited (mock / headless), so run_day is untouched.
    dialogue_cap: int = 0
    _dialogues_today: int = 0
    _dialogue_day: int = -1

    def __post_init__(self) -> None:
        live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        if live and self.dialogue_cap == 0:
            try:
                self.dialogue_cap = int(os.environ.get("AI_TOWN_DIALOGUE_CAP", "12"))
            except ValueError:
                self.dialogue_cap = 12

    def _roll_day(self, now: int) -> None:
        day = now // (24 * 60)
        if day != self._dialogue_day:
            self._dialogue_day = day
            self._dialogues_today = 0

    def dialogue_cap_reached(self, now: int) -> bool:
        """True once today's dialogue budget is spent -- the should_talk gate then
        says 'no' by rule, saving even the cheap LLM call. Resets each sim-day."""
        if self.dialogue_cap <= 0:
            return False
        self._roll_day(now)
        return self._dialogues_today >= self.dialogue_cap

    async def decide(
        self, agent: Agent, world: World, obs: Observation, now: int
    ) -> Decision:
        minute_of_day = now % (24 * 60)
        entry = agent.routine.current(minute_of_day)
        obs_text = obs.describe(world)
        memories: list[str] = []
        model_used = "rules"

        decision: Decision | None = None

        # ---- Level 2 (resume): chasing a rumor's source to confront ---
        if agent.state.seek_target and agent.state.current_action != "sleep":
            seek_dec = self._resume_seek(agent, world)
            if seek_dec is not None:
                self.traces.append(DecisionTrace(
                    minute=now, agent_id=agent.id, observation=obs_text,
                    retrieved_memories=[], decision=seek_dec, model="rules",
                ))
                return seek_dec
            # gave up (source unreachable) -> fall through to the routine

        # ---- Level 0: hard rules --------------------------------------
        if agent.state.current_action == "sleep" and entry.action == "sleep":
            decision = Decision("sleep", duration=agent.routine.next_boundary(minute_of_day) - minute_of_day,
                                reason="asleep per routine")
        elif agent.state.energy <= LOW_ENERGY and entry.action not in ("sleep", "rest"):
            decision = Decision("rest", duration=30, reason=f"energy {agent.state.energy} <= {LOW_ENERGY}")

        # ---- Level 1: social trigger (cheap LLM) ----------------------
        # Once the day's dialogue budget is spent, decline by rule -- no should_talk
        # LLM call either. Agents still meet and move; they just chat less.
        if decision is None and obs.arrivals and not self.dialogue_cap_reached(now):
            partner_id = obs.arrivals[0]
            partner = world.agents[partner_id]
            last = agent.state.last_talk_minute.get(partner_id, -10**9)
            partner_free = (
                partner.state.busy_until <= now
                and partner.state.current_action != "sleep"
            )
            if partner_free and now - last >= TALK_COOLDOWN_MIN and agent.state.current_action != "sleep":
                memories = await agent.memory.retrieve_async(f"{partner.name} {obs.location}", k=5)
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

        # ---- Level 2: react to a rumor about oneself ------------------
        if agent.state.pending_concern and agent.state.current_action != "sleep":
            concern = agent.state.pending_concern
            agent.state.pending_concern = None  # react only once, whatever we choose
            told_by = world.agents.get(concern.get("told_by", ""))
            rumor = self.rumors.rumors.get(concern.get("rumor_id", ""))
            mine = [v for v in rumor.versions if v.agent_id == agent.id] if rumor else []
            heard = mine[-1].text if mine else "something about me"
            memories = await agent.memory.retrieve_async(heard, k=5)
            observation = (
                f"You heard people are saying something about you: {heard}. "
                f"{told_by.name if told_by else 'Someone'} told you this."
            )
            res = await self.router.generate(
                task="decision",
                messages=builders.decision_prompt(
                    agent, observation, memories, ["continue_routine", "seek_out"]
                ),
                agent_id=agent.id,
                sim_minute=now,
                schema={"type": "object"},
                max_tokens=80,
            )
            model_used = f"{res.provider}/{res.model}"
            action = str(res.parsed.get("action", "")) if isinstance(res.parsed, dict) else ""
            # Confront the SOURCE of the rumor, not just the messenger. Fall back
            # to the messenger only if the origin is unknown (or is the messenger).
            origin = world.agents.get(rumor.origin) if rumor else None
            target = origin or told_by
            if action == "seek_out" and target is not None:
                confront_text = f"I heard people are saying: {heard}. Did this come from you?"
                if target.state.location == agent.state.location and target.state.current_action != "sleep":
                    decision = Decision(  # already together -> confront right now
                        action="talk", talk_partner=target.id, duration=TALK_DURATION_MIN,
                        level=2, reason=f"confronting {target.name} about a rumor",
                        confront_text=confront_text, confront_rumor_id=rumor.id if rumor else "",
                    )
                else:                     # elsewhere -> go find them, then confront on arrival
                    agent.state.seek_target = target.id
                    agent.state.seek_text = confront_text
                    agent.state.seek_rumor_id = rumor.id if rumor else ""
                    agent.state.seek_tries = 0
                    decision = Decision(
                        action="move", target_location=target.state.location,
                        duration=10, level=2,
                        reason=f"heard a rumor about self; seeking out {target.name}",
                        narrative_verb="seek_out", narrative_target=target.id,
                    )
            # continue_routine: leave decision as-is so the routine default fills it.

        # ---- Level 0 default: follow the routine ----------------------
        if decision is None:
            dest = entry.location
            # Economy: if the routine sends us to eat at a shop we're shunning
            # (heard a bad rumor about its owner), eat at home instead.
            if entry.action == "eat" and agent.state.avoid_location and dest == agent.state.avoid_location:
                dest = agent.home

            # ---- world events (pure Level 0, no LLM) ------------------
            rain = world.effect_active("rain")
            festival = world.effect_active("festival")
            # Festival: spend downtime (rest slots) at the festivities; work and
            # sleep are untouched, so people only drift over once they're off.
            if festival and entry.action == "rest":
                dest = festival["location"]
            # Rain (overrides festival): no one goes to a park -- head home instead,
            # and those already at the park leave on their next decision.
            park_rained_out = False
            if rain:
                d_loc = world.locations.get(dest)
                if d_loc is not None and d_loc.kind == "park":
                    dest = agent.home
                    park_rained_out = True
            # The festival crowd is in good spirits.
            if festival and agent.state.location == festival["location"]:
                agent.state.mood = "happy"

            until_next = agent.routine.next_boundary(minute_of_day) - minute_of_day
            if agent.state.location != dest:
                decision = Decision(
                    "move", target_location=dest,
                    duration=10, reason=f"routine: head to {dest}",
                )
            else:
                act = "rest" if park_rained_out else entry.action
                decision = Decision(
                    act, duration=max(15, min(until_next, 120)),
                    reason="routine: rest (rained out)" if park_rained_out else f"routine: {entry.action}",
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

    def _resume_seek(self, agent: Agent, world: World) -> Decision | None:
        """Continue a confrontation-in-progress. Returns a talk (reached them),
        a move (chase again, max 2 tries), or None (gave up -> clears state)."""
        target = world.agents.get(agent.state.seek_target)
        text = agent.state.seek_text
        rumor_id = agent.state.seek_rumor_id
        if target is not None and target.state.location == agent.state.location \
                and target.state.current_action != "sleep":
            agent.state.seek_target = ""
            agent.state.seek_text = ""
            agent.state.seek_rumor_id = ""
            agent.state.seek_tries = 0
            return Decision(
                action="talk", talk_partner=target.id, duration=TALK_DURATION_MIN,
                level=2, reason=f"confronting {target.name} about a rumor",
                confront_text=text, confront_rumor_id=rumor_id,
            )
        if target is not None and agent.state.seek_tries < 2:
            agent.state.seek_tries += 1
            return Decision(
                action="move", target_location=target.state.location, duration=10,
                level=2, reason=f"still looking for {target.name}",
                narrative_verb="seek_out", narrative_target=target.id,
            )
        agent.state.seek_target = ""   # unreachable -> give up
        agent.state.seek_text = ""
        agent.state.seek_rumor_id = ""
        agent.state.seek_tries = 0
        return None

    # ---- Level 2: one-call conversation ------------------------------

    async def _maybe_share_rumor(self, a: Agent, b: Agent, world: World, now: int) -> dict | None:
        """Decide (in the decision layer) whether ``a`` passes a rumor to ``b``.
        On success: records b's memory + the registry spread, and returns a
        descriptor for the engine to publish as an event. Returns None when
        nothing is shared (the common case -- keeps rumor-free runs untouched)."""
        candidates = [
            (r, v) for r, v in self.rumors.known_by(a.id)
            if not self.rumors.knows(r.id, b.id) and not r.resolved  # resolved rumors stop spreading
        ]
        if not candidates:
            return None
        rumor, version = max(candidates, key=lambda rv: rv[1].minute)  # most recently heard

        p = (
            0.25
            + 0.5 * a.profile.extraversion
            + (0.2 if "gossipy" in a.profile.traits else 0.0)
            + (0.15 if "talkative" in a.profile.traits else 0.0)
        )
        p = min(p, 0.95)
        seed = int(hashlib.sha256(f"{a.id}|{b.id}|{rumor.id}|{now}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        if rng.random() >= p:
            return None

        text = version.text
        if rng.random() < RUMOR_DISTORT_CHANCE:
            res = await self.router.generate(
                task="distort",
                messages=builders.distort_prompt(text),
                agent_id=a.id,
                sim_minute=now,
                schema={"type": "object"},
                max_tokens=80,
            )
            if isinstance(res.parsed, dict) and res.parsed.get("text"):
                text = str(res.parsed["text"])

        if rumor.subject and rumor.subject == b.id:
            # The rumor made it back to the person it is about -- no relationship
            # math against oneself; instead it lands hard and demands a reaction.
            b.memory.add(MemoryItem(
                minute=now, text=f"{a.name} told me people are saying: {text}",
                importance=8, kind="rumor", rumor_id=rumor.id,
            ))
            if rumor.sentiment < 0:
                b.state.mood = "upset"
            b.state.pending_concern = {"rumor_id": rumor.id, "told_by": a.id}
        else:
            b.memory.add(MemoryItem(
                minute=now, text=f"Heard from {a.name}: {text}",
                importance=4, kind="rumor", rumor_id=rumor.id,
            ))
            # Relationship math stays in Python; the rumor's polarity moves it.
            if rumor.subject:
                rel = b.rel(rumor.subject)
                rel.trust += rumor.sentiment * 5
                rel.friendship += rumor.sentiment * 2
                rel.clamp()
                if rumor.sentiment <= -0.4:
                    # A strongly negative rumor about a shopkeeper drives custom away:
                    # b now shuns any shop that subject runs (economy bite).
                    shop = next((lid for lid, loc in world.locations.items()
                                 if loc.owner == rumor.subject and loc.price > 0), "")
                    if shop:
                        b.state.avoid_location = shop
                    if rel.friendship > 55:
                        b.state.mood = "worried"  # a friend is being badmouthed
            # Sharing gossip brings the two a little closer.
            gossip_bond = b.rel(a.id)
            gossip_bond.friendship += 1
            gossip_bond.clamp()

        self.rumors.record_spread(rumor.id, a.id, b.id, text, now)
        return {"rumor_id": rumor.id, "text": text, "from": a.id, "to": b.id}

    async def run_conversation(
        self, a: Agent, b: Agent, world: World, now: int, confront_text: str | None = None,
        confront_rumor_id: str = "",
    ) -> tuple[list[dict], dict, list[dict], dict | None]:
        """One LLM call produces the whole exchange (played back turn by
        turn in the UI later) + numeric relationship signals. Gossip flows
        both ways: each side may pass the other a rumor. ``shared_rumors``
        (0-2 entries, one per direction) carries the passed-on wordings for
        the engine to publish. ``confront_text``, when set, is what ``a`` opens
        with (a rumor confrontation) and takes priority over any forward share;
        with ``confront_rumor_id`` also set the exchange is a confrontation that
        settles that rumor. The 4th return value is a confrontation descriptor
        (``{"rumor_id", "outcome", "admitted"}``) or ``None`` for normal chats."""
        is_confront = bool(confront_text and confront_rumor_id)
        fwd = await self._maybe_share_rumor(a, b, world, now)   # a -> b
        rev = await self._maybe_share_rumor(b, a, world, now)   # b -> a (lets a stationary initiator hear too)
        shared_rumors = [sr for sr in (fwd, rev) if sr]
        a_mem = await a.memory.retrieve_async(b.name, k=3)
        b_mem = await b.memory.retrieve_async(a.name, k=3)
        res = await self.router.generate(
            task="dialogue",
            messages=builders.dialogue_prompt(
                a, b, a_mem, b_mem,
                a_wants_to_mention=confront_text or (fwd["text"] if fwd else None),
                b_wants_to_mention=rev["text"] if rev else None,
                is_confrontation=is_confront,
                time_hint=builders.time_of_day(now),
            ),
            agent_id=a.id,
            sim_minute=now,
            schema={"type": "object"},
            # Chinese runs ~2-3x the tokens/char of English; 4 turns + JSON needs
            # more headroom or the last turn truncates.
            max_tokens=600 if builders.lang_is_zh() else 400,
            validate=_dialogue_ok,
        )
        self._roll_day(now)
        self._dialogues_today += 1   # count every conversation against the daily budget
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

        confrontation = None
        if is_confront:
            confrontation = self._settle_confrontation(a, b, world, confront_rumor_id, parsed, now)
        return turns, signals, shared_rumors, confrontation

    def _settle_confrontation(
        self, a: Agent, b: Agent, world: World, rumor_id: str, parsed: dict, now: int
    ) -> dict:
        """Confrontation consequences (pure Python math -- the LLM only emits the
        admit/deny verdict). ``a`` confronted ``b`` about ``rumor_id``. Whatever
        the answer, the rumor is now settled and stops spreading."""
        admitted = bool(parsed.get("admitted", False))
        outcome = "admitted" if admitted else "denied"
        rumor = self.rumors.rumors.get(rumor_id)
        self.rumors.resolve(rumor_id, now, outcome)   # the propagation terminus

        # Truth is out: customers who were shunning the subject's shop drift back.
        subj = rumor.subject if rumor else ""
        if subj:
            shops = {lid for lid, loc in world.locations.items() if loc.owner == subj}
            for ag in world.agents.values():
                if ag.state.avoid_location in shops:
                    ag.state.avoid_location = ""

        rel_ab = a.rel(b.id)
        if admitted:
            rel_ab.trust -= 12
            rel_ab.conflict += 10
            rel_ab.friendship -= 6
            rel_ba = b.rel(a.id)
            rel_ba.conflict += 5          # the sting of being caught out
            rel_ba.clamp()
        else:
            rel_ab.trust -= 5
            rel_ab.conflict += 4          # lingering half-belief
        rel_ab.clamp()
        a.state.mood = "neutral"          # matter settled, whatever the outcome -- mood lands

        a.memory.add(MemoryItem(
            minute=now, importance=6, kind="conversation",
            text=f"Confronted {b.name} about the rumor. They {'admitted it' if admitted else 'denied it'}.",
        ))
        b.memory.add(MemoryItem(
            minute=now, importance=6, kind="conversation",
            text=f"{a.name} confronted me about the rumor I "
                 f"{'started' if admitted else 'was accused of starting'}.",
        ))
        return {"rumor_id": rumor_id, "outcome": outcome, "admitted": admitted}

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
            max_tokens=300 if builders.lang_is_zh() else 200,
        )
        insights = []
        if isinstance(res.parsed, dict):
            insights = [str(x) for x in res.parsed.get("insights", [])]
        for ins in insights:
            agent.memory.add(MemoryItem(minute=now, text=ins, importance=5, kind="reflection"))
        agent.memory.importance_since_reflection = 0
        return insights
