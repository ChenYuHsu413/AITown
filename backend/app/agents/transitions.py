"""Life transitions -- Level 0, data-driven.

A long-held intention ("I should quit my job") only matters if the town can act
on it. A ``TransitionTemplate`` is a named life change with a precondition (may
this agent do it now?) and pure ``effects`` (mutate occupation / wage / routine /
mood). Reflection proposes one (see decision.maybe_reflect); the engine applies
it at the next daily settlement so the routine swaps on a clean day boundary.

No LLM here -- templates are plain rules. The registry is the whole surface: add
a template, it becomes available to every agent whose precondition passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .routine import Routine, RoutineEntry, hm

if TYPE_CHECKING:
    from ..world.world import World
    from .agent import Agent

TRANSITION_COOLDOWN_DAYS = 7          # min sim-days between one agent's life changes

# ---- relationship stages (milestones) --------------------------------------
# Friendship bands. A conversation that crosses a boundary is a visible beat.
_STAGE_ORDER = ["stranger", "acquaintance", "friend", "close"]


def rel_stage(friendship: float) -> str:
    return ("stranger" if friendship < 35 else "acquaintance" if friendship < 55
            else "friend" if friendship < 75 else "close")


def stage_rank(stage: str) -> int:
    return _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else 0


# ---- routine builders ------------------------------------------------------
# Each keeps the agent's own home as the sleep anchor, so `Agent.home` stays valid
# after a swap. Capture `home` BEFORE reassigning the routine (home reads it).

def _mk(entries: list[tuple[int, str, str]]) -> Routine:
    return Routine([RoutineEntry(t, a, l) for (t, a, l) in entries])


def job_search_routine(home: str) -> Routine:
    """A job-seeker's day: mornings updating applications at home, then out around
    town (market, cafe, park) asking around, home by evening."""
    return _mk([
        (hm(8, 0), "eat", home),
        (hm(9, 0), "rest", home),          # applications / calls at home
        (hm(11, 0), "rest", "market"),     # asking around
        (hm(13, 0), "eat", "cafe"),
        (hm(14, 30), "rest", "park"),
        (hm(16, 30), "rest", "market"),
        (hm(18, 30), "eat", home),
        (hm(20, 0), "rest", home),
        (hm(22, 30), "sleep", home),
    ])


def shop_follow_routine(home: str, shop: str) -> Routine:
    """Staff shadow the shop's open hours. The shop's weekly closed day is handled
    by the same day-off logic as the owner (see decision.decide via `employer`), so
    it isn't baked into the timetable here."""
    return _mk([
        (hm(7, 30), "eat", home),
        (hm(8, 30), "work", shop),
        (hm(12, 30), "eat", shop),
        (hm(13, 30), "work", shop),
        (hm(18, 0), "eat", home),
        (hm(20, 0), "rest", home),
        (hm(22, 30), "sleep", home),
    ])


def freelance_routine(home: str) -> Routine:
    """Working for oneself from home, with a cafe lunch and an evening unwind."""
    return _mk([
        (hm(8, 30), "eat", home),
        (hm(9, 30), "work", home),
        (hm(12, 30), "eat", "cafe"),
        (hm(14, 0), "work", home),
        (hm(18, 30), "eat", home),
        (hm(20, 0), "rest", "park"),
        (hm(22, 30), "sleep", home),
    ])


# ---- template model --------------------------------------------------------

@dataclass
class TransitionTemplate:
    id: str
    label: str                                   # one line, shown to reflection
    goal: str                                    # the new life goal this installs
    occupation: str
    precondition: Callable[["Agent", "World"], bool]
    effects: Callable[["Agent", "World"], None]  # mutate profile/state/routine only
    clears_goal: tuple[str, ...] = ()            # drop existing goals matching these substrings
    resolves_secret_kw: tuple[str, ...] = ()     # resolve owner's secrets whose text matches (engine-side)


REGISTRY: dict[str, TransitionTemplate] = {}


def _register(t: TransitionTemplate) -> TransitionTemplate:
    REGISTRY[t.id] = t
    return t


def _owns_shop(a: "Agent", w: "World") -> bool:
    return any(l.owner == a.id and l.price > 0 for l in w.locations.values())


_NO_JOB = {"job seeker", "retired", "student", "freelancer"}


def available_for(agent: "Agent", world: "World") -> list[TransitionTemplate]:
    """Every template whose precondition currently passes for this agent."""
    return [t for t in REGISTRY.values() if t.precondition(agent, world)]


# ---- the four starter templates --------------------------------------------

def _pre_quit(a: "Agent", w: "World") -> bool:
    return a.profile.occupation.lower() not in _NO_JOB and not _owns_shop(a, w)


def _eff_quit(a: "Agent", w: "World") -> None:
    home = a.home                         # read before the routine swap
    a.profile.occupation = "job seeker"
    a.profile.daily_wage = 0.0
    a.state.employer = ""
    a.state.mood = "anxious"
    a.routine = job_search_routine(home)


_register(TransitionTemplate(
    id="quit_job", label="quit your current job to look for something that fits better",
    goal="Find a job that feels right", occupation="job seeker",
    precondition=_pre_quit, effects=_eff_quit,
    clears_goal=("job", "quit", "resign"),
    resolves_secret_kw=("interview", "resign", "quit", "job"),
))


def _shop_precondition(shop_id: str):
    def pre(a: "Agent", w: "World") -> bool:
        shop = w.locations.get(shop_id)
        if shop is None or not shop.owner or shop.owner == a.id:
            return False
        if a.state.employer == shop.owner:       # already works there
            return False
        if _owns_shop(a, w):                      # a shop owner won't go be someone's staff
            return False
        seeking = a.profile.occupation.lower() == "job seeker"
        return seeking or a.rel(shop.owner).friendship >= 50
    return pre


def _shop_effects(shop_id: str, occupation: str):
    def eff(a: "Agent", w: "World") -> None:
        home = a.home
        shop = w.locations[shop_id]
        a.profile.occupation = occupation
        a.profile.daily_wage = 45.0               # paid by the owner (real transfer at settlement)
        a.state.employer = shop.owner
        a.state.mood = "neutral"
        a.routine = shop_follow_routine(home, shop_id)
    return eff


_register(TransitionTemplate(
    id="take_job_cafe", label="take a job working at the cafe",
    goal="Make a real go of working at the cafe", occupation="cafe staff",
    precondition=_shop_precondition("cafe"), effects=_shop_effects("cafe", "cafe staff"),
    clears_goal=("job", "quit", "resign"),
))

_register(TransitionTemplate(
    id="take_job_bakery", label="take a job working at the bakery",
    goal="Make a real go of working at the bakery", occupation="bakery staff",
    precondition=_shop_precondition("bakery"), effects=_shop_effects("bakery", "bakery staff"),
    clears_goal=("job", "quit", "resign"),
))


def _pre_freelance(a: "Agent", w: "World") -> bool:
    return a.profile.occupation.lower() != "freelancer" and not _owns_shop(a, w)


def _eff_freelance(a: "Agent", w: "World") -> None:
    home = a.home
    a.profile.occupation = "freelancer"
    a.profile.daily_wage = 35.0
    a.state.employer = ""
    a.state.mood = "neutral"
    a.routine = freelance_routine(home)


_register(TransitionTemplate(
    id="freelance_from_home", label="strike out on your own, freelancing from home",
    goal="Build a freelance living on my own terms", occupation="freelancer",
    precondition=_pre_freelance, effects=_eff_freelance,
    clears_goal=("job", "quit", "resign"),
))
