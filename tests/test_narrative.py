"""The narrative data layer: event tiers, daily digests, relationship deltas.

    python -m unittest tests.test_narrative -v

All rule-layer: a digest is written on the daily path, and the daily path never
gains an LLM call, so these run on the mock without counting a single extra call.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import texts
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

START = 6 * 60


def make_engine():
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(build_router(live=False)))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(START)
    return world, engine


def zh():
    return mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"})


class Tiers(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_engine()
        self.a, self.b = self.world.agents["lengyue"], self.world.agents["ange"]

    def last(self):
        return self.engine.bus.events[-1]

    def test_the_table(self):
        self.engine._publish("system", "wish_born", actor=self.a)
        self.assertEqual(self.last().tier, "major")
        self.engine._publish("action", "confide", actor=self.a, target=self.b)
        self.assertEqual(self.last().tier, "notable")
        self.engine._publish("action", "eat", actor=self.a)
        self.assertEqual(self.last().tier, "ambient")

    def test_only_a_pairs_first_talk_is_notable(self):
        self.engine._publish("action", "talk_start", actor=self.a, target=self.b)
        self.assertEqual(self.last().tier, "notable")
        self.engine._publish("action", "talk_start", actor=self.b, target=self.a)   # same pair, either way
        self.assertEqual(self.last().tier, "ambient")

    def test_seeded_friends_are_not_meeting_for_the_first_time(self):
        self.engine._publish("action", "talk_start", actor=self.a, target=self.world.agents["oula"])
        self.assertEqual(self.last().tier, "ambient")

    def test_the_chronicle_and_feed_carry_the_tier(self):
        self.engine._publish("action", "confide", actor=self.a, target=self.b, detail="x")
        self.assertEqual(self.engine.chronicle[-1]["tier"], "notable")


class Digest(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_engine()
        self.a, self.b = self.world.agents["lengyue"], self.world.agents["ange"]

    def test_a_day_with_beats_tells_them_majors_first(self):
        with zh():
            self.engine._publish("action", "confide", actor=self.a, target=self.b, minute=600)
            self.engine._publish("system", "wish_born", actor=self.b, minute=700)
            d = self.engine.narrative.settle(1)
        self.assertEqual(d["text"], "{agent:ange}心裡悄悄有了一個新的願望;{agent:lengyue}向{agent:ange}吐露了心事。")
        self.assertEqual([r["verb"] for r in d["refs"]], ["confide", "wish_born"])

    def test_a_quiet_day_still_gets_a_plain_line(self):
        with zh():
            d = self.engine.narrative.settle(1)
        self.assertTrue(texts.has_cjk(d["text"]))
        self.assertEqual(d["refs"], [])

    def test_a_wish_is_never_named(self):
        with zh():
            self.engine._publish("system", "wish_born", actor=self.a, text="minor",
                                 detail="I want to get to know Ange")
            d = self.engine.narrative.settle(1)
        self.assertNotIn("Ange", d["text"])
        self.assertNotIn("know", d["text"])

    def test_a_beat_at_midnight_belongs_to_the_next_day(self):
        self.engine._publish("action", "confide", actor=self.a, target=self.b, minute=DAY_MIN)
        self.assertEqual(self.engine.narrative.settle(1)["refs"], [])
        self.assertEqual(len(self.engine.narrative.settle(2)["refs"]), 1)

    def test_a_closing_chapter_is_told_once(self):
        with zh():
            self.engine._publish("system", "chapter_closed", actor=self.a, minute=600)
            self.engine._publish("system", "chapter_started", actor=self.a, minute=600)
            d = self.engine.narrative.settle(1)
        self.assertEqual(d["text"], "{agent:lengyue}的人生翻過了一頁。")

    def test_a_setback_surfaces_as_a_content_free_beat(self):
        self.a.memory.add(MemoryItem(minute=900, importance=4, kind="reflection",
                                     text=texts.T("wish_frustration"), tags=["goal:w1"]))
        d = self.engine.narrative.settle(1)
        self.assertEqual([r["verb"] for r in d["refs"]], ["wish_frustrated"])
        ev = next(e for e in self.engine.bus.events if e.verb == "wish_frustrated")
        self.assertEqual((ev.minute, ev.tier, ev.text), (900, "notable", ""))

    def test_the_daily_settlement_writes_it(self):
        got = []
        self.engine.narrative.on_digest = got.append

        async def run():
            await self.engine.run_until(START + DAY_MIN)
            await self.engine.drain(START + DAY_MIN)
        asyncio.run(run())
        self.assertEqual([d["day"] for d in got], [1])
        self.assertTrue(got[0]["text"])


class RelationshipDeltas(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_engine()

    def test_only_what_moved_is_recorded(self):
        lengyue = self.world.agents["lengyue"]
        lengyue.rel("ange").friendship += 4
        lengyue.rel("ange").conflict += 2
        self.engine.narrative.settle(1)
        rows = self.engine.narrative.recent_deltas
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["a"], r["b"], r["day"]), ("lengyue", "ange", 1))
        self.assertEqual((r["friendship"], r["d_friendship"], r["d_trust"], r["d_conflict"]),
                         (34.0, 4.0, 0.0, 2.0))
        self.engine.narrative.settle(2)
        self.assertEqual(self.engine.narrative.recent_deltas, [], "nothing moved since")

    def test_seeded_friendships_are_not_a_day_one_jump(self):
        self.engine.narrative.settle(1)
        self.assertFalse(any(r["a"] == "lengyue" and r["b"] == "oula"
                             for r in self.engine.narrative.recent_deltas))


class Snapshot(unittest.TestCase):
    def test_the_days_story_survives_a_restart(self):
        world, engine = make_engine()
        a, b = world.agents["lengyue"], world.agents["ange"]
        engine._publish("action", "talk_start", actor=a, target=b, minute=500)
        engine._publish("action", "confide", actor=a, target=b, minute=510)
        a.rel("ange").trust += 5
        payload = snapshot_mod.capture(engine, world, engine.decisions)

        world2, engine2 = make_engine()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        engine2._publish("action", "talk_start", actor=b, target=a, minute=520)
        self.assertEqual(engine2.bus.events[-1].tier, "ambient", "they already talked")
        d = engine2.narrative.settle(1)
        self.assertEqual([r["verb"] for r in d["refs"]], ["talk_start", "confide"])
        # The baseline rode in the snapshot, so a move made before the restart still counts.
        moved = [r for r in engine2.narrative.recent_deltas if (r["a"], r["b"]) == ("lengyue", "ange")]
        self.assertEqual([r["d_trust"] for r in moved], [5.0])

    def test_an_old_snapshot_measures_from_where_it_stands(self):
        world, engine = make_engine()
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        payload.pop("narrative")
        payload["agents"]["lengyue"]["relationships"]["ange"] = {"friendship": 70.0, "trust": 70.0}
        world2, engine2 = make_engine()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        engine2.narrative.settle(1)
        self.assertEqual(engine2.narrative.recent_deltas, [])


if __name__ == "__main__":
    unittest.main()
