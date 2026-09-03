"""Self-grown wishes: strict data, rule-only progress, rare generation."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import asdict, dataclass, field

from .chapters import DAY_MIN, make_pursuit, memory_id
from .core import MemoryItem

SCALES = ("small", "major")
STATUSES = ("active", "completed", "failed", "abandoned")
REQUIREMENT_KINDS = ("action_count", "location_visits", "talk_count", "friendship", "trust",
                     "money", "money_gain", "event_count")
ALLOWED_ACTIONS = ("sleep", "eat", "work", "rest", "idle", "arrive", "talk_start", "repaired")
ALLOWED_EVENTS = ("day_summary", "breakdown", "repaired", "meetup_arranged", "met_up",
                  "rain_start", "rain_end", "festival_start", "festival_end", "transition")

MATERIAL_MIN_COUNT = 3
MATERIAL_MIN_IMPORTANCE = 10
MATERIAL_MAX = 8
GENERATION_RETRY_MIN_DAYS = 3
GENERATION_RETRY_MAX_DAYS = 7
GENERATION_BASE_COOLDOWN_DAYS = 7
MAJOR_SOFT_TARGET = 3
MAJOR_THRESHOLD_STEP = 2
MIN_WISH_DAYS = 2
MAX_WISH_DAYS = 120
PROGRESS_MEMORY_EVERY = 3
ABANDON_MIN_DAYS = 5
ABANDON_STALE_DAYS = 3
_FRUSTRATION_WORDS = ("failed", "couldn't", "cannot", "unable", "stuck", "setback",
                      "frustrat", "blocked", "avoided", "no progress", "gave up")


@dataclass
class Requirement:
    kind: str
    threshold: float
    current: float = 0.0
    unit: str = "count"
    completed: bool = False
    target: str = ""
    baseline: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict) -> "Requirement | None":
        try:
            r = cls(kind=str(raw["kind"]), threshold=float(raw["threshold"]),
                    current=float(raw.get("current", 0)), unit=str(raw.get("unit", "count")),
                    target=str(raw.get("target", "")), baseline=float(raw.get("baseline", 0)))
            r.completed = bool(raw.get("completed", r.current >= r.threshold))
            return r
        except (KeyError, TypeError, ValueError, OverflowError):
            return None


@dataclass
class Wish:
    id: str
    owner_id: str
    title: str
    statement: str
    motivation: str
    scale: str
    status: str
    created_day: int
    ended_day: int = 0
    source_memory_refs: list[dict] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    failure_conditions: list[dict] = field(default_factory=list)
    progress: float = 0.0
    last_progress_day: int = 0
    secret_id: str = ""
    related_chapter_id: str = ""
    outcome_reason: str = ""
    counted_event_keys: list[str] = field(default_factory=list)
    progress_marks: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Wish | None":
        try:
            reqs = [x for x in (Requirement.from_dict(r) for r in raw.get("requirements", [])) if x]
            w = cls(id=str(raw["id"]), owner_id=str(raw["owner_id"]), title=str(raw["title"]),
                    statement=str(raw["statement"]), motivation=str(raw.get("motivation", "")),
                    scale=str(raw["scale"]), status=str(raw.get("status", "active")),
                    created_day=int(raw["created_day"]), requirements=reqs)
            for k in ("ended_day", "source_memory_refs", "failure_conditions", "progress",
                      "last_progress_day", "secret_id", "related_chapter_id", "outcome_reason",
                      "counted_event_keys", "progress_marks"):
                if k in raw:
                    setattr(w, k, raw[k])
            if w.scale not in SCALES or w.status not in STATUSES or not w.id or not w.owner_id:
                return None
            return w
        except (KeyError, TypeError, ValueError):
            return None


def active_wish(agent, scale: str | None = None) -> Wish | None:
    return next((w for w in agent.wishes if w.status == "active" and (scale is None or w.scale == scale)), None)


def eligible_material(agent, since_minute: int = -1) -> list[dict]:
    good = [m for m in agent.memory.items if m.minute > since_minute and m.kind != "biography"
            and m.importance >= 3 and len(m.text.strip()) >= 24
            and not re.fullmatch(r"(?i)(ok|fine|nothing|the usual)[.! ]*", m.text.strip())]
    good.sort(key=lambda m: (m.importance, m.minute), reverse=True)
    return [{"id": memory_id(agent.id, m), "text": m.text, "minute": m.minute,
             "importance": m.importance} for m in good[:MATERIAL_MAX]]


def generation_threshold(active_major_count: int) -> int:
    return MATERIAL_MIN_IMPORTANCE + max(0, active_major_count - MAJOR_SOFT_TARGET) * MAJOR_THRESHOLD_STEP


def generation_eligible(agent, day: int, active_major_count: int) -> tuple[bool, list[dict]]:
    if active_wish(agent, "major") or agent.chapter is not None and agent.chapter.chapter_type != "ordinary":
        return False, []
    if day < agent.wish_next_attempt_day:
        return False, []
    material = eligible_material(agent, agent.wish_last_attempt_minute)
    if len(material) < MATERIAL_MIN_COUNT:
        return False, material
    if sum(m["importance"] for m in material) < generation_threshold(active_major_count):
        return False, material
    return True, material


def retry_day(agent_id: str, day: int) -> int:
    seed = int(hashlib.sha256(f"wish-retry|{agent_id}|{day}".encode()).hexdigest()[:8], 16)
    span = GENERATION_RETRY_MAX_DAYS - GENERATION_RETRY_MIN_DAYS + 1
    return day + GENERATION_RETRY_MIN_DAYS + seed % span


def generation_slot(agent_id: str, day: int) -> bool:
    """Deterministically spread ten residents across days (roughly 1/7 per day)."""
    n = int(hashlib.sha256(f"wish-slot|{agent_id}".encode()).hexdigest()[:8], 16)
    return day % GENERATION_BASE_COOLDOWN_DAYS == n % GENERATION_BASE_COOLDOWN_DAYS


def material_for_prompt(agent, world, memories: list[dict], secrets) -> dict:
    last = agent.chapter_history[-1].to_dict() if agent.chapter_history else None
    beliefs = [{"subject": b.subject, "text": b.text, "confidence": b.confidence}
               for b in sorted(agent.semantic.beliefs, key=lambda b: b.confidence, reverse=True)[:5]]
    rels = [{"agent_id": oid, "friendship": round(r.friendship), "trust": round(r.trust)}
            for oid, r in sorted(agent.relationships.items(), key=lambda x: x[1].friendship, reverse=True)[:5]]
    safe_secrets = [{"id": s.id, "text": s.text} for s in secrets.active_secrets_of(agent.id)
                    if getattr(s, "source_kind", "") != "wish"]
    return {"memories": memories, "last_chapter": last, "beliefs": beliefs, "relationships": rels,
            "money": agent.state.money, "occupation": agent.profile.occupation,
            "personality": dict(agent.profile.personality), "secrets": safe_secrets,
            "agents": list(world.agents), "locations": list(world.locations),
            "actions": list(ALLOWED_ACTIONS), "events": list(ALLOWED_EVENTS)}


def _finite_positive(x, lo=1, hi=10000) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x)) and lo <= float(x) <= hi


def validate_generation(raw: object, agent, world, material: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("no_wish") is True:
        return None
    allowed = {"title", "statement", "motivation", "scale", "source_memory_refs",
               "requirements", "failure_conditions"}
    if set(raw) - allowed:
        return None
    title, statement = str(raw.get("title", "")).strip(), str(raw.get("statement", "")).strip()
    scale = str(raw.get("scale", ""))
    if not title or len(statement) < 12 or scale not in SCALES:
        return None
    provided = {m["id"]: m for m in material["memories"]}
    refs = []
    for rid in raw.get("source_memory_refs", []):
        if str(rid) not in provided:
            return None
        refs.append({"id": str(rid), "text": provided[str(rid)]["text"]})
    if not refs:
        return None
    reqs = []
    for rr in raw.get("requirements", []):
        if not isinstance(rr, dict) or set(rr) - {"kind", "target", "threshold", "unit"}:
            return None
        r = Requirement.from_dict(rr)
        if r is None or r.kind not in REQUIREMENT_KINDS or not _finite_positive(r.threshold):
            return None
        if r.kind in ("talk_count", "friendship", "trust") and r.target not in world.agents:
            return None
        if r.kind == "location_visits" and r.target not in world.locations:
            return None
        if r.kind == "action_count" and r.target not in ALLOWED_ACTIONS:
            return None
        if r.kind == "event_count" and r.target not in ALLOWED_EVENTS:
            return None
        if r.kind in ("friendship", "trust") and r.threshold > 100:
            return None
        if r.kind in ("money", "money_gain"):
            r.baseline = agent.state.money
            r.unit = "currency"
        reqs.append(r)
    effort = sum(r.threshold if r.kind not in ("friendship", "trust", "money")
                 else max(1, r.threshold - r.baseline) for r in reqs)
    if scale == "major" and effort < 2:
        return None
    if scale == "small" and effort > 12:
        return None
    if scale == "major" and not reqs:
        return None
    failure = []
    for fc in raw.get("failure_conditions", []):
        if not isinstance(fc, dict) or set(fc) - {"kind", "days"} or fc.get("kind") != "deadline":
            return None
        if not _finite_positive(fc.get("days"), MIN_WISH_DAYS, MAX_WISH_DAYS):
            return None
        failure.append({"kind": "deadline", "days": int(fc["days"])})
    return {"title": title[:120], "statement": statement[:400],
            "motivation": str(raw.get("motivation", ""))[:500], "scale": scale,
            "source_memory_refs": refs, "requirements": reqs, "failure_conditions": failure}


def install(agent, secrets, clean: dict, day: int) -> Wish | None:
    if active_wish(agent, clean["scale"]):
        return None
    if (clean["scale"] == "major" and agent.chapter is not None
            and agent.chapter.chapter_type != "ordinary"):
        return None
    w = Wish(id=uuid.uuid4().hex[:8], owner_id=agent.id, title=clean["title"],
             statement=clean["statement"], motivation=clean["motivation"], scale=clean["scale"],
             status="active", created_day=day, source_memory_refs=clean["source_memory_refs"],
             requirements=clean["requirements"], failure_conditions=clean["failure_conditions"],
             last_progress_day=day)
    s = secrets.add(agent.id, w.statement, 0.8, (day - 1) * DAY_MIN,
                    source_kind="wish", source_id=w.id, social_enabled=False)
    w.secret_id = s.id
    if w.scale == "major":
        ch = make_pursuit(w.statement, w.title, f"I am pursuing this now: {w.statement}", day)
        ch.related_goal_id = w.id
        w.related_chapter_id = ch.id
        agent.chapter = ch
    agent.wishes.append(w)
    return w


def _event_key(ev) -> str:
    return hashlib.sha1(f"{ev.minute}|{ev.verb}|{ev.actor}|{ev.target}|{ev.location}|{ev.text}".encode()).hexdigest()[:12]


def update_from_event(wish: Wish, agent, ev, day: int) -> bool:
    if wish.status != "active": return False
    key = _event_key(ev)
    if key in wish.counted_event_keys: return False
    changed = False
    for r in wish.requirements:
        hit = ((r.kind == "action_count" and ev.actor == agent.id and ev.verb == r.target)
               or (r.kind == "location_visits" and ev.actor == agent.id and ev.verb == "arrive" and ev.location == r.target)
               or (r.kind == "talk_count" and ev.verb == "talk_start"
                   and {ev.actor, ev.target} == {agent.id, r.target})
               or (r.kind == "event_count" and ev.verb == r.target and (not ev.actor or ev.actor == agent.id)))
        if hit and not r.completed:
            r.current += 1; r.completed = r.current >= r.threshold; changed = True
    if changed:
        wish.counted_event_keys.append(key); wish.last_progress_day = day
        wish.progress = sum(min(1, r.current / r.threshold) for r in wish.requirements) / max(1, len(wish.requirements))
    return changed


def update_state(wish: Wish, agent, day: int) -> bool:
    changed = False
    for r in wish.requirements:
        old = r.current
        if r.kind == "friendship" and r.target in agent.relationships: r.current = agent.rel(r.target).friendship
        elif r.kind == "trust" and r.target in agent.relationships: r.current = agent.rel(r.target).trust
        elif r.kind == "money": r.current = agent.state.money
        elif r.kind == "money_gain": r.current = max(0, agent.state.money - r.baseline)
        r.completed = r.current >= r.threshold
        changed |= r.current != old
    if changed:
        wish.last_progress_day = day
        wish.progress = sum(min(1, r.current/r.threshold) for r in wish.requirements)/max(1,len(wish.requirements))
    return changed


def outcome(wish: Wish, day: int) -> tuple[str, str] | None:
    if wish.requirements and all(r.completed for r in wish.requirements):
        return "completed", "all observable requirements were met"
    for fc in wish.failure_conditions:
        if fc["kind"] == "deadline" and day >= wish.created_day + fc["days"]:
            return "failed", f"deadline passed after {fc['days']} days"
    return None


def finish(wish: Wish, secrets, status: str, day: int, reason: str) -> bool:
    if wish.status != "active" or status not in STATUSES[1:]: return False
    wish.status, wish.ended_day, wish.outcome_reason = status, day, reason
    secrets.resolve(wish.secret_id, (day - 1) * DAY_MIN, reason, from_wish=True)
    return True


def abandonment_score(agent, wish: Wish, day: int, refs: list[MemoryItem]) -> float:
    stale = max(0, day - wish.last_progress_day)
    frustration = min(0.45, len(refs) * 0.12) + min(0.25, stale * 0.03)
    recent_penalty = max(0, ABANDON_STALE_DAYS - stale) * 0.12
    resistance = (agent.profile.personality.get("conscientiousness", 0.5) * 0.75
                  + wish.progress * 0.65 + recent_penalty)
    if wish.scale == "small": frustration += 0.15
    return frustration - resistance


def validate_abandon(agent, wish: Wish, day: int, memory_refs: list[str],
                     allowed_refs: set[str] | None = None) -> tuple[bool, list[dict]]:
    if wish.status != "active" or day - wish.created_day < ABANDON_MIN_DAYS or day == wish.last_progress_day:
        return False, []
    by_id = {memory_id(agent.id, m): m for m in agent.memory.items
             if m.minute >= (wish.created_day - 1) * DAY_MIN and m.importance >= 4}
    refs = [by_id[r] for r in memory_refs if r in by_id and (allowed_refs is None or r in allowed_refs)]
    refs = [m for m in refs if any(w in m.text.lower() for w in _FRUSTRATION_WORDS)]
    if not refs: return False, []
    return abandonment_score(agent, wish, day, refs) >= 0, [{"id": memory_id(agent.id,m),"text":m.text} for m in refs]
