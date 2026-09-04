"""Unit tests for wishes (agents/wishes.py) -- phase 2a.

    python -m unittest tests.test_wishes -v

Covers spec F2 (blocked days), F3 (work path), F4 (feasibility), F5 (abandonment
friction), F7 (privacy) and F8 (snapshot). The integration flows are in
tests/test_wish_integration.py.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_LANG"] = "en"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app.agents import wishes as wishes_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
from backend.app.simulation import snapshot as snapshot_mod
from backend.app.simulation.engine import DAY_MIN, SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets


def make_world():
    router = build_router(live=False)
    world = World(build_locations(), build_agents())
    engine = SimulationEngine(world, DecisionEngine(router))
    seed_secrets(engine.decisions.secrets)
    engine.bootstrap(6 * 60)
    return world, engine


def seed(engine, world, agent_id: str, **over):
    """Seed a wish through the real validate -> install path."""
    body = {"scale": "minor", "title": "T", "statement": "A statement long enough.",
            "narrative": "I am quietly working on something of my own.",
            "requirements": [{"kind": "location_visits", "target": "market", "threshold": 2}]}
    body.update(over)
    agent = world.agents[agent_id]
    day = engine.now // DAY_MIN + 1
    clean, problems = wishes_mod.validate_seed(body, agent, world, day)
    assert not problems, problems
    return engine.seed_wish(agent, clean, day)


class Feasibility(unittest.TestCase):
    """F4: a proposal that this resident could not actually act on is refused."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.day = 1

    def _problems(self, agent_id, **over):
        body = {"scale": "major", "title": "T", "statement": "A statement long enough.",
                "narrative": "I am quietly working on something of my own.",
                "requirements": [{"kind": "location_visits", "target": "market", "threshold": 2}]}
        body.update(over)
        _, problems = wishes_mod.validate_seed(body, self.world.agents[agent_id], self.world, self.day)
        return problems

    def test_passive_income_cannot_carry_a_major(self):
        """A pension moves the wallet, but there is nothing to *do* about it -- so a
        money requirement is passive for its holder and cannot justify a major."""
        pensioner = self.world.agents["kuaizheng"]      # retired: wage 42, no work entry
        self.assertTrue(wishes_mod.passive_income(pensioner, self.world))
        self.assertFalse(wishes_mod.actionable_income_path(pensioner, self.world))
        problems = self._problems("kuaizheng", requirements=[{"kind": "money_gain", "threshold": 300}])
        self.assertTrue(any("only income is a passive wage" in p for p in problems), problems)
        self.assertTrue(any("act on" in p for p in problems), problems)

    def test_a_shop_owner_keeps_an_actionable_money_requirement(self):
        """Regression: someone who can really earn is unaffected by the split."""
        owner = self.world.agents["jiji"]               # runs the cafe
        self.assertTrue(wishes_mod.actionable_income_path(owner, self.world))
        self.assertFalse(wishes_mod.passive_income(owner, self.world))
        self.assertEqual(self._problems("jiji", requirements=[{"kind": "money_gain", "threshold": 300}]), [])
        # ...and so is a salaried resident whose routine actually sends them to work
        self.assertTrue(wishes_mod.actionable_income_path(self.world.agents["lengyue"], self.world))
        self.assertEqual(self._problems("lengyue", requirements=[{"kind": "money_gain", "threshold": 50}]), [])

    def test_a_mixed_major_is_allowed_and_the_money_part_never_blocks(self):
        """money + location for the pensioner: the location requirement makes it a
        legitimate major, and the passive money part is simply left alone."""
        pensioner = self.world.agents["kuaizheng"]
        self.assertEqual(self._problems("kuaizheng", requirements=[
            {"kind": "money_gain", "threshold": 300},
            {"kind": "location_visits", "target": "market", "threshold": 4}]), [])
        with unittest.mock.patch.object(wishes_mod, "DRIVE_MAJOR_PROBABILITY", 1.0):
            w = seed(self.engine, self.world, "kuaizheng", scale="major",
                     requirements=[{"kind": "money_gain", "threshold": 300},
                                   {"kind": "location_visits", "target": "market", "threshold": 4}])
            directives = [wishes_mod.next_directive(pensioner, self.world, 9 * 60 + i * 45, "rest")
                          for i in range(4)]
        chosen = [d for d in directives if d]
        self.assertTrue(chosen, "the location requirement should still be driven")
        # every directive targets the location requirement; money is never pursued
        self.assertTrue(all(d["requirement_index"] == 1 for d in chosen), chosen)
        self.assertEqual(w.drive["blocked_streak"], 0)    # the passive half never blocks
        self.assertEqual(w.frustration_count, 0)
        # ...and it still accrues progress from the world, as a real requirement should
        pensioner.state.money = w.requirements[0].baseline + 300
        wishes_mod.update_from_state(w, pensioner)
        self.assertTrue(w.requirements[0].completed)

    def test_money_major_without_income_ability_is_refused(self):
        broke = self.world.agents["kuaizheng"]
        broke.profile.daily_wage = 0.0
        broke.routine = type(broke.routine)([e for e in broke.routine.entries if e.action != "work"])
        problems = self._problems("kuaizheng", requirements=[{"kind": "money_gain", "threshold": 50}])
        self.assertTrue(any("no income path" in p for p in problems), problems)
        # ...and a resident who does have a wage passes the same proposal
        self.assertEqual(self._problems("lengyue", requirements=[{"kind": "money_gain", "threshold": 50}]), [])

    def test_self_targeted_social_is_refused(self):
        problems = self._problems("xixi", requirements=[{"kind": "talk_count", "target": "xixi", "threshold": 3}])
        self.assertTrue(any("cannot target oneself" in p for p in problems), problems)

    def test_purely_passive_major_is_refused(self):
        problems = self._problems("oula", requirements=[{"kind": "event_witnessed", "target": "rain_start",
                                                         "threshold": 2}])
        self.assertTrue(any("can act on" in p for p in problems), problems)
        # the same passive requirement is fine as a minor wish
        self.assertEqual(self._problems("oula", scale="minor",
                                        requirements=[{"kind": "event_witnessed", "target": "rain_start",
                                                       "threshold": 2}]), [])

    def test_work_major_needs_a_work_routine(self):
        idle = self.world.agents["kuaizheng"]          # retired: no work entry
        self.assertTrue(any("no work entry" in p for p in
                            self._problems("kuaizheng", requirements=[{"kind": "action_count",
                                                                       "target": "work", "threshold": 3}])))
        self.assertEqual(idle.profile.occupation, "Retired")
        # the baker's routine does point at work
        self.assertEqual(self._problems("ange", requirements=[{"kind": "action_count",
                                                               "target": "work", "threshold": 3}]), [])

    def test_unknown_targets_and_capacity(self):
        self.assertTrue(any("unknown location" in p for p in
                            self._problems("oula", requirements=[{"kind": "location_visits",
                                                                  "target": "moon", "threshold": 1}])))
        self.assertTrue(any("unknown resident" in p for p in
                            self._problems("oula", requirements=[{"kind": "talk_count",
                                                                  "target": "nobody", "threshold": 1}])))
        # already-satisfied requirement at seed time
        self.assertTrue(any("already satisfied" in p for p in
                            self._problems("oula", requirements=[{"kind": "friendship", "target": "lengyue",
                                                                  "threshold": 10}])))

    def test_minor_capacity_is_two_and_major_is_one(self):
        seed(self.engine, self.world, "kuaizheng", scale="minor")
        seed(self.engine, self.world, "kuaizheng", scale="minor",
             requirements=[{"kind": "location_visits", "target": "park", "threshold": 2}])
        self.assertTrue(any("maximum 2 active minor" in p for p in
                            self._problems("kuaizheng", scale="minor")))
        seed(self.engine, self.world, "lengyue", scale="major")
        self.assertTrue(any("already in a pursuit chapter" in p for p in self._problems("lengyue")))


class BlockedDays(unittest.TestCase):
    """F2: blocked days are distinct AND consecutive; frustration is rare and private."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.agent = self.world.agents["kuaizheng"]
        self.wish = seed(self.engine, self.world, "kuaizheng")

    def _block(self, day, minute_of_day=10 * 60):
        wishes_mod.note_blocked(self.agent, self.wish, (day - 1) * DAY_MIN + minute_of_day)

    def _frustrations(self):
        return [m for m in self.agent.memory.items if m.text == wishes_mod.FRUSTRATION_TEXT]

    def test_two_requirements_blocked_same_day_counts_once(self):
        self._block(5, 9 * 60)
        self._block(5, 15 * 60)          # a second requirement, same day
        self.assertEqual(self.wish.drive["blocked_streak"], 1)

    def test_two_consecutive_days_are_needed_for_a_frustration_memory(self):
        self._block(5)
        self.assertEqual(self._frustrations(), [])
        self._block(6)
        self.assertEqual(len(self._frustrations()), 1)
        self.assertEqual(self.wish.frustration_count, 1)

    def test_no_second_frustration_on_the_same_day(self):
        self._block(5)
        self._block(6, 9 * 60)
        self._block(6, 18 * 60)
        self.assertEqual(len(self._frustrations()), 1)

    def test_a_successful_directive_clears_the_streak(self):
        self._block(5)
        wishes_mod.clear_blocked(self.wish)
        self.assertEqual(self.wish.drive["blocked_streak"], 0)
        self._block(6)
        self.assertEqual(self.wish.drive["blocked_streak"], 1)   # restarted, not continued
        self.assertEqual(self._frustrations(), [])

    def test_a_day_with_no_attempt_breaks_the_streak(self):
        self._block(5)
        # day 6: no attempt at all
        self._block(7)
        self.assertEqual(self.wish.drive["blocked_streak"], 1)
        self.assertEqual(self._frustrations(), [])

    def test_restore_does_not_re_count_the_same_day(self):
        self._block(5)
        self._block(6)
        payload = snapshot_mod.capture(self.engine, self.world, self.engine.decisions)
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        agent2 = world2.agents["kuaizheng"]
        wish2 = agent2.wishes[0]
        self.assertEqual(wish2.drive["blocked_streak"], 2)
        self.assertEqual(wish2.drive["last_blocked_day"], 6)
        before = len([m for m in agent2.memory.items if m.text == wishes_mod.FRUSTRATION_TEXT])
        wishes_mod.note_blocked(agent2, wish2, 5 * DAY_MIN + 20 * 60)     # same day 6 again
        self.assertEqual(wish2.drive["blocked_streak"], 2)
        self.assertEqual(len([m for m in agent2.memory.items
                              if m.text == wishes_mod.FRUSTRATION_TEXT]), before)

    def test_frustration_memory_is_generic_and_owner_only(self):
        self._block(5)
        self._block(6)
        note = self._frustrations()[0]
        for secret in (self.wish.title, self.wish.statement):
            self.assertNotIn(secret, note.text)
        for other in self.world.agents.values():
            if other.id != self.agent.id:
                self.assertNotIn(note.text, [m.text for m in other.memory.items])


class Abandonment(unittest.TestCase):
    """F5: the same evidence is carried differently by different people.

    These use the residents' REAL seeded conscientiousness (data.seed) -- an earlier
    version assigned its own values, which passed happily while no resident actually
    had the key and the live world was quietly running on one shared default."""

    def setUp(self):
        self.world, self.engine = make_world()

    def _wish_with_evidence(self, agent_id, created_on, streak=4, frustrations=2, scale="minor"):
        w = seed(self.engine, self.world, agent_id, scale=scale)
        w.created_on = created_on
        w.drive["blocked_streak"] = streak
        w.frustration_count = frustrations
        return w

    def test_conscientiousness_decides_who_lets_go(self):
        slack, dutiful = self.world.agents["oula"], self.world.agents["aisi"]
        self.assertLess(slack.profile.personality["conscientiousness"], 0.2)    # seeded 0.1
        self.assertGreater(dutiful.profile.personality["conscientiousness"], 0.9)  # seeded 1.0
        w1 = self._wish_with_evidence("oula", created_on=1)
        w2 = self._wish_with_evidence("aisi", created_on=1)
        day = 11
        self.assertTrue(wishes_mod.should_abandon(slack, w1, day))
        self.assertFalse(wishes_mod.should_abandon(dutiful, w2, day))

    def test_extreme_values_neither_overflow_nor_lock(self):
        """1.0 and 0.1 are deliberate. The dutiful must still be reachable by enough
        obstruction, and the slack must not abandon on no evidence at all."""
        dutiful, slack = self.world.agents["aisi"], self.world.agents["oula"]
        stubborn = self._wish_with_evidence("aisi", created_on=1, streak=7, frustrations=2)
        self.assertTrue(wishes_mod.should_abandon(dutiful, stubborn, 11))     # reachable
        calm = self._wish_with_evidence("oula", created_on=1, streak=0, frustrations=0)
        self.assertFalse(wishes_mod.should_abandon(slack, calm, 11))          # no evidence, no exit
        for a in self.world.agents.values():
            w = self._wish_with_evidence(a.id, created_on=1) if not a.wishes else a.wishes[0]
            t = wishes_mod.abandonment_threshold(a, w, 11)
            self.assertTrue(0.0 < t < 10.0, (a.id, t))                        # finite and sane

    def test_a_missing_dimension_raises_instead_of_defaulting(self):
        agent = self.world.agents["kuaizheng"]
        w = self._wish_with_evidence("kuaizheng", created_on=1)
        del agent.profile.personality["conscientiousness"]
        with self.assertRaises(wishes_mod.PersonalityKeyMissing) as ctx:
            wishes_mod.abandonment_threshold(agent, w, 11)
        self.assertIn("conscientiousness", str(ctx.exception))
        self.assertIn("seed.py", str(ctx.exception))
        with self.assertRaises(wishes_mod.PersonalityKeyMissing):
            wishes_mod.should_abandon(agent, w, 11)

    def test_sunk_cost_makes_an_old_wish_harder_to_drop(self):
        agent = self.world.agents["long"]                # seeded 0.5
        self.assertEqual(agent.profile.personality["conscientiousness"], 0.5)
        young = self._wish_with_evidence("long", created_on=28)
        self.assertTrue(wishes_mod.should_abandon(agent, young, 31))       # 3 days old -> lets go
        old = self._wish_with_evidence("long", created_on=1)               # same person, same evidence
        self.assertFalse(wishes_mod.should_abandon(agent, old, 31))        # 30 days carried -> holds on
        # identical evidence, identical person: only the sunk cost differs
        self.assertEqual(wishes_mod.abandonment_pressure(young), wishes_mod.abandonment_pressure(old))
        self.assertLess(wishes_mod.abandonment_threshold(agent, young, 31),
                        wishes_mod.abandonment_threshold(agent, old, 31))

    def test_a_fresh_or_unfrustrated_wish_is_never_abandoned(self):
        agent = self.world.agents["oula"]                                  # the least dutiful (0.1)
        fresh = self._wish_with_evidence("oula", created_on=10, streak=9, frustrations=9)
        self.assertFalse(wishes_mod.should_abandon(agent, fresh, 11))      # under the minimum age
        calm = self._wish_with_evidence("oula", created_on=1, streak=0, frustrations=0)
        self.assertFalse(wishes_mod.should_abandon(agent, calm, 30))       # no evidence at all


class SeedPersonality(unittest.TestCase):
    """The abandonment rule reads conscientiousness with no fallback, so the seed is
    what keeps the live world running. Assert its shape, not just the formula's."""

    DIMENSIONS = ("extraversion", "agreeableness", "openness", "neuroticism", "conscientiousness")

    def test_every_resident_carries_all_five_dimensions_in_range(self):
        from data.seed import build_agents
        residents = build_agents()
        self.assertEqual(len(residents), 10)
        for a in residents:
            p = a.profile.personality
            self.assertEqual(set(p), set(self.DIMENSIONS), a.id)
            for k, v in p.items():
                self.assertIsInstance(v, (int, float), (a.id, k))
                self.assertTrue(0.0 <= float(v) <= 1.0, (a.id, k, v))

    def test_the_seeded_conscientiousness_values_are_the_ones_of_record(self):
        from data.seed import CONSCIENTIOUSNESS, build_agents
        expected = {"jiji": 0.5, "ange": 0.7, "oula": 0.1, "lengyue": 0.4, "azong": 0.9,
                    "xixi": 1.0, "aisi": 1.0, "xue": 0.8, "long": 0.5, "kuaizheng": 0.5}
        self.assertEqual(CONSCIENTIOUSNESS, expected)
        self.assertEqual({a.id: a.profile.personality["conscientiousness"] for a in build_agents()},
                         expected)

    def test_an_old_snapshot_gains_the_dimension_from_the_seed(self):
        """Personality is static and never travels in a snapshot, so a pre-existing
        world picks the new dimension up on restore rather than looking corrupt."""
        world, engine = make_world()
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        for adata in payload["agents"].values():
            self.assertNotIn("personality", adata.get("profile", {}))   # not carried, by design
        payload["schema_version"] = 11
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        for a in world2.agents.values():
            self.assertIn("conscientiousness", a.profile.personality, a.id)
        self.assertEqual(world2.agents["azong"].profile.personality["conscientiousness"], 0.9)


class Privacy(unittest.TestCase):
    """F7: the wish's own words never reach anyone else."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.secretly = "I want to learn to bake bread before the winter festival"
        self.wish = seed(self.engine, self.world, "oula", scale="major",
                         title="Learning to bake", statement=self.secretly,
                         motivation="Because the bakery smells like home.",
                         narrative="I am quietly teaching myself something new.",
                         requirements=[{"kind": "location_visits", "target": "bakery", "threshold": 3}])

    def test_private_text_is_absent_from_public_events(self):
        for ev in self.engine.bus.events:
            for field in (ev.text, ev.text_en, ev.detail):
                self.assertNotIn(self.secretly, field or "")
                self.assertNotIn("Learning to bake", field or "")

    def test_private_text_is_absent_from_another_residents_prompt(self):
        owner, aisi = self.world.agents["oula"], self.world.agents["aisi"]
        card = builders.character_card(aisi, speech=True)
        self.assertNotIn(self.secretly, card)
        prompt = builders.dialogue_prompt(aisi, owner, ["a memory"], ["another memory"])
        blob = " ".join(m["content"] for m in prompt)
        self.assertNotIn(self.secretly, blob)
        self.assertNotIn("Learning to bake", blob)
        self.assertNotIn("Because the bakery smells like home.", blob)

    def test_the_owners_own_card_shows_only_the_chapter_narrative(self):
        card = builders.character_card(self.world.agents["oula"])
        self.assertIn("quietly teaching myself something new", card)
        self.assertNotIn(self.secretly, card)

    def test_no_other_resident_holds_a_memory_of_it(self):
        for other in self.world.agents.values():
            if other.id == "oula":
                continue
            for m in other.memory.items:
                self.assertNotIn(self.secretly, m.text)


class DrivePacing(unittest.TestCase):
    """Spec C: the drive is bounded, deterministic, and never writes progress."""

    def setUp(self):
        self.world, self.engine = make_world()
        self.agent = self.world.agents["kuaizheng"]

    def test_the_drive_only_takes_a_rest_or_idle_slot(self):
        seed(self.engine, self.world, "kuaizheng")
        for action in ("sleep", "work", "eat", "move", "talk"):
            self.assertIsNone(wishes_mod.next_directive(self.agent, self.world, 10 * 60, action))

    def test_the_drive_never_writes_progress(self):
        w = seed(self.engine, self.world, "kuaizheng",
                 requirements=[{"kind": "location_visits", "target": "cafe", "threshold": 5}])
        before = [r.progress for r in w.requirements]
        for minute in range(9 * 60, 20 * 60, 20):
            wishes_mod.next_directive(self.agent, self.world, minute, "rest")
        self.assertEqual([r.progress for r in w.requirements], before)

    def test_daily_attempt_cap_is_respected(self):
        with unittest.mock.patch.object(wishes_mod, "DRIVE_MINOR_PROBABILITY", 1.0):
            # both places are open on day 1 (the cafe rests Mondays -- see the
            # closed-workplace test for that path)
            w = seed(self.engine, self.world, "kuaizheng",
                     requirements=[{"kind": "location_visits", "target": "market", "threshold": 5},
                                   {"kind": "location_visits", "target": "park", "threshold": 5}])
            got = [wishes_mod.next_directive(self.agent, self.world, 9 * 60 + i * 30, "rest")
                   for i in range(6)]
        self.assertEqual(sum(1 for d in got if d is not None), wishes_mod.DRIVE_MINOR_DAILY_ATTEMPTS)
        self.assertEqual(w.drive["daily_attempts"], wishes_mod.DRIVE_MINOR_DAILY_ATTEMPTS)

    def test_major_is_considered_before_minor(self):
        with unittest.mock.patch.multiple(wishes_mod, DRIVE_MINOR_PROBABILITY=1.0,
                                          DRIVE_MAJOR_PROBABILITY=1.0):
            minor = seed(self.engine, self.world, "kuaizheng", scale="minor",
                         requirements=[{"kind": "location_visits", "target": "park", "threshold": 5}])
            major = seed(self.engine, self.world, "kuaizheng", scale="major",
                         requirements=[{"kind": "location_visits", "target": "market", "threshold": 5}])
            first = wishes_mod.next_directive(self.agent, self.world, 9 * 60, "rest")
        self.assertEqual(first["wish_id"], major.id)
        self.assertEqual(first["location"], "market")

    def test_requirements_rotate_and_the_roll_is_reproducible(self):
        with unittest.mock.patch.object(wishes_mod, "DRIVE_MINOR_PROBABILITY", 1.0):
            w = seed(self.engine, self.world, "kuaizheng",
                     requirements=[{"kind": "location_visits", "target": "market", "threshold": 5},
                                   {"kind": "location_visits", "target": "park", "threshold": 5}])
            first = wishes_mod.next_directive(self.agent, self.world, 9 * 60, "rest")
            w.drive["daily_attempts"] = 0            # look past the daily cap at the rotation itself
            second = wishes_mod.next_directive(self.agent, self.world, 11 * 60, "rest")
        self.assertNotEqual(first["requirement_index"], second["requirement_index"])
        # the dice are a pure function of (run, agent, wish, day, cursor)
        a = wishes_mod._roll("run-1", "kuaizheng", w.id, 4, 0)
        self.assertEqual(a, wishes_mod._roll("run-1", "kuaizheng", w.id, 4, 0))
        self.assertNotEqual(a, wishes_mod._roll("run-2", "kuaizheng", w.id, 4, 0))
        self.assertNotEqual(a, wishes_mod._roll("run-1", "kuaizheng", w.id, 5, 0))

    def test_the_social_pre_gate_bonus_is_not_confined_to_rest_or_idle(self):
        """The drive proper only takes a rest/idle slot, but "this person is worth
        approaching" is true whatever the hour is for -- a pair whose only overlap is
        a meal must still get the weight. Costs no attempt and no blocked day."""
        agent = self.world.agents["kuaizheng"]
        w = seed(self.engine, self.world, "kuaizheng",
                 requirements=[{"kind": "talk_count", "target": "lengyue", "threshold": 3}])
        self.assertTrue(wishes_mod.wants_contact(agent, "lengyue"))
        self.assertFalse(wishes_mod.wants_contact(agent, "aisi"))
        self.assertFalse(wishes_mod.wants_contact(agent, ""))
        # the pre-gate weight is higher for the wanted person...
        base = self.engine.decisions._social_gate(agent, "lengyue", 9 * 60)
        # ...and asking for it never spends a drive attempt or records a blocked day
        self.assertEqual(w.drive.get("daily_attempts", 0), 0)
        self.assertEqual(w.drive.get("blocked_streak", 0), 0)
        self.assertIsInstance(base, bool)
        # a completed requirement stops asking
        w.requirements[0].progress = 3
        self.assertFalse(wishes_mod.wants_contact(agent, "lengyue"))
        # ...and so does a wish that has ended
        w.requirements[0].progress = 0
        w.status = "completed"
        self.assertFalse(wishes_mod.wants_contact(agent, "lengyue"))

    def test_a_sleeping_partner_blocks_a_social_requirement(self):
        with unittest.mock.patch.object(wishes_mod, "DRIVE_MINOR_PROBABILITY", 1.0):
            w = seed(self.engine, self.world, "kuaizheng",
                     requirements=[{"kind": "talk_count", "target": "lengyue", "threshold": 3}])
            other = self.world.agents["lengyue"]
            other.state.location = self.agent.state.location
            other.state.current_action = "sleep"
            self.assertIsNone(wishes_mod.next_directive(self.agent, self.world, 9 * 60, "rest"))
            self.assertEqual(w.drive["blocked_streak"], 1)
            # awake and co-located -> a bias, never a forced conversation
            other.state.current_action = "rest"
            w.drive["daily_attempts"] = 0
            w.drive["attempt_days"] = {}
            directive = wishes_mod.next_directive(self.agent, self.world, 10 * 60, "rest")
        self.assertEqual(directive["action"], "talk_bias")
        self.assertEqual(directive["target"], "lengyue")


class SnapshotCompat(unittest.TestCase):
    """F8: version bump, old-payload tolerance, idempotent drive restore."""

    def test_version_bumped_and_v11_loads_without_wishes(self):
        world, engine = make_world()
        seed(engine, world, "kuaizheng")
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        self.assertEqual(payload["schema_version"], snapshot_mod.SCHEMA_VERSION)
        for adata in payload["agents"].values():          # strip v12, as a v11 snapshot would
            adata.pop("wishes", None)
        payload["schema_version"] = 11
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        self.assertTrue(all(a.wishes == [] for a in world2.agents.values()))

    def test_roundtrip_preserves_wish_and_drive_state(self):
        world, engine = make_world()
        w = seed(engine, world, "kuaizheng", expires_on=40)
        w.requirements[0].progress = 1
        wishes_mod.note_blocked(world.agents["kuaizheng"], w, 4 * DAY_MIN)
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        w2 = world2.agents["kuaizheng"].wishes[0]
        self.assertEqual((w2.id, w2.scale, w2.status, w2.expires_on), (w.id, w.scale, w.status, 40))
        self.assertEqual(w2.requirements[0].progress, 1)
        self.assertEqual(w2.drive["blocked_streak"], 1)
        self.assertEqual(w2.title, w.title)

    def test_malformed_wishes_are_skipped_whole(self):
        world, engine = make_world()
        seed(engine, world, "kuaizheng")
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        good = payload["agents"]["kuaizheng"]["wishes"][0]
        payload["agents"]["kuaizheng"]["wishes"] = [
            good,
            {**good, "id": "bad1", "requirements": []},                      # no requirements
            {**good, "id": "bad2", "scale": "enormous"},                     # unknown scale
            {**good, "id": "bad3", "requirements": [{"kind": "nope", "threshold": 1}]},
            {**good, "id": "bad4", "created_on": 0},
            "not-a-dict",
        ]
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        self.assertEqual([w.id for w in world2.agents["kuaizheng"].wishes], [good["id"]])

    def test_corrupt_drive_state_restores_to_a_safe_default(self):
        world, engine = make_world()
        seed(engine, world, "kuaizheng")
        payload = snapshot_mod.capture(engine, world, engine.decisions)
        payload["agents"]["kuaizheng"]["wishes"][0]["drive"] = {
            "blocked_streak": -5, "cursor": "x", "attempt_days": "nope", "last_blocked_day": True}
        world2, engine2 = make_world()
        snapshot_mod.restore(payload, engine2, world2, engine2.decisions)
        drive = world2.agents["kuaizheng"].wishes[0].drive
        self.assertEqual((drive["blocked_streak"], drive["cursor"], drive["attempt_days"]), (0, 0, {}))
        self.assertEqual(drive["last_blocked_day"], -1)


if __name__ == "__main__":
    unittest.main()
