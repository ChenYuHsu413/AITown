"""Structural novelty: the same resident wanting the same doings again.

    python -m unittest tests.test_wish_novelty_struct -v

The embedding cannot hear "the same thing said differently" in Chinese, so a wish is
also compared by what it would DO -- its requirement (kind, target) set -- against the
resident's recent wishes (active, or ended within 14 days). The cases below are the
new era's own (run 7b977300): ㄐㄐ's market wishes and 阿總's office wishes, with the
real outcomes and days, plus the two refusals the real data has not produced yet.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import wishes as wishes_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.llm.providers.base import LLMResult
from backend.app.simulation.engine import SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations

R = wishes_mod.Requirement


def wish(wid, title, status, created, ended, reqs):
    return wishes_mod.Wish(id=wid, owner="", scale="minor", status=status, created_on=created,
                           ended_on=ended, title=title, statement="s",
                           requirements=[R(kind=k, target=t, threshold=1) for k, t in reqs])


def make_world():
    world = World(build_locations(), build_agents())
    SimulationEngine(world, DecisionEngine(build_router(live=False)))
    return world


# The real ledger rows (title, status, created_on, ended_on, (kind, target) pairs).
JIJI = [
    ("84d13d0d", "去市場找找客源", "completed", 3, 5, [("location_visits", "market")]),
    ("13e2a726", "把咖啡館帶進螢火公園", "completed", 6, 8, [("location_visits", "park"), ("talk_count", "aisi")]),
    ("f349acdc", "去和安哥說上話", "failed", 10, 18, [("talk_count", "ange")]),
    ("af5df0cc", "把咖啡館帶去市集", "completed", 13, 18, [("location_visits", "market"), ("talk_count", "xue")]),
]
AZONG_D8 = ("88b612fc", "去辦公室談一次真正的價", "completed", 8, 10,
            [("location_visits", "office"), ("talk_count", "kuaizheng"), ("trust", "kuaizheng")])


class RealCases(unittest.TestCase):
    def setUp(self):
        self.world = make_world()

    def holding(self, agent_id, rows):
        a = self.world.agents[agent_id]
        a.wishes = [wish(*r) for r in rows]
        return a

    def test_jiji_d19_is_an_identical_rerun_one_day_after_d13(self):
        # D19 「去市場看看人氣」 = D13's exact requirements, one day after D13 completed
        # (D18): a rerun, not a sequel -- refused under the 3-day rule. From D21 on the
        # same set would be allowed, and anything added makes it a sequel at once.
        a = self.holding("jiji", JIJI)
        d19 = [R(kind="location_visits", target="market", threshold=1), R(kind="talk_count", target="xue", threshold=2)]
        self.assertEqual(wishes_mod.struct_overlap(d19, a.wishes[3].requirements), 1.0)
        ok, why = wishes_mod.structural_novelty_ok(a, d19, 19)
        self.assertFalse(ok)
        self.assertIn("completed only 1 day(s) ago", why)
        self.assertTrue(wishes_mod.structural_novelty_ok(a, d19, 21)[0])
        more = d19 + [R(kind="talk_count", target="ange", threshold=1)]
        self.assertTrue(wishes_mod.structural_novelty_ok(a, more, 19)[0])
        titles = [w["title"] for w in wishes_mod.generation_material(a, self.world, 19)["recent_completed"]]
        self.assertIn("把咖啡館帶去市集", titles)     # the prompt names it as a possible sequel

    def test_jiji_d13_after_the_completed_d3_passes_at_half_overlap(self):
        a = self.holding("jiji", JIJI[:2])
        d13 = [R(kind="location_visits", target="market", threshold=1), R(kind="talk_count", target="xue", threshold=1)]
        self.assertEqual(wishes_mod.struct_overlap(d13, a.wishes[0].requirements), 0.5)
        self.assertTrue(wishes_mod.structural_novelty_ok(a, d13, 13)[0])

    def test_azong_d8_to_d17_is_a_lawful_deepening(self):
        # Unaffected by the rerun rule: D17's set is a subset of D8's, not the same set,
        # and it came 7 days after the completion.
        # 「再進一次辦公室」(D17): office + kuaizheng, after the completed D8 (ended D10).
        a = self.holding("azong", [AZONG_D8])
        d17 = [R(kind="location_visits", target="office", threshold=1), R(kind="talk_count", target="kuaizheng", threshold=1)]
        self.assertEqual(wishes_mod.struct_overlap(d17, a.wishes[0].requirements), 1.0)
        self.assertEqual(wishes_mod.structural_novelty_ok(a, d17, 17), (True, ""))


class TheRules(unittest.TestCase):
    def setUp(self):
        self.world = make_world()

    def test_1_an_active_wish_cannot_be_held_twice(self):
        a = self.world.agents["jiji"]
        a.wishes = [wish("w1", "去市場看看人氣", "active", 19, 0, [("location_visits", "market"), ("talk_count", "xue")])]
        ok, why = wishes_mod.structural_novelty_ok(
            a, [R(kind="location_visits", target="market", threshold=2)], 20)
        self.assertFalse(ok)
        self.assertIn("already pursuing", why)

    def test_2_a_failed_wish_needs_its_cooldown(self):
        # ㄐㄐ's 「去和安哥說上話」 failed on D18: talking to 安哥 again on D20 is too soon,
        # on D25 (7 days on) it is trying again.
        a = self.world.agents["jiji"]
        a.wishes = [wish(*JIJI[2])]
        again = [R(kind="talk_count", target="ange", threshold=3)]
        ok, why = wishes_mod.structural_novelty_ok(a, again, 20)
        self.assertFalse(ok)
        self.assertIn("let go only 2 day(s) ago", why)
        self.assertTrue(wishes_mod.structural_novelty_ok(a, again, 25)[0])

    def test_3_an_old_or_unrelated_wish_does_not_count(self):
        a = self.world.agents["jiji"]
        a.wishes = [wish("w0", "old", "completed", 1, 2, [("location_visits", "market")])]
        self.assertTrue(wishes_mod.structural_novelty_ok(
            a, [R(kind="location_visits", target="market", threshold=1)], 20)[0])   # ended 18 days ago
        a.wishes = [wish("w1", "x", "active", 19, 0, [("location_visits", "park")])]
        self.assertTrue(wishes_mod.structural_novelty_ok(
            a, [R(kind="location_visits", target="market", threshold=1)], 20)[0])


class InTheGenerationLoop(unittest.TestCase):
    def test_a_rejection_is_counted_and_fed_back(self):
        world = make_world()
        engine = SimulationEngine(world, DecisionEngine(build_router(live=False)))
        d = engine.decisions
        a = world.agents["kuaizheng"]
        clean = {"scale": "minor", "title": "t", "statement": "我想去市場走走。", "motivation": "m",
                 "requirements": [R(kind="location_visits", target="market", threshold=1)],
                 "provenance": [], "expires_on": 9}
        a.wishes = [wish("w1", "去市場", "active", 3, 0, [("location_visits", "market")])]
        prompts = []
        real = builders.wish_generation_prompt

        def spy(agent, material, rejection=""):
            prompts.append(rejection)
            return real(agent, material, rejection)
        before = builders.GATE_REJECTS.get("wish_novelty_struct", 0)
        with mock.patch.object(wishes_mod, "validate_generation", return_value=(clean, [])), \
                mock.patch.object(wishes_mod, "deviation_ok", return_value=(True, "")), \
                mock.patch.object(builders, "wish_generation_prompt", side_effect=spy), \
                mock.patch.object(d.router, "generate", mock.AsyncMock(return_value=LLMResult(
                    text="{}", parsed={"title": "t", "statement": "我想去市場走走。"},
                    provider="mock", model="mock-1"))):
            rows: list = []
            got, outcome = asyncio.run(d.grow_wish(a, world, 5, "", attempts=rows))
        self.assertIsNone(got)
        self.assertEqual(outcome, "gates")
        self.assertEqual(builders.GATE_REJECTS["wish_novelty_struct"] - before, 1 + wishes_mod.GENERATION_RETRIES)
        self.assertTrue(all(r.get("gate") == "wish_novelty_struct" for r in rows if r["outcome"] == "gate_rejected"))
        self.assertIn("already pursuing", prompts[1])      # the retry is told why


class ThePrompt(unittest.TestCase):
    def test_a_recent_completion_is_named_as_a_possible_sequel(self):
        world = make_world()
        a = world.agents["azong"]
        a.wishes = [wish(*AZONG_D8)]
        with mock.patch.dict(os.environ, {"AI_TOWN_LANG": "zh-TW"}):
            user = builders.wish_generation_prompt(a, wishes_mod.generation_material(a, world, 17))[1]["content"]
        self.assertIn("你最近完成了〈去辦公室談一次真正的價〉(Day 10)", user)


if __name__ == "__main__":
    unittest.main()
