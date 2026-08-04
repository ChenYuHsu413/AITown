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
    """A weekly timetable: one table for weekdays (Mon-Fri), an optional second
    for weekends (Sat-Sun). ``day_of_week`` is 0=Mon .. 6=Sun. When no weekend
    table is given the same table runs all seven days (backward compatible)."""

    def __init__(
        self,
        weekday_entries: list[RoutineEntry],
        weekend_entries: list[RoutineEntry] | None = None,
    ):
        self.entries = sorted(weekday_entries, key=lambda e: e.start)   # weekday table (also `home` source)
        self._weekday_starts = [e.start for e in self.entries]
        if weekend_entries is None:
            self._weekend = self.entries
            self._weekend_starts = self._weekday_starts
        else:
            self._weekend = sorted(weekend_entries, key=lambda e: e.start)
            self._weekend_starts = [e.start for e in self._weekend]

    def _table(self, dow: int) -> tuple[list[RoutineEntry], list[int]]:
        if dow >= 5:                                          # Sat / Sun
            return self._weekend, self._weekend_starts
        return self.entries, self._weekday_starts

    def current(self, minute_of_day: int, dow: int = 0) -> RoutineEntry:
        """The entry in effect at this time on day-of-week ``dow`` (wraps to the
        last entry overnight)."""
        entries, starts = self._table(dow)
        i = bisect_right(starts, minute_of_day) - 1
        return entries[i]  # i == -1 wraps to the last (sleep) entry

    def next_boundary(self, minute_of_day: int, dow: int = 0) -> int:
        """Minutes-of-day of the next schedule change. When today has no later
        entry it wraps to tomorrow's FIRST entry -- and tomorrow may run a
        different table (Fri->Sat, Sun->Mon), so we cross tables here."""
        _, starts = self._table(dow)
        i = bisect_right(starts, minute_of_day)
        if i < len(starts):
            return starts[i]
        _, tomorrow_starts = self._table((dow + 1) % 7)       # cross to tomorrow's table
        return tomorrow_starts[0] + 24 * 60
