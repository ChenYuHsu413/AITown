"""Phase 2b: a wish grows out of a resident's own life, or nothing does.

    python -m unittest tests.test_wish_generation -v

Covers spec E: the gates (E3), the soft threshold curve (E4), privacy (E5), the
no-template fallback (E6), and the whole 2b -> 2a handover through the real
scheduler (E2).
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
from backend.app.llm.prompts import builders
from backend.app.llm.router import ProvidersExhausted
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


def give_material(agent, day=10):
    """A life with something in it: a closed chapter and some weighted memories."""
    agent.memory.add(MemoryItem(minute=(day - 6) * DAY_MIN, importance=9, kind="biography",
                                text="I finished the thing I had been carrying, and it is done.",
                                source_chapter_id="ch-old", tags=["old"]))
    for i in range(4):
        agent.memory.add(MemoryItem(minute=(day - 4 + i) * DAY_MIN, importance=6, kind="reflection",
                                    text=f"Something worth noticing happened on day {day - 4 + i}."))


class Material(unittest.TestCase):
    def test_it_offers_biography_routine_and_what_lies_outside_it(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        m = wishes_mod.generation_material(agent, world, 10, residue="restless")
        self.assertTrue(m["biography"], "the closed chapter must be offered")
        self.assertTrue(m["memories"])
        self.assertEqual(m["residue"], "restless")
        # the routine, and what falls outside it
        self.assertTrue(m["routine"])
        supplied = {r["location"] for r in m["routine"]}
        self.assertFalse(supplied & set(m["unvisited_locations"]))
        self.assertNotIn(agent.id, m["no_regular_overlap_with"])
        self.assertEqual(m["capacity"], {"major": 1, "minor": 2})

    def test_the_prompt_carries_the_material_and_the_rules(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        m = wishes_mod.generation_material(agent, world, 10, residue="restless")
        msgs = builders.wish_generation_prompt(agent, m)
        sysmsg, usermsg = msgs[0]["content"], msgs[1]["content"]
        self.assertIn("no_wish", sysmsg)                       # declining is offered
        self.assertIn("provenance", sysmsg)
        self.assertIn("Kuaizheng is male", sysmsg)             # roster + gender
        self.assertIn("Places their routine never takes them:", usermsg)
        self.assertIn("restless", usermsg)
        self.assertIn(m["biography"][0]["id"], usermsg)        # ids are citable
        # a rejection is fed back verbatim on a retry
        again = builders.wish_generation_prompt(agent, m, rejection="too similar to X")
        self.assertIn("REJECTED", again[1]["content"])
        self.assertIn("too similar to X", again[1]["content"])


class PlaceholderResolution(unittest.TestCase):
    """Memory text is stored with {agent:id}/{loc:id}/{landmark:id} placeholders.
    Every free-text prompt resolves them before the model sees them; a raw
    placeholder in the material invites the model to write one back out."""

    def test_the_model_sees_resolved_names_not_placeholders(self):
        world, engine = make_engine()
        agent = world.agents["aisi"]
        seen = {}
        orig = builders.wish_generation_prompt

        def capture(a, material, rejection=""):
            seen["material"] = material
            return orig(a, material, rejection)

        with mock.patch.object(builders, "wish_generation_prompt", capture):
            asyncio.run(engine.decisions.grow_wish(agent, world, 10))
        blob = " ".join(m["text"] for m in seen["material"]["biography"] + seen["material"]["memories"])
        self.assertTrue(blob, "aisi starts with a seeded memory")
        self.assertNotIn("{landmark:", blob)
        self.assertNotIn("{loc:", blob)
        self.assertNotIn("{agent:", blob)
        self.assertIn("interactive light installation", blob)     # resolved to its real name
        self.assertIn("Firefly Park", blob)


class Gates(unittest.TestCase):
    """E3: each gate rejects the thing it exists for, and nothing else."""

    def setUp(self):
        self.world, self.engine = make_engine()
        self.agent = self.world.agents["kuaizheng"]
        give_material(self.agent)
        self.material = wishes_mod.generation_material(self.agent, self.world, 10)
        self.real_id = self.material["biography"][0]["id"]

    def _proposal(self, **over):
        p = {"title": "T", "statement": "I want to spend real time somewhere new to me.",
             "motivation": "M", "narrative": "I am quietly reaching for something.",
             "scale": "minor", "expires_in_days": 10, "provenance": [self.real_id],
             "requirements": [{"kind": "location_visits", "target": "cafe", "threshold": 3}]}
        p.update(over)
        return p

    def test_a_display_name_target_is_accepted_not_refused(self):
        """The roster the model is shown says "Aisi"; the world's id is "aisi". A
        proposal following the roster must not be thrown away over capitalisation --
        this rejected the first real social wish the town ever produced."""
        for spelling in ("Aisi", "aisi", "艾斯"):
            clean, problems = wishes_mod.validate_generation(
                self._proposal(requirements=[{"kind": "talk_count", "target": spelling,
                                              "threshold": 3}]),
                self.agent, self.world, 10, self.material)
            self.assertEqual(problems, [], spelling)
            self.assertEqual(clean["requirements"][0].target, "aisi", spelling)
        # a location display name resolves too
        clean, problems = wishes_mod.validate_generation(
            self._proposal(requirements=[{"kind": "location_visits",
                                          "target": "Tide Cafe", "threshold": 3}]),
            self.agent, self.world, 10, self.material)
        self.assertEqual(problems, [])
        self.assertEqual(clean["requirements"][0].target, "cafe")
        # ...and something that really is not a resident is still refused
        _, problems = wishes_mod.validate_generation(
            self._proposal(requirements=[{"kind": "talk_count", "target": "Nobody",
                                          "threshold": 3}]),
            self.agent, self.world, 10, self.material)
        self.assertTrue(any("unknown resident" in p for p in problems), problems)

    def test_the_prompt_states_the_id_form(self):
        msgs = builders.wish_generation_prompt(self.agent, self.material)
        self.assertIn("always lower-case, never a display name", msgs[0]["content"])
        self.assertIn("aisi", msgs[0]["content"])

    def test_a_hallucinated_memory_id_is_refused(self):
        clean, problems = wishes_mod.validate_generation(
            self._proposal(provenance=["deadbeef"]), self.agent, self.world, 10, self.material)
        self.assertIsNone(clean)
        self.assertTrue(any("never offered" in p for p in problems), problems)

    def test_provenance_is_required(self):
        for bad in ([], None):
            clean, problems = wishes_mod.validate_generation(
                self._proposal(provenance=bad), self.agent, self.world, 10, self.material)
            self.assertIsNone(clean)
            self.assertTrue(any("provenance is required" in p for p in problems), problems)

    def test_a_real_id_and_a_feasible_body_pass_gate_one(self):
        clean, problems = wishes_mod.validate_generation(
            self._proposal(), self.agent, self.world, 10, self.material)
        self.assertEqual(problems, [])
        self.assertEqual(clean["expires_on"], 20)              # day 10 + 10 days
        self.assertEqual(clean["provenance"][0]["id"], self.real_id)

    def test_deviation_rejects_a_wish_the_week_already_grants(self):
        # kuaizheng's routine already takes him to the market
        self.assertIn("market", wishes_mod.routine_locations(self.agent))
        clean, _ = wishes_mod.validate_generation(
            self._proposal(requirements=[{"kind": "location_visits", "target": "market", "threshold": 3}]),
            self.agent, self.world, 10, self.material)
        ok, why = wishes_mod.deviation_ok(self.agent, self.world, clean["requirements"])
        self.assertFalse(ok)
        self.assertIn("already part of this", why)
        # ...and accepts one that reaches outside it
        outside = self.material["unvisited_locations"][0]
        clean2, _ = wishes_mod.validate_generation(
            self._proposal(requirements=[{"kind": "location_visits", "target": outside, "threshold": 3}]),
            self.agent, self.world, 10, self.material)
        ok2, _ = wishes_mod.deviation_ok(self.agent, self.world, clean2["requirements"])
        self.assertTrue(ok2)

    def test_work_and_rest_can_never_be_the_deviation(self):
        for target in ("work", "rest", "idle"):
            req = wishes_mod.Requirement(kind="action_count", target=target, threshold=3)
            self.assertTrue(wishes_mod.routine_supplies(self.agent, self.world, req), target)
        ok, why = wishes_mod.deviation_ok(
            self.agent, self.world, [wishes_mod.Requirement(kind="action_count", target="rest", threshold=3)])
        self.assertFalse(ok)

    def test_the_monday_reroute_counts_as_routine_supply(self):
        """A cafe regular is handed the bakery every Monday, so wishing for the
        bakery would not actually change their week."""
        azong = self.world.agents["azong"]
        self.assertIn("cafe", wishes_mod.routine_locations(azong))
        self.assertNotIn("bakery", wishes_mod.routine_locations(azong))
        self.assertIn("bakery", wishes_mod.reroute_supplied_shops(azong, self.world))
        req = wishes_mod.Requirement(kind="location_visits", target="bakery", threshold=2)
        self.assertTrue(wishes_mod.routine_supplies(azong, self.world, req))

    def test_social_deviation_uses_real_routine_overlap(self):
        azong, lengyue = self.world.agents["azong"], self.world.agents["lengyue"]
        self.assertTrue(wishes_mod.routine_overlap(azong, lengyue))    # they lunch together
        req = wishes_mod.Requirement(kind="talk_count", target="lengyue", threshold=3)
        self.assertTrue(wishes_mod.routine_supplies(azong, self.world, req))

    def test_novelty_rejects_a_restatement_and_allows_a_departure(self):
        emb = MockEmbedding()
        text = wishes_mod.novelty_text("I want to spend real time at the office.",
                                       [wishes_mod.Requirement(kind="location_visits",
                                                               target="office", threshold=3)])
        vector = asyncio.run(emb.embed(text))
        past = wishes_mod.Wish(id="old", owner=self.agent.id, scale="minor", status="completed",
                               created_on=1, title="An old longing", statement="x",
                               requirements=[], embedding=vector)
        self.agent.wishes.append(past)
        ok, why = wishes_mod.novelty_ok(vector, self.agent, self.world)     # identical
        self.assertFalse(ok)
        self.assertIn("too similar", why)
        self.assertIn("An old longing", why)
        other = asyncio.run(emb.embed(wishes_mod.novelty_text(
            "I want to know Ange properly after all these years.",
            [wishes_mod.Requirement(kind="talk_count", target="ange", threshold=3)])))
        ok2, _ = wishes_mod.novelty_ok(other, self.agent, self.world)
        self.assertTrue(ok2)

    def test_novelty_also_looks_at_what_the_town_is_already_carrying(self):
        emb = MockEmbedding()
        text = wishes_mod.novelty_text("I want to spend real time at the office.",
                                       [wishes_mod.Requirement(kind="location_visits",
                                                               target="office", threshold=3)])
        vector = asyncio.run(emb.embed(text))
        neighbour = self.world.agents["long"]
        neighbour.wishes.append(wishes_mod.Wish(
            id="theirs", owner="long", scale="minor", status="active", created_on=1,
            title="Their longing", statement="x", requirements=[], embedding=vector))
        ok, why = wishes_mod.novelty_ok(vector, self.agent, self.world)
        self.assertFalse(ok)
        self.assertIn("someone else", why)


class SoftThreshold(unittest.TestCase):
    """E4: the town-wide pressure valve."""

    def test_the_curve_matches_the_constants_of_record(self):
        p = wishes_mod.generation_probability
        self.assertAlmostEqual(p(0), 0.80, places=3)
        self.assertAlmostEqual(p(2), 0.338, places=3)
        self.assertAlmostEqual(p(4), 0.1428, places=3)
        for n in range(6):                                   # monotonically decreasing, never negative
            self.assertGreater(p(n), p(n + 1))
            self.assertGreater(p(n + 1), 0)

    def test_the_roll_is_deterministic_per_run_agent_and_day(self):
        world, engine = make_engine()
        engine.set_run_seed("run-A")
        a = world.agents["kuaizheng"]
        first = engine._may_attempt_generation(a, 40)
        self.assertEqual(first, engine._may_attempt_generation(a, 40))
        engine.set_run_seed("run-B")
        flips = sum(1 for d in range(40, 80) if engine._may_attempt_generation(a, d) != first)
        self.assertGreater(flips, 0, "a different run must not replay the same schedule")

    def test_a_resident_with_no_capacity_is_never_asked(self):
        world, engine = make_engine()
        a = world.agents["kuaizheng"]
        for i in range(3):                                   # 1 major + 2 minor = full
            a.wishes.append(wishes_mod.Wish(
                id=f"w{i}", owner=a.id, scale="major" if i == 0 else "minor",
                status="active", created_on=1, title="t", statement="s",
                requirements=[wishes_mod.Requirement(kind="location_visits", target="park", threshold=1)]))
        self.assertFalse(engine._may_attempt_generation(a, 40))


class NoTemplateFallback(unittest.TestCase):
    """E6: when the chain cannot answer, nothing is invented."""

    class _Dead:
        def __init__(self, real):
            self.usage, self.tiers, self.task_chains = real.usage, real.tiers, real.task_chains
            self.budget_usd = real.budget_usd
        async def generate(self, **kw):
            raise ProvidersExhausted("all providers down")

    def test_a_dead_chain_yields_no_wish_and_ordinary_days(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        agent.chapter = chapters_mod.make_interlude("restless", 8, 10)
        engine.decisions.router = self._Dead(engine.decisions.router)

        async def run():
            engine._last_day = 9                             # day 10 begins: the interlude is spent
            engine._advance_chapters()
            await engine.drain(engine.now + 5)
        asyncio.run(run())

        self.assertEqual(agent.wishes, [], "no template wish may be manufactured")
        self.assertEqual(chapters_mod.chapter_type(agent), "ordinary")
        self.assertEqual(engine.wish_stats["gen_failed"], 1)
        self.assertEqual(engine.wish_stats["grown"], 0)

    def test_declining_is_recorded_and_leaves_ordinary_days(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]                     # no material -> the mock declines
        agent.chapter = chapters_mod.make_interlude("relieved", 8, 10)

        async def run():
            engine._last_day = 9
            engine._advance_chapters()
            await engine.drain(engine.now + 5)
        asyncio.run(run())
        self.assertEqual(agent.wishes, [])
        self.assertEqual(chapters_mod.chapter_type(agent), "ordinary")
        self.assertEqual(engine.wish_stats["gen_declined"], 1)


class Handover(unittest.TestCase):
    """E2: interlude ends -> generation -> gates -> live -> pursuit -> 2a drive."""

    def test_a_grown_wish_opens_a_pursuit_and_the_drive_takes_over(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        agent.chapter = chapters_mod.make_interlude("restless", 8, 10)

        async def run():
            engine._last_day = 9
            engine._advance_chapters()                       # the 2b hook
            await engine.drain(engine.now + 5)
        with mock.patch.object(wishes_mod, "GENERATION_BASE_P", 1.0):
            asyncio.run(run())

        self.assertEqual(len(agent.wishes), 1, engine.wish_stats)
        wish = agent.wishes[0]
        self.assertEqual(wish.born, "grown")
        self.assertTrue(wish.provenance, "a grown wish must cite its material")
        self.assertTrue(wish.embedding, "its novelty vector is stored for next time")
        self.assertEqual(chapters_mod.chapter_type(agent), "pursuit")
        self.assertEqual(agent.chapter.id, wish.chapter_id)
        # it survived the deviation gate: something it asks for is outside the week
        ok, _ = wishes_mod.deviation_ok(agent, world, wish.requirements)
        self.assertTrue(ok)
        # the public beat says only the scale
        born = [e for e in engine.bus.events if e.verb == "wish_born"]
        self.assertEqual(len(born), 1)
        self.assertEqual(born[0].text, wish.scale)

        # ...and 2a's drive now moves it at least one step, through the real funnel
        async def live():
            end = engine.now + 3 * DAY_MIN
            await engine.run_until(end)
            await engine.drain(end)
        with mock.patch.multiple(wishes_mod, DRIVE_MAJOR_PROBABILITY=1.0, DRIVE_MINOR_PROBABILITY=1.0):
            asyncio.run(live())
        # 2a has taken it up if any of these is true: a requirement moved, a decision
        # was made for the wish, or the drive tried and the world refused (a blocked
        # day is engagement too -- a social wish needs the other person to be there).
        moved = any(r.progress > 0 for r in wish.requirements)
        drove = [t for t in engine.decisions.traces
                 if t.agent_id == agent.id and "private intention" in t.decision.reason]
        tried = wish.drive.get("daily_attempts", 0) or wish.drive.get("blocked_streak", 0)
        self.assertTrue(moved or drove or tried,
                        f"2a should have picked the wish up (drive state: {wish.drive})")

    def test_the_public_beats_never_carry_the_wish_text(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        give_material(agent)
        agent.chapter = chapters_mod.make_interlude("restless", 8, 10)

        async def run():
            engine._last_day = 9
            engine._advance_chapters()
            await engine.drain(engine.now + 5)
        with mock.patch.object(wishes_mod, "GENERATION_BASE_P", 1.0):
            asyncio.run(run())
        wish = agent.wishes[0]
        for ev in engine.bus.events:
            for field in (ev.text, ev.text_en, ev.detail):
                for private in (wish.statement, wish.motivation, wish.title):
                    if private:
                        self.assertNotIn(private, field or "")


class SnapshotCompat(unittest.TestCase):
    def test_v13_roundtrip_and_v12_tolerance(self):
        world, engine = make_engine()
        agent = world.agents["kuaizheng"]
        agent.wish_last_attempt_day = 42
        agent.wishes.append(wishes_mod.Wish(
            id="w1", owner=agent.id, scale="minor", status="active", created_on=1,
            title="t", statement="s", born="grown", embedding=[0.1, 0.2, 0.3],
            requirements=[wishes_mod.Requirement(kind="location_visits", target="park", threshold=2)]))
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        self.assertEqual(payload["schema_version"], 13)

        world2, engine2 = make_engine()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        a2 = world2.agents["kuaizheng"]
        self.assertEqual(a2.wish_last_attempt_day, 42)
        self.assertEqual(a2.wishes[0].born, "grown")
        self.assertEqual(a2.wishes[0].embedding, [0.1, 0.2, 0.3])

        # a v12 payload has neither field: the wish loads, blocks nobody on novelty,
        # and the resident is simply due an attempt
        for adata in payload["agents"].values():
            adata.pop("wish_last_attempt_day", None)
            for w in adata.get("wishes", []):
                w.pop("embedding", None)
                w.pop("born", None)
        payload["schema_version"] = 12
        world3, engine3 = make_engine()
        snapshot_mod.restore(payload, engine3, world3, engine3.decisions)
        a3 = world3.agents["kuaizheng"]
        self.assertEqual(a3.wish_last_attempt_day, 0)
        self.assertEqual(a3.wishes[0].embedding, [])
        self.assertEqual(a3.wishes[0].born, "seeded")


if __name__ == "__main__":
    unittest.main()
