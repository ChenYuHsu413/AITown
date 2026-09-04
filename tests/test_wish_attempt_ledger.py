"""The generation ledger: every attempt becomes a durable row, however it ended.

    python -m unittest tests.test_wish_attempt_ledger -v

Before this, only ``wish_born`` survived a restart. A decline, a gate rejection
and a dead chain lived in per-process counters and stdout, so a stopped server
erased the whole distribution -- the outcome of Aisi's day-125 attempt is gone
for good and is deliberately not backfilled.

The table is the operator's: it never becomes an event, never enters a
resident's context, and it does not carry the rejected proposal's own words.
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
from backend.app.llm.embeddings import MockEmbedding
from backend.app.llm.factory import build_router
from backend.app.llm.usage import LLMCall
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

START = 6 * 60
LEDGER_COLUMNS = {"run_id", "agent_id", "sim_day", "trigger", "attempt_no",
                  "outcome", "gate", "reason", "wish_id", "cost_usd"}


def make_engine(fail_ledger: bool = False):
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(build_router(live=False)))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(START)
    rows: list[dict] = []

    def sink(row):
        if fail_ledger:
            raise RuntimeError("attempt ledger queue is down")
        rows.append(row)

    engine.on_wish_attempt = sink
    return world, engine, rows


def give_material(agent, day=10):
    agent.memory.add(MemoryItem(minute=(day - 6) * DAY_MIN, importance=9, kind="biography",
                                text="I finished the thing I had been carrying, and it is done.",
                                source_chapter_id="ch-old", tags=["old"]))
    for i in range(4):
        agent.memory.add(MemoryItem(minute=(day - 4 + i) * DAY_MIN, importance=6, kind="reflection",
                                    text=f"Something worth noticing happened on day {day - 4 + i}."))


def run_generation(engine, world, agent_id="kuaizheng", day=10, ordinary=False):
    """Drive a real generation run through the engine, either trigger."""
    agent = world.agents[agent_id]

    async def run():
        engine._last_day = day - 1
        if ordinary:
            agent.wish_last_attempt_day = 0                # cadence: due an attempt
            engine._roll_wish_generation()
        else:
            agent.chapter = chapters_mod.make_interlude("restless", day - 2, day)
            engine._advance_chapters()
        await engine.drain(engine.now + 5)

    with mock.patch.object(wishes_mod, "GENERATION_BASE_P", 1.0):
        asyncio.run(run())
    return agent


def reject_at(gate: str, reason: str = "the gate said no"):
    """Force one named gate to refuse everything, leaving the others real."""
    if gate == "wish_feasibility":
        return mock.patch.object(wishes_mod, "validate_generation",
                                 lambda *a, **k: (None, [reason]))
    if gate == "wish_deviation":
        return mock.patch.object(wishes_mod, "deviation_ok", lambda *a, **k: (False, reason))
    return mock.patch.object(wishes_mod, "novelty_ok", lambda *a, **k: (False, reason))


class Shape(unittest.TestCase):
    """Every row carries the columns the table declares, and nothing else."""

    def test_rows_only_use_declared_columns(self):
        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        run_generation(engine, world)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row) - LEDGER_COLUMNS, set(), row)
            self.assertIsInstance(row["cost_usd"], float)
            self.assertGreaterEqual(row["cost_usd"], 0.0)
            self.assertGreaterEqual(row["attempt_no"], 1)


class Outcomes(unittest.TestCase):
    """Each way an attempt can end lands one row saying so."""

    def test_a_born_wish_writes_one_row_linked_to_it(self):
        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        agent = run_generation(engine, world)
        self.assertEqual(len(agent.wishes), 1, engine.wish_stats)

        self.assertEqual([r["outcome"] for r in rows], ["born"])
        row = rows[0]
        self.assertEqual(row["wish_id"], agent.wishes[0].id)
        self.assertEqual((row["agent_id"], row["sim_day"], row["attempt_no"]), ("kuaizheng", 10, 1))
        self.assertEqual(row["trigger"], "interlude_end")
        self.assertEqual((row["gate"], row["reason"]), ("", ""))

    def test_a_decline_is_recorded_with_the_models_own_reason(self):
        world, engine, rows = make_engine()
        agent = run_generation(engine, world)          # no material -> the mock declines
        self.assertEqual(agent.wishes, [])
        self.assertEqual([r["outcome"] for r in rows], ["declined"])
        self.assertEqual(rows[0]["reason"], "no material to build on")
        self.assertEqual(rows[0]["wish_id"], "")

    def test_each_gate_names_itself_once_per_retry(self):
        for gate in ("wish_feasibility", "wish_deviation", "wish_novelty"):
            with self.subTest(gate=gate):
                world, engine, rows = make_engine()
                give_material(world.agents["kuaizheng"])
                with reject_at(gate):
                    agent = run_generation(engine, world)
                self.assertEqual(agent.wishes, [])
                self.assertEqual(engine.wish_stats["gen_gates"], 1)
                # one row per attempt in the retry loop, numbered in order
                self.assertEqual(len(rows), 1 + wishes_mod.GENERATION_RETRIES)
                self.assertEqual([r["attempt_no"] for r in rows], [1, 2, 3])
                self.assertEqual({r["outcome"] for r in rows}, {"gate_rejected"})
                self.assertEqual({r["gate"] for r in rows}, {gate})
                self.assertEqual({r["reason"] for r in rows}, {"the gate said no"})

    def test_a_rejection_followed_by_a_wish_keeps_both_rows(self):
        """The row a coarse 'it worked' summary would have thrown away."""
        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        real = wishes_mod.deviation_ok
        calls = {"n": 0}

        def once(*a, **k):
            calls["n"] += 1
            return (False, "not on the first try") if calls["n"] == 1 else real(*a, **k)

        with mock.patch.object(wishes_mod, "deviation_ok", once):
            agent = run_generation(engine, world)

        self.assertEqual(len(agent.wishes), 1, engine.wish_stats)
        self.assertEqual([(r["attempt_no"], r["outcome"]) for r in rows],
                         [(1, "gate_rejected"), (2, "born")])
        self.assertEqual(rows[0]["gate"], "wish_deviation")
        self.assertEqual(rows[1]["wish_id"], agent.wishes[0].id)
        self.assertEqual(rows[0]["wish_id"], "", "a rejected attempt links to no wish")

    def test_a_dead_chain_is_recorded_rather_than_vanishing(self):
        class Dead:
            def __init__(self, real):
                self.usage, self.tiers, self.task_chains = real.usage, real.tiers, real.task_chains
                self.budget_usd = real.budget_usd

            async def generate(self, **kw):
                raise RuntimeError("all providers down")

        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        engine.decisions.router = Dead(engine.decisions.router)
        agent = run_generation(engine, world)

        self.assertEqual(agent.wishes, [])
        self.assertEqual(engine.wish_stats["gen_failed"], 1)
        self.assertEqual([r["outcome"] for r in rows], ["gen_failed"])
        self.assertIn("all providers down", rows[0]["reason"])


class Trigger(unittest.TestCase):
    def test_the_two_entry_points_are_told_apart(self):
        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        run_generation(engine, world, ordinary=True)
        self.assertTrue(rows)
        self.assertEqual({r["trigger"] for r in rows}, {"ordinary_cadence"})


class Cost(unittest.TestCase):
    def test_only_this_attempts_own_generation_spend_is_attributed(self):
        world, engine, _ = make_engine()
        u = engine.decisions.router.usage

        def call(agent_id, task, cost):
            u.record(LLMCall(sim_minute=0, agent_id=agent_id, task_type=task,
                             provider="p", model="m", input_tokens=1, output_tokens=1,
                             latency_ms=1, estimated_cost=cost))

        call("kuaizheng", "wish_generation", 0.001)       # before the mark: someone else's attempt
        mark = len(u.calls)
        call("kuaizheng", "wish_generation", 0.002)       # this attempt, two providers deep
        call("kuaizheng", "wish_generation", 0.003)
        call("kuaizheng", "dialogue", 0.500)              # a different task entirely
        call("aisi", "wish_generation", 0.700)            # a different resident, concurrently

        row = engine.decisions._attempt_row("kuaizheng", 1, mark, "born")
        self.assertEqual(row["cost_usd"], 0.005)


class Privacy(unittest.TestCase):
    """The rejected proposal's own words never become a durable row, an event, or
    anything a resident could read."""

    def test_a_rejected_proposals_text_appears_nowhere(self):
        world, engine, rows = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        seen: list[dict] = []
        real = wishes_mod.validate_generation

        def capture(raw, *a, **k):
            seen.append(raw)
            return real(raw, *a, **k)

        with mock.patch.object(wishes_mod, "validate_generation", capture), \
                reject_at("wish_deviation", "every actionable requirement is already routine"):
            run_generation(engine, world)

        private = [str(p.get(f, "")) for p in seen for f in ("title", "statement", "motivation")]
        private = [t for t in private if len(t) > 8]
        self.assertTrue(private, "the mock must actually have proposed something")

        ledger_blob = " ".join(str(v) for r in rows for v in r.values())
        event_blob = " ".join(" ".join(str(getattr(e, f, "") or "") for f in
                                       ("text", "text_en", "detail", "speech"))
                              for e in engine.bus.events)
        memory_blob = " ".join(m.text for a in world.agents.values() for m in a.memory.items)
        belief_blob = " ".join(b.text for a in world.agents.values() for b in a.semantic.beliefs)
        chronicle_blob = " ".join(str(c) for c in engine.chronicle)
        for text in private:
            self.assertNotIn(text, ledger_blob, "the ledger must not quote the proposal")
            self.assertNotIn(text, event_blob)
            self.assertNotIn(text, memory_blob)
            self.assertNotIn(text, belief_blob)
            self.assertNotIn(text, chronicle_blob)

    def test_a_gate_that_quotes_a_private_title_is_redacted(self):
        """The novelty gate argues its case by naming the wish it collided with --
        someone else's private wording. The score survives; the title does not."""
        world, engine, _ = make_engine()
        agent = world.agents["kuaizheng"]
        vector = asyncio.run(MockEmbedding().embed(wishes_mod.novelty_text(
            "I want to spend real time at the office.",
            [wishes_mod.Requirement(kind="location_visits", target="office", threshold=3)])))
        world.agents["long"].wishes.append(wishes_mod.Wish(
            id="theirs", owner="long", scale="minor", status="active", created_on=1,
            title="A longing nobody else may read", statement="x", requirements=[],
            embedding=vector))

        ok, why = wishes_mod.novelty_ok(vector, agent, world)
        self.assertFalse(ok)
        self.assertIn("A longing nobody else may read", why)          # the raw gate message does
        stored = wishes_mod.ledger_reason(why)
        self.assertNotIn("A longing nobody else may read", stored)    # the stored row does not
        self.assertIn("too similar", stored)
        self.assertIn("someone else", stored)

    def test_the_redaction_is_length_bounded_and_null_safe(self):
        self.assertEqual(wishes_mod.ledger_reason(None), "")
        self.assertEqual(wishes_mod.ledger_reason(""), "")
        self.assertEqual(len(wishes_mod.ledger_reason("x" * 900)), 300)


class LedgerFailure(unittest.TestCase):
    """Same philosophy as the event snapshot: the attempt already happened."""

    def test_a_failed_write_costs_the_generation_nothing(self):
        world, engine, rows = make_engine(fail_ledger=True)
        give_material(world.agents["kuaizheng"])
        agent = run_generation(engine, world)

        self.assertEqual(len(agent.wishes), 1, "the wish landed regardless")
        self.assertEqual(rows, [])
        self.assertEqual(engine.wish_stats["attempt_ledger_failures"], 1)
        self.assertEqual(engine.wish_stats["gen_ok"], 1)

    def test_one_bad_row_does_not_swallow_the_rest(self):
        world, engine, rows = make_engine()
        give_material(world.agents["kuaizheng"])
        calls = {"n": 0}

        def flaky(row):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            rows.append(row)

        engine.on_wish_attempt = flaky
        with reject_at("wish_novelty"):
            run_generation(engine, world)
        self.assertEqual(engine.wish_stats["attempt_ledger_failures"], 1)
        self.assertEqual([r["attempt_no"] for r in rows], [2, 3])


class Headless(unittest.TestCase):
    def test_no_hook_means_no_ledger_and_no_error(self):
        world, engine, _ = make_engine()
        engine.on_wish_attempt = None
        give_material(world.agents["kuaizheng"])
        agent = run_generation(engine, world)
        self.assertEqual(len(agent.wishes), 1)
        self.assertEqual(engine.wish_stats["attempt_ledger_failures"], 0)


if __name__ == "__main__":
    unittest.main()
