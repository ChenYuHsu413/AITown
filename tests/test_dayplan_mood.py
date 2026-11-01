"""The morning micro-decision and mood (活化 phases B + D).

    python -m unittest tests.test_dayplan_mood -v
"""

from __future__ import annotations

import asyncio
import os
import unittest
import unittest.mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app import pacing
from backend.app.agents import dayplan as dayplan_mod
from backend.app.agents import mood as mood_mod
from backend.app.agents import texts
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

DAY = 2                                   # a Tuesday: the cafe is open, Jiji works
DAY0 = (DAY - 1) * DAY_MIN


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def slot(fr, to, place, doing="visit"):
    return {"from": fr, "to": to, "place": place, "doing": doing}


class Validate(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.jiji = self.world.agents["jiji"]

    def v(self, who, **raw):
        return dayplan_mod.validate({"intent": "A quieter day, I think.", **raw},
                                    self.world.agents[who], self.world, DAY)

    def test_a_good_slot_is_kept_and_mapped(self):
        p = self.v("jiji", slots=[slot(14, 16, "Firefly Park")])
        self.assertEqual(p["slots"], [{"from": 14 * 60, "to": 16 * 60, "location": "park", "action": "idle"}])
        self.assertEqual(p["dropped"], [])

    def test_bad_slots_are_dropped_one_by_one(self):
        p = self.v("jiji", slots=[slot(14, 16, "the moon"), slot(23, 24, "park"),
                                  slot(9, 11, "park", "work")])
        self.assertEqual(p["slots"], [])
        self.assertEqual(p["dropped"], ["no such place", "hours out of range", "no work there"])

    def test_sleep_is_not_the_plans_to_change(self):
        p = self.v("ange", slots=[slot(20, 22, "park")])         # Ange sleeps from 22:00 -- fine
        self.assertEqual(len(p["slots"]), 1)
        p = self.v("oula", slots=[slot(20, 22, "park")])         # Oula is asleep by 21:00
        self.assertEqual(p["dropped"], ["sleep time"])

    def test_someone_elses_home_is_refused_but_ones_own_is_fine(self):
        p = self.v("jiji", slots=[slot(14, 16, "Slopeview Flats"), slot(17, 18, "home")])
        self.assertEqual(p["dropped"], ["no such place"])
        self.assertEqual(p["slots"][0]["location"], "home_a")

    def test_a_meal_out_needs_the_money(self):
        self.jiji.state.money = 1
        p = self.v("jiji", slots=[slot(15, 16, "Hearth Bakery", "eat")])
        self.assertEqual(p["dropped"], ["can't afford it"])

    def test_nothing_special_changes_nothing(self):
        p = self.v("jiji", nothing_special=True, slots=[slot(14, 16, "park")], skip_work=1.0)
        self.assertEqual(p["slots"], [])
        self.assertFalse(p["skip_work"])

    def test_skipping_work_must_outweigh_conscientiousness(self):
        self.assertTrue(self.v("jiji", skip_work=0.6)["skip_work"])          # 0.6 > 0.5
        self.assertFalse(self.v("jiji", skip_work=0.4)["skip_work"])
        self.assertFalse(self.v("xixi", skip_work=0.99)["skip_work"])        # 1.0: never
        self.assertFalse(self.v("kuaizheng", skip_work=1.0)["skip_work"])    # no work to skip


class Priority(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.jiji = self.world.agents["jiji"]

    def test_the_plan_outranks_the_routine_and_never_touches_sleep(self):
        self.jiji.state.day_plan = {"day": DAY, "intent": "x", "skip_work": True,
                                    "slots": [{"from": 14 * 60, "to": 16 * 60, "location": "park", "action": "idle"}]}
        dow = (DAY - 1) % 7
        at = lambda h: dayplan_mod.entry_for(self.jiji, DAY0 + h * 60, self.jiji.routine.current(h * 60, dow))  # noqa: E731
        e, until = at(15)
        self.assertEqual((e.action, e.location, until), ("idle", "park", 16 * 60))
        e, until = at(10)                                        # skipped work -> home, resting
        self.assertEqual((e.action, e.location), ("rest", "home_a"))
        self.assertIsNone(at(23.5) if False else dayplan_mod.entry_for(
            self.jiji, DAY0 + 23 * 60 + 30, self.jiji.routine.current(23 * 60 + 30, dow)))
        self.assertIsNone(dayplan_mod.entry_for(self.jiji, DAY0 + DAY_MIN + 15 * 60,      # tomorrow: gone
                                                self.jiji.routine.current(15 * 60, dow + 1)))

    def test_decide_follows_the_plan(self):
        self.jiji.state.day_plan = {"day": DAY, "intent": "x", "skip_work": False,
                                    "slots": [{"from": 14 * 60, "to": 16 * 60, "location": "park", "action": "idle"}]}
        self.jiji.state.location, self.jiji.state.current_action = "cafe", "work"
        now = DAY0 + 14 * 60 + 5
        obs = self.world.observe(self.jiji, since_minute=now - 1, now=now)
        d = asyncio.run(self.engine.decisions.decide(self.jiji, self.world, obs, now))
        self.assertEqual((d.action, d.target_location), ("move", "park"))
        self.assertIn("today's own plan", d.reason)


class SkippedWork(unittest.TestCase):
    def test_an_owner_who_skips_shuts_the_shop_and_customers_find_it_closed(self):
        world, engine = make_world()
        engine.now = DAY0 + 60
        jiji, lengyue = world.agents["jiji"], world.agents["lengyue"]
        plan = dayplan_mod.validate({"intent": "I need a day.", "skip_work": 0.9}, jiji, world, DAY)
        engine.apply_day_plan(jiji, plan)
        self.assertEqual(world.locations["cafe"].closed_on, DAY)
        self.assertIn("skipped_work", [e.verb for e in engine.bus.events])
        self.assertIn(texts.T("skipped_work_self"), [m.text for m in jiji.memory.items])
        # Lengyue lunches at the cafe (12:30) -- today she is turned away, and remembers it.
        lengyue.state.location, lengyue.state.current_action = "office", "work"
        now = DAY0 + 12 * 60 + 40
        obs = world.observe(lengyue, since_minute=now - 1, now=now)
        d = asyncio.run(engine.decisions.decide(lengyue, world, obs, now))
        self.assertNotEqual(d.target_location, "cafe")
        self.assertTrue(any("cafe" in m.text and m.minute == now for m in lengyue.memory.items))

    def test_a_plan_that_arrives_after_its_day_is_dropped(self):
        world, engine = make_world()
        engine.now = DAY0 + DAY_MIN + 60
        jiji = world.agents["jiji"]
        engine.apply_day_plan(jiji, dayplan_mod.validate({"intent": "late", "skip_work": 0.9}, jiji, world, DAY))
        self.assertIsNone(jiji.state.day_plan)
        self.assertEqual(world.locations["cafe"].closed_on, 0)


class MorningCall(unittest.TestCase):
    def test_material_is_the_residents_own_and_labels_a_stuck_chapter(self):
        world, engine = make_world()
        xixi = world.agents["xixi"]
        day = xixi.chapter.started_on + pacing.DAYPLAN_STUCK_DAYS + 1
        mat = dayplan_mod.material(xixi, world, engine.decisions, day)
        msgs = builders.day_plan_prompt(xixi, mat)
        blob = msgs[1]["content"]
        if xixi.chapter.chapter_type == "pursuit":
            self.assertIn("it is stuck", blob)
        self.assertIn("Your usual day:", blob)
        self.assertIn("Task: day_plan.", msgs[0]["content"])

    def test_the_mock_floor_wakes_up_to_an_ordinary_day(self):
        world, engine = make_world()
        jiji = world.agents["jiji"]
        plan = asyncio.run(engine.decisions.plan_day(jiji, world, DAY, DAY0))
        self.assertEqual(plan["slots"], [])
        self.assertFalse(plan["skip_work"])
        self.assertTrue(plan["intent"])


class Mood(unittest.TestCase):
    def test_nudges_clamp_note_and_decay(self):
        world, _ = make_world()
        a = world.agents["jiji"]
        mood_mod.nudge(a, -0.3, "a bad day")
        self.assertEqual(a.state.mood_note, "a bad day")
        mood_mod.nudge(a, -5)
        self.assertEqual(a.state.mood_valence, -1.0)
        mood_mod.decay(a)
        self.assertAlmostEqual(a.state.mood_valence, -1.0 * pacing.MOOD_DAILY_DECAY)
        self.assertIn("really down", mood_mod.describe(a, world))
        for _ in range(8):
            mood_mod.decay(a)
        self.assertEqual(a.state.mood_note, "")

    def test_a_conversation_moves_each_side_by_its_own_feeling(self):
        world, engine = make_world()
        for i in ("jiji", "lengyue"):
            world.agents[i].state.location = "park"
        a, b = world.agents["jiji"], world.agents["lengyue"]
        engine.decisions.secrets.secrets.clear()
        from backend.app.llm.providers.base import LLMResult
        lines = [{"text": "So good to see you!", "sentiment": 1.0, "end": False},
                 {"text": "Hm. I have to go.", "sentiment": 0.0, "conflict_signal": 0.8, "end": True}]
        calls = []

        async def gen(**kw):
            calls.append(1)
            return LLMResult(text="", parsed=lines[len(calls) - 1], provider="fake", model="f")

        async def go():
            plan = await engine.decisions.start_conversation(a, b, world, DAY0 + 600)
            with unittest.mock.patch.object(engine.decisions.router, "generate", gen):
                res = await engine.decisions.generate_conversation(plan)
            engine.decisions.settle_conversation(plan, world, res)
        asyncio.run(go())
        self.assertGreater(a.state.mood_valence, 0)
        self.assertLess(b.state.mood_valence, 0)
        self.assertEqual(b.state.mood_note, texts.T("note_talk_bad", other="jiji"))


class SnapshotTolerance(unittest.TestCase):
    def test_v14_snapshot_loads_with_even_mood_and_no_plan(self):
        world, engine = make_world()
        jiji = world.agents["jiji"]
        jiji.state.mood_valence, jiji.state.mood_note = -0.4, "x"
        jiji.state.day_plan = {"day": 1, "intent": "x", "slots": [], "skip_work": False}
        world.locations["cafe"].closed_on = 1
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        self.assertEqual(world2.agents["jiji"].state.mood_valence, -0.4)
        self.assertEqual(world2.locations["cafe"].closed_on, 1)
        for adata in payload["agents"].values():                  # strip the v15 fields
            for k in ("mood_valence", "mood_note", "day_plan"):
                adata["state"].pop(k, None)
        for ldata in payload["locations"].values():
            ldata.pop("closed_on", None)
        payload["schema_version"] = 14
        world3, engine3 = make_world()
        snapshot_mod.restore(payload, engine3, world3, engine3.decisions)
        self.assertEqual(world3.agents["jiji"].state.mood_valence, 0.0)
        self.assertIsNone(world3.agents["jiji"].state.day_plan)
        self.assertEqual(world3.locations["cafe"].closed_on, 0)


if __name__ == "__main__":
    unittest.main()
