"""Romance -- a second relationship track, independent of friendship/trust.

``Relationship.romance`` (0-100, directional, so a one-sided crush is expressible)
grows purely by rules from warm conversations between people who are already
friends; the pair's ``romance_stage`` walks none -> crushing -> dating -> partners.
A crush is private (it plants a secret, not a chronicle beat); confessions and
pairings are public. No LLM decides outcomes -- the model only plays the scene the
rules have already settled. Everything here is pure functions + constants; the
wiring lives in decision.py (growth/crush/confession) and engine.py (co-presence,
chronicle, hooks).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent

# ---- thresholds ------------------------------------------------------------
GROW_FRIEND_MIN = 45.0        # romance only grows between people already this friendly
GROW_SENTIMENT_MIN = 0.6      # ...and only on a warm-enough exchange
CONFIDE_BONUS = 3.0           # a vulnerable confide draws them closer
SETTING_MULT = 1.5            # festival, or the park after dark (romantic landmark)
DECAY_AFTER_DAYS = 30         # no interaction this long -> -1/day
DECAY_PER_DAY = 1.0

CRUSH = 40.0                  # one side past this -> crushing (plants a private secret)
CONFESS_ROMANCE = 60.0        # may decide to confess at/above this...
CONFESS_FRIEND = 55.0         # ...and this friendship
CONFESS_COOLDOWN_DAYS = 14
ACCEPT_ROMANCE = 45.0         # the other side at/above this -> confession accepted
PARTNER_ROMANCE = 70.0        # ...and this -> a proposal is accepted
DATING_TO_PARTNER_DAYS = 30   # must date at least this long before proposing
REJECT_ROMANCE_HIT = 15.0     # a rejection costs the confessor this much romance
AWKWARD_DAYS = 7              # ...and dampens their talk rate for a week
AWKWARD_TALK_MULT = 0.3

# ignition (organic spark): enough time together + mutual openness -> a low chance
# of a "you keep noticing them" nudge into reflection.
IGNITE_HOURS = 30
IGNITE_MINUTES = IGNITE_HOURS * 60
IGNITE_INCL_MIN = 0.3
IGNITE_PROB = 0.15
IGNITE_COOLDOWN_DAYS = 14
IGNITE_ROMANCE_KICK = 12.0    # the spark itself: proximity-born attraction, before friendship deepens it

AGE_GAP_MAX = 20              # absurd pairings (19 vs 61) are excluded outright

STAGES = ("none", "crushing", "dating", "partners")


def stage_rank(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else 0


def orientation_coeff(a: "Agent", b: "Agent") -> float:
    """A's directional propensity for a romance with B, by B's gender (0..1).
    Missing/partial bias falls back to the defaults."""
    bias = getattr(a.profile, "orientation_bias", None) or {}
    key = "same" if a.profile.gender and a.profile.gender == b.profile.gender else "other"
    default = 0.2 if key == "same" else 1.0
    try:
        return float(bias.get(key, default))
    except (TypeError, ValueError):
        return default


def eligible_pair(a: "Agent", b: "Agent") -> bool:
    """Both willing to a degree, and not an absurd age gap. inclination 0 opts a
    character out of the romance line entirely."""
    return (a.profile.romantic_inclination > 0 and b.profile.romantic_inclination > 0
            and abs(a.profile.age - b.profile.age) <= AGE_GAP_MAX)


def growth(a: "Agent", b: "Agent", sentiment: float, setting: bool, confided: bool) -> float:
    """Romance gained on this exchange (symmetric). Product of both inclinations
    keeps low-openness people slow to fall; a romantic setting amplifies; a confide
    adds a flat closeness bump."""
    g = a.profile.romantic_inclination * b.profile.romantic_inclination * max(0.0, sentiment) * 2.0
    if setting:
        g *= SETTING_MULT
    if confided:
        g += CONFIDE_BONUS
    return g


def crush_secret_text(target_pinyin: str) -> str:
    """English (internal rule); the display layer translates it. Pinyin name so the
    secret system resolves the subject like any other."""
    return f"I think I'm developing feelings for {target_pinyin}."
