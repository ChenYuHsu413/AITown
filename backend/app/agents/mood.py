"""Mood as a light, continuous state (活化 phase D).

``state.mood`` (a label: neutral/happy/worried/upset/anxious) is set by a handful
of hard rules and read by many. Underneath it, ``state.mood_valence`` (-1..1) is
moved by what actually happens to the resident -- how a conversation felt to them,
a walk-out, a promise kept or missed, a wish closing, the day's takings -- and
drifts back toward even every night. ``state.mood_note`` keeps the latest weighty
cause (a template line, never free text), so the morning decision and dialogue
context can say *why* someone feels the way they do.

Pure rules, no LLM. Weights are external (AI_TOWN_PACE_MOOD_*).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import pacing

if TYPE_CHECKING:
    from .agent import Agent

NOTE_MIN = 0.15          # a nudge at least this big becomes the remembered cause


def nudge(agent: "Agent", delta: float, note: str = "") -> None:
    st = agent.state
    st.mood_valence = max(-1.0, min(1.0, float(st.mood_valence) + delta))
    if note and abs(delta) >= NOTE_MIN:
        st.mood_note = note


def from_conversation(agent: "Agent", sentiment: float, conflict: float) -> None:
    """How the exchange felt to THIS speaker (their own signals)."""
    nudge(agent, (sentiment - 0.5) * pacing.MOOD_TALK_WEIGHT - conflict * pacing.MOOD_CONFLICT_WEIGHT)


def decay(agent: "Agent") -> None:
    """Overnight: feelings soften toward even; a faded cause is forgotten."""
    st = agent.state
    st.mood_valence = round(float(st.mood_valence) * pacing.MOOD_DAILY_DECAY, 4)
    if abs(st.mood_valence) < 0.1:
        st.mood_note = ""


def describe(agent: "Agent", world) -> str:
    """One English phrase for prompts: the label, its weight, and its cause."""
    v = float(agent.state.mood_valence)
    feel = ("in high spirits" if v >= 0.5 else "in a good mood" if v >= 0.2
            else "really down" if v <= -0.5 else "a bit low" if v <= -0.2 else "even")
    out = f"{agent.state.mood} ({feel})"
    if agent.state.mood_note and abs(v) >= 0.1:
        from .decision import _resolve_placeholders     # lazy: decision imports this module
        out += f" -- mostly because: {_resolve_placeholders(agent.state.mood_note, world)}"
    return out
