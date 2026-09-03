"""Headless phase-2 lifecycle: generation -> rules -> closure, with no DB."""
from __future__ import annotations
import asyncio, os, unittest
os.environ.update(AI_TOWN_LIVE="0",AI_TOWN_LANG="en",AI_TOWN_DB_URL="")

from backend.app.agents import chapters, wishes
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.agents.routine import Routine, RoutineEntry
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot
from backend.app.simulation.engine import DAY_MIN, Scheduler, SimulationEngine
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
        secret=e.decisions.secrets.secrets[wish.secret_id]
        secret.resolved=False; secret.resolved_minute=-1; secret.resolution=''
        self.assertEqual(e.reconcile_wishes(),1); self.assertEqual(a.chapter.chapter_type,'interlude')
        self.assertTrue(secret.resolved)
        self.assertEqual(a.chapter_history[-1].biography_line,'')
        self.assertEqual(e.reconcile_wishes(),0)

    def test_normal_tick_drives_move_then_arrive_progress_without_llm(self):
        async def run():
            world,e,a,_=setup()
            a.routine=Routine([RoutineEntry(0,'rest','home_a')]); a.state.location='home_a'
            a.memory.importance_since_reflection=0
            wish=wishes.Wish('natural-drive',a.id,'Private','PRIVATE_MARKER stays hidden.','Private',
                'small','active',1,requirements=[wishes.Requirement('location_visits',1,target='park')])
            a.wishes=[wish]
            day=next(d for d in range(1,100) if wishes._drive_roll(a.id,wish.id,d,0)<wishes.DRIVE_SMALL_PROBABILITY)
            start=(day-1)*DAY_MIN+12*60
            e.now=start; e._last_day=start//DAY_MIN; e.scheduler=Scheduler(); e.scheduler.schedule(a.id,start)
            e._last_decision_at[a.id]=start-1
            before=e.decisions.router.usage.total_calls
            await e.tick(); await settle(e)
            self.assertEqual(a.state.location,'park')
            self.assertEqual(wish.status,'completed')
            self.assertEqual(wish.requirements[0].current,1)
            self.assertEqual(e.decisions.router.usage.total_calls,before)
            self.assertTrue(any(ev.verb=='arrive' and ev.actor==a.id for ev in e.bus.events))
        asyncio.run(run())

    def test_social_drive_does_not_bypass_talk_cooldown(self):
        async def run():
            world,e,a,_=setup(); target=world.agents['ange']
            a.routine=Routine([RoutineEntry(0,'rest','home_a')]); a.state.location='home_a'
            target.state.location='home_a'; target.state.current_action='rest'; target.state.busy_until=0
            wish=wishes.Wish('social-hard-limit',a.id,'PRIVATE_MARKER','PRIVATE_MARKER stays hidden.',
                'Private','major','active',1,
                requirements=[wishes.Requirement('talk_count',2,target=target.id)])
            a.wishes=[wish]
            day=next(d for d in range(1,80) if wishes._drive_roll(a.id,wish.id,d,0)<wishes.DRIVE_MAJOR_PROBABILITY)
            now=(day-1)*DAY_MIN+12*60; a.state.last_talk_minute[target.id]=now
            obs=world.observe(a,now-1,now); before=e.decisions.router.usage.total_calls
            decision=await e.decisions.decide(a,world,obs,now)
            self.assertNotEqual(decision.action,'talk')
            self.assertEqual(e.decisions.router.usage.total_calls,before)
            self.assertEqual(a.rel(target.id).friendship,30)
        asyncio.run(run())

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
            e.now=DAY_MIN; e._last_day=1; e._daily_settlement(); await settle(e)
            self.assertTrue(all(w.status=='completed' for w in made))
            self.assertEqual(e.decisions.router.usage.total_calls,0)
        asyncio.run(run())

if __name__=="__main__": unittest.main()
