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
DIALOGUE_TELLS_MAX = _num("DIALOGUE_TELLS_MAX", 2, int)      # news handed over per conversation (1 per speaker)
DIALOGUE_TENSE_TRUST_MARGIN = _num("DIALOGUE_TENSE_TRUST_MARGIN", 5.0)   # trust this close to a confide bar -> smart tier
DIALOGUE_STORM_OFF_FRIENDSHIP = _num("DIALOGUE_STORM_OFF_FRIENDSHIP", 4.0)   # a walk-out costs both sides this
DIALOGUE_STORM_OFF_TRUST = _num("DIALOGUE_STORM_OFF_TRUST", 3.0)

# ---- mood (agents/mood.py) -----------------------------------------------------------
MOOD_TALK_WEIGHT = _num("MOOD_TALK_WEIGHT", 0.4)             # (own sentiment - 0.5) x this
MOOD_CONFLICT_WEIGHT = _num("MOOD_CONFLICT_WEIGHT", 0.3)     # own friction x this, downward
MOOD_DAILY_DECAY = _num("MOOD_DAILY_DECAY", 0.5)             # overnight: valence x this
MOOD_STORM_OFF = _num("MOOD_STORM_OFF", 0.3)
MOOD_PROMISE_KEPT = _num("MOOD_PROMISE_KEPT", 0.2)
MOOD_PROMISE_MISSED = _num("MOOD_PROMISE_MISSED", 0.15)
MOOD_WISH = {"completed": _num("MOOD_WISH_COMPLETED", 0.4), "failed": -_num("MOOD_WISH_FAILED", 0.3),
             "abandoned": -_num("MOOD_WISH_ABANDONED", 0.05)}
MOOD_REVENUE = _num("MOOD_REVENUE", 0.15)                    # a slow / busy day at one's own shop

# ---- the morning micro-decision (agents/dayplan.py) ------------------------------------
DAYPLAN_MAX_SLOTS = _num("DAYPLAN_MAX_SLOTS", 3, int)        # changes to the usual day
DAYPLAN_MAX_SLOT_HOURS = _num("DAYPLAN_MAX_SLOT_HOURS", 4, int)
DAYPLAN_STUCK_DAYS = _num("DAYPLAN_STUCK_DAYS", 3, int)      # a pursuit this old with nothing moving reads as stuck

# ---- the world's things ------------------------------------------------------------
FRESH_DAYS = _num("FRESH_DAYS", 7, int)                      # a finished thing reads as "new" this long
