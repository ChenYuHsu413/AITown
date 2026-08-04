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
import re
import unicodedata
from dataclasses import dataclass, field

from ..llm.prompts import builders
from ..llm.router import LLMRouter
from ..social.rumors import RumorRegistry
from ..social.secrets import SecretRegistry
from ..world.world import Observation, World
from .agent import Agent
from .core import Belief, MemoryItem

BELIEF_CONTEXT_MIN = 0.3      # a belief this confident (or more) colours dialogue + trust math

RUMOR_DISTORT_CHANCE = 0.35   # probability a shared rumor mutates in the retelling

CONFIDE_TRUST_BASE = 55       # base trust needed to confide; the bar rises with the secret's sensitivity
CONFIDE_MAX_P = 0.5           # cap on the per-conversation confide probability
LEAK_MAX_P = 0.35            # cap on the per-conversation leak probability

TALK_COOLDOWN_MIN = 90        # don't re-approach the same person within 90 sim-minutes
TALK_DURATION_MIN = 10
LOW_ENERGY = 20

# Social tiering (pure rules, before the cheap should_talk call). Close friends
# chat freely; acquaintances less; near-strangers rarely -- so the cheap-tier call
# volume stays bounded as the town grows and social circles form on their own.
SOCIAL_TIER_FRIEND = 55       # friendship >= this -> full rate
SOCIAL_TIER_ACQUAINT = 35     # 35..55 -> x0.6; below -> x0.3
INTROVERT_EXTRAVERSION = 0.45  # quiet personalities (Leo, Grace) damp their rate a further x0.7


_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# Latin letters glued straight onto CJK with no space -- the tell of weak-model
# "character soup" (e.g. "吃asks导"). Real zh keeps English proper nouns spaced
# and capitalized, so this stays clear of "覺得 David 最近...".
_GLUE_RE = re.compile(r"[㐀-䶿一-鿿][a-z]{3,}|[a-z]{3,}[㐀-䶿一-鿿]")


def _zh_text_ok(text: str) -> bool:
    """Gibberish gate for a single zh dialogue turn (the precondition for putting
    a new model on the zh chain). Rejects character-soup weak models emit:
      - the Unicode replacement char (encoding corruption),
      - a run of 4+ identical characters (a stuck loop),
      - latin letters glued into CJK with no spacing (mixed-script soup),
      - fewer than 60% CJK among the letters (excluding punctuation/space) --
        i.e. the "Chinese" turn isn't actually mostly Chinese.
    A rejected turn drops the whole conversation to the next provider / mock."""
    if "�" in text:
        return False
    if _GLUE_RE.search(text):
        return False
    if re.search(r"(.)\1{3,}", text):
        return False
    letters = [c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")]
    if not letters:
        return False
    cjk = sum(1 for c in letters if _CJK_RE.match(c))
    return cjk / len(letters) >= 0.6


def _dialogue_ok(result) -> bool:
    """Structural quality gate for a generated conversation (passed to the router
    so a bad result is retried / falls through instead of landing on-screen):
    real turns, each with non-empty, sensibly-sized text. Catches truncated JSON
    and the empty-"text" JSON that Groq's 70b intermittently emits.

    In a zh run it ALSO runs the gibberish gate per turn (``_zh_text_ok``), which
    is what lets a Chinese-capable OpenRouter model sit on the zh chain safely:
    any character-soup it emits is rejected and falls through to mock. English
    runs are unaffected (the zh check only applies when the language is zh)."""
    parsed = result.parsed
    if not isinstance(parsed, dict):
        return False
    turns = parsed.get("turns")
    if not isinstance(turns, list) or not turns or len(turns) > 12:
        return False
    zh = builders.lang_is_zh()
    for t in turns:
        if not isinstance(t, dict):
            return False
        text = str(t.get("text", "")).strip()
        if not text or len(text) > 500:
            return False
        if zh and not _zh_text_ok(text):
            return False
    return True


# Memory text stores {agent:id}/{loc:id}/{landmark:id} placeholders. For the LLM
# context we resolve them to pinyin/English (the model stays in an English name
# space); the UI resolves the same placeholders to zh/en names at display time.
_PLACEHOLDER_RE = re.compile(r"\{(agent|loc|landmark):([a-z_]+)\}")


def _resolve_placeholders(text: str, world: "World") -> str:
    def repl(m: "re.Match") -> str:
        kind, key = m.group(1), m.group(2)
        if kind == "agent":
            return key.capitalize()                       # pinyin name for the model
        if kind == "loc":
            loc = world.locations.get(key)
            return loc.name if loc is not None else key
        for loc in world.locations.values():              # landmark: find by id
            for lm in loc.landmarks:
                if lm.get("id") == key:
                    return lm.get("name", key)
        return key
    return _PLACEHOLDER_RE.sub(repl, text)


def _resolve_mems(mems: list[str], world: "World") -> list[str]:
    return [_resolve_placeholders(m, world) for m in mems]


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
    secrets: SecretRegistry = field(default_factory=SecretRegistry)
    # Per-sim-day dialogue budget (live only) -- keeps free-tier token spend
    # bounded. 0 = unlimited (mock / headless), so run_day is untouched.
    dialogue_cap: int = 0
    _dialogues_today: int = 0
    _dialogue_day: int = -1

    def __post_init__(self) -> None:
        live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        if live and self.dialogue_cap == 0:
            try:
                self.dialogue_cap = int(os.environ.get("AI_TOWN_DIALOGUE_CAP", "25"))
            except ValueError:
                self.dialogue_cap = 25

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
        dow = (now // (24 * 60)) % 7        # 0=Mon .. 6=Sun (Day 1 = Monday)
        entry = agent.routine.current(minute_of_day, dow)
        obs_text = obs.describe(world)
        self._record_observations(agent, world, now)
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
            decision = Decision("sleep", duration=agent.routine.next_boundary(minute_of_day, dow) - minute_of_day,
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
            if partner_free and now - last >= TALK_COOLDOWN_MIN and agent.state.current_action != "sleep" \
                    and self._social_gate(agent, partner_id, now):
                memories = await agent.memory.retrieve_async(f"{partner.name} {obs.location}", k=5)
                res = await self.router.generate(
                    task="should_talk",
                    messages=builders.should_talk_prompt(agent, partner.name, _resolve_mems(memories, world)),
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
                    agent, observation, _resolve_mems(memories, world), ["continue_routine", "seek_out"]
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
            # Betrayal: if this rumor was leaked from a secret, the owner knows only
            # someone they confided in could have known -- so they confront the LEAKER,
            # not the rumor's chain origin.
            if rumor and rumor.from_secret_id:
                secret = self.secrets.secrets.get(rumor.from_secret_id)
                leaker = world.agents.get(secret.leaked_by) if secret and secret.leaked_by else None
                if leaker is not None:
                    target = leaker
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
            # Day off: a shop owner on their shop's closing day, or an agent with a
            # personal weekly rest day (the postman on Sunday). They don't work or
            # patrol -- they relax at home in the morning and drift out as a free
            # agent later (meals still redirect them around town, incl. a rival's shop).
            own_shop = next((l for l in world.locations.values()
                             if l.owner == agent.id and l.price > 0), None)
            off_today = (dow in agent.profile.off_days) \
                or (own_shop is not None and dow in own_shop.closed_days)
            day_off_rest = off_today and (
                entry.action == "work"
                or (entry.action == "rest" and entry.location != agent.home)
            )
            if day_off_rest:
                if minute_of_day < 12 * 60:
                    dest = agent.home                          # sleep in / relax at home
                else:
                    opts = ["park", "market", agent.home]
                    dest = opts[(minute_of_day // 160) % len(opts)]   # drift around town
            # Economy: if the routine sends us to eat at a shop we're shunning
            # (a bad rumor about its owner) OR at a shop that's closed today (weekly
            # day off), take our custom to a rival shop that's open -- so a rumor
            # that hurts one shop, or a closing day, feeds the other. Home is the
            # last resort.
            dest_loc = world.locations.get(dest)
            shunned = agent.state.avoid_location and dest == agent.state.avoid_location
            closed = dest_loc is not None and dow in dest_loc.closed_days
            if entry.action == "eat" and (shunned or closed):
                rival = next(
                    (lid for lid, loc in world.locations.items()
                     if loc.price > 0 and loc.owner and lid != dest and dow not in loc.closed_days),
                    None,
                )
                dest = rival or agent.home

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

            until_next = agent.routine.next_boundary(minute_of_day, dow) - minute_of_day
            if agent.state.location != dest:
                decision = Decision(
                    "move", target_location=dest,
                    duration=10, reason=f"routine: head to {dest}",
                )
            else:
                act = "rest" if (park_rained_out or day_off_rest) else entry.action
                reason = ("routine: rest (rained out)" if park_rained_out
                          else "day off" if day_off_rest else f"routine: {entry.action}")
                decision = Decision(
                    act, duration=max(15, min(until_next, 120)), reason=reason,
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

    def _social_gate(self, agent: Agent, partner_id: str, now: int) -> bool:
        """Tiered pre-check before the cheap should_talk call. Returns True to let
        the LLM decide, False to skip by rule. Probability keys off how close the
        two already are (a fresh pair defaults to the neutral friendship 30, i.e.
        a stranger) and dampens for quiet personalities. Deterministic per
        (pair, minute) so mock runs stay reproducible."""
        rel = agent.relationships.get(partner_id)
        f = rel.friendship if rel is not None else 30.0
        p = 1.0 if f >= SOCIAL_TIER_FRIEND else 0.6 if f >= SOCIAL_TIER_ACQUAINT else 0.3
        if agent.profile.extraversion < INTROVERT_EXTRAVERSION:
            p *= 0.7
        if p >= 1.0:
            return True
        seed = int(hashlib.sha256(f"talk|{agent.id}|{partner_id}|{now}".encode()).hexdigest()[:8], 16)
        return random.Random(seed).random() < p

    def _record_observations(self, agent: Agent, world: World, now: int) -> None:
        """Pure-rules observable-activity memory. Noticing a neighbour at work on a
        landmark, or a landmark that has visibly advanced since you last looked
        (>= 0.25), leaves a light (importance 2) memory; ordinary rest/eat around
        you never does. Throttled per agent via ``seen_landmark_progress`` so a
        long stint beside the mural doesn't spam the same sighting."""
        if agent.state.current_action == "sleep":
            return
        loc = world.locations.get(agent.state.location)
        if loc is None or not loc.landmarks:
            return
        seen = agent.state.seen_landmark_progress
        for lm in loc.landmarks:
            cb = lm.get("created_by")
            if cb == agent.id:
                continue  # your own work earns the completion memory, not sightings
            lid, cur = lm["id"], lm["progress"]
            prev = seen.get(lid)
            worker = None
            if cb:
                o = world.agents.get(cb)
                if o is not None and o.state.location == agent.state.location \
                        and o.state.current_action == "work":
                    worker = o
            jumped = prev is not None and (cur - prev) >= 0.25
            if worker is not None and (prev is None or jumped):
                agent.memory.add(MemoryItem(
                    minute=now, importance=2,
                    text=f"Saw {{agent:{worker.id}}} working on {{landmark:{lid}}} at {{loc:{loc.id}}}.",
                ))
                seen[lid] = cur
            elif jumped:
                agent.memory.add(MemoryItem(
                    minute=now, importance=2,
                    text=f"{{landmark:{lid}}} at {{loc:{loc.id}}} has come along since I last saw it.",
                ))
                seen[lid] = cur
            elif prev is None:
                seen[lid] = cur  # first sight -> baseline it, no memory yet

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
                minute=now, text=f"{{agent:{a.id}}} told me people are saying: {text}",
                importance=8, kind="rumor", rumor_id=rumor.id,
            ))
            if rumor.sentiment < 0:
                b.state.mood = "upset"
            b.state.pending_concern = {"rumor_id": rumor.id, "told_by": a.id}
        else:
            b.memory.add(MemoryItem(
                minute=now, text=f"Heard from {{agent:{a.id}}}: {text}",
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

    # ---- secrets: confiding & leaking --------------------------------

    def _maybe_confide(self, a: Agent, b: Agent, now: int) -> dict | None:
        """Decide whether ``a`` confides one of their OWN secrets in ``b`` -- pure
        Python, no LLM. Gated on trust vs. the secret's sensitivity (more private ->
        higher bar); each secret is confided to a person at most once; a low mood
        makes confiding likelier. Returns a descriptor (for the prompt + a confide
        event) or None (the common case)."""
        rel = a.rel(b.id)
        eligible = [
            s for s in self.secrets.secrets_of(a.id)
            if not self.secrets.knows(s.id, b.id)                      # b isn't owner and hasn't been told
            and rel.trust >= CONFIDE_TRUST_BASE + s.sensitivity * 30
        ]
        if not eligible:
            return None
        secret = max(eligible, key=lambda s: s.sensitivity)            # the weightiest one they can share
        p = min(CONFIDE_MAX_P,
                0.15 + rel.trust / 200 + (0.1 if a.state.mood in ("worried", "upset") else 0.0))
        seed = int(hashlib.sha256(f"confide|{a.id}|{b.id}|{secret.id}|{now}".encode()).hexdigest()[:8], 16)
        if random.Random(seed).random() >= p:
            return None
        b.memory.add(MemoryItem(
            minute=now, text=f"{{agent:{a.id}}} confided in me: {secret.text}",
            importance=int(4 + secret.sensitivity * 4), kind="secret", secret_id=secret.id,
        ))
        self.secrets.record_confide(secret.id, b.id, now)
        rel.trust += 3; rel.clamp()                                    # confiding deepens the confider's trust
        br = b.rel(a.id); br.friendship += 4; br.clamp()              # being trusted feels good
        return {"secret_id": secret.id, "text": secret.text, "from": a.id, "to": b.id}

    async def _maybe_leak(self, b: Agent, c: Agent, world: World, now: int) -> dict | None:
        """Decide whether ``b`` leaks a secret they were confided (owner != b) to
        ``c``, turning it into a rumor. Rare: high-trust confidants almost never
        leak, a low-trust gossip is the danger. On a leak the secret becomes a
        rumor (leak_prompt gives third-person wording + sentiment) and enters the
        ordinary rumor lifecycle; returns a share-rumor descriptor (published as
        share_rumor -- the world only sees a rumor appear, never the leak itself)."""
        candidates = [
            s for s in self.secrets.secrets.values()
            if not s.leaked and s.owner != b.id and b.id in s.confided_to
            and s.owner != c.id and not self.secrets.knows(s.id, c.id)
        ]
        if not candidates:
            return None
        secret = candidates[0]
        trust_to_owner = b.rel(secret.owner).trust
        p = (1 - trust_to_owner / 100) * 0.3 * (1.2 - secret.sensitivity)
        if "gossipy" in b.profile.traits or "talkative" in b.profile.traits:
            p *= 1.5
        p = min(LEAK_MAX_P, p)
        seed = int(hashlib.sha256(f"leak|{b.id}|{c.id}|{secret.id}|{now}".encode()).hexdigest()[:8], 16)
        if random.Random(seed).random() >= p:
            return None

        owner = world.agents.get(secret.owner)
        owner_name = secret.owner.capitalize() if owner else secret.owner   # pinyin for the model
        res = await self.router.generate(       # rephrase to a 3rd-person rumor + appraise, in one call
            task="leak", messages=builders.leak_prompt(owner_name, secret.text),
            agent_id=b.id, sim_minute=now, schema={"type": "object"}, max_tokens=80,
        )
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        text = str(parsed.get("text") or "").strip() or f"{owner_name} has been hiding something."
        try:
            sentiment = max(-1.0, min(1.0, float(parsed.get("sentiment", -0.3))))
        except (TypeError, ValueError):
            sentiment = -0.3

        rumor = self.rumors.seed(
            agent_id=c.id, text=text, minute=now, subject=secret.owner,
            sentiment=sentiment, from_secret_id=secret.id,
        )
        self.secrets.mark_leaked(secret.id, leaked_by=b.id)
        # c now holds the leaked rumor: record it + apply the recipient relationship math.
        c.memory.add(MemoryItem(
            minute=now, text=f"Heard from {{agent:{b.id}}}: {text}", importance=5, kind="rumor", rumor_id=rumor.id))
        rel = c.rel(secret.owner)
        rel.trust += sentiment * 5; rel.friendship += sentiment * 2; rel.clamp()
        if sentiment <= -0.4:
            shop = next((lid for lid, loc in world.locations.items()
                         if loc.owner == secret.owner and loc.price > 0), "")
            if shop:
                c.state.avoid_location = shop
        gossip_bond = c.rel(b.id); gossip_bond.friendship += 1; gossip_bond.clamp()
        return {"rumor_id": rumor.id, "text": text, "from": b.id, "to": c.id}

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
        (``{"rumor_id", "outcome", "admitted"}``) or ``None`` for normal chats; the
        5th is ``confided`` (0-2 descriptors, one per direction someone opened up)
        for the engine to publish as content-free ``confide`` events."""
        is_confront = bool(confront_text and confront_rumor_id)
        fwd = await self._maybe_share_rumor(a, b, world, now)   # a -> b
        rev = await self._maybe_share_rumor(b, a, world, now)   # b -> a (lets a stationary initiator hear too)
        # Leaking a confided secret surfaces AS a rumor share (same descriptor shape).
        leak_fwd = await self._maybe_leak(a, b, world, now)     # a leaks a secret confided to a, to b
        leak_rev = await self._maybe_leak(b, a, world, now)
        shared_rumors = [sr for sr in (fwd, rev, leak_fwd, leak_rev) if sr]
        # Confiding is separate from gossip: a shares one of their OWN secrets with b.
        confide_fwd = self._maybe_confide(a, b, now)
        confide_rev = self._maybe_confide(b, a, now)
        confided = [c for c in (confide_fwd, confide_rev) if c]
        a_mem = await a.memory.retrieve_async(b.name, k=3)
        b_mem = await b.memory.retrieve_async(a.name, k=3)
        # A held impression of the other person rides into the dialogue context, so
        # the model naturally carries the weight of the relationship's history.
        a_imp = self._impression_of(a, b.id)
        b_imp = self._impression_of(b, a.id)
        res = await self.router.generate(
            task="dialogue",
            messages=builders.dialogue_prompt(
                a, b, _resolve_mems(a_mem, world), _resolve_mems(b_mem, world),
                a_wants_to_mention=confront_text or (fwd or leak_fwd or {}).get("text"),
                b_wants_to_mention=(rev or leak_rev or {}).get("text"),
                is_confrontation=is_confront,
                time_hint=builders.time_of_day(now),
                a_impression=a_imp, b_impression=b_imp,
                a_confide=confide_fwd["text"] if confide_fwd else None,
                b_confide=confide_rev["text"] if confide_rev else None,
                nearby_landmark=self._nearby_landmark(world, a.state.location),
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
        a.memory.add(MemoryItem(
            minute=now, text=f"Talked with {{agent:{b.id}}} at {{loc:{a.state.location}}}.",
            importance=3, kind="conversation"))
        b.memory.add(MemoryItem(
            minute=now, text=f"Talked with {{agent:{a.id}}} at {{loc:{b.state.location}}}.",
            importance=3, kind="conversation"))
        a.apply_conversation_signals(b.id, **signals)
        b.apply_conversation_signals(a.id, **signals)
        a.state.last_talk_minute[b.id] = now
        b.state.last_talk_minute[a.id] = now

        confrontation = None
        if is_confront:
            confrontation = self._settle_confrontation(a, b, world, confront_rumor_id, parsed, now)
        return turns, signals, shared_rumors, confrontation, confided

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

        # Betrayal: this rumor came from a secret ``a`` confided in ``b``. That is far
        # worse than idle gossip -- the owner is certain and the trust math bites hard.
        secret = self.secrets.secrets.get(rumor.from_secret_id) if rumor else None
        is_betrayal = bool(secret and secret.leaked_by == b.id)

        rel_ab = a.rel(b.id)
        if is_betrayal:
            rel_ab.trust -= 20
            rel_ab.friendship -= 12
            rel_ab.conflict += 15
            rel_ba = b.rel(a.id)
            rel_ba.conflict += 6          # the shame of being caught betraying a confidence
            rel_ba.clamp()
        elif admitted:
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

        if is_betrayal:
            a.memory.add(MemoryItem(
                minute=now, importance=9, kind="conversation",
                text=f"Confronted {{agent:{b.id}}} for leaking the secret I trusted them with. Betrayed.",
            ))
            b.memory.add(MemoryItem(
                minute=now, importance=8, kind="conversation",
                text=f"{{agent:{a.id}}} confronted me for leaking their secret.",
            ))
        else:
            a.memory.add(MemoryItem(
                minute=now, importance=6, kind="conversation",
                text=f"Confronted {{agent:{b.id}}} about the rumor. They {'admitted it' if admitted else 'denied it'}.",
            ))
            b.memory.add(MemoryItem(
                minute=now, importance=6, kind="conversation",
                text=f"{{agent:{a.id}}} confronted me about the rumor I "
                     f"{'started' if admitted else 'was accused of starting'}.",
            ))
        return {"rumor_id": rumor_id, "outcome": outcome, "admitted": admitted, "betrayal": is_betrayal}

    # ---- semantic memory (beliefs) -----------------------------------

    @staticmethod
    def _nearby_landmark(world: World, location_id: str) -> str | None:
        """A one-line environment note for a conversation happening where a landmark
        stands (e.g. ``"the mural, 70% complete"``) -- real models weave the setting
        into the talk on their own."""
        loc = world.locations.get(location_id)
        if loc is None or not loc.landmarks:
            return None
        lm = loc.landmarks[0]
        if lm.get("state") == "completed":
            return f"{lm['name']}, now complete"
        return f"{lm['name']}, {int(lm['progress'] * 100)}% complete"

    @staticmethod
    def _impression_of(agent: Agent, other_id: str) -> str | None:
        """The agent's lasting impression of ``other_id`` -- but only once it's
        confident enough to matter (else dialogue/trust stay uncoloured)."""
        b = agent.semantic.about(other_id)
        return b.text if b is not None and b.confidence >= BELIEF_CONTEXT_MIN else None

    @staticmethod
    def _resolve_subject(agent: Agent, world: World, raw: object) -> tuple[str, str] | None:
        """Map a belief's subject NAME (from the model) back to an id. Returns
        (id, display_name) or None when it matches nobody/nowhere (then dropped)."""
        n = str(raw or "").strip().lower()
        if not n:
            return None
        if n in ("self", agent.name.lower()):
            return ("self", agent.name)
        for a in world.agents.values():
            if a.name.lower() == n:
                return (a.id, a.name)
        for loc in world.locations.values():
            if loc.name.lower() == n:
                return (loc.id, loc.name)
        return None

    def _form_beliefs(self, agent: Agent, world: World, raw: object, now: int) -> list[dict]:
        """Merge the reflection's proposed beliefs into the agent's semantic
        memory (pure Python). Same subject -> reinforce (confidence up, source
        count up, new wording wins); new subject -> add, capped at 0.6. Returns
        one descriptor per formed/reinforced belief for the engine to publish."""
        events: list[dict] = []
        if not isinstance(raw, list):
            return events
        for rb in raw[:2]:                                # at most 2 per reflection
            if not isinstance(rb, dict):
                continue
            text = str(rb.get("text", "")).strip()
            if not text:
                continue
            resolved = self._resolve_subject(agent, world, rb.get("subject", ""))
            if resolved is None:                          # unmatched subject -> drop
                continue
            sid, sname = resolved
            try:
                conf = max(0.0, min(1.0, float(rb.get("confidence", 0.4))))
            except (TypeError, ValueError):
                conf = 0.4
            try:
                sent = max(-1.0, min(1.0, float(rb.get("sentiment", 0.0))))
            except (TypeError, ValueError):
                sent = 0.0

            existing = agent.semantic.about(sid)
            if existing is not None:                      # reinforce the same slot
                existing.text = text
                existing.confidence = min(1.0, existing.confidence * 0.7 + conf * 0.5)
                existing.sentiment = sent
                existing.source_count += 1
                existing.last_reinforced_minute = now
            else:                                         # brand-new impression -> cap at 0.6
                agent.semantic.beliefs.append(Belief(
                    subject=sid, text=text, confidence=min(0.6, conf), sentiment=sent,
                    formed_minute=now, last_reinforced_minute=now, source_count=1,
                ))
                agent.semantic._prune()
            events.append({"subject_id": sid, "subject_name": sname, "text": text})
        return events

    # ---- Level 3: reflection -----------------------------------------

    async def maybe_reflect(
        self, agent: Agent, world: World, now: int
    ) -> tuple[list[str], list[dict]]:
        # Individualized: quiet background characters (Grace, Mei) reflect less
        # often, trimming their smart-tier spend without silencing the leads.
        if agent.memory.importance_since_reflection < agent.profile.reflection_threshold:
            return [], []
        day_start = now - (now % (24 * 60))
        events = _resolve_mems(agent.memory.today(day_start), world)
        res = await self.router.generate(
            task="reflection",
            messages=builders.reflection_prompt(agent, events),
            agent_id=agent.id,
            sim_minute=now,
            schema={"type": "object"},
            max_tokens=300 if builders.lang_is_zh() else 200,
        )
        insights: list[str] = []
        belief_events: list[dict] = []
        if isinstance(res.parsed, dict):
            insights = [str(x) for x in res.parsed.get("insights", [])]
            belief_events = self._form_beliefs(agent, world, res.parsed.get("beliefs", []), now)
            self._maybe_new_secret(agent, res.parsed.get("new_secret"), now)
        for ins in insights:
            agent.memory.add(MemoryItem(minute=now, text=ins, importance=5, kind="reflection"))
        agent.memory.importance_since_reflection = 0
        return insights, belief_events

    def _maybe_new_secret(self, agent: Agent, raw: object, now: int) -> None:
        """Add a private matter surfaced by reflection (owner = the reflecting
        agent). Capped at 4 per agent so it can't run away; secrets carry no
        event -- they live only in the registry until confided or leaked."""
        if not isinstance(raw, dict):
            return
        text = str(raw.get("text", "")).strip()
        if not text or len(self.secrets.secrets_of(agent.id)) >= 4:
            return
        try:
            sensitivity = float(raw.get("sensitivity", 0.5))
        except (TypeError, ValueError):
            sensitivity = 0.5
        self.secrets.add(agent.id, text, sensitivity, now)
