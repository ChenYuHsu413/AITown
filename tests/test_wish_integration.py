"""Headless phase-2 lifecycle: generation -> rules -> closure, with no DB."""
from __future__ import annotations
import asyncio, os, unittest
os.environ.update(AI_TOWN_LIVE="0",AI_TOWN_LANG="en",AI_TOWN_DB_URL="")

from backend.app.agents import chapters, wishes
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot
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

    def test_external_chapter_signals_settle_linked_wish_once(self):
        async def run():
            for trigger in ('transition','secret_resolved','manual'):
                world,e,a,mat=setup()
                clean=wishes.validate_generation({"title":"Private","statement":"I want to visit the park twice privately.",
                    "motivation":"m","scale":"major","source_memory_refs":[mat['memories'][0]['id']],
                    "requirements":[{"kind":"location_visits","target":"park","threshold":2,"unit":"visits"}],
                    "failure_conditions":[]},a,world,mat)
                wish=wishes.install(a,e.decisions.secrets,clean,1)
                record=await e.close_chapter(a,'abandoned',trigger=trigger,reason='external close')
                self.assertEqual((record.outcome,wish.status),('abandoned','abandoned'))
                self.assertTrue(e.decisions.secrets.secrets[wish.secret_id].resolved)
                self.assertFalse(e._request_wish_finish(a,wish,'completed','race'))
                self.assertEqual(len(a.chapter_history),1)
        asyncio.run(run())

    def test_restore_repairs_interrupted_and_missing_states_without_llm(self):
        world,e,a,mat=setup()
        clean=wishes.validate_generation({"title":"Private","statement":"I want to visit the park twice privately.",
            "motivation":"m","scale":"major","source_memory_refs":[mat['memories'][0]['id']],
            "requirements":[{"kind":"location_visits","target":"park","threshold":2,"unit":"visits"}],
            "failure_conditions":[]},a,world,mat)
        wish=wishes.install(a,e.decisions.secrets,clean,1)
        chapters.apply_closure(a,world,'completed','I finished a private chapter and let it rest.',
                               'fulfilled',[],2*24*60)
        payload=snapshot.capture(e,world,e.decisions)
        w2,e2,a2,_=setup(); before=e2.decisions.router.usage.total_calls
        snapshot.restore(payload,e2,w2,e2.decisions)
        restored=w2.agents[a.id].wishes[0]
        self.assertEqual(restored.status,'completed')
        self.assertEqual(e2.decisions.router.usage.total_calls,before)
        self.assertEqual(e2.reconcile_wishes(),0)

        world3,e3,a3,mat3=setup('ange')
        clean3=wishes.validate_generation({"title":"Private","statement":"I want to visit the park twice privately.",
            "motivation":"m","scale":"major","source_memory_refs":[mat3['memories'][0]['id']],
            "requirements":[{"kind":"location_visits","target":"park","threshold":2,"unit":"visits"}],
            "failure_conditions":[]},a3,world3,mat3)
        missing=wishes.install(a3,e3.decisions.secrets,clean3,1); a3.chapter=chapters.make_ordinary(a3,2)
        self.assertEqual(e3.reconcile_wishes(),1); self.assertEqual(missing.status,'failed')
        self.assertIn('missing',missing.outcome_reason); self.assertEqual(e3.reconcile_wishes(),0)

    def test_terminal_wish_dangling_pursuit_repairs_once(self):
        world,e,a,mat=setup()
        clean=wishes.validate_generation({"title":"Private","statement":"I want to visit the park twice privately.",
            "motivation":"m","scale":"major","source_memory_refs":[mat['memories'][0]['id']],
            "requirements":[{"kind":"location_visits","target":"park","threshold":2,"unit":"visits"}],
            "failure_conditions":[]},a,world,mat)
        wish=wishes.install(a,e.decisions.secrets,clean,1)
        wishes.finish(wish,e.decisions.secrets,'failed',2,'persisted outcome')
        self.assertEqual(e.reconcile_wishes(),1); self.assertEqual(a.chapter.chapter_type,'interlude')
        self.assertEqual(a.chapter_history[-1].biography_line,'')
        self.assertEqual(e.reconcile_wishes(),0)

    def test_daily_settlement_advances_relationship_and_money_requirements(self):
        async def run():
            world,e,_,_=setup()
            for resident in world.agents.values(): resident.wish_next_attempt_day=999
            made=[]
            for aid,kind,target in (('jiji','friendship','ange'),('ange','trust','jiji'),('oula','money','')):
                a=world.agents[aid]; a.chapter=chapters.make_ordinary(a,1); refs=[]
                for i in range(3):
                    m=MemoryItem(100+i,f'A concrete daily experience {i} gave {aid} a grounded reason to act.',5)
                    a.memory.add(m); refs.append({'id':chapters.memory_id(a.id,m),'text':m.text,'minute':m.minute,'importance':5})
                mat=wishes.material_for_prompt(a,world,refs,e.decisions.secrets)
                current=(getattr(a.rel(target),kind) if kind!='money' else a.state.money)
                raw={"title":"Private","statement":"I want to make one small measurable change.","motivation":"m",
                     "scale":"small","source_memory_refs":[refs[0]['id']],
                     "requirements":[{"kind":kind,"target":target,"threshold":current+1,"unit":"count"}],
                     "failure_conditions":[]}
                made.append(wishes.install(a,e.decisions.secrets,wishes.validate_generation(raw,a,world,mat),1))
                a.wish_next_attempt_day=999
            world.agents['jiji'].rel('ange').friendship+=2
            world.agents['ange'].rel('jiji').trust+=2
            e.now=24*60; e._last_day=1; e._daily_settlement(); await settle(e)
            self.assertTrue(all(w.status=='completed' for w in made))
            self.assertEqual(e.decisions.router.usage.total_calls,0)
        asyncio.run(run())

if __name__=="__main__": unittest.main()
