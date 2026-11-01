"""The zh-native era's tempo -- one notch faster, with the weight of a life untouched.

    python -m unittest tests.test_pacing -v
"""

from __future__ import annotations

import os
import unittest

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""

from backend.app import pacing
from backend.app.agents import chapters as chapters_mod
from backend.app.agents import wishes as wishes_mod
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.router import LLMRouter
from backend.app.llm.providers.base import LLMProvider
from data.seed import build_agents


class _Live(LLMProvider):          # any non-mock provider makes the engine "live"
    name = "live"

    async def generate(self, messages, **kw):
        raise NotImplementedError


class Tempo(unittest.TestCase):
    def test_the_values_of_record(self):
        self.assertEqual(wishes_mod.GENERATION_ORDINARY_EVERY, 3)
        self.assertEqual(wishes_mod.GENERATION_DECAY, 0.75)
        self.assertEqual(wishes_mod.GENERATION_BASE_P, 0.80)
        self.assertEqual(wishes_mod.DEFAULT_SPAN_DAYS, {"major": 14, "minor": 7})

    def test_an_interlude_lasts_two_to_four_days(self):
        lengths = {chapters_mod.interlude_days(r, a)
                   for a in build_agents() for r in chapters_mod.RESIDUES}
        self.assertEqual(min(lengths), 2)
        self.assertEqual(max(lengths), 4)

    def test_a_live_day_holds_twenty_conversations(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_TOWN_DIALOGUE_CAP"}
        from unittest import mock
        with mock.patch.dict(os.environ, env, clear=True):
            d = DecisionEngine(LLMRouter(tiers={"cheap": [_Live()], "normal": [_Live()], "smart": [_Live()]}))
        self.assertEqual(d.dialogue_cap, pacing.DIALOGUE_CAP)
        self.assertEqual(pacing.DIALOGUE_CAP, 20)

    def test_the_weight_of_a_life_did_not_move(self):
        # frustration / blocked days / abandonment friction stay the formal era's
        self.assertEqual(wishes_mod.FRUSTRATION_BLOCKED_DAYS, 2)
        self.assertEqual(wishes_mod.FRUSTRATION_COOLDOWN_DAYS, 3)
        self.assertEqual(wishes_mod.ABANDON_MIN_AGE_DAYS, 3)
        self.assertEqual(wishes_mod.ABANDON_BASE, {"major": 1.30, "minor": 0.80})


if __name__ == "__main__":
    unittest.main()
