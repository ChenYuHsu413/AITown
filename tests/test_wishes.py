"""Phase-2 wish model, validation, lifecycle, privacy and persistence tests."""
from __future__ import annotations
import asyncio, copy, os, unittest
os.environ.update(AI_TOWN_LIVE="0", AI_TOWN_LANG="en", AI_TOWN_DB_URL="")

from backend.app.agents import chapters, wishes
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.llm.factory import build_router
from backend.app.simulation import snapshot
from backend.app.simulation.engine import Event, SimulationEngine, DAY_MIN
from backend.app.world.world import World
from data.seed import build_agents, build_locations, seed_secrets

def env():
    world=World(build_locations(),build_agents()); engine=SimulationEngine(world,DecisionEngine(build_router(False)))
    seed_secrets(engine.decisions.secrets); engine.bootstrap(360); return world,engine

def ordinary(world, aid="jiji"):
    a=world.agents[aid]; a.chapter=chapters.make_ordinary(a,1); return a

def material(a):
    refs=[]
    for i in range(3):
        m=MemoryItem(minute=100+i,importance=5,text=f"A concrete new experience number {i} changed how I see the park.")
        a.memory.add(m); refs.append({"id":chapters.memory_id(a.id,m),"text":m.text,"minute":m.minute,"importance":m.importance})
    return refs

def proposal(refs,scale="major",kind="location_visits",target="park",threshold=2):
    return {"title":"Notice the town","statement":"I want to visit the park and pay attention to its changing life.",
            "motivation":"Recent experiences made this matter.","scale":scale,
            "source_memory_refs":[refs[0]["id"]],"requirements":[{"kind":kind,"target":target,"threshold":threshold,"unit":"count"}],
            "failure_conditions":[{"kind":"deadline","days":14}]}

class Validation(unittest.TestCase):
    def setUp(self):
        self.w,self.e=env(); self.a=ordinary(self.w); self.refs=material(self.a)
        self.mat=wishes.material_for_prompt(self.a,self.w,self.refs,self.e.decisions.secrets)
    def test_rejects_unknown_refs_targets_kinds_and_fields(self):
        for mutate in (lambda x:x.update(source_memory_refs=["bad"]),
                       lambda x:x["requirements"][0].update(target="nowhere"),
                       lambda x:x["requirements"][0].update(kind="python_predicate"),
                       lambda x:x.update(predicate="exec()")):
            raw=proposal(self.refs); mutate(raw); self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
    def test_major_opens_one_chapter_small_does_not(self):
        clean=wishes.validate_generation(proposal(self.refs),self.a,self.w,self.mat)
        w=wishes.install(self.a,self.e.decisions.secrets,clean,1)
        self.assertEqual(self.a.chapter.chapter_type,"pursuit"); self.assertEqual(self.a.chapter.related_goal_id,w.id)
        self.assertIsNone(wishes.install(self.a,self.e.decisions.secrets,clean,1))
        b=ordinary(self.w,"ange"); refs=material(b); mat=wishes.material_for_prompt(b,self.w,refs,self.e.decisions.secrets)
        sw=wishes.install(b,self.e.decisions.secrets,wishes.validate_generation(proposal(refs,"small"),b,self.w,mat),1)
        self.assertEqual(b.chapter.chapter_type,"ordinary"); self.assertEqual(sw.scale,"small")
    def test_soft_threshold_monotonic_not_cap(self):
        vals=[wishes.generation_threshold(n) for n in range(8)]
        self.assertEqual(vals[:4],[10]*4); self.assertGreater(vals[-1],vals[4]); self.assertTrue(all(x<100 for x in vals))

class Eligibility(unittest.TestCase):
    def test_insufficient_material_and_same_attempt_do_not_call(self):
        w,e=env(); a=ordinary(w); a.wish_last_attempt_minute=max(m.minute for m in a.memory.items)
        a.memory.add(MemoryItem(a.wish_last_attempt_minute+1,"A concrete but solitary experience.",5))
        ok,_=wishes.generation_eligible(a,10,0); self.assertFalse(ok); self.assertEqual(e.decisions.router.usage.total_calls,0)
        material(a); a.wish_last_attempt_minute=1000
        ok,_=wishes.generation_eligible(a,10,0); self.assertFalse(ok)
    def test_cooldown_with_new_material_retries(self):
        w,e=env(); a=ordinary(w); material(a); a.wish_next_attempt_day=8
        self.assertFalse(wishes.generation_eligible(a,7,0)[0]);
        a.memory.add(MemoryItem(2000,"A later concrete event gave me a genuinely new reason to try.",8))
        self.assertTrue(wishes.generation_eligible(a,8,0)[0])

class ProgressAndPrivacy(unittest.TestCase):
    def setUp(self):
        self.w,self.e=env(); self.a=ordinary(self.w); self.refs=material(self.a)
        mat=wishes.material_for_prompt(self.a,self.w,self.refs,self.e.decisions.secrets)
        self.wish=wishes.install(self.a,self.e.decisions.secrets,wishes.validate_generation(proposal(self.refs),self.a,self.w,mat),1)
    def test_event_dedup_and_resume(self):
        ev=Event(500,"action","arrive",actor=self.a.id,location="park")
        self.assertTrue(wishes.update_from_event(self.wish,self.a,ev,1)); self.assertFalse(wishes.update_from_event(self.wish,self.a,ev,1))
        payload=snapshot.capture(self.e,self.w,self.e.decisions); w2,e2=env(); snapshot.restore(payload,e2,w2,e2.decisions)
        got=wishes.active_wish(w2.agents[self.a.id]); self.assertEqual(got.requirements[0].current,1); self.assertFalse(wishes.update_from_event(got,w2.agents[self.a.id],ev,1))
    def test_completion_precedes_deadline_and_is_idempotent(self):
        r=self.wish.requirements[0]; r.current=r.threshold; r.completed=True
        self.assertEqual(wishes.outcome(self.wish,99)[0],"completed")
        self.assertTrue(wishes.finish(self.wish,self.e.decisions.secrets,"completed",99,"done")); self.assertFalse(wishes.finish(self.wish,self.e.decisions.secrets,"failed",99,"late"))
        self.assertTrue(self.e.decisions.secrets.secrets[self.wish.secret_id].resolved)
    def test_wish_secret_is_socially_isolated(self):
        s=self.e.decisions.secrets.secrets[self.wish.secret_id]
        self.assertFalse(s.social_enabled); self.assertEqual(s.source_kind,"wish"); self.assertEqual(s.source_id,self.wish.id)
        self.assertNotIn(s,self.e.decisions.secrets.active_secrets_of(self.a.id))
        self.assertFalse(self.e.decisions.secrets.resolve(s.id,10,"generic"))
    def test_private_text_never_enters_public_bus(self):
        self.e._publish("system","chapter_started",actor=self.a,text="A private pursuit")
        corpus=" ".join((x.text+x.detail+x.text_en) for x in self.e.bus.events)
        self.assertNotIn(self.wish.statement,corpus)

class SnapshotAndAbandon(unittest.TestCase):
    def test_old_and_malformed_snapshot(self):
        w,e=env(); p=snapshot.capture(e,w,e.decisions); p["schema_version"]=11
        for x in p["agents"].values(): x.pop("wishes",None); x.pop("wish_generation",None)
        w2,e2=env(); snapshot.restore(p,e2,w2,e2.decisions); self.assertTrue(all(not a.wishes for a in w2.agents.values()))
        p=snapshot.capture(e,w,e.decisions); p["agents"]["jiji"]["wishes"]=[{"bad":True}]
        snapshot.restore(p,e2,w2,e2.decisions); self.assertEqual(w2.agents["jiji"].wishes,[])
    def test_roundtrip_linkage_and_cooldown(self):
        w,e=env(); a=ordinary(w); refs=material(a); mat=wishes.material_for_prompt(a,w,refs,e.decisions.secrets)
        wi=wishes.install(a,e.decisions.secrets,wishes.validate_generation(proposal(refs),a,w,mat),3)
        wi.requirements[0].current=1; wi.counted_event_keys=["abc"]; a.wish_next_attempt_day=12; a.wish_last_material_hash="hash"
        p=snapshot.capture(e,w,e.decisions); w2,e2=env(); snapshot.restore(p,e2,w2,e2.decisions); x=w2.agents[a.id]
        self.assertEqual(x.wishes[0].secret_id,wi.secret_id); self.assertEqual(x.wishes[0].counted_event_keys,["abc"]); self.assertEqual(x.wish_next_attempt_day,12)
    def test_abandonment_friction_and_grounding(self):
        w,e=env(); low=ordinary(w,"jiji"); high=ordinary(w,"ange")
        low.profile.personality["conscientiousness"]=0.1; high.profile.personality["conscientiousness"]=0.9
        for a in (low,high):
            refs=material(a); mat=wishes.material_for_prompt(a,w,refs,e.decisions.secrets)
            wi=wishes.install(a,e.decisions.secrets,wishes.validate_generation(proposal(refs),a,w,mat),1); wi.last_progress_day=1
            for i in range(3): a.memory.add(MemoryItem(2*DAY_MIN+i,f"A concrete frustrating setback stopped this intention again {i}.",6))
        ids=lambda a:[chapters.memory_id(a.id,m) for m in a.memory.items[-3:]]
        self.assertTrue(wishes.validate_abandon(low,low.wishes[0],10,ids(low))[0]); self.assertFalse(wishes.validate_abandon(high,high.wishes[0],10,ids(high))[0])
        self.assertFalse(wishes.validate_abandon(low,low.wishes[0],10,[])[0])
        old=MemoryItem(-100,"A frustrating setback happened before this wish existed.",8); low.memory.add(old)
        self.assertFalse(wishes.validate_abandon(low,low.wishes[0],10,[chapters.memory_id(low.id,old)])[0])
        low.wishes[0].progress=.9; self.assertLess(wishes.abandonment_score(low,low.wishes[0],10,low.memory.items[-3:]),0)

if __name__ == "__main__": unittest.main()
