"""Event-driven snapshots: a rare beat is persisted the moment it lands.

    python -m unittest tests.test_event_snapshot -v

The fixed cadence (daily settlement, the server's wall clock) leaves gaps, and a
restart rolls back to the last write. Wish generation is non-deterministic, so a
rolled-back wish is not regrown -- it is simply erased. These tests pin the
trigger points, the same-minute debounce, and that a failed write costs the beat
nothing.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import chapters as chapters_mod
from backend.app.agents import wishes as wishes_mod
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

START = 6 * 60


class Writes:
    """Stands in for the host's ``_take_snapshot``: captures the same payload the
    server would and queues it. ``fail=True`` is a queue that is down."""

    def __init__(self, engine, world, fail: bool = False):
        self.engine, self.world, self.fail = engine, world, fail
        self.queue: list[dict] = []

    def __call__(self) -> None:
        if self.fail:
            raise RuntimeError("snapshot queue is down")
        self.queue.append(snapshot_mod.capture(self.engine, self.world, self.engine.decisions))

    def __len__(self) -> int:
        return len(self.queue)


def make_engine(fail: bool = False):
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(build_router(live=False)))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(START)
    writes = Writes(engine, world, fail)
    engine.on_snapshot = writes
    return world, engine, writes


def give_material(agent, day=10):
    """A life with something in it -- enough for a wish to grow out of."""
    agent.memory.add(MemoryItem(minute=(day - 6) * DAY_MIN, importance=9, kind="biography",
                                text="I finished the thing I had been carrying, and it is done.",
                                source_chapter_id="ch-old", tags=["old"]))
    for i in range(4):
        agent.memory.add(MemoryItem(minute=(day - 4 + i) * DAY_MIN, importance=6, kind="reflection",
                                    text=f"Something worth noticing happened on day {day - 4 + i}."))


def seed_minor(engine, world, agent_id, day, expires_on):
    agent = world.agents[agent_id]
    clean, problems = wishes_mod.validate_seed(
        {"scale": "minor", "title": "T", "statement": "A statement long enough to pass.",
         "narrative": "I am quietly working on something of my own.",
         "expires_on": expires_on,
         "requirements": [{"kind": "location_visits", "target": "cafe", "threshold": 2}]},
        agent, world, day)
    assert not problems, problems
    return engine.seed_wish(agent, clean, day)


def grow_a_wish(engine, world, agent_id="kuaizheng", day=10):
    """The real 2b path: a spent interlude, generation, gates, a live wish."""
    agent = world.agents[agent_id]
    give_material(agent, day)
    agent.chapter = chapters_mod.make_interlude("restless", day - 2, day)

    async def run():
        engine._last_day = day - 1
        engine._advance_chapters()
        await engine.drain(engine.now + 5)

    with mock.patch.object(wishes_mod, "GENERATION_BASE_P", 1.0):
        asyncio.run(run())
    return agent


class WishBorn(unittest.TestCase):
    """A self-grown wish is the whole point -- a rollback would not regrow it."""

    def test_a_born_wish_queues_exactly_one_snapshot_carrying_it(self):
        world, engine, writes = make_engine()
        agent = grow_a_wish(engine, world)
        self.assertEqual(len(agent.wishes), 1, engine.wish_stats)

        self.assertEqual(len(writes), 1, "the birth must buy exactly one write")
        self.assertEqual(engine.snapshot_stats["event"], 1)
        self.assertEqual(engine.snapshot_stats["periodic"], 0)
        # ...and the payload really carries the wish, so a resume rebuilds it
        stored = writes.queue[-1]["agents"][agent.id]["wishes"]
        self.assertEqual([w["id"] for w in stored], [agent.wishes[0].id])

    def test_a_pursuit_chapter_rides_the_same_write(self):
        """A major wish opens its pursuit chapter on the minute it is born."""
        world, engine, writes = make_engine()
        agent = grow_a_wish(engine, world)
        if agent.wishes[0].scale != "major":
            self.skipTest("the mock grew a minor wish this run; no pursuit chapter")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes.queue[-1]["agents"][agent.id]["chapter"]["id"],
                         agent.wishes[0].chapter_id)


class Debounce(unittest.TestCase):
    """Several beats on one sim minute cost one write, not several."""

    def test_two_wishes_ending_on_the_same_minute_write_once(self):
        world, engine, writes = make_engine()
        day = engine.now // DAY_MIN + 1
        for aid in ("kuaizheng", "aisi"):
            seed_minor(engine, world, aid, day, expires_on=day + 1)
        self.assertEqual(len(writes), 0, "seeding is God Mode -- it does not trigger")

        engine._last_day = day + 1            # settling day+2: both deadlines have passed
        engine._settle_wishes()

        closed = [e for e in engine.bus.events if e.verb == "wish_closed"]
        self.assertEqual(len(closed), 2, "both wishes really ended")
        self.assertEqual(len(writes), 1, "...on one minute, so one write")
        self.assertEqual(engine.snapshot_stats["event"], 1)
        self.assertEqual(engine.snapshot_stats["debounced"], 1)

    def test_a_closing_chapter_and_its_linked_wish_write_once(self):
        """The Azong case: wish_closed and chapter_closed land on the same minute."""
        world, engine, writes = make_engine()
        agent = world.agents["kuaizheng"]
        day = engine.now // DAY_MIN + 1
        clean, problems = wishes_mod.validate_seed(
            {"scale": "major", "title": "T", "statement": "A statement long enough to pass.",
             "narrative": "Something I mean to see through.",
             "requirements": [{"kind": "location_visits", "target": "cafe", "threshold": 2}]},
            agent, world, day)
        self.assertFalse(problems, problems)
        wish = engine.seed_wish(agent, clean, day)
        self.assertEqual(len(writes), 0)
        self.assertEqual(chapters_mod.chapter_type(agent), "pursuit")

        async def close():
            engine._close_wish(agent, wish, "completed", "it was done")
            await engine.drain(engine.now + 5)
        asyncio.run(close())

        verbs = sorted(e.verb for e in engine.bus.events
                       if e.verb in ("wish_closed", "chapter_closed"))
        self.assertEqual(verbs, ["chapter_closed", "wish_closed"])
        self.assertEqual(len(writes), 1, "one page turned -> one write")
        self.assertEqual(writes.queue[-1]["agents"][agent.id]["wishes"][0]["status"], "completed")


class MinorEndingStandsAlone(unittest.TestCase):
    """A minor wish ends with no chapter beat, so nothing else would persist it."""

    def test_a_minor_ending_triggers_on_its_own(self):
        world, engine, writes = make_engine()
        day = engine.now // DAY_MIN + 1
        wish = seed_minor(engine, world, "kuaizheng", day, expires_on=day + 1)
        engine._last_day = day + 1
        engine._settle_wishes()
        self.assertEqual(wish.status, "failed")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes.queue[-1]["agents"]["kuaizheng"]["wishes"][0]["status"], "failed")


class WriteFailure(unittest.TestCase):
    """The beat already happened. A failed write is logged and counted, and costs
    the simulation nothing."""

    def test_the_wish_still_lands_and_the_failure_is_counted(self):
        world, engine, writes = make_engine(fail=True)
        agent = grow_a_wish(engine, world)

        self.assertEqual(len(agent.wishes), 1, "the wish landed regardless")
        self.assertEqual(len(writes), 0)
        self.assertEqual(engine.snapshot_stats["event_snapshot_failures"], 1)
        self.assertEqual(engine.snapshot_stats["event"], 0)

        # ...and the town keeps running: three more days through the real funnel,
        # with every further snapshot failing too
        before = len(engine.bus.events)
        end = engine.now + 3 * DAY_MIN

        async def keep_going():
            await engine.run_until(end)
            await engine.drain(end)
        asyncio.run(keep_going())
        self.assertGreater(len(engine.bus.events), before, "the town went on living")
        self.assertTrue(engine.decisions.traces)
        self.assertEqual(len(writes), 0)

    def test_a_failure_does_not_retry_within_the_same_minute(self):
        world, engine, writes = make_engine(fail=True)
        day = engine.now // DAY_MIN + 1
        for aid in ("kuaizheng", "aisi"):
            seed_minor(engine, world, aid, day, expires_on=day + 1)
        engine._last_day = day + 1
        engine._settle_wishes()
        self.assertEqual(engine.snapshot_stats["event_snapshot_failures"], 1, "no retry storm")
        self.assertEqual(engine.snapshot_stats["debounced"], 1)


class CadenceSanity(unittest.TestCase):
    """Over a mock run: writes = fixed cadence + big beats - debounced."""

    def test_the_write_count_is_exactly_accounted_for(self):
        world, engine, writes = make_engine()
        # Two deadlines inside the window, so the run really has big beats in it.
        day = engine.now // DAY_MIN + 1
        for aid in ("kuaizheng", "aisi"):
            seed_minor(engine, world, aid, day, expires_on=day + 1)

        triggers: list[str] = []
        real = engine._event_snapshot

        def counting(verb):
            triggers.append(verb)
            return real(verb)
        engine._event_snapshot = counting

        end = START + 2 * DAY_MIN

        async def run():
            await engine.run_until(end)
            await engine.drain(end)
        asyncio.run(run())

        s = engine.snapshot_stats
        self.assertEqual(len(triggers), s["event"] + s["debounced"] + s["event_snapshot_failures"],
                         "every trigger is written, deduped, or counted as failed")
        self.assertEqual(len(writes), s["periodic"] + s["event"])
        self.assertEqual(s["event_snapshot_failures"], 0)
        self.assertEqual(s["periodic"], 2, "one settlement per midnight crossed")
        self.assertTrue(triggers, "the run must actually contain big beats")
        print(f"\n[cadence] writes={len(writes)} = periodic {s['periodic']} + event {s['event']}"
              f"  (triggers {len(triggers)}, debounced {s['debounced']})"
              f"  verbs={sorted(set(triggers))}")


if __name__ == "__main__":
    unittest.main()
