"""Routine Engine (Level 0 -- pure rules, zero LLM).

A routine is a daily timetable. "It's 07:30, should Alice wake up?" is
never a question for an AI; the schedule just executes. The LLM is only
consulted when something *deviates* from the routine (see decision.py).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


def hm(h: int, m: int = 0) -> int:
    """Hours:minutes -> minutes since midnight."""
    return h * 60 + m


@dataclass
class RoutineEntry:
    start: int            # minutes since midnight
    action: str           # move / work / rest / eat / sleep / open_shop ...
    location: str


class Routine:
    def __init__(self, entries: list[RoutineEntry]):
        self.entries = sorted(entries, key=lambda e: e.start)
        self._starts = [e.start for e in self.entries]

    def current(self, minute_of_day: int) -> RoutineEntry:
        """The entry in effect at this time (wraps to last entry overnight)."""
        i = bisect_right(self._starts, minute_of_day) - 1
        return self.entries[i]  # i == -1 wraps to the last (sleep) entry

    def next_boundary(self, minute_of_day: int) -> int:
        """Minutes-of-day of the next schedule change (may be tomorrow)."""
        i = bisect_right(self._starts, minute_of_day)
        if i < len(self._starts):
            return self._starts[i]
        return self._starts[0] + 24 * 60  # wraps to tomorrow
