"""Wishes -- a structured private intention that really steers behaviour.

Phase 2a: the object, its lifecycle, and the rule-layer drive. A wish is seeded
by hand (God Mode); *generating* one from a resident's own history is 2b, and
this module deliberately contains no LLM call at all.

Three rules hold the design together:

  1. **Progress has exactly one source: a real event.** Nothing here ever adds
     to a requirement because an agent "tried". A counter moves only when the
     simulation actually published the matching Event (``update_from_event``),
     or when the daily settlement reads a world value the ordinary systems
     already changed -- money from wages/sales, friendship/trust from real
     conversations (``update_from_state``). The drive below cannot touch it.
  2. **The drive only ever asks for a slot the routine was already giving away.**
     It is consulted after sleep / low energy / repair duty / a kept meetup, and
     only when the routine itself says "rest" or "idle". It returns a *directive*
     -- a suggestion the decision layer must still run through every ordinary
     precondition (shop closing days, rain, cooldowns, the dialogue cap, the
     partner's own refusal). It never teleports and never forces a conversation.
  3. **Giving up is earned, not rolled.** Frustration accrues from *distinct,
     consecutive* blocked days; abandonment is a pure comparison of that
     pressure against a threshold personalised by conscientiousness, how long
     the wish has been carried (sunk cost), and its scale.

Privacy: ``title`` / ``statement`` / ``motivation`` are the owner's own. They
never enter another resident's prompt, another resident's memory, a public event
or the chronicle. The owner perceives the wish only through their chapter
narrative (the phase-1 mechanism).
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .chapters import DAY_MIN
from .core import MemoryItem

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

SCALES = ("major", "minor")
STATUSES = ("active", "completed", "failed", "abandoned")
TERMINAL = STATUSES[1:]

# Per-resident concurrency (spec A).
MAX_ACTIVE_MAJOR = 1
MAX_ACTIVE_MINOR = 2
REQUIREMENTS_MAX = 8

# ---- requirement vocabulary -------------------------------------------------
# Every kind names ONE real progress source and nothing else can write to it.
#
#   kind             target            progress source
#   ---------------- ----------------- --------------------------------------------
#   location_visits  location id       Event(verb="arrive", actor=owner, location=target)
#   action_count     work|rest|idle    Event(verb=target, actor=owner)
#   talk_count       agent id          Event(verb="talk_start") between owner and target
#   meetups_kept     agent id | ""     Event(verb="met_up") involving owner (and target)
#   friendship       agent id          daily read of agent.rel(target).friendship
#   trust            agent id          daily read of agent.rel(target).trust
#   money_gain       ""                daily read of agent.state.money minus baseline
#   event_witnessed  event verb        Event(verb=target) anywhere -- PASSIVE
#
# The four state-derived kinds are sampled at the daily settlement; the values
# themselves are moved only by the ordinary conversation / economy systems.
EVENT_KINDS = ("location_visits", "action_count", "talk_count", "meetups_kept", "event_witnessed")
STATE_KINDS = ("friendship", "trust", "money_gain")
REQUIREMENT_KINDS = EVENT_KINDS + STATE_KINDS
# Always passive: the resident cannot act on it, it merely happens to them.
# ``money_gain`` is passive *conditionally* -- see requirement_actionable: a wage
# that arrives whether or not you get out of bed is no more actionable than the
# weather. Actionability is therefore a question about a kind AND a resident.
PASSIVE_KINDS = ("event_witnessed",)
CONDITIONAL_KINDS = ("money_gain",)
ACTIONABLE_KINDS = tuple(k for k in REQUIREMENT_KINDS if k not in PASSIVE_KINDS)

DRIVE_ACTIONS = ("work", "rest", "idle")                  # action_count targets the drive can pursue
SOCIAL_KINDS = ("talk_count", "meetups_kept", "friendship", "trust")
WORK_KINDS = ("money_gain",)                              # need an income path to be feasible
RELATIVE_KINDS = ("friendship", "trust")                  # threshold is an absolute 0..100 level

# ---- drive pacing (spec C) --------------------------------------------------
DRIVE_MAJOR_DAILY_ATTEMPTS = 2
DRIVE_MINOR_DAILY_ATTEMPTS = 1
DRIVE_MAJOR_PROBABILITY = 0.70
DRIVE_MINOR_PROBABILITY = 0.20
DRIVE_MEETUP_PROBABILITY = 0.55
DRIVE_SOCIAL_GATE_MULT = 2.0                              # bias, never a bypass (see decision._social_gate)

# ---- frustration & abandonment (spec D) -------------------------------------
FRUSTRATION_BLOCKED_DAYS = 2        # consecutive blocked days before the first frustration memory
FRUSTRATION_COOLDOWN_DAYS = 3       # min days between two frustration memories for one wish
ABANDON_MIN_AGE_DAYS = 3            # a wish seeded yesterday is never given up on
BLOCKED_WEIGHT = 0.25               # pressure per consecutive blocked day
FRUSTRATION_WEIGHT = 0.35           # pressure per frustration memory already written
ABANDON_BASE = {"major": 1.30, "minor": 0.80}
CONSCIENTIOUSNESS_WEIGHT = 1.00     # a dutiful resident holds on longer
SUNK_COST_MAX = 0.60                # a long-carried wish is harder to drop
SUNK_COST_FULL_DAYS = 30

COUNTED_EVENT_KEYS_MAX = 256        # bounded dedup history

# Owner-only, deliberately generic: the memory must not leak what the wish is.
FRUSTRATION_TEXT = "Something I have been quietly working toward got blocked again today."
PROGRESS_TEXT = "I took a small real step toward something I have been keeping to myself."
MINOR_CLOSE_TEXT = {
    "completed": "A small thing I had set my mind on is done.",
    "failed": "A small thing I had set my mind on ran out of time.",
    "abandoned": "I have quietly let go of a small thing I had set my mind on.",
}


# ---- model ------------------------------------------------------------------


@dataclass
class Requirement:
    """One observable condition. ``progress`` is the only mutable measure and is
    written exclusively by ``update_from_event`` / ``update_from_state``.

    ``baseline`` is internal bookkeeping: money_gain measures a delta from the
    wallet at seed time, and friendship/trust record where the relationship stood
    so the seeding validator can refuse an already-satisfied requirement."""

    kind: str
    target: str = ""
    threshold: float = 1.0
    progress: float = 0.0
    baseline: float = 0.0

    @property
    def completed(self) -> bool:
        return self.progress >= self.threshold

    @property
    def fraction(self) -> float:
        return min(1.0, self.progress / self.threshold) if self.threshold > 0 else 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "Requirement | None":
        if not isinstance(raw, dict) or raw.get("kind") not in REQUIREMENT_KINDS:
            return None
        try:
            r = cls(kind=str(raw["kind"]), target=str(raw.get("target", "")),
                    threshold=float(raw.get("threshold", 1)),
                    progress=float(raw.get("progress", 0)),
                    baseline=float(raw.get("baseline", 0)))
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(x) for x in (r.threshold, r.progress, r.baseline)):
            return None
        if not 0 < r.threshold <= 10000 or r.progress < 0:
            return None
        return r


@dataclass
class Wish:
    id: str
    owner: str
    scale: str                                   # major | minor
    status: str                                  # active | completed | failed | abandoned
    created_on: int                              # sim day
    title: str = ""                              # PRIVATE
    statement: str = ""                          # PRIVATE
    motivation: str = ""                         # PRIVATE
    provenance: list = field(default_factory=list)      # [{"id","text"}] -- empty when hand-seeded
    requirements: list = field(default_factory=list)
    expires_on: int | None = None                # sim day; past it -> failed
    ended_on: int = 0
    outcome_reason: str = ""
    chapter_id: str = ""                         # the pursuit chapter a major wish opened
    counted_event_keys: list = field(default_factory=list)
    frustration_count: int = 0
    drive: dict = field(default_factory=dict)    # see _drive_state

    @property
    def progress(self) -> float:
        if not self.requirements:
            return 0.0
        return sum(r.fraction for r in self.requirements) / len(self.requirements)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["requirements"] = [r.to_dict() for r in self.requirements]
        return d

    @classmethod
    def from_dict(cls, raw: object) -> "Wish | None":
        """Tolerant restore: a malformed wish is skipped whole rather than
        half-loaded (a half-loaded intention would drive nonsense behaviour)."""
        if not isinstance(raw, dict):
            return None
        try:
            reqs = [Requirement.from_dict(r) for r in (raw.get("requirements") or [])]
            if not reqs or any(r is None for r in reqs) or len(reqs) > REQUIREMENTS_MAX:
                return None
            w = cls(id=str(raw["id"]), owner=str(raw["owner"]), scale=str(raw["scale"]),
                    status=str(raw.get("status", "active")), created_on=int(raw["created_on"]),
                    title=str(raw.get("title", "")), statement=str(raw.get("statement", "")),
                    motivation=str(raw.get("motivation", "")), requirements=reqs,
                    ended_on=int(raw.get("ended_on", 0)),
                    outcome_reason=str(raw.get("outcome_reason", "")),
                    chapter_id=str(raw.get("chapter_id", "")),
                    frustration_count=int(raw.get("frustration_count", 0)))
        except (KeyError, TypeError, ValueError):
            return None
        if w.scale not in SCALES or w.status not in STATUSES or w.created_on < 1:
            return None
        exp = raw.get("expires_on")
        if exp is not None:
            if isinstance(exp, bool) or not isinstance(exp, int) or exp < w.created_on:
                return None
            w.expires_on = exp
        prov = raw.get("provenance") or []
        if not isinstance(prov, list):
            return None
        w.provenance = [{"id": str(p.get("id", "")), "text": str(p.get("text", ""))}
                        for p in prov if isinstance(p, dict)]
        keys = raw.get("counted_event_keys") or []
        if isinstance(keys, list):
            w.counted_event_keys = [str(k) for k in keys][-COUNTED_EVENT_KEYS_MAX:]
        w.drive = _clean_drive(raw.get("drive"))
        if w.frustration_count < 0:
            return None
        return w


def _clean_drive(raw: object) -> dict:
    """Restore the drive bookkeeping defensively -- a corrupt value must not make
    the resident look permanently blocked (or permanently fresh)."""
    d = raw if isinstance(raw, dict) else {}

    def as_int(key: str, default: int, lo: int) -> int:
        v = d.get(key, default)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= lo else default

    attempts = d.get("attempt_days")
    attempts = ({str(k): int(v) for k, v in attempts.items()
                 if isinstance(v, int) and not isinstance(v, bool)}
                if isinstance(attempts, dict) else {})
    return {"attempt_days": attempts,
            "cursor": as_int("cursor", 0, 0),
            "blocked_streak": as_int("blocked_streak", 0, 0),
            "last_blocked_day": as_int("last_blocked_day", -1, -1),
            "last_frustration_day": as_int("last_frustration_day", -1, -1),
            "daily_day": as_int("daily_day", -1, -1),
            "daily_attempts": as_int("daily_attempts", 0, 0)}


# ---- queries ----------------------------------------------------------------


def active_wishes(agent: "Agent", scale: str | None = None) -> list:
    return [w for w in agent.wishes if w.status == "active" and (scale is None or w.scale == scale)]


def wish_by_chapter(agent: "Agent", chapter_id: str):
    return next((w for w in agent.wishes if w.scale == "major" and w.status == "active"
                 and w.chapter_id and w.chapter_id == chapter_id), None)


def capacity_left(agent: "Agent", scale: str) -> int:
    cap = MAX_ACTIVE_MAJOR if scale == "major" else MAX_ACTIVE_MINOR
    return cap - len(active_wishes(agent, scale))


# ---- feasibility (spec A) ---------------------------------------------------


def _routine_entries(agent: "Agent") -> list:
    seen = list(agent.routine.entries)
    return seen + [e for e in agent.routine._weekend if e not in seen]


def work_locations(agent: "Agent", world: "World") -> list:
    """Every valid place this resident's own timetable sends them to work."""
    return list(dict.fromkeys(e.location for e in _routine_entries(agent)
                              if e.action == "work" and e.location in world.locations))


def actionable_income_path(agent: "Agent", world: "World") -> bool:
    """Money this resident can go out and earn: a work entry in their own timetable
    pointing at a real place, or a shop of their own that takes money."""
    return (bool(work_locations(agent, world))
            or any(l.owner == agent.id and l.price > 0 for l in world.locations.values()))


def passive_income(agent: "Agent", world: "World") -> bool:
    """Money that arrives regardless -- a pension, a stipend. It moves the wallet
    but there is nothing to *do* about it."""
    return agent.profile.daily_wage > 0 and not actionable_income_path(agent, world)


def has_income_ability(agent: "Agent", world: "World") -> bool:
    """Any income at all: enough for a money requirement to be able to progress,
    which is a weaker claim than being able to pursue it (see requirement_actionable)."""
    return actionable_income_path(agent, world) or agent.profile.daily_wage > 0


def requirement_actionable(agent: "Agent", world: "World", req: Requirement) -> bool:
    """Can THIS resident do something about this requirement? Always false for a
    passive kind; for a money kind, only when they have a way to earn rather than
    merely receive. A non-actionable requirement still accrues progress from real
    events, but the drive never pursues it -- so it never produces a blocked day,
    never breeds frustration, and can never be the thing that justifies a major."""
    if req.kind in PASSIVE_KINDS:
        return False
    if req.kind in CONDITIONAL_KINDS:
        return actionable_income_path(agent, world)
    return True


def requirement_feasible(agent: "Agent", world: "World", req: Requirement) -> tuple[bool, str]:
    """Can this resident actually move this requirement? Returns (ok, reason)."""
    if req.kind == "location_visits":
        if req.target not in world.locations:
            return False, f"unknown location '{req.target}'"
        return True, ""
    if req.kind in ("talk_count", "friendship", "trust"):
        if req.target not in world.agents:
            return False, f"unknown resident '{req.target}'"
        if req.target == agent.id:
            return False, "a social requirement cannot target oneself"
        return True, ""
    if req.kind == "meetups_kept":
        if req.target and req.target not in world.agents:
            return False, f"unknown resident '{req.target}'"
        if req.target == agent.id:
            return False, "a social requirement cannot target oneself"
        return True, ""
    if req.kind == "money_gain":
        if not has_income_ability(agent, world):
            return False, "no income path: no wage, no shop, no work entry in the routine"
        return True, ""     # feasible; whether it is *actionable* is a separate question
    if req.kind == "action_count":
        if req.target not in DRIVE_ACTIONS:
            return False, f"action_count target must be one of {list(DRIVE_ACTIONS)}"
        if req.target == "work" and not work_locations(agent, world):
            return False, "no work entry in the routine points at a valid location"
        return True, ""
    if req.kind == "event_witnessed":
        return True, ""                     # valid, but passive -- see validate_seed
    return False, f"unknown requirement kind '{req.kind}'"


def validate_seed(raw: object, agent: "Agent", world: "World", day: int) -> tuple[dict | None, list]:
    """Hard gate for a hand-seeded wish. Returns (clean, problems); ``clean`` is
    None whenever ``problems`` is non-empty -- nothing half-valid is ever installed."""
    problems: list = []
    if not isinstance(raw, dict):
        return None, ["body must be an object"]
    scale = str(raw.get("scale", "")).strip()
    if scale not in SCALES:
        problems.append(f"scale must be one of {list(SCALES)}")
    title = str(raw.get("title", "")).strip()
    statement = str(raw.get("statement", "")).strip()
    if not title:
        problems.append("title is required")
    if len(statement) < 12:
        problems.append("statement must be a real sentence (>= 12 chars)")
    if capacity_left(agent, scale) <= 0 if scale in SCALES else False:
        cap = MAX_ACTIVE_MAJOR if scale == "major" else MAX_ACTIVE_MINOR
        problems.append(f"{agent.id} already holds the maximum {cap} active {scale} wish(es)")
    if scale == "major" and agent.chapter is not None and agent.chapter.chapter_type == "pursuit":
        problems.append(f"{agent.id} is already in a pursuit chapter "
                        f"(\"{agent.chapter.title}\") -- close it before seeding a major wish")
    narrative = str(raw.get("narrative", "")).strip()
    if scale == "major" and len(narrative) < 12:
        problems.append("a major wish needs a chapter 'narrative' (English, >= 12 chars)")

    raw_reqs = raw.get("requirements")
    reqs: list = []
    if not isinstance(raw_reqs, list) or not raw_reqs:
        problems.append("requirements must be a non-empty list")
    elif len(raw_reqs) > REQUIREMENTS_MAX:
        problems.append(f"at most {REQUIREMENTS_MAX} requirements")
    else:
        seen = set()
        for i, rr in enumerate(raw_reqs):
            r = Requirement.from_dict(rr)
            if r is None:
                problems.append(f"requirement[{i}] is malformed or has an unknown kind")
                continue
            ok, why = requirement_feasible(agent, world, r)
            if not ok:
                problems.append(f"requirement[{i}] ({r.kind}): {why}")
                continue
            key = (r.kind, r.target)
            if key in seen:
                problems.append(f"requirement[{i}] duplicates {key}")
                continue
            seen.add(key)
            # Anchor the state-derived kinds to where life stands right now, and
            # refuse anything already satisfied at seed time.
            if r.kind in RELATIVE_KINDS:
                r.baseline = r.progress = float(getattr(agent.rel(r.target), r.kind))
                if r.threshold > 100:
                    problems.append(f"requirement[{i}] ({r.kind}): threshold above the 0-100 scale")
            elif r.kind == "money_gain":
                r.baseline, r.progress = float(agent.state.money), 0.0
            else:
                r.baseline = r.progress = 0.0
            if r.completed:
                problems.append(f"requirement[{i}] ({r.kind}) is already satisfied at seed time")
            reqs.append(r)

    if scale == "major" and reqs:
        if not any(requirement_actionable(agent, world, r) for r in reqs):
            why = f"passive kinds are {list(PASSIVE_KINDS)}"
            if any(r.kind in CONDITIONAL_KINDS for r in reqs) and passive_income(agent, world):
                why = (f"{agent.id}'s only income is a passive wage (no work entry, no shop), so a "
                       f"money requirement is passive for them -- they cannot act to earn more")
            problems.append("a major wish needs at least one requirement this resident can act on; " + why)

    expires = raw.get("expires_on")
    if expires is not None:
        if isinstance(expires, bool) or not isinstance(expires, int) or expires <= day:
            problems.append("expires_on must be a sim day later than today")

    prov = raw.get("provenance") or []
    if not isinstance(prov, list) or any(not isinstance(p, dict) for p in prov):
        problems.append("provenance must be a list of {id, text} objects")

    if problems:
        return None, problems
    return {
        "scale": scale, "title": title[:120], "statement": statement[:400],
        "motivation": str(raw.get("motivation", ""))[:500], "narrative": narrative[:400],
        "requirements": reqs, "expires_on": expires,
        "provenance": [{"id": str(p.get("id", "")), "text": str(p.get("text", ""))} for p in prov],
    }, []


def install(agent: "Agent", clean: dict, day: int) -> "Wish":
    """Create the wish. A major wish's pursuit chapter is opened by the caller
    (the engine) so the phase-1 chapter machinery stays in one place."""
    w = Wish(id=uuid.uuid4().hex[:8], owner=agent.id, scale=clean["scale"], status="active",
             created_on=day, title=clean["title"], statement=clean["statement"],
             motivation=clean["motivation"], provenance=list(clean["provenance"]),
             requirements=list(clean["requirements"]), expires_on=clean["expires_on"])
    agent.wishes.append(w)
    return w


# ---- progress: the ONLY two writers -----------------------------------------


def _event_key(ev) -> str:
    return hashlib.sha1(
        f"{ev.minute}|{ev.verb}|{ev.actor}|{ev.target}|{ev.location}".encode()).hexdigest()[:12]


def update_from_event(wish: "Wish", agent: "Agent", ev) -> bool:
    """Count a real published Event against this wish. Idempotent per event."""
    if wish.status != "active":
        return False
    key = _event_key(ev)
    if key in wish.counted_event_keys:
        return False
    changed = False
    for r in wish.requirements:
        if r.completed:
            continue
        hit = (
            (r.kind == "location_visits" and ev.verb == "arrive"
             and ev.actor == agent.id and ev.location == r.target)
            or (r.kind == "action_count" and ev.verb == r.target and ev.actor == agent.id)
            or (r.kind == "talk_count" and ev.verb == "talk_start"
                and {ev.actor, ev.target} == {agent.id, r.target})
            or (r.kind == "meetups_kept" and ev.verb == "met_up"
                and agent.id in (ev.actor, ev.target)
                and (not r.target or r.target in (ev.actor, ev.target)))
            or (r.kind == "event_witnessed" and ev.verb == r.target)
        )
        if hit:
            r.progress += 1
            changed = True
    if changed:
        wish.counted_event_keys.append(key)
        del wish.counted_event_keys[:-COUNTED_EVENT_KEYS_MAX]
    return changed


def update_from_state(wish: "Wish", agent: "Agent") -> bool:
    """Sample the world values the ordinary systems moved (daily settlement)."""
    if wish.status != "active":
        return False
    changed = False
    for r in wish.requirements:
        before = r.progress
        if r.kind in RELATIVE_KINDS and r.target in agent.relationships:
            r.progress = float(getattr(agent.rel(r.target), r.kind))
        elif r.kind == "money_gain":
            r.progress = max(0.0, float(agent.state.money) - r.baseline)
        changed |= r.progress != before
    return changed


def outcome(wish: "Wish", day: int) -> tuple | None:
    """(status, reason) once the wish has ended, else None. Completion wins over
    an expiry falling on the same day."""
    if wish.status != "active":
        return None
    if wish.requirements and all(r.completed for r in wish.requirements):
        return "completed", "every requirement was met"
    if wish.expires_on is not None and day > wish.expires_on:
        return "failed", f"the deadline on day {wish.expires_on} passed"
    return None


def finish(wish: "Wish", status: str, day: int, reason: str) -> bool:
    if wish.status != "active" or status not in TERMINAL:
        return False
    wish.status, wish.ended_on, wish.outcome_reason = status, day, reason
    return True


# ---- the drive (spec C) -----------------------------------------------------


def _drive_state(wish: "Wish", day: int) -> dict:
    st = wish.drive
    if not st:
        st.update(_clean_drive(None))
    if st.get("daily_day") != day:
        st["daily_day"], st["daily_attempts"] = day, 0
    return st


def _roll(run_seed: str, agent_id: str, wish_id: str, day: int, cursor: int) -> float:
    """Stable across processes and restarts (spec: SHA-256 over run/day/agent/wish)."""
    raw = hashlib.sha256(
        f"wish-drive|{run_seed}|{agent_id}|{wish_id}|{day}|{cursor}".encode()).hexdigest()
    return int(raw[:8], 16) / 0xFFFFFFFF


def location_open_for(agent: "Agent", world: "World", location: str, now: int) -> bool:
    """The same rules the routine itself respects: a shuttered shop and a rained-out
    park are closed to a wish exactly as they are to a timetable."""
    loc = world.locations.get(location)
    if loc is None:
        return False
    if world.effect_active("rain") and loc.kind == "park":
        return False
    dow = (now // DAY_MIN) % 7
    return not (loc.owner and loc.price > 0 and dow in loc.closed_days and loc.owner != agent.id)


def work_available(agent: "Agent", world: "World", location: str, now: int) -> bool:
    dow = (now // DAY_MIN) % 7
    if dow in agent.profile.off_days or not location_open_for(agent, world, location, now):
        return False
    loc = world.locations.get(location)
    if loc is None:
        return False
    if loc.owner and loc.price > 0 and dow in loc.closed_days:
        return False
    employer_shop = next((l for l in world.locations.values()
                          if l.owner == agent.state.employer and l.price > 0), None)
    return not (employer_shop is not None and dow in employer_shop.closed_days)


def _todays_work_location(agent: "Agent", world: "World", now: int) -> str:
    dow = (now // DAY_MIN) % 7
    entries = agent.routine._table(dow)[0]
    return next((e.location for e in entries
                 if e.action == "work" and e.location in world.locations), "")


def actionable_summary(agent: "Agent", world: "World", reqs: list) -> list:
    """Per-requirement actionability, for the seeding report."""
    return [requirement_actionable(agent, world, r) for r in reqs]


def social_target(agent: "Agent") -> str:
    """The resident an active wish most wants time with -- read by the existing
    meetup system, which then applies all of its own rules."""
    for w in sorted(active_wishes(agent), key=lambda w: (w.scale != "major", w.created_on, w.id)):
        r = next((r for r in w.requirements if not r.completed and r.kind in SOCIAL_KINDS and r.target), None)
        if r is not None:
            return r.target
    return ""


def next_directive(agent: "Agent", world: "World", now: int, routine_action: str,
                   run_seed: str = "") -> dict | None:
    """One soft directive for a discretionary slot, or None.

    Major wishes are considered before minor ones. Within a wish the least-advanced
    unmet requirement wins, with a deterministic cursor breaking ties so a wish with
    several requirements rotates instead of fixating. A requirement that cannot be
    acted on right now (shop shut, partner asleep, rain) records a blocked day."""
    if routine_action not in ("rest", "idle") or agent.state.current_action == "sleep":
        return None
    day = now // DAY_MIN + 1
    for wish in sorted(active_wishes(agent), key=lambda w: (w.scale != "major", w.created_on, w.id)):
        st = _drive_state(wish, day)
        cap = DRIVE_MAJOR_DAILY_ATTEMPTS if wish.scale == "major" else DRIVE_MINOR_DAILY_ATTEMPTS
        prob = DRIVE_MAJOR_PROBABILITY if wish.scale == "major" else DRIVE_MINOR_PROBABILITY
        if st["daily_attempts"] >= cap:
            continue
        if _roll(run_seed, agent.id, wish.id, day, st["cursor"]) >= prob:
            continue
        # Only requirements this resident can actually act on are ever pursued, so a
        # passive one can never be the reason a day is recorded as blocked.
        candidates = [(i, r) for i, r in enumerate(wish.requirements)
                      if not r.completed and requirement_actionable(agent, world, r)
                      and st["attempt_days"].get(str(i)) != day]
        if not candidates:
            continue
        n = len(wish.requirements)
        candidates.sort(key=lambda x: (x[1].fraction, (x[0] - st["cursor"]) % n))
        index, req = candidates[0]
        directive = _directive_for(agent, world, req, now)
        # A move that only *prepares* a work step keeps the requirement available
        # today, so arriving and then working is one intention, not two attempts.
        # Walking to the workplace and then working is ONE intention, not two: the
        # preparatory move neither spends the day's attempt nor retires the
        # requirement, so the work step itself can still happen today.
        preparing = (directive is not None and directive.get("action") == "move"
                     and req.kind in ("action_count", "money_gain") and req.target != "rest")
        if not preparing:
            st["attempt_days"][str(index)] = day
            st["daily_attempts"] += 1
        st["cursor"] = (index + 1) % n
        if directive is None:
            note_blocked(agent, wish, now)
            continue
        clear_blocked(wish)
        directive.update(wish_id=wish.id, requirement_index=index)
        return directive
    return None


def _directive_for(agent: "Agent", world: "World", req: Requirement, now: int) -> dict | None:
    """Translate one requirement into a legal next step, or None when the world
    currently forbids it (which is what makes a day 'blocked')."""
    if req.kind == "location_visits":
        if agent.state.location == req.target:
            return None                      # already here; the arrive event is what counts
        return ({"action": "move", "location": req.target}
                if location_open_for(agent, world, req.target, now) else None)
    if req.kind in ("talk_count", "friendship", "trust"):
        other = world.agents.get(req.target)
        if other is None or other.state.current_action == "sleep":
            return None
        if other.state.location == agent.state.location:
            return {"action": "talk_bias", "target": other.id}
        return None                          # elsewhere -> the meetup system's job
    if req.kind == "meetups_kept":
        return None                          # arranged only through the meetup system
    if req.kind == "money_gain" or (req.kind == "action_count" and req.target == "work"):
        if req.kind == "money_gain" and not actionable_income_path(agent, world):
            return None
        work = _todays_work_location(agent, world, now)
        if not work or not work_available(agent, world, work, now):
            return None
        return ({"action": "work"} if agent.state.location == work
                else {"action": "move", "location": work})
    if req.kind == "action_count" and req.target in ("rest", "idle"):
        return {"action": req.target}
    return None


# ---- blocked days & frustration (spec D) ------------------------------------


def note_blocked(agent: "Agent", wish: "Wish", now: int) -> bool:
    """Record that today was a blocked day for this wish. Distinct and consecutive:
    a second block on the same day is a no-op, and a gap resets the streak to 1.
    Writes at most one frustration memory per day, past the cooldown. Returns True
    if a frustration memory was written."""
    if wish.status != "active":
        return False
    day = now // DAY_MIN + 1
    st = _drive_state(wish, day)
    last = st["last_blocked_day"]
    if last == day:
        return False                                   # already counted today
    st["blocked_streak"] = st["blocked_streak"] + 1 if last == day - 1 else 1
    st["last_blocked_day"] = day
    if st["blocked_streak"] < FRUSTRATION_BLOCKED_DAYS:
        return False
    if st["last_frustration_day"] >= 0 and day - st["last_frustration_day"] < FRUSTRATION_COOLDOWN_DAYS:
        return False
    agent.memory.add(MemoryItem(minute=now, importance=4, kind="reflection",
                                text=FRUSTRATION_TEXT, tags=[f"goal:{wish.id}"]))
    st["last_frustration_day"] = day
    wish.frustration_count += 1
    return True


def clear_blocked(wish: "Wish") -> None:
    """A directive the world actually allowed -- the streak is over."""
    st = wish.drive
    if st:
        st["blocked_streak"] = 0
        st["last_blocked_day"] = -1


def abandonment_pressure(wish: "Wish") -> float:
    st = wish.drive or {}
    return (BLOCKED_WEIGHT * int(st.get("blocked_streak", 0))
            + FRUSTRATION_WEIGHT * wish.frustration_count)


def abandonment_threshold(agent: "Agent", wish: "Wish", day: int) -> float:
    """How much frustration this resident will carry before letting go: their
    conscientiousness, how long they have already carried it, and the weight of
    the wish itself."""
    consc = float(agent.profile.personality.get("conscientiousness", 0.5))
    age = max(0, day - wish.created_on)
    sunk = SUNK_COST_MAX * min(1.0, age / SUNK_COST_FULL_DAYS)
    return ABANDON_BASE.get(wish.scale, 1.0) + CONSCIENTIOUSNESS_WEIGHT * consc + sunk


def should_abandon(agent: "Agent", wish: "Wish", day: int) -> bool:
    """Pure rules, evaluated once per day at the settlement."""
    if wish.status != "active" or day - wish.created_on < ABANDON_MIN_AGE_DAYS:
        return False
    pressure = abandonment_pressure(wish)
    if pressure <= 0:
        return False
    return pressure > abandonment_threshold(agent, wish, day)
