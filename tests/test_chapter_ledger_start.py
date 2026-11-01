"""The chapters ledger learns of a chapter when it opens, not when it closes.

    python -m unittest tests.test_chapter_ledger_start -v

Every runtime path (a wish opening a pursuit, an interlude, interlude -> ordinary)
already wrote its started row; the seeded pursuits of a fresh run opened before the
ledger was attached and so reached it only at their close. A fresh run now writes
them at once; a resumed run does not write them again.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ["AI_TOWN_LIVE"] = "0"
os.environ["AI_TOWN_DB_URL"] = ""


class FakePersistence:
    """Stands in for db.persistence.Persistence: records ledger rows, no database."""

    resumed = False

    def __init__(self, url, *a, **kw):
        self.run_id = "run-test"
        self.chapters: list[dict] = []

    async def start(self, note="", resume=False, restore_cb=None):
        return self.resumed

    def on_event(self, e): pass
    def on_llm_call(self, c): pass
    def on_memory(self, aid, item): pass
    def on_snapshot(self, payload): pass
    def on_chapter(self, row): self.chapters.append(row)
    def on_wish(self, row): pass
    def on_wish_attempt(self, row): pass
    def on_public_object(self, obj): pass
    def on_digest(self, row): pass
    def on_rel_deltas(self, rows): pass

    async def load_translation_cache(self, lang): return []

    def vector_retriever(self, agent_id, memory=None):
        async def r(query, k=5): return []
        return r


class SeededChaptersAtStart(unittest.TestCase):
    def boot(self, resumed: bool):
        from backend.app import server
        FakePersistence.resumed = resumed
        sim = server.Sim()
        with mock.patch.dict(os.environ, {"AI_TOWN_DB_URL": "postgresql+asyncpg://fake/db"}), \
                mock.patch("backend.app.db.persistence.Persistence", FakePersistence):
            asyncio.run(sim.attach_persistence())
        return sim, sim.persistence

    def test_a_fresh_run_writes_every_seeded_chapter_at_once(self):
        sim, p = self.boot(resumed=False)
        open_now = {a.id for a in sim.world.agents.values() if a.chapter is not None}
        self.assertTrue(open_now, "the seed has pursuits")
        self.assertEqual({r["agent_id"] for r in p.chapters}, open_now)
        self.assertTrue(all(r["ended_on"] == 0 and r["outcome"] == "" for r in p.chapters))

    def test_a_resume_does_not_write_them_again(self):
        sim, p = self.boot(resumed=True)
        self.assertEqual(p.chapters, [])


if __name__ == "__main__":
    unittest.main()
