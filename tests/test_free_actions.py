"""Free actions and public objects (活化 phase C).

    python -m unittest tests.test_free_actions -v
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
from backend.app.agents import actions as actions_mod
from backend.app.agents import dayplan as dayplan_mod
from backend.app.agents import texts
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

DAY = 2
DAY0 = (DAY - 1) * DAY_MIN


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def act(hour=15, place="Firefly Park", what="sketch the pond", leaves="a chalk sketch by the pond", cost=0):
    return {"hour": hour, "place": place, "what": what, "leaves": leaves, "cost": cost}


def plan_for(agent, world, *acts, **extra):
    return dayplan_mod.validate({"intent": "A small thing today.", "actions": list(acts), **extra},
                                agent, world, DAY)


def decide(engine, world, agent, now):
    obs = world.observe(agent, since_minute=now - 1, now=now)
    return asyncio.run(engine.decisions.decide(agent, world, obs, now))


class PlanSide(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.kz = self.world.agents["kuaizheng"]          # retired: free afternoons

    def test_a_good_action_is_kept_and_holds_its_hour(self):
        p = plan_for(self.kz, self.world, act())
        self.assertEqual(len(p["actions"]), 1)
        self.assertEqual(p["actions"][0]["location"], "park")
        self.assertIn({"from": 900, "to": 960, "location": "park", "action": "free", "act": 0}, p["slots"])

    def test_the_contract_refuses_what_is_not_ones_own(self):
        p = plan_for(self.kz, self.world,
                     act(what="leave a note for Lengyue"),               # names someone
                     act(hour=16, place="Rainlisten House"))             # someone else's home
        self.assertEqual(p["actions"], [])
        self.assertIn("action: involves someone else", p["dropped"])

    def test_limits(self):
        p = plan_for(self.kz, self.world, act(cost=pacing.FREE_ACTION_MAX_COST + 1), act(hour=22),
                     act(hour=10))
        self.assertEqual(p["dropped"][:2], ["action: too costly", "action: sleep time"])
        self.assertEqual(len(p["actions"]), 0)                    # the cap is 2: the third is never read
        p = plan_for(self.kz, self.world, act(hour=10), act(hour=11), act(hour=12))
        self.assertEqual(len(p["actions"]), pacing.FREE_ACTIONS_PER_DAY)

    def test_nothing_may_be_left_in_someone_elses_shop(self):
        p = plan_for(self.kz, self.world, act(place="Tide Cafe", what="read a whole book", leaves="a bookmark"))
        self.assertEqual(p["actions"][0]["leaves"], "")


class Execution(unittest.TestCase):
    def setUp(self):
        self.world, self.engine = make_world()
        self.kz = self.world.agents["kuaizheng"]
        self.kz.state.day_plan = plan_for(self.kz, self.world, act(cost=5))
        self.kz.state.location, self.kz.state.current_action = "park", "rest"
        self.money0 = self.kz.state.money

    def test_it_lands_with_a_memory_a_deduction_and_a_public_object(self):
        d = decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(d.action, "idle")
        self.assertEqual(self.kz.state.money, self.money0 - 5)
        self.assertEqual(self.kz.state.day_plan["actions"][0]["state"], "done")
        obj = self.world.objects[-1]
        self.assertEqual((obj["location"], obj["creator"], obj["description"]),
                         ("park", "kuaizheng", "a chalk sketch by the pond"))
        mem = [m.text for m in self.kz.memory.items]
        self.assertIn(texts.T("action_self", loc="park", what="sketch the pond"), mem)
        verbs = [b["verb"] for b in self.engine.decisions.outbox]
        self.assertEqual(verbs, ["made_object", "__object__"])
        # once landed, it does not land again
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 30)
        self.assertEqual(len(self.world.objects), 1)

    def test_the_engine_publishes_it_and_writes_the_ledger(self):
        written = []
        self.engine.on_public_object = written.append
        self.engine.scheduler = type(self.engine.scheduler)()     # only Kuaizheng is due
        self.engine._last_day = DAY0 // DAY_MIN                    # the day is already settled
        self.engine.scheduler.schedule("kuaizheng", DAY0 + 15 * 60 + 2)
        asyncio.run(self.engine.tick())
        self.assertEqual([o["description"] for o in written], ["a chalk sketch by the pond"])
        ev = [e for e in self.engine.bus.events if e.verb == "made_object"]
        self.assertEqual((ev[-1].actor, ev[-1].location, ev[-1].text),
                         ("kuaizheng", "park", "a chalk sketch by the pond"))
        self.assertEqual(ev[-1].tier, "notable")
        self.assertEqual(self.engine.action_stats["made_object"], 1)

    def test_no_money_means_a_failure_beat_and_memory(self):
        self.kz.state.money = 1
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(self.world.objects, [])
        beat = self.engine.decisions.outbox[-1]
        self.assertEqual((beat["verb"], beat["detail"]), ("action_failed", texts.T("fail_money")))
        self.assertIn(texts.T("action_failed", what="sketch the pond", loc="park", reason=texts.T("fail_money")),
                      [m.text for m in self.kz.memory.items])

    def test_rain_keeps_it_off_the_park(self):
        self.world.set_effect("rain", "", DAY0 + DAY_MIN)
        decide(self.engine, self.world, self.kz, DAY0 + 15 * 60 + 2)
        self.assertEqual(self.engine.decisions.outbox[-1]["detail"], texts.T("fail_rain"))

    def test_an_hour_that_slips_by_is_a_failure(self):
        self.kz.state.location = "home_b"
        decide(self.engine, self.world, self.kz, DAY0 + 16 * 60 + 5)
        self.assertEqual(self.engine.decisions.outbox[-1]["detail"], texts.T("fail_time"))
        self.assertEqual(self.kz.state.day_plan["actions"][0]["state"], "failed")


class Objects(unittest.TestCase):
    def test_passers_by_take_it_in_once_and_scenes_show_it(self):
        world, engine = make_world()
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "a chalk sketch by the pond", "born_minute": DAY0})
        jiji = world.agents["jiji"]
        jiji.state.location = "park"
        with unittest.mock.patch.object(pacing, "OBJECT_NOTICE_P", 1.0):
            actions_mod.notice_objects(jiji, world, DAY0 + 60)
            actions_mod.notice_objects(jiji, world, DAY0 + 120)
        seen = [m for m in jiji.memory.items if "chalk sketch" in m.text]
        self.assertEqual(len(seen), 1)
        lines = actions_mod.scene_lines(world, "park", DAY0 + 60)
        self.assertEqual(lines, [texts.T("aged_today", name="a chalk sketch by the pond")])
        self.assertEqual(actions_mod.scene_lines(world, "park", DAY0 + 30 * DAY_MIN),
                         ["a chalk sketch by the pond"])
        lengyue = world.agents["lengyue"]
        lengyue.state.location = "park"
        plan = asyncio.run(engine.decisions.start_conversation(jiji, lengyue, world, DAY0 + 600))
        self.assertIn("Also here: " + texts.T("aged_today", name="a chalk sketch by the pond"),
                      plan.preview("jiji"))

    def test_wish_and_closure_material_see_actions_objects_and_promises(self):
        from backend.app.agents import chapters as chapters_mod
        from backend.app.agents.core import MemoryItem
        from backend.app.llm.prompts import builders
        world, engine = make_world()
        aisi = world.agents["aisi"]
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "a chalk sketch by the pond", "born_minute": DAY0})
        aisi.memory.add(MemoryItem(minute=DAY0 + 60, importance=5, kind="reflection", tags=["action"],
                                   text=texts.T("action_self", loc="park", what="sketch the pond")))
        aisi.memory.add(MemoryItem(minute=DAY0 + 90, importance=5, kind="conversation", tags=["promise"],
                                   text=texts.T("promise_kept", other="jiji", loc="cafe")))
        from backend.app.agents import wishes as wishes_mod
        mat = wishes_mod.generation_material(aisi, world, DAY)
        self.assertEqual(len(mat["town_objects"]), 1)
        self.assertEqual(len(mat["my_actions"]), 1)
        blob = builders.wish_generation_prompt(aisi, mat)[1]["content"]
        self.assertIn("Things they did lately", blob)
        self.assertIn("a chalk sketch by the pond", blob)
        cm = chapters_mod.closure_material(aisi, world, aisi.chapter, DAY0 + 600)
        self.assertEqual(len(cm["actions"]), 1)
        self.assertEqual(len(cm["promises"]), 1)

    def test_v15_snapshot_loads_with_no_objects(self):
        world, engine = make_world()
        world.objects.append({"id": "o1", "location": "park", "creator": "kuaizheng",
                              "description": "x", "born_minute": 1})
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        w2, e2 = make_world()
        snapshot_mod.restore(payload, e2, w2, e2.decisions)
        self.assertEqual(len(w2.objects), 1)
        payload.pop("objects")
        payload["schema_version"] = 15
        w3, e3 = make_world()
        snapshot_mod.restore(payload, e3, w3, e3.decisions)
        self.assertEqual(w3.objects, [])


if __name__ == "__main__":
    unittest.main()
