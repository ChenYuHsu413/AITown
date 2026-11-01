"""Range-asserted smoke for the activation phases: a zh mock town for 4 days.

    python -m unittest tests.test_activation_smoke -v

Not a behaviour test of any one rule (those live next door) -- this pins the
whole loop's shape: conversations are multi-line and bounded, nobody is left
locked in a conversation, every resident gets one morning call per day, the
mock floor's morning is an ordinary day, and nothing escapes in English.
"""

from __future__ import annotations

import asyncio
import collections
import os
import unittest
import unittest.mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app import pacing
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

DAYS = 4


class ActivationSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with unittest.mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW", "AI_TOWN_MEETUPS": "1"}):
            router = build_router(live=False)
            world = World(build_locations(), build_agents())
            engine = SimulationEngine(world, DecisionEngine(router))
            seed_secrets(engine.decisions.secrets)

            async def run():
                engine.bootstrap(6 * 60)
                await engine.run_until(DAYS * DAY_MIN)
                await engine.drain(DAYS * DAY_MIN)
            asyncio.run(run())
        cls.world, cls.engine, cls.router = world, engine, router
        cls.events = engine.bus.events

    def test_conversations_are_multi_line_and_bounded(self):
        starts = [e for e in self.events if e.verb == "talk_start"]
        self.assertGreaterEqual(len(starts), 20 * (DAYS - 1) // 2)        # a lively town, not a silent one
        by_pair = collections.defaultdict(list)
        for e in starts:
            by_pair[frozenset((e.actor, e.target))].append(e.minute)
        lines = collections.Counter()
        for e in self.events:
            if e.verb == "say":
                ms = [m for m in by_pair[frozenset((e.actor, e.target))] if m <= e.minute]
                lines[(frozenset((e.actor, e.target)), ms[-1])] += 1
        self.assertTrue(lines)
        self.assertGreaterEqual(min(lines.values()), 2)
        self.assertLessEqual(max(lines.values()), pacing.DIALOGUE_MAX_TURNS)

    def test_nobody_is_left_locked_in_a_conversation(self):
        self.assertEqual(self.engine._in_dialogue, {})

    def test_one_morning_call_per_resident_per_day(self):
        calls = collections.Counter(c.task_type for c in self.router.usage.calls)
        self.assertEqual(calls["day_plan"], len(self.world.agents) * (DAYS - 1))   # day 1 starts mid-morning
        self.assertEqual(self.engine.plan_stats["plans"], len(self.world.agents) * (DAYS - 1))
        self.assertEqual(self.engine.plan_stats["changed"], 0)                 # the floor's morning is ordinary
        self.assertEqual(self.engine.plan_stats["skipped_work"], 0)

    def test_turn_calls_dominate_and_the_tense_tier_is_the_exception(self):
        calls = collections.Counter(c.task_type for c in self.router.usage.calls)
        self.assertGreater(calls["dialogue_turn"], calls["dialogue_turn_tense"])
        self.assertEqual(calls["dialogue"], 0)                                 # the one-shot path is gone

    def test_every_spoken_line_is_chinese(self):
        from backend.app.agents.decision import _zh_reject_reason
        says = [e.text for e in self.events if e.verb == "say"]
        bad = [t for t in says if _zh_reject_reason(t) is not None]
        self.assertEqual(bad, [])

    def test_mood_stays_bounded_and_moves(self):
        vals = [a.state.mood_valence for a in self.world.agents.values()]
        self.assertTrue(all(-1.0 <= v <= 1.0 for v in vals))
        self.assertTrue(any(abs(v) > 0.01 for v in vals))


if __name__ == "__main__":
    unittest.main()
