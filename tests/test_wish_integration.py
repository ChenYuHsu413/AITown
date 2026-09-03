"""Headless phase-2 lifecycle: generation -> rules -> closure, with no DB."""
from __future__ import annotations
import asyncio, os, unittest
os.environ.update(AI_TOWN_LIVE="0",AI_TOWN_LANG="en",AI_TOWN_DB_URL="")

from backend.app.agents import chapters, wishes
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation.engine import SimulationEngine
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

def setup(aid="jiji"):
    world=World(build_locations(),build_agents()); engine=SimulationEngine(world,DecisionEngine(build_router(False)))
    seed_secrets(engine.decisions.secrets); engine.bootstrap(360); a=world.agents[aid]; a.chapter=chapters.make_ordinary(a,1)
    for i in range(3): a.memory.add(MemoryItem(100+i,f"A concrete experience {i} made the park feel newly important to me.",5))
    mem=wishes.eligible_material(a); mat=wishes.material_for_prompt(a,world,mem,engine.decisions.secrets)
    return world,engine,a,mat

async def settle(engine):
    while engine._tasks:
        await asyncio.gather(*list(engine._tasks),return_exceptions=True)

class WishFlow(unittest.TestCase):
    def test_major_generation_progress_and_single_closure_calls(self):
        async def run():
            world,e,a,mat=setup()
            day=next(d for d in range(1,8) if wishes.generation_slot(a.id,d))
            e.now=(day-1)*24*60; e._last_day=day-1; a.wish_next_attempt_day=1
            e._schedule_wish_generation(); await settle(e)
            wish=wishes.active_wish(a,"major"); self.assertIsNotNone(wish)
            self.assertEqual(a.chapter.chapter_type,"pursuit")
            e._publish("action","arrive",actor=a,location_id="park",minute=500)
            e._publish("action","arrive",actor=a,location_id="park",minute=600)
            await settle(e)
            self.assertEqual(wish.status,"completed"); self.assertEqual(a.chapter.chapter_type,"interlude")
            self.assertEqual(len(a.chapter_history),1)
            calls=[c.task_type for c in e.decisions.router.usage.calls]
            self.assertEqual(calls.count("wish_generation"),1); self.assertEqual(calls.count("chapter_closure"),1)
            self.assertEqual(len([x for x in e.bus.events if x.verb=="chapter_closed"]),1)
            public=" ".join(x.text+x.detail+x.text_en for x in e.bus.events)
            self.assertNotIn(wish.statement,public); self.assertNotIn(wish.title,public)
        asyncio.run(run())

    def test_small_completion_stays_ordinary_without_closure(self):
        async def run():
            world,e,a,mat=setup("ange")
            raw={"title":"Notice the park","statement":"I want to visit the park twice this week.","motivation":"It helps me breathe.",
                 "scale":"small","source_memory_refs":[mat["memories"][0]["id"]],
                 "requirements":[{"kind":"location_visits","target":"park","threshold":1,"unit":"visits"}],"failure_conditions":[]}
            wish=wishes.install(a,e.decisions.secrets,wishes.validate_generation(raw,a,world,mat),1)
            e._publish("action","arrive",actor=a,location_id="park",minute=500); await settle(e)
            self.assertEqual(wish.status,"completed"); self.assertEqual(a.chapter.chapter_type,"ordinary")
            self.assertFalse(any(m.kind=="biography" for m in a.memory.items)); self.assertFalse(any(x.verb=="chapter_closed" for x in e.bus.events))
            self.assertEqual(e.decisions.router.usage.total_calls,0)
        asyncio.run(run())

    def test_major_deadline_fails_and_closes_once(self):
        async def run():
            world,e,a,mat=setup("jiji")
            raw={"title":"Notice the park","statement":"I want to visit the park many times.","motivation":"It matters.",
                 "scale":"major","source_memory_refs":[mat["memories"][0]["id"]],
                 "requirements":[{"kind":"location_visits","target":"park","threshold":99,"unit":"visits"}],
                 "failure_conditions":[{"kind":"deadline","days":2}]}
            wish=wishes.install(a,e.decisions.secrets,wishes.validate_generation(raw,a,world,mat),1)
            e.now=3*24*60; e._last_day=3; e._advance_wishes(); e._advance_wishes(); await settle(e)
            self.assertEqual(wish.status,"failed"); self.assertEqual(a.chapter_history[-1].outcome,"failed")
            self.assertEqual(len([x for x in e.bus.events if x.verb=="chapter_closed"]),1)
        asyncio.run(run())

    def test_generation_failure_and_stale_result_leave_state_untouched(self):
        async def run():
            world,e,a,mat=setup()
            async def fail(*args): raise RuntimeError("bad json")
            e.decisions.generate_wish=fail; await e._wish_generation_task(a,mat,1)
            self.assertEqual(a.wishes,[]); self.assertEqual(a.chapter.chapter_type,"ordinary")
            async def stale(*args):
                a.chapter=chapters.make_interlude("relieved",1,5)
                return wishes.validate_generation({"title":"X","statement":"I want to visit the park twice.","motivation":"m","scale":"major",
                    "source_memory_refs":[mat["memories"][0]["id"]],"requirements":[{"kind":"location_visits","target":"park","threshold":2,"unit":"visits"}],"failure_conditions":[]},a,world,mat)
            e.decisions.generate_wish=stale; await e._wish_generation_task(a,mat,1)
            self.assertEqual(a.wishes,[]); self.assertEqual(a.chapter.chapter_type,"interlude")
        asyncio.run(run())

if __name__=="__main__": unittest.main()
