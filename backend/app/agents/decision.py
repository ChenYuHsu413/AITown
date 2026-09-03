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
from ..llm.usage import LLMCall
from ..social.rumors import RumorRegistry
from ..social.secrets import SecretRegistry
from ..world.world import Observation, World
from . import chapters as chapters_mod
from . import romance as romance_mod
from . import transitions as transitions_mod
from . import wishes as wishes_mod
from .agent import Agent
from .core import Belief, MemoryItem

BELIEF_CONTEXT_MIN = 0.3      # a belief this confident (or more) colours dialogue + trust math

RUMOR_DISTORT_CHANCE = 0.35   # probability a shared rumor mutates in the retelling

CONFIDE_TRUST_BASE = 55       # base trust needed to confide; the bar rises with the secret's sensitivity
CONFIDE_MAX_P = 0.5           # cap on the per-conversation confide probability
LEAK_MAX_P = 0.35            # cap on the per-conversation leak probability

# Per-PROVIDER timeout for dialogue generation (seconds). A provider slower than
# this is abandoned and the chain falls through to the next one (recorded normally),
# so a genuinely hung model hands off instead of dropping the whole conversation to
# the mock floor. Set ABOVE DeepSeek's full latency tail (measured p95 ~18s, worst-
# case tail to the low 40s) on purpose: DeepSeek is the quality anchor for zh, and
# the free fallbacks (Gemma) fail the strict zh gate far more often, so cutting
# DeepSeek early trades a slow turn for a likely mock floor. Give it the room to
# answer; hand off only when it truly hangs. Widened 40s -> 60s under the "rather
# slow than canned" policy: the user explicitly accepts a slower turn, and the
# non-blocking engine means the extra wait only makes the two speakers "brew" a
# little longer -- it never freezes the town. Env-overridable.
try:
    DIALOGUE_PROVIDER_TIMEOUT_S: float = float(os.environ.get("AI_TOWN_DIALOGUE_PROVIDER_TIMEOUT", "60") or "60")
except ValueError:
    DIALOGUE_PROVIDER_TIMEOUT_S = 60.0

# Per-provider timeout for the cheap tasks that run SYNCHRONOUSLY inside a tick
# (should_talk, decision, distort, leak). These block the pacing loop until they
# return, so a single hung provider here would freeze the whole town (clock, agents,
# broadcasts) -- the observed night "freeze". 10s is generous for a cheap/short call;
# past that we abandon the provider and fall through the chain, so any one hung
# provider steals at most ~10s instead of parking the loop. Dialogue/reflection are
# backgrounded (own longer timeouts) and deliberately excluded. Env-overridable.
try:
    SYNC_CALL_TIMEOUT_S: float = float(os.environ.get("AI_TOWN_SYNC_CALL_TIMEOUT", "10") or "10")
except ValueError:
    SYNC_CALL_TIMEOUT_S = 10.0

TALK_COOLDOWN_MIN = 90        # don't re-approach the same person within 90 sim-minutes
TALK_DURATION_MIN = 10
LOW_ENERGY = 20

# Social initiative (maybe_arrange_meetup): a resident may, at most once every few days,
# arrange to meet a friend at a shared free window later that day.
MEETUP_DAILY_P = 0.18            # per-eligible-agent daily chance to try to arrange one
MEETUP_FRIEND_MIN = 45          # only invite someone you're at least this friendly with
MEETUP_PERSON_COOLDOWN = 3      # a person initiates at most once per this many sim-days
MEETUP_PAIR_COOLDOWN = 7        # a given pair meets at most once per this many sim-days
MEETUP_WINDOW_MIN = 180         # attend within this many sim-minutes of the appointed time, else drop
MEETUP_SOCIAL_ACTIONS = frozenset({"idle", "rest", "eat"})  # routine states open to a drop-in

# Words that named a PRIOR run's world -- e.g. a park "mural" (壁畫), long since
# replaced by a light installation. A generated turn mentioning one is almost
# certainly stale context bleeding through (the old culprit was a hardcoded mock
# line; the real model is fenced against inventing facts). We LOG it rather than
# reject -- the false-positive rate on a legitimate mention is unknown -- so the
# frequency can be watched and confirmed trending to zero. Extend as new artifacts
# from retired runs surface.
STALE_WORLD_TERMS = ("mural", "壁畫")

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


# Diagnostic mode: when set, every zh-gate rejection prints the offending turn text
# plus a reason code (grep '[gate-reject]'), so a sampling run can judge whether the
# gate is false-positiving on legitimate dialogue. Off by default -- pure observability,
# never changes behaviour.
_GATE_DIAG = os.environ.get("AI_TOWN_GATE_DIAG") == "1"


def _has_cjk(text: str) -> bool:
    """True if the text contains any CJK -- used to reject a zh hallucination in
    English-canonical knowledge (distort/leak), which must stay English so the
    version chain has one language and the display layer can translate it."""
    return bool(_CJK_RE.search(text or ""))


_BELIEF_JUNK = {
    "ok", "okay", "none", "n/a", "na", "good", "fine", "bad", "nothing", "idk",
    "unknown", "yes", "no", "true", "false", "null", "nil", "...", "…", "n a",
}


def belief_text_ok(text: str, world: "World") -> bool:
    """Quality gate for reflection free text (lasting impressions + new secrets):
    reject the model's throwaway filler so 'ok' never lands in memory. Rejects
    generic non-answers; otherwise a CJK sentence (dense script) needs only a few
    characters, while a Latin sentence needs >=15 chars and either >=4 words or an
    explicit reference to a known resident/place. CJK-aware so historical Chinese
    knowledge is never mistaken for filler."""
    t = (text or "").strip()
    if not t:
        return False
    if t.lower().strip(" .!?\"'…").replace("/", " ").strip() in _BELIEF_JUNK:
        return False
    if _CJK_RE.search(t):                 # zh: a handful of characters is already a real sentence
        return len(t) >= 6
    if len(t) < 15:
        return False
    words = t.split()
    if len(words) >= 4:
        return True
    names = ({a.name.lower() for a in world.agents.values()}
             | {a.id for a in world.agents.values()}
             | {loc.name.lower() for loc in world.locations.values()}
             | set(world.locations))
    return any(w.lower().strip(".,;:!?'\"") in names for w in words)


def _zh_reject_reason(text: str) -> str | None:
    """Gibberish gate for a single zh dialogue turn, as a reason code (or None if the
    turn is clean). This is the precondition for putting a new model on the zh chain.
    Rejects the character-soup weak models emit:
      - the Unicode replacement char (encoding corruption),
      - latin letters glued into CJK with no spacing (mixed-script soup),
      - a long run of identical characters (a stuck decode loop),
      - fewer than 60% CJK among the letters (excluding punctuation/space) --
        i.e. the "Chinese" turn isn't actually mostly Chinese.
    A rejected turn drops the whole conversation to the next provider / mock.

    Refined against a live sample of rejections (task 1.2): the stuck-loop rule now
    fires at 6+ (was 4+) identical chars and never on CJK, so a genuine laugh/stall
    ("哈哈哈哈", "嗯嗯嗯嗯", "……") -- normal casual zh -- is not mistaken for soup; and
    the CJK-ratio check ignores ASCII digits/symbols so a turn quoting a time, price,
    or number ("晚上 7:30 老地方") is not demoted to a canned line."""
    if "�" in text:
        return "replacement-char"
    if _GLUE_RE.search(text):
        return "latin-glue"
    # A stuck decode loop repeats the SAME non-CJK char many times (e.g. "!!!!!!!");
    # in Chinese, repeated characters are ordinary emphasis/laughter, so exempt CJK
    # and only trip on a long run (6+).
    m = re.search(r"(.)\1{5,}", text)
    if m and not _CJK_RE.match(m.group(1)):
        return "char-repeat"
    # Count script only over "word" characters: CJK + Latin letters. Punctuation,
    # spaces, digits and symbols don't count either way, so a turn that legitimately
    # carries a clock time or a price isn't dragged under the 60% CJK bar by its digits.
    cjk = latin = 0
    for c in text:
        if _CJK_RE.match(c):
            cjk += 1
        elif c.isalpha():
            latin += 1
    total = cjk + latin
    if total == 0:
        return "no-letters"
    if cjk / total < 0.6:
        return f"low-cjk({cjk}/{total})"
    return None


def _zh_text_ok(text: str) -> bool:
    """Bool wrapper over ``_zh_reject_reason``; a rejected turn drops the whole
    conversation to the next provider / mock."""
    return _zh_reject_reason(text) is None


def _zh_reject_reason_legacy(text: str) -> str | None:
    """The pre-refinement gate, kept ONLY for the diagnostic comparison (task 1.2):
    a turn the legacy gate rejected but ``_zh_reject_reason`` now passes is a recovered
    false-positive -- direct evidence the refinement was warranted. Not used in the
    live path."""
    if "�" in text:
        return "replacement-char"
    if _GLUE_RE.search(text):
        return "latin-glue"
    if re.search(r"(.)\1{3,}", text):
        return "char-repeat"
    letters = [c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")]
    if not letters:
        return "no-letters"
    cjk = sum(1 for c in letters if _CJK_RE.match(c))
    if cjk / len(letters) < 0.6:
        return f"low-cjk({cjk}/{len(letters)})"
    return None


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
    prov = f"{getattr(result, 'provider', '?')}/{getattr(result, 'model', '?')}"
    for t in turns:
        if not isinstance(t, dict):
            return False
        text = str(t.get("text", "")).strip()
        if not text or len(text) > 500:
            return False
        if zh:
            reason = _zh_reject_reason(text)
            if reason is not None:
                if _GATE_DIAG:  # task 1.2: capture the rejected sample for over-kill review
                    print(f"[gate-reject] {prov} reason={reason} text={text[:140]!r}", flush=True)
                return False
            if _GATE_DIAG:  # a turn the OLD gate would have wrongly killed but we now pass
                legacy = _zh_reject_reason_legacy(text)
                if legacy is not None:
                    print(f"[gate-rescue] {prov} was={legacy} text={text[:140]!r}", flush=True)
    return True


# ---- referential-integrity gate (anchored pronouns; no self-third-person) ----------
# Two lenient heuristics that catch only OBVIOUS breakdown, so normal reporting and
# third-party mentions ("Aisi and Azong") pass untouched:
#   (a) the speaker juxtaposes the LISTENER as if a separate person -- "你和{listener}"
#   (b) the speaker names THEMSELVES as a third-person subject -- "{self}覺得" / "是{self}"
# Quoted spans (reported speech) are exempt. A failure fails the dialogue gate -> the
# router retries / falls through (same path as any bad turn).
_REF_QUOTE_RE = re.compile(r"[「『“][^」』”]*[」』”]|\"[^\"]*\"")
_REF_SELF_VERBS = "覺得|認為|想|說|會|要|喜歡|知道|希望|打算|決定|問|告訴|擔心|以為"
_REF_EN_SELF_VERBS = r"thinks?|feels?|says?|said|wants?|knows?|will|would|hopes?|worries|decides?"


def _ref_name_forms(agent: "Agent") -> set[str]:
    """Every way this speaker's name can surface in a turn: the display (zh) name AND
    the pinyin/English form, since a weak model slips between them."""
    return {s for s in (getattr(agent, "name", "").strip(), agent.id.capitalize()) if s}


def _referential_ok(parsed: object, a: "Agent", b: "Agent") -> bool:
    turns = parsed.get("turns") if isinstance(parsed, dict) else None
    if not isinstance(turns, list):
        return True                                   # structural gate owns this case
    a_forms, b_forms = _ref_name_forms(a), _ref_name_forms(b)
    for t in turns:
        if not isinstance(t, dict):
            continue
        speaker = str(t.get("speaker", "")).strip()
        body = _REF_QUOTE_RE.sub(" ", str(t.get("text", "") or ""))   # drop quoted (reported) spans
        if speaker in a_forms:
            self_forms, partner_forms = a_forms, b_forms
        elif speaker in b_forms:
            self_forms, partner_forms = b_forms, a_forms
        else:
            continue                                  # unknown speaker label -> can't anchor; skip (lenient)
        for pn in partner_forms:                      # (a) listener juxtaposed as a third party
            if re.search(rf"[你妳您][和跟與、]\s*{re.escape(pn)}", body) \
                    or re.search(rf"{re.escape(pn)}\s*[和跟與、]\s*[你妳您]", body) \
                    or re.search(rf"\byou\s+and\s+{re.escape(pn)}\b", body, re.I) \
                    or re.search(rf"\b{re.escape(pn)}\s+and\s+you\b", body, re.I):
                return False
        for sn in self_forms:                         # (b) speaker names themselves in 3rd person
            if re.search(rf"(?<!我){re.escape(sn)}\s*(?:{_REF_SELF_VERBS})", body) \
                    or re.search(rf"(?<!我)是{re.escape(sn)}(?=[。.!?！？，,、\s]|$)", body) \
                    or re.search(rf"\b{re.escape(sn)}\s+(?:{_REF_EN_SELF_VERBS})\b", body, re.I):
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
    is_meetup: bool = False            # a kept social appointment -> conversation bypasses the daily cap


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
class ConvPlan:
    """A conversation captured at initiation. Every pre-dialogue effect (rumor
    shares, leaks, confides -- each a memory/relationship mutation) is already
    applied and the prompt is fully built, so the slow dialogue generation can run
    in a background task without the world's state drifting under the prompt.
    ``settle_conversation`` consumes the generated result to apply the aftermath."""
    a: Agent
    b: Agent
    location: str
    init_minute: int
    messages: list
    validate: object                 # Callable[[result], bool] -- the dialogue quality gate
    max_tokens: int
    is_confront: bool
    confront_rumor_id: str
    shared_rumors: list              # fwd/rev/leak descriptors -> engine publishes at initiation
    confided: list                   # confide descriptors -> engine publishes at initiation
    confession: dict | None


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
    # Chapter-closure signal (set by the engine): (agent, outcome, trigger, reason).
    # Fired when a decision-layer event ends a pursuit -- today, a resolved secret
    # whose theme is the chapter's (see _resolve_secret). The engine owns the
    # pipeline (LLM call + atomic apply + event); this layer only raises the flag.
    on_chapter_signal: object = None
    on_wish_abandon: object = None

    def __post_init__(self) -> None:
        live = any(p.name != "mock" for chain in self.router.tiers.values() for p in chain)
        self._live = live
        if live and self.dialogue_cap == 0:
            try:
                self.dialogue_cap = int(os.environ.get("AI_TOWN_DIALOGUE_CAP", "25"))
            except ValueError:
                self.dialogue_cap = 25

    # Social initiative is a live-mode beat (it drives real LLM conversations); the mock
    # baseline stays deterministic. AI_TOWN_MEETUPS=1 force-enables it for testing.
    @property
    def _meetups_enabled(self) -> bool:
        return self._live or os.environ.get("AI_TOWN_MEETUPS") == "1"

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

        # ---- Level 2 (resume): keeping a social appointment -----------
        if agent.state.pending_meetup and agent.state.current_action != "sleep":
            meet_dec = self._resume_meetup(agent, world, now)
            if meet_dec is not None:
                self.traces.append(DecisionTrace(
                    minute=now, agent_id=agent.id, observation=obs_text,
                    retrieved_memories=[], decision=meet_dec, model="rules",
                ))
                return meet_dec
            # not time yet / lapsed -> fall through to the routine

        # ---- Level 0: hard rules --------------------------------------
        if agent.state.current_action == "sleep" and entry.action == "sleep":
            decision = Decision("sleep", duration=agent.routine.next_boundary(minute_of_day, dow) - minute_of_day,
                                reason="asleep per routine")
        elif agent.state.energy <= LOW_ENERGY and entry.action not in ("sleep", "rest"):
            decision = Decision("rest", duration=30, reason=f"energy {agent.state.energy} <= {LOW_ENERGY}")

        # ---- Repair dispatch: the town's tech drops everything for a fault ----
        if decision is None and agent.profile.occupation == "Repair Technician" \
                and agent.state.current_action != "sleep":
            target = next((lid for lid, l in world.locations.items() if l.broken), None)
            if target is not None:
                if agent.state.location != target:
                    decision = Decision("move", target_location=target, duration=10,
                                        reason=f"heading to a repair job at {target}",
                                        narrative_verb="repair_go", narrative_target=target)
                else:
                    decision = Decision("work", duration=120,
                                        reason=f"repairing the equipment at {target}",
                                        narrative_verb="repair", narrative_target=target)

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
                memories = await agent.memory.retrieve_async(
                    f"{partner.id.capitalize()} {obs.location}", k=5, location=obs.location)
                res = await self.router.generate(
                    task="should_talk",
                    messages=builders.should_talk_prompt(agent, partner.name, _resolve_mems(memories, world)),
                    agent_id=agent.id,
                    sim_minute=now,
                    schema={"type": "object"},
                    cache_key=f"{agent.id}|{partner_id}|{agent.state.mood}|{obs.location}",
                    max_tokens=60,
                    per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick: never park the pacing loop on a hung provider
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
                per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick: never park the pacing loop on a hung provider
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
            # Shop staff rest on their employer's shop's closing day, same as the owner.
            staff_shop = next((l for l in world.locations.values()
                               if l.owner == agent.state.employer and l.price > 0), None) \
                if agent.state.employer else None
            off_today = (dow in agent.profile.off_days) \
                or (own_shop is not None and dow in own_shop.closed_days) \
                or (staff_shop is not None and dow in staff_shop.closed_days)
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
            # Interlude (just closed a chapter, see chapters.py): routine adherence dips
            # a little -- now and then a work/rest slot becomes an aimless wander to a
            # public place. The roll is sticky per (agent, 2-hour window) so a wander
            # is a real stretch there, not a move-and-come-straight-back; deterministic
            # so mock runs reproduce.
            interlude_drift = False
            if chapters_mod.in_interlude(agent) and not day_off_rest \
                    and entry.action in ("work", "rest") and minute_of_day >= 9 * 60:
                window = now // 120
                seed = int(hashlib.sha256(f"drift|{agent.id}|{window}".encode()).hexdigest()[:8], 16)
                if random.Random(seed).random() < chapters_mod.INTERLUDE_DRIFT_P:
                    opts = [p for p in ("park", "market", "cafe") if p in world.locations]
                    if opts:
                        dest = opts[window % len(opts)]
                        interlude_drift = True
            # Don't loiter at a shuttered door. If the routine points us at a shop
            # that's shunned (a bad rumor about its owner) or closed for the owner's
            # weekly day off, redirect -- whatever the action was. The owner is the
            # only one who may enter their own shop while it's closed (stock/clean).
            dest_loc = world.locations.get(dest)
            shunned = agent.state.avoid_location and dest == agent.state.avoid_location
            is_owned_shop = dest_loc is not None and dest_loc.owner and dest_loc.price > 0
            closed = is_owned_shop and dow in dest_loc.closed_days and dest_loc.owner != agent.id
            if entry.action == "eat" and (shunned or closed):
                # Take our custom to a rival shop that's open and affordable
                # (bakery <-> cafe); if none, eat at home -- so a closing day feeds
                # the other shop's takings.
                rival = next(
                    (lid for lid, loc in world.locations.items()
                     if loc.price > 0 and loc.owner and lid != dest and dow not in loc.closed_days
                     and agent.state.money >= loc.price),
                    None,
                )
                if closed:
                    self._note_closed(agent, dest, rival or agent.home, now)
                dest = rival or agent.home
            elif closed:
                # A non-meal routine (rest/idle) aimed at a closed shop: drift to a
                # public space instead of standing at the door.
                alt = next((p for p in ("market", "park") if p in world.locations), agent.home)
                self._note_closed(agent, dest, alt, now)
                dest = alt

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
                    "move", target_location=dest, duration=10,
                    reason=(f"interlude: drifting over to {dest}" if interlude_drift
                            else f"routine: head to {dest}"),
                )
            else:
                act = "rest" if (park_rained_out or day_off_rest or interlude_drift) else entry.action
                reason = ("routine: rest (rained out)" if park_rained_out
                          else "day off" if day_off_rest
                          else "interlude: drifting, nothing to push forward" if interlude_drift
                          else f"routine: {entry.action}")
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

    def _note_closed(self, agent: Agent, shop: str, went_to: str, now: int) -> None:
        """Leave a light 'found it closed' memory when a routine gets rerouted around
        a shop's day off -- at most once per shop per sim-day (no water-treading).
        Fuels gossip ("the cafe wasn't even open, wasted a trip")."""
        day = now // (24 * 60)
        if agent.state.closed_reroute_notes.get(shop) == day:
            return
        agent.state.closed_reroute_notes[shop] = day
        agent.memory.add(MemoryItem(
            minute=now, importance=1,
            text=f"{{loc:{shop}}} was closed today, went to {{loc:{went_to}}} instead.",
        ))

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
        # Interlude: a little more socially forward than usual (see chapters.py).
        if chapters_mod.in_interlude(agent):
            p *= chapters_mod.INTERLUDE_SOCIAL_MULT
        # Post-rejection awkwardness: for a week the pair can barely face each other.
        if agent.state.awkward_until.get(partner_id, -1) >= now // (24 * 60):
            p *= romance_mod.AWKWARD_TALK_MULT
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

    # ---- social initiative: arranged meetups -------------------------

    def _social_venue(self, world: "World") -> str:
        """A public place both parties can head to for a meetup (cafe first, else park,
        else any non-home location)."""
        for pref in ("cafe", "park"):
            if pref in world.locations:
                return pref
        for lid, loc in world.locations.items():
            if getattr(loc, "kind", "") != "home":
                return lid
        return next(iter(world.locations), "")

    def _common_free_window(self, a: Agent, b: Agent, world: "World", now: int) -> tuple[int, str] | None:
        """Find the earliest daytime slot today where BOTH agents' routines are in a
        drop-in-friendly state (idle/rest/eat -- never asleep or working), with at least
        an hour of lead. Location: where they'd already both be, else a public venue.
        Returns (absolute sim-minute, location_id) or None."""
        day_start = (now // (24 * 60)) * (24 * 60)
        dow = (now // (24 * 60)) % 7
        for t_mod in range(10 * 60, 20 * 60, 30):         # 10:00 .. 19:30, half-hour steps
            minute_abs = day_start + t_mod
            if minute_abs < now + 60:                     # need real lead time
                continue
            ea, eb = a.routine.current(t_mod, dow), b.routine.current(t_mod, dow)
            if ea.action in MEETUP_SOCIAL_ACTIONS and eb.action in MEETUP_SOCIAL_ACTIONS:
                loc = ea.location if ea.location == eb.location else self._social_venue(world)
                if loc:
                    return minute_abs, loc
        return None

    def maybe_arrange_meetup(self, agent: Agent, world: "World", now: int) -> dict | None:
        """Once-a-day roll: ``agent`` may invite a friend to meet later today. Honors the
        per-person and per-pair cooldowns, asks the friend (a rejection is possible), and
        on acceptance finds a shared free window and sets the mirrored ``pending_meetup``
        on both. Returns an outcome dict for the engine to publish
        ({"verb": "meetup_arranged"|"meetup_declined", "a", "b", "minute"?, "location"?}),
        or None (no attempt / no window). Disabled under mock so the baseline is untouched."""
        if not self._meetups_enabled:
            return None
        day = now // (24 * 60)
        st = agent.state
        if day - st.last_meetup_day < MEETUP_PERSON_COOLDOWN:
            return None
        if st.pending_meetup is not None:
            return None
        # Deterministic per (day, agent): reproducible, and independent of call order.
        rng = random.Random(int(hashlib.sha256(f"meetup|{day}|{agent.id}".encode()).hexdigest()[:8], 16))
        p_try = MEETUP_DAILY_P * (chapters_mod.INTERLUDE_MEETUP_MULT if chapters_mod.in_interlude(agent) else 1.0)
        if rng.random() >= p_try:
            return None
        # Candidate friends: friendly enough, not on the per-pair cooldown, free of an
        # existing appointment, and not the agent themselves.
        cands = []
        for other in world.agents.values():
            if other.id == agent.id or other.state.pending_meetup is not None:
                continue
            rel = agent.relationships.get(other.id)   # read-only: don't create empty entries
            if rel is None or rel.friendship < MEETUP_FRIEND_MIN:
                continue
            if day - st.meetup_with_day.get(other.id, -100) < MEETUP_PAIR_COOLDOWN:
                continue
            cands.append(other)
        if not cands:
            return None
        cands.sort(key=lambda o: agent.rel(o.id).friendship, reverse=True)
        b = cands[rng.randrange(min(3, len(cands)))]     # one of the top few friends
        # The friend may decline -- warmer relationships accept more readily; a sour mood
        # dampens it. Record the per-pair cooldown either way so they don't re-ask daily.
        st.meetup_with_day[b.id] = day
        b.state.meetup_with_day[agent.id] = day
        p_accept = min(0.95, 0.30 + 0.006 * b.rel(agent.id).friendship)
        if b.state.mood in ("upset", "worried", "anxious"):
            p_accept *= 0.6
        if rng.random() >= p_accept:
            return {"verb": "meetup_declined", "a": agent.id, "b": b.id}
        window = self._common_free_window(agent, b, world, now)
        if window is None:
            return None                                  # willing, but no shared gap today
        minute_abs, loc = window
        st.last_meetup_day = day                         # per-person throttle on the INITIATOR
        appt = {"partner": "", "location": loc, "minute": minute_abs}
        st.pending_meetup = {**appt, "partner": b.id}
        b.state.pending_meetup = {**appt, "partner": agent.id}
        return {"verb": "meetup_arranged", "a": agent.id, "b": b.id,
                "minute": minute_abs, "location": loc}

    def _resume_meetup(self, agent: Agent, world: "World", now: int) -> Decision | None:
        """Drive a kept appointment: once the appointed time arrives, head to the venue
        and, when both are there and free, start the (cap-exempt) conversation. Returns a
        move/idle/talk Decision, or None (not time yet, or the window lapsed -> cleared)."""
        m = agent.state.pending_meetup
        if not m or agent.state.current_action == "sleep":
            return None
        if now < m["minute"]:
            return None                                  # not yet -- live the normal routine
        if now > m["minute"] + MEETUP_WINDOW_MIN:
            agent.state.pending_meetup = None            # missed the window
            return None
        partner = world.agents.get(m["partner"])
        pm = partner.state.pending_meetup if partner else None
        if partner is None or not pm or pm.get("partner") != agent.id:
            agent.state.pending_meetup = None            # partner isn't coming any more
            return None
        loc = m["location"]
        if agent.state.location != loc:
            return Decision(action="move", target_location=loc, duration=10, level=2,
                            reason=f"heading to meet {partner.name}",
                            narrative_verb="meetup_go", narrative_target=partner.id)
        if (partner.state.location == loc and partner.state.current_action != "sleep"
                and partner.state.busy_until <= now):
            agent.state.pending_meetup = None            # engine clears the partner's on initiation
            return Decision(action="talk", talk_partner=partner.id, duration=TALK_DURATION_MIN,
                            level=2, is_meetup=True, reason=f"catching up with {partner.name}")
        return Decision(action="idle", duration=10, level=2,
                        reason=f"waiting at {loc} for {partner.name}")

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
                per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick (start_conversation): don't park the loop
            )
            new = str(res.parsed.get("text") or "") if isinstance(res.parsed, dict) else ""
            if new and not _has_cjk(new):   # reject a zh hallucination -> keep the prior English version
                text = new

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

    def _resolve_secret(self, owner: Agent, secret, now: int, resolution: str) -> bool:
        """Lay a secret to rest and leave the owner a memory of it (importance 5) --
        letting go is itself worth remembering and gives later reflection something
        to grow a 'lighter lately' impression from. Idempotent (once per secret)."""
        if not self.secrets.resolve(secret.id, now, resolution):
            return False
        owner.memory.add(MemoryItem(minute=now, text=resolution, importance=5, kind="reflection"))
        self.rewrite_goals_on_resolve(owner, secret, now)   # the worry's goal moves forward too
        owner.memory.suppress_theme(self.secret_subject(secret),
                                    self.secret_theme_keywords(secret), now)  # old anxiety fades
        # A laid-to-rest worry that IS the current pursuit ends that chapter (Xixi
        # finally asking Aisi; Xue coming to terms with the job question).
        if self.on_chapter_signal is not None and chapters_mod.secret_matches_chapter(owner.chapter, secret):
            self.on_chapter_signal(owner, "completed", "secret_resolved", resolution)
        return True

    # ---- chapter closure: the one LLM call (smart tier, rare) ----------------

    async def closure_reflection(self, agent: Agent, world: World, material: dict,
                                 outcome: str) -> dict | None:
        """Ask the smart tier for the biography line + residue + memory refs. Returns
        the validated dict, or None on any failure (chain exhausted, timeout, junk) --
        the engine then uses the rule-layer template line, so closure never blocks.
        ``no_floor``: a canned mock line must not become someone's life story when a
        real chain exists (mock-only runs still serve the deterministic mock)."""
        try:
            res = await self.router.generate(
                task="chapter_closure",
                messages=builders.chapter_closure_prompt(
                    agent, material, outcome, chapters_mod.relationship_summary_lines(material, world)),
                agent_id=agent.id, sim_minute=material["window"]["end_minute"],
                schema={"type": "object"}, max_tokens=200,
                validate=lambda r: chapters_mod.validate_closure_output(r.parsed, material) is not None,
                per_call_timeout=30.0, no_floor=True,
            )
        except Exception as err:
            print(f"[chapter] closure reflection failed for {agent.id} ({err!r}); using template line", flush=True)
            return None
        return chapters_mod.validate_closure_output(res.parsed, material)

    @staticmethod
    def secret_subject(secret) -> str:
        """Who/what a secret is about -- the seeded ``about`` id, else the first
        proper-noun name in the text (so a pre-``about`` secret still resolves)."""
        if secret.about:
            return secret.about.lower()
        for w in secret.text.split():
            c = w.strip(".,;:!?'\"")
            if c.istitle() and len(c) > 2 and c.lower() not in ("i",):
                return c.lower()
        return ""

    @staticmethod
    def secret_theme_keywords(secret) -> set:
        """The distinctive words of a secret's worry -- used to fade pre-resolution
        memories of the same theme."""
        return {w.strip(".,;:!?'\"").lower() for w in secret.text.split() if len(w) > 3} \
            - DecisionEngine._GOAL_STOP

    def rebuild_suppressed_themes(self, world: World) -> None:
        """Re-derive every agent's suppressed themes from the resolved secrets --
        called after a snapshot restore, so the down-weight survives a resume without
        needing its own snapshot slot (the secrets are the source of truth)."""
        for a in world.agents.values():
            a.memory.suppressed = []
        for s in self.secrets.secrets.values():
            if s.resolved:
                owner = world.agents.get(s.owner)
                if owner is not None:
                    before = s.resolved_minute if s.resolved_minute >= 0 else 10 ** 12
                    owner.memory.suppress_theme(self.secret_subject(s), self.secret_theme_keywords(s), before)

    # Common short words to ignore when matching a goal to a secret's theme.
    _GOAL_STOP = {"want", "have", "been", "that", "this", "with", "from", "they", "them",
                  "their", "about", "every", "time", "work", "just", "keep", "make", "your",
                  "into", "will", "would", "could", "should", "there", "when", "what"}

    @staticmethod
    def goal_matches_secret(goal_text: str, secret) -> bool:
        """A goal is 'about' a secret when it names the secret's subject or shares
        enough distinctive words with it."""
        g = {w.strip(".,;:!?'\"").lower() for w in goal_text.split()}
        about = (secret.about or "").lower()
        if about and about in g:
            return True
        kw = {w.strip(".,;:!?'\"").lower() for w in secret.text.split()
              if len(w) > 3} - DecisionEngine._GOAL_STOP
        if about:
            kw.add(about)
        return len(kw & g) >= 2

    @staticmethod
    def forward_goal(goal_text: str, secret) -> str:
        """Rewrite an anxious goal into a moving-forward one (story advances, not
        erased). Handles the "ask X to teach me Y" shape precisely; otherwise a
        gentle generic reframe toward the secret's subject."""
        m = re.search(r"ask (\w+) to teach me (.+?)(?:\.|$| but| every)", goal_text, re.I)
        if m:
            return f"Keep learning {m.group(2).strip()} from {m.group(1).capitalize()}"
        about = (secret.about or "").capitalize()
        if not about:
            for w in secret.text.split():           # else pull a name out of the secret text
                if w.istitle() and len(w) > 2:
                    about = w
                    break
        return f"Keep building on things with {about}" if about else ""

    def rewrite_goals_on_resolve(self, owner: Agent, secret, now: int) -> list[tuple[str, str]]:
        """Rewrite any of the owner's goals that were about this now-settled worry.
        Returns (old, new) pairs; leaves an importance-4 memory of the shift."""
        changed: list[tuple[str, str]] = []
        for g in owner.profile.goals:
            old = str(g.get("goal", ""))
            if not self.goal_matches_secret(old, secret):
                continue
            new = self.forward_goal(old, secret)
            if new and new != old:
                g["goal"] = new
                owner.memory.add(MemoryItem(
                    minute=now, importance=4, kind="reflection",
                    text=f"That old worry is behind me now -- my focus is on the next chapter: {new}."))
                changed.append((old, new))
        return changed

    def _maybe_confide(self, a: Agent, b: Agent, now: int) -> dict | None:
        """Decide whether ``a`` confides one of their OWN secrets in ``b`` -- pure
        Python, no LLM. Gated on trust vs. the secret's sensitivity (more private ->
        higher bar); each secret is confided to a person at most once; a low mood
        makes confiding likelier. Resolved secrets are never confided. Confiding a
        worry straight to the person it is *about* resolves it. Returns a descriptor
        (for the prompt + a confide event) or None (the common case)."""
        rel = a.rel(b.id)
        eligible = [
            s for s in self.secrets.active_secrets_of(a.id)           # resolved secrets are laid to rest
            if not self.secrets.knows(s.id, b.id)                      # b isn't owner and hasn't been told
            and rel.trust >= CONFIDE_TRUST_BASE + s.sensitivity * 30
        ]
        if not eligible:
            return None
        # If a worry is *about* the listener, opening up to them is the whole point --
        # prefer it over a merely weightier unrelated secret.
        about_matches = [s for s in eligible if s.about and s.about == b.id]
        secret = max(about_matches or eligible, key=lambda s: s.sensitivity)
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
        # Confiding to the very person the worry is about closes it out.
        if secret.about and secret.about == b.id:
            self._resolve_secret(
                a, secret, now,
                f"{a.id.capitalize()} finally found the courage to open up to {b.id.capitalize()}.")
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
            if not s.leaked and not s.resolved and s.social_enabled  # wish secrets wait for phase 3
            and s.owner != b.id and b.id in s.confided_to
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
            per_call_timeout=SYNC_CALL_TIMEOUT_S,   # in-tick (start_conversation): don't park the loop
        )
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        text = str(parsed.get("text") or "").strip()
        if not text or _has_cjk(text):   # English-canonical -> reject a zh hallucination, use a plain fallback
            text = f"{owner_name} has been hiding something."
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
        return {"rumor_id": rumor.id, "text": text, "from": b.id, "to": c.id,
                "leak": True, "subject": secret.owner, "secret_text": secret.text}

    async def start_conversation(
        self, a: Agent, b: Agent, world: World, now: int, confront_text: str | None = None,
        confront_rumor_id: str = "", count_against_cap: bool = True,
    ) -> ConvPlan:
        """Initiation (runs synchronously w.r.t. the world clock, in the tick):
        decide gossip / leaks / confides now -- each mutates memory + relationships
        while both parties are about to be locked -- snapshot each side's retrieved
        memories and impressions, and build the dialogue prompt. Returns a ``ConvPlan``
        the engine hands to a background task; the world keeps advancing while the
        dialogue itself generates, so only the two participants freeze.

        ``confront_text``/``confront_rumor_id`` make the exchange a rumor
        confrontation (``a`` opens with it) that settles that rumor. The plan's
        ``shared_rumors``/``confided`` are already-decided descriptors for the engine
        to publish at initiation."""
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
        # Confession: if either has resolved to confess to the other, this solo scene
        # becomes it. The outcome is settled by the rules now; the scene just plays it.
        confession = None
        if a.state.pending_confession == b.id:
            confession = {"from": a.id, "to": b.id, "from_name": a.name, "to_name": b.name,
                          "accepted": b.rel(a.id).romance >= romance_mod.ACCEPT_ROMANCE}
        elif b.state.pending_confession == a.id:
            confession = {"from": b.id, "to": a.id, "from_name": b.name, "to_name": a.name,
                          "accepted": a.rel(b.id).romance >= romance_mod.ACCEPT_ROMANCE}
        # Query by the English/pinyin name, matching how memories are stored (a 2-char
        # zh name gets filtered to noise by the bag-of-words embedder -- see MockEmbedding).
        # The place + whatever is explicitly on the table (a rumor, a confrontation)
        # feed biography surfacing only -- the ordinary top-k query is unchanged.
        a_topic = " ".join(x for x in (confront_text or "", (fwd or leak_fwd or {}).get("text", "")) if x)
        b_topic = " ".join(x for x in ((rev or leak_rev or {}).get("text", ""),) if x)
        a_mem = await a.memory.retrieve_async(b.id.capitalize(), k=3, location=a.state.location, topic=a_topic)
        b_mem = await b.memory.retrieve_async(a.id.capitalize(), k=3, location=b.state.location, topic=b_topic)
        # A held impression of the other person rides into the dialogue context, so
        # the model naturally carries the weight of the relationship's history.
        a_imp = self._impression_of(a, b.id)
        b_imp = self._impression_of(b, a.id)
        # Confide/confront are the emotionally weighty beats: a 1-line version is a
        # failure, so gate them at >=3 turns. A short result then triggers the
        # router's same-provider retry and, failing that, the fallback chain (the
        # mock floor always emits >=4 for these) -- better a model swap than a
        # one-line heart-to-heart. Ordinary chat keeps the base structural gate.
        intimate = is_confront or bool(confide_fwd or confide_rev) or confession is not None
        def _validate(r: object) -> bool:
            if not _dialogue_ok(r):
                return False
            if not _referential_ok(getattr(r, "parsed", None), a, b):
                if _GATE_DIAG:
                    prov = f"{getattr(r, 'provider', '?')}/{getattr(r, 'model', '?')}"
                    print(f"[ref-reject] {prov} {a.id}<->{b.id}", flush=True)
                return False
            if not intimate:
                return True
            turns = r.parsed.get("turns") if isinstance(r.parsed, dict) else None
            return isinstance(turns, list) and len(turns) >= 3
        messages = builders.dialogue_prompt(
            a, b, _resolve_mems(a_mem, world), _resolve_mems(b_mem, world),
            a_wants_to_mention=confront_text or (fwd or leak_fwd or {}).get("text"),
            b_wants_to_mention=(rev or leak_rev or {}).get("text"),
            is_confrontation=is_confront,
            time_hint=builders.time_of_day(now),
            a_impression=a_imp, b_impression=b_imp,
            a_confide=confide_fwd["text"] if confide_fwd else None,
            b_confide=confide_rev["text"] if confide_rev else None,
            nearby_landmark=self._nearby_landmark(world, a.state.location),
            confession=confession,
        )
        # Count the conversation against the daily budget at initiation (serialized
        # in the tick), so the cap stays honest even though generation is now async.
        # A kept meetup is exempt: it's a deliberate social beat, not routine chatter.
        self._roll_day(now)
        if count_against_cap:
            self._dialogues_today += 1
        return ConvPlan(
            a=a, b=b, location=a.state.location, init_minute=now,
            messages=messages, validate=_validate,
            # Chinese runs ~2-3x the tokens/char of English; 4 turns + JSON needs
            # more headroom or the last turn truncates.
            max_tokens=600 if builders.lang_is_zh() else 400,
            is_confront=is_confront, confront_rumor_id=confront_rumor_id,
            shared_rumors=shared_rumors, confided=confided, confession=confession,
        )

    async def generate_conversation(self, plan: ConvPlan) -> object:
        """The slow half of a conversation: one LLM call produces the whole
        exchange. Run inside a background task so the town doesn't wait on it. Each
        provider is bounded by ``DIALOGUE_PROVIDER_TIMEOUT_S`` so a slow model hands
        off to the fallback (recorded) rather than starving the call. ``no_floor``
        means a whole-chain failure raises ``ProvidersExhausted`` instead of returning
        the canned mock -- the engine catches it, brews a pause, and retries."""
        return await self.router.generate(
            task="dialogue", messages=plan.messages, agent_id=plan.a.id,
            sim_minute=plan.init_minute, schema={"type": "object"},
            max_tokens=plan.max_tokens, validate=plan.validate,
            per_call_timeout=DIALOGUE_PROVIDER_TIMEOUT_S,
            # Never serve a canned mock line: a total chain failure raises
            # ProvidersExhausted so the engine can brew a pause and re-run the chain.
            no_floor=True,
        )

    async def mock_dialogue(self, plan: ConvPlan) -> object:
        """Deterministic floor exchange -- used only when even the router's own chain
        is abandoned (the outer backstop timeout), so the two participants always
        unlock with a valid scene rather than hanging. Records the call to usage (it
        bypasses ``router.generate``) so this floor is visible in /api/usage and
        llm_calls, not silently invisible."""
        mock = self.router.tiers["normal"][-1]   # the mock floor is always the chain's last tier
        res = await mock.generate(plan.messages, schema={"type": "object"}, max_tokens=plan.max_tokens)
        self.router.usage.record(LLMCall(
            sim_minute=plan.init_minute, agent_id=plan.a.id, task_type="dialogue",
            provider=res.provider, model=res.model,
            input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            latency_ms=res.latency_ms, estimated_cost=0.0))
        return res

    @staticmethod
    def _warn_stale_world_terms(a: Agent, b: Agent, turns: object, now: int) -> None:
        """Light observability gate: log (never reject) a generated turn that names a
        prior run's world (see STALE_WORLD_TERMS), so any lingering stale-context bleed
        is visible in the log and its frequency can be watched to zero."""
        if not isinstance(turns, list):
            return
        for turn in turns:
            text = str(turn.get("text", "")) if isinstance(turn, dict) else ""
            low = text.lower()
            for term in STALE_WORLD_TERMS:
                if term.lower() in low:
                    print(f"[stale-world] minute {now}: {a.id}/{b.id} dialogue mentions "
                          f"'{term}' -> {text[:90]!r}", flush=True)
                    return

    def settle_conversation(
        self, plan: ConvPlan, world: World, res: object
    ) -> tuple[list[dict], dict, dict | None, dict | None, list[dict]]:
        """Settlement (synchronous, runs when the background generation returns):
        parse the exchange, write both sides' conversation memory, apply the
        relationship signals, and resolve milestone / romance / confrontation.
        Returns ``(turns, signals, confrontation, milestone, romance_events)`` for
        the engine to publish (stamped at the conversation's initiation minute)."""
        a, b, now = plan.a, plan.b, plan.init_minute
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        turns = parsed.get("turns", [])
        self._warn_stale_world_terms(a, b, turns, now)   # observe (don't block) stale-world bleed
        signals = {
            "sentiment": float(parsed.get("sentiment", 0.5)),
            "trust_signal": float(parsed.get("trust_signal", 0.0)),
            "conflict_signal": float(parsed.get("conflict_signal", 0.0)),
        }
        # Both sides remember the conversation (importance via cheap tier
        # is skipped here: conversation gets a flat importance; a real
        # importance call is wired for notable events in the engine).
        a.memory.add(MemoryItem(
            minute=now, text=f"Talked with {{agent:{b.id}}} at {{loc:{plan.location}}}.",
            importance=3, kind="conversation"))
        b.memory.add(MemoryItem(
            minute=now, text=f"Talked with {{agent:{a.id}}} at {{loc:{plan.location}}}.",
            importance=3, kind="conversation"))
        f_before = a.rel(b.id).friendship                     # for the relationship-milestone check
        a.apply_conversation_signals(b.id, **signals)
        b.apply_conversation_signals(a.id, **signals)
        a.state.last_talk_minute[b.id] = now
        b.state.last_talk_minute[a.id] = now
        milestone = self._detect_milestone(a, b, f_before, now)

        # Romance: a confession settles by rule (its own scene just played); any
        # other warm exchange nudges the track and may tip a side into crushing.
        romance_events: list[dict] = []
        if plan.confession is not None:
            romance_events.append(self._settle_confession(world, plan.confession, now))
        else:
            self._grow_romance(a, b, world, signals["sentiment"], bool(plan.confided), now)

        confrontation = None
        if plan.is_confront:
            confrontation = self._settle_confrontation(a, b, world, plan.confront_rumor_id, parsed, now)
        return turns, signals, confrontation, milestone, romance_events

    # ---- romance (an independent track; see romance.py) --------------

    @staticmethod
    def _romantic_setting(world: World, location_id: str, now: int) -> bool:
        """A festival anywhere, or the park after dark -- settings that amplify romance."""
        if world.effect_active("festival"):
            return True
        loc = world.locations.get(location_id)
        if loc is None or loc.kind != "park":
            return False
        h = (now % (24 * 60)) // 60
        return h >= 19 or h < 6

    @staticmethod
    def _set_pair_stage(a: Agent, b: Agent, stage: str, now: int) -> None:
        for x, y in ((a, b), (b, a)):
            r = x.rel(y.id)
            r.romance_stage = stage
            r.romance_stage_minute = now

    def _grow_romance(self, a: Agent, b: Agent, world: World, sentiment: float,
                      confided: bool, now: int) -> None:
        """Warm exchanges between people already friends nudge romance up (symmetric),
        independent of the friendship number itself. A side crossing the crush line
        quietly enters 'crushing'."""
        if not romance_mod.eligible_pair(a, b):
            return
        rel_ab, rel_ba = a.rel(b.id), b.rel(a.id)
        if not (sentiment >= romance_mod.GROW_SENTIMENT_MIN or confided):
            return
        if rel_ab.friendship < romance_mod.GROW_FRIEND_MIN or rel_ba.friendship < romance_mod.GROW_FRIEND_MIN:
            return
        setting = self._romantic_setting(world, a.state.location, now)
        base = romance_mod.growth(a, b, sentiment, setting, confided)
        if base <= 0:
            return
        # Directional: each side's gain is scaled by their own orientation toward the
        # other's gender, so an asymmetric attraction is expressible.
        rel_ab.romance = min(100.0, rel_ab.romance + base * romance_mod.orientation_coeff(a, b))
        rel_ba.romance = min(100.0, rel_ba.romance + base * romance_mod.orientation_coeff(b, a))
        self._maybe_crush(a, b, now)
        self._maybe_crush(b, a, now)

    def _maybe_crush(self, x: Agent, y: Agent, now: int) -> None:
        """x's romance for y past the crush line -> the pair enters 'crushing' (once)
        and x starts quietly keeping the feeling -- a real secret (about=y) that can
        be confided, leaked into a rumor, and find its way back to y."""
        rel = x.rel(y.id)
        if rel.romance < romance_mod.CRUSH or rel.romance_stage != "none":
            return
        self._set_pair_stage(x, y, "crushing", now)
        if not any(s.about == y.id and not s.resolved and "feelings" in s.text.lower()
                   for s in self.secrets.secrets_of(x.id)):
            s = self.secrets.add(x.id, romance_mod.crush_secret_text(y.id.capitalize()), 0.7, now)
            s.about = y.id

    def _settle_confession(self, world: World, conf: dict, now: int) -> dict:
        """Apply a confession's rule-decided outcome. Accept -> the pair starts dating;
        reject -> the confessor's romance takes a hit, a sour mood, and a week of
        awkwardness dampening their talk rate. Returns a chronicle descriptor."""
        a = world.agents[conf["from"]]
        b = world.agents[conf["to"]]
        a.state.pending_confession = ""
        day = now // (24 * 60)
        if conf["accepted"]:
            self._set_pair_stage(a, b, "dating", now)
            a.rel(b.id).romance = max(a.rel(b.id).romance, romance_mod.CONFESS_ROMANCE)
            a.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=f"I confessed to {{agent:{b.id}}} -- and we're together now."))
            b.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=f"{{agent:{a.id}}} confessed to me, and I said yes."))
            return {"verb": "romance_dating", "a": a.id, "b": b.id}
        rel = a.rel(b.id)
        rel.romance = max(0.0, rel.romance - romance_mod.REJECT_ROMANCE_HIT)
        a.state.mood = "upset"
        a.state.awkward_until[b.id] = day + romance_mod.AWKWARD_DAYS
        b.state.awkward_until[a.id] = day + romance_mod.AWKWARD_DAYS
        a.memory.add(MemoryItem(minute=now, importance=7, kind="reflection",
            text=f"I confessed to {{agent:{b.id}}}, but the feeling wasn't mutual."))
        b.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
            text=f"{{agent:{a.id}}} confessed to me; I had to turn them down as gently as I could."))
        return {"verb": "romance_rejected", "a": a.id, "b": b.id}

    def _detect_milestone(self, a: Agent, b: Agent, f_before: float, now: int) -> dict | None:
        """A conversation that moves ``a``'s friendship for ``b`` across a stage
        boundary (stranger/acquaintance/friend/close) is a visible beat. Debounced
        to at most one milestone per pair per 3 sim-days so a pair hovering on a
        threshold doesn't spam. Symmetric: the debounce day is stamped on both."""
        s_before = transitions_mod.rel_stage(f_before)
        s_after = transitions_mod.rel_stage(a.rel(b.id).friendship)
        if s_after == s_before:
            return None
        day = now // (24 * 60)
        last = max(a.state.rel_stage_day.get(b.id, -100), b.state.rel_stage_day.get(a.id, -100))
        if day - last < 3:
            return None
        a.state.rel_stage_day[b.id] = day
        b.state.rel_stage_day[a.id] = day
        up = transitions_mod.stage_rank(s_after) > transitions_mod.stage_rank(s_before)
        return {"a": a.id, "b": b.id, "stage": s_after, "up": up,
                "fa": round(a.rel(b.id).friendship), "fb": round(b.rel(a.id).friendship)}

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
        confident enough to matter (else dialogue/trust stay uncoloured). A belief
        down-weighted by a chapter closure needs proportionally more confidence."""
        b = agent.semantic.about(other_id)
        return b.text if b is not None and b.confidence * b.weight >= BELIEF_CONTEXT_MIN else None

    @staticmethod
    def _resolve_subject(agent: Agent, world: World, raw: object) -> tuple[str, str] | None:
        """Map a belief's subject NAME (from the model) back to an id. Returns
        (id, display_name) or None when it matches nobody/nowhere (then dropped)."""
        n = str(raw or "").strip().lower()
        if not n:
            return None
        # Beliefs are now English-canonical, so the model names people by their
        # pinyin (== the agent id); still accept the zh name for older payloads.
        if n in ("self", agent.name.lower(), agent.id):
            return ("self", agent.name)
        for a in world.agents.values():
            if n in (a.name.lower(), a.id):
                return (a.id, a.name)
        for loc in world.locations.values():
            if n in (loc.name.lower(), loc.id):
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
            if not belief_text_ok(text, world):           # quality gate: no "ok"/filler impressions
                if text:
                    print(f'[belief] rejected low-quality: "{text}"', flush=True)
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
                final_conf = existing.confidence
            else:                                         # brand-new impression -> cap at 0.6
                final_conf = min(0.6, conf)
                agent.semantic.beliefs.append(Belief(
                    subject=sid, text=text, confidence=final_conf, sentiment=sent,
                    formed_minute=now, last_reinforced_minute=now, source_count=1,
                ))
                agent.semantic._prune()
            events.append({"subject_id": sid, "subject_name": sname, "text": text,
                           "confidence": round(final_conf, 2)})
        return events

    # ---- rare wish generation (smart; backgrounded by the engine) ----

    async def generate_wish(self, agent: Agent, world: World, material: dict, now: int) -> dict | None:
        res = await self.router.generate(
            task="wish_generation", messages=builders.wish_generation_prompt(agent, material),
            agent_id=agent.id, sim_minute=now, schema={"type": "object"}, max_tokens=420,
            no_floor=True,
        )
        return wishes_mod.validate_generation(res.parsed, agent, world, material)

    # ---- Level 3: reflection -----------------------------------------

    def should_reflect(self, agent: Agent) -> bool:
        """Cheap synchronous gate (run in the tick): has enough weight accumulated?
        Individualized -- quiet background characters carry a higher threshold, so
        they reflect less often and spend less smart-tier without being silenced."""
        return agent.memory.importance_since_reflection >= agent.profile.reflection_threshold

    async def reflect(
        self, agent: Agent, world: World, now: int
    ) -> tuple[list[str], list[dict], bool, list[dict]]:
        """The reflection body -- run in a background task (it's a slow smart-tier
        call). Does NOT reset ``importance_since_reflection``; the engine resets it at
        initiation so a second reflection can't pile up while this one generates."""
        day_start = now - (now % (24 * 60))
        events = _resolve_mems(agent.memory.today(day_start), world)
        # The agent's own still-open worries ride into the reflection so it can judge
        # which, if any, recent experience has laid to rest.
        open_secrets = self.secrets.active_secrets_of(agent.id)
        # Life decisions (a transition, a confession, a proposal) are offered only
        # when nothing is already staged -- reflection can't queue two at once.
        offer = []
        if not agent.state.pending_transition and not agent.state.pending_confession:
            if now // (24 * 60) - agent.state.last_transition_day >= transitions_mod.TRANSITION_COOLDOWN_DAYS:
                offer += [(t.id, t.label) for t in transitions_mod.available_for(agent, world)]
            offer += self._romance_options(agent, world, now)
        wish = wishes_mod.active_wish(agent)
        frustration = []
        if wish is not None:
            frustration = [{"id": chapters_mod.memory_id(agent.id, m), "text": m.text}
                           for m in agent.memory.items
                           if m.minute >= (wish.created_day - 1) * 24 * 60 and m.importance >= 4][-6:]
        res = await self.router.generate(
            task="reflection",
            messages=builders.reflection_prompt(agent, events, open_secrets, offer or None,
                                                wish=wish, frustration=frustration),
            agent_id=agent.id,
            sim_minute=now,
            schema={"type": "object"},
            max_tokens=300 if builders.lang_is_zh() else 200,
            # belief/secret/life_decision quality must never be a canned mock line:
            # a total chain failure raises ProvidersExhausted so the engine can brew a
            # pause and retry rather than settle for the floor (task 3).
            no_floor=True,
        )
        insights: list[str] = []
        belief_events: list[dict] = []
        secret_born = False
        romance_events: list[dict] = []
        if isinstance(res.parsed, dict):
            # Insights land straight in memory, so gate them like beliefs -- a dodge
            # ("ok") or filler must never become a remembered reflection. (The router
            # already rejects a junk reflection; this is the belt-and-suspenders for a
            # floor result that slipped through.)
            insights = [str(x) for x in res.parsed.get("insights", []) if belief_text_ok(str(x), world)]
            belief_events = self._form_beliefs(agent, world, res.parsed.get("beliefs", []), now)
            secret_born = self._maybe_new_secret(agent, world, res.parsed.get("new_secret"), now)
            self._resolve_reflected_secrets(agent, res.parsed.get("resolved_secret_ids"), open_secrets, now)
            if offer:
                romance_events = self._stage_life_decision(agent, world, res.parsed.get("life_decision"), now)
            abandon = res.parsed.get("wish_abandonment")
            if (wish is not None and isinstance(abandon, dict) and abandon.get("abandon") is True
                    and str(abandon.get("wish_id", "")) == wish.id):
                refs = [str(x) for x in abandon.get("frustration_memory_refs", [])]
                allowed = {m["id"] for m in frustration}
                ok, grounded = wishes_mod.validate_abandon(
                    agent, wish, now // (24 * 60) + 1, refs, allowed)
                if ok and self.on_wish_abandon is not None:
                    self.on_wish_abandon(agent, wish, str(abandon.get("reason", "")).strip(), grounded)
        for ins in insights:
            agent.memory.add(MemoryItem(minute=now, text=ins, importance=5, kind="reflection"))
        return insights, belief_events, secret_born, romance_events

    def _resolve_reflected_secrets(self, agent: Agent, raw: object, open_secrets: list, now: int) -> None:
        """Resolve the secrets reflection judged settled. Only ids that were actually
        offered to this reflection (the agent's own open secrets) are honoured, so a
        hallucinated id can't touch someone else's secret."""
        if not isinstance(raw, list):
            return
        by_id = {s.id: s for s in open_secrets}
        for sid in raw:
            secret = by_id.get(str(sid))
            if secret is not None:
                self._resolve_secret(
                    agent, secret, now,
                    f"{agent.id.capitalize()} has come to terms with this; it no longer weighs on them.")

    def _stage_life_decision(self, agent: Agent, world: World, raw: object, now: int) -> list[dict]:
        """Route a reflection's life decision: a transition (staged for the next day
        boundary), a confession (armed for the next solo talk), or a proposal (settled
        now). Returns chronicle descriptors for anything settled immediately."""
        if not isinstance(raw, dict):
            return []
        tid = str(raw.get("action", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        tmpl = transitions_mod.REGISTRY.get(tid)
        if tmpl is not None:
            if agent.state.pending_transition or not tmpl.precondition(agent, world):
                return []
            agent.state.pending_transition = tid
            agent.state.pending_transition_reason = reason
            print(f"[transition] {agent.id} decided '{tid}' (applies next settlement)", flush=True)
            return []
        if tid.startswith("confess_to_"):
            target = world.agents.get(tid[len("confess_to_"):])
            if target is None or agent.state.pending_confession:
                return []
            rel = agent.rel(target.id)
            if rel.romance < romance_mod.CONFESS_ROMANCE or rel.friendship < romance_mod.CONFESS_FRIEND:
                return []
            agent.state.pending_confession = target.id
            agent.state.last_confess_day = now // (24 * 60)
            print(f"[romance] {agent.id} resolved to confess to {target.id}", flush=True)
            return []
        if tid.startswith("propose_to_"):
            return self._settle_proposal(agent, world.agents.get(tid[len("propose_to_"):]), now)
        return []

    def _romance_options(self, agent: Agent, world: World, now: int) -> list[tuple[str, str]]:
        """Life-decision options on the romance track: confess (own romance & friendship
        high enough, off cooldown) or propose (dating long enough)."""
        opts: list[tuple[str, str]] = []
        day = now // (24 * 60)
        can_confess = (not agent.state.pending_confession
                       and day - agent.state.last_confess_day >= romance_mod.CONFESS_COOLDOWN_DAYS)
        for other in world.agents.values():
            if other.id == agent.id or not romance_mod.eligible_pair(agent, other):
                continue
            rel = agent.rel(other.id)
            if (can_confess and rel.romance_stage in ("none", "crushing")
                    and rel.romance >= romance_mod.CONFESS_ROMANCE
                    and rel.friendship >= romance_mod.CONFESS_FRIEND):
                opts.append((f"confess_to_{other.id}", f"confess your feelings to {other.id.capitalize()}"))
            elif (rel.romance_stage == "dating"
                    and day - rel.romance_stage_minute // (24 * 60) >= romance_mod.DATING_TO_PARTNER_DAYS):
                opts.append((f"propose_to_{other.id}", f"ask {other.id.capitalize()} to make it permanent"))
        return opts

    def _settle_proposal(self, a: Agent, b: Agent | None, now: int) -> list[dict]:
        """A proposal is accepted when the other side is deep enough in (romance >= 70)
        and they've been dating long enough. Success -> partners + a small weekend
        together-time tweak."""
        if b is None or a.rel(b.id).romance_stage != "dating":
            return []
        if b.rel(a.id).romance < romance_mod.PARTNER_ROMANCE:
            return []
        self._set_pair_stage(a, b, "partners", now)
        for x, y in ((a, b), (b, a)):
            x.memory.add(MemoryItem(minute=now, importance=8, kind="reflection",
                text=f"{{agent:{y.id}}} and I decided to make it permanent."))
        self._partner_routine_tweak(a, b)
        return [{"verb": "romance_partners", "a": a.id, "b": b.id}]

    @staticmethod
    def _partner_routine_tweak(a: Agent, b: Agent) -> None:
        """Partners drift together in the evenings: add a shared weekend park hour to
        both (once)."""
        from .routine import Routine, RoutineEntry
        for x in (a, b):
            we = list(x.routine._weekend)
            if any(e.start == 20 * 60 + 30 and e.location == "park" for e in we):
                continue
            we.append(RoutineEntry(20 * 60 + 30, "rest", "park"))
            x.routine = Routine(list(x.routine.entries), we)

    def _maybe_ignite(self, agent: Agent, world: World, now: int) -> None:
        """Organic spark: once a pair has spent enough waking time together and both
        are open to romance, a low chance drops a 'you keep noticing them' nudge into
        the agent's memory -- material for the next reflection and warmer talks.
        Deterministic per (pair, day); one spark per reflection; 14-day cooldown."""
        if agent.profile.romantic_inclination < romance_mod.IGNITE_INCL_MIN:
            return
        day = now // (24 * 60)
        for other in world.agents.values():
            if (other.id == agent.id or not romance_mod.eligible_pair(agent, other)
                    or other.profile.romantic_inclination < romance_mod.IGNITE_INCL_MIN):
                continue
            rel = agent.rel(other.id)
            if rel.romance_stage != "none":
                continue
            if agent.state.copresence.get(other.id, 0) < romance_mod.IGNITE_MINUTES:
                continue
            if day - agent.state.ignite_day.get(other.id, -100) < romance_mod.IGNITE_COOLDOWN_DAYS:
                continue
            seed = int(hashlib.sha256(f"ignite|{agent.id}|{other.id}|{day}".encode()).hexdigest()[:8], 16)
            # The spark is likelier toward a gender this agent leans to (orientation).
            if random.Random(seed).random() >= romance_mod.IGNITE_PROB * romance_mod.orientation_coeff(agent, other):
                continue
            agent.state.ignite_day[other.id] = day
            # The spark itself: time-together turns into the first flicker of romance,
            # independent of how deep the friendship is. Conversations grow it from here.
            rel.romance = min(100.0, rel.romance + romance_mod.IGNITE_ROMANCE_KICK)
            self._maybe_crush(agent, other, now)
            agent.memory.add(MemoryItem(
                minute=now, importance=4, kind="reflection",
                text=f"Lately I keep noticing {{agent:{other.id}}}'s presence more than I'd expect."))
            return

    def _maybe_new_secret(self, agent: Agent, world: World, raw: object, now: int) -> bool:
        """Add a private matter surfaced by reflection (owner = the reflecting
        agent). Capped at 4 per agent so it can't run away. Returns True if one was
        born (the engine then publishes a content-free ``secret_born`` beat)."""
        if not isinstance(raw, dict):
            return False
        text = str(raw.get("text", "")).strip()
        if not belief_text_ok(text, world):               # same quality gate as beliefs
            if text:
                print(f'[secret] rejected low-quality: "{text}"', flush=True)
            return False
        if len(self.secrets.secrets_of(agent.id)) >= 4:
            return False
        try:
            sensitivity = float(raw.get("sensitivity", 0.5))
        except (TypeError, ValueError):
            sensitivity = 0.5
        self.secrets.add(agent.id, text, sensitivity, now)
        return True
