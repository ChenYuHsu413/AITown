"""The town's tempo, in one place.

Each value can be overridden from the environment (``AI_TOWN_PACE_<NAME>``), so a
run can be retuned without a code change. Only the speed of the life-cycle lives
here: how often a wish can surface, how long one is given, how long the pause
after a chapter lasts, how many conversations a day can hold. The weight of a
life -- abandonment friction, blocked days, frustration -- is deliberately NOT
here and stays where it is: a faster turnover must not make wishes lighter.

2026-11 (zh-native era): one notch faster than the formal era --
ordinary cadence 5 -> 3 days, generation decay 0.65 -> 0.75 (base unchanged),
deadline guidance major 21 -> 14 / minor 10 -> 7 days, interlude 3-7 -> 2-4 days,
daily dialogue cap -> 20.
"""

from __future__ import annotations

import os


def _num(name: str, default, cast=float):
    raw = os.environ.get(f"AI_TOWN_PACE_{name}", "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


# ---- wish generation -------------------------------------------------------------
GENERATION_BASE_P = _num("GENERATION_BASE_P", 0.80)          # attempt probability, no active major
GENERATION_DECAY = _num("GENERATION_DECAY", 0.75)            # multiplied per active major town-wide
GENERATION_ORDINARY_EVERY = _num("ORDINARY_EVERY", 3, int)   # min sim-days between two attempts
# Deadline guidance: the default span, and what the generation prompt suggests.
SPAN_DAYS = {"major": _num("SPAN_MAJOR", 14, int), "minor": _num("SPAN_MINOR", 7, int)}

# ---- the pause after a chapter -----------------------------------------------------
INTERLUDE_MIN_DAYS = _num("INTERLUDE_MIN", 2, int)
INTERLUDE_MAX_DAYS = _num("INTERLUDE_MAX", 4, int)
# Base interlude length per emotional residue (personality then moves it +-1 and the
# range above clamps it). The formal era's 3-7 table, scaled into 2-4.
RESIDUE_DAYS = {"fulfilled": 3, "relieved": 2, "proud": 3, "wistful": 4, "restless": 3, "unmoored": 4}

# ---- conversations --------------------------------------------------------------
DIALOGUE_CAP = _num("DIALOGUE_CAP", 20, int)                 # per sim-day, live runs only
DIALOGUE_MAX_TURNS = _num("DIALOGUE_MAX_TURNS", 8, int)      # hard cap, one LLM call per line
DIALOGUE_INTIMATE_MIN_TURNS = _num("DIALOGUE_INTIMATE_MIN_TURNS", 4, int)   # confide/confront/confession
DIALOGUE_TELLS_MAX = _num("DIALOGUE_TELLS_MAX", 3, int)      # news handed over per conversation
DIALOGUE_TENSE_TRUST_MARGIN = _num("DIALOGUE_TENSE_TRUST_MARGIN", 5.0)   # trust this close to a confide bar -> smart tier
DIALOGUE_STORM_OFF_FRIENDSHIP = _num("DIALOGUE_STORM_OFF_FRIENDSHIP", 4.0)   # a walk-out costs both sides this
DIALOGUE_STORM_OFF_TRUST = _num("DIALOGUE_STORM_OFF_TRUST", 3.0)

# ---- the world's things ------------------------------------------------------------
FRESH_DAYS = _num("FRESH_DAYS", 7, int)                      # a finished thing reads as "new" this long
