"""Phase-2 wish model, validation, lifecycle, privacy and persistence tests."""
from __future__ import annotations
import asyncio, copy, math, os, unittest
from unittest import mock
os.environ.update(AI_TOWN_LIVE="0", AI_TOWN_LANG="en", AI_TOWN_DB_URL="")

from backend.app.agents import chapters, wishes
from backend.app.agents.core import MemoryItem
from backend.app.agents.decision import DecisionEngine
from backend.app.agents.routine import Routine, RoutineEntry
from backend.app.llm.factory import build_router
from backend.app.llm.prompts import builders
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

def drive_wish(a, wid, kind, target='', threshold=2, scale='major'):
    w=wishes.Wish(wid,a.id,'Private','A private statement stays hidden.','Private',scale,'active',1,
                  requirements=[wishes.Requirement(kind,threshold,target=target)])
    a.wishes.append(w); return w

def passing_day(a,w,cursor=0,limit=80):
    p=wishes.DRIVE_MAJOR_PROBABILITY if w.scale=='major' else wishes.DRIVE_SMALL_PROBABILITY
    return next(d for d in range(1,limit) if wishes._drive_roll(a.id,w.id,d,cursor)<p)

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

    def test_requirements_start_from_live_state_and_must_need_work(self):
        cases=(('friendship','ange',self.a.rel('ange').friendship),
               ('trust','ange',self.a.rel('ange').trust),('money','',self.a.state.money))
        for kind,target,current in cases:
            raw=proposal(self.refs,scale='small',kind=kind,target=target,threshold=current)
            self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
            raw['requirements'][0]['threshold']=current+1
            clean=wishes.validate_generation(raw,self.a,self.w,self.mat)
            self.assertEqual(clean['requirements'][0].current,current)
            self.assertEqual(clean['requirements'][0].baseline,current)
        raw=proposal(self.refs,kind='money_gain',target='',threshold=5)
        r=wishes.validate_generation(raw,self.a,self.w,self.mat)['requirements'][0]
        self.assertEqual((r.current,r.baseline),(0,self.a.state.money))

    def test_empty_and_duplicate_requirements_are_rejected(self):
        for scale in ('small','major'):
            raw=proposal(self.refs,scale=scale); raw['requirements']=[]
            self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
        raw=proposal(self.refs); raw['requirements']*=2
        self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))

    def test_major_actionable_requirements_must_be_feasible_for_owner(self):
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a')])
        raw=proposal(self.refs,kind='action_count',target='work')
        self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
        raw=proposal(self.refs,kind='talk_count',target=self.a.id)
        self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
        self.a.profile.daily_wage=0
        for loc in self.w.locations.values():
            if loc.owner == self.a.id: loc.owner=''
        raw=proposal(self.refs,kind='money_gain',target='')
        self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))

    def test_work_and_location_feasibility_accept_existing_paths(self):
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a'),RoutineEntry(600,'work','office')])
        raw=proposal(self.refs,kind='action_count',target='work')
        self.assertIsNotNone(wishes.validate_generation(raw,self.a,self.w,self.mat))
        raw=proposal(self.refs,kind='location_visits',target='missing')
        self.assertIsNone(wishes.validate_generation(raw,self.a,self.w,self.mat))

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

    def test_secret_marker_is_redacted_from_public_prompts_and_memories(self):
        marker='ULTRAVIOLET_WREN_9341'
        self.wish.title=marker+' title'; self.wish.statement='I want '+marker+' to remain mine.'
        self.a.chapter.title=self.wish.title; self.a.chapter.goal=self.wish.statement
        self.a.chapter.narrative='I am pursuing '+marker+'.'
        other=self.w.agents['ange']
        prompts=(builders.dialogue_prompt(self.a,other,[],[]),
                 builders.should_talk_prompt(self.a,other.name,[]),
                 builders.decision_prompt(self.a,'A quiet room',[],['rest']))
        for prompt in prompts:
            self.assertNotIn(marker,str(prompt))
        reflection=builders.reflection_prompt(self.a,[],wish=self.wish,frustration=[])
        self.assertIn(marker,str(reflection))
        self.e._wish_progressed(self.a,self.wish,1)
        self.assertNotIn(marker,' '.join(m.text for m in self.a.memory.items))
        self.assertIn(marker,str(self.wish.to_dict()))

    def test_actorless_global_event_does_not_advance_private_wish(self):
        self.wish.requirements=[wishes.Requirement('event_count',1,target='transition')]
        ev=Event(500,'system','transition')
        self.assertFalse(wishes.update_from_event(self.wish,self.a,ev,1))

    def test_event_dedup_history_is_bounded(self):
        self.wish.requirements=[wishes.Requirement('action_count',1000,target='rest')]
        for i in range(wishes.COUNTED_EVENT_KEYS_MAX+20):
            wishes.update_from_event(self.wish,self.a,Event(i,'action','rest',actor=self.a.id),1)
        self.assertEqual(len(self.wish.counted_event_keys),wishes.COUNTED_EVENT_KEYS_MAX)

    def test_private_wish_biography_never_surfaces(self):
        marker='ULTRAVIOLET_WREN_9341'
        self.a.chapter.goal=marker; self.a.chapter.theme=[marker.lower()]
        chapters.apply_closure(self.a,self.w,'completed',f'I completed {marker}.','fulfilled',[],500)
        self.assertEqual(self.a.memory.biography_hits(marker,'park'),[])

class SnapshotAndAbandon(unittest.TestCase):
    def test_old_and_malformed_snapshot(self):
        w,e=env(); p=snapshot.capture(e,w,e.decisions); p["schema_version"]=11
        for x in p["agents"].values(): x.pop("wishes",None); x.pop("wish_generation",None)
        w2,e2=env(); snapshot.restore(p,e2,w2,e2.decisions); self.assertTrue(all(not a.wishes for a in w2.agents.values()))
        p=snapshot.capture(e,w,e.decisions); p["agents"]["jiji"]["wishes"]=[{"bad":True}]
        snapshot.restore(p,e2,w2,e2.decisions); self.assertEqual(w2.agents["jiji"].wishes,[])

    def test_nested_malformed_wishes_are_skipped_whole(self):
        w,e=env(); a=ordinary(w); refs=material(a); mat=wishes.material_for_prompt(a,w,refs,e.decisions.secrets)
        good=wishes.install(a,e.decisions.secrets,wishes.validate_generation(proposal(refs),a,w,mat),1).to_dict()
        bad=[]
        for mutate in (
            lambda x:x['requirements'][0].update(kind='unknown'),
            lambda x:x['requirements'][0].update(threshold=math.nan),
            lambda x:x.update(progress='0.5'),
            lambda x:x.update(failure_conditions=[{'kind':'deadline'}]),
            lambda x:x.update(requirements=[]),
            lambda x:x['requirements'][0].update(target='missing-place')):
            item=copy.deepcopy(good); item['id']+='x'; mutate(item); bad.append(item)
        p=snapshot.capture(e,w,e.decisions); p['agents'][a.id]['wishes']=bad+[good]
        w2,e2=env(); snapshot.restore(p,e2,w2,e2.decisions)
        self.assertEqual([x.id for x in w2.agents[a.id].wishes],[good['id']])
        r=wishes.Requirement.from_dict({'kind':'action_count','target':'rest','threshold':2,
                                        'current':2,'completed':False})
        self.assertTrue(r.completed)

    def test_material_window_excludes_preclosure_and_biography(self):
        w,e=env(); a=ordinary(w)
        old=MemoryItem(100,'Old dominant park memory should not return after closure.',10)
        bio=MemoryItem(4*DAY_MIN,'Biography alone must not trigger generation.',9,kind='biography')
        new=MemoryItem(5*DAY_MIN,'A fresh concrete experience after closure can support a wish.',5)
        a.memory.items=[old,bio,new]
        old_ch=chapters.make_pursuit('old park matter','Old', 'Old',1)
        a.chapter_history=[chapters.ChapterRecord(old_ch.to_dict(),4,'completed','done','fulfilled')]
        got=wishes.eligible_material(a,-1,6)
        self.assertEqual([x['text'] for x in got],[new.text])
        a.chapter_history=[]; a.memory.items=[old]
        self.assertEqual(wishes.eligible_material(a,-1,100),[])
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

class WishDrive(unittest.TestCase):
    def setUp(self):
        self.world,self.engine=env(); self.a=ordinary(self.world)
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a')])
        self.a.state.location='home_a'; self.a.state.energy=80

    def test_location_drive_attempts_move_without_direct_progress(self):
        w=drive_wish(self.a,'loc-drive','location_visits','park')
        day=passing_day(self.a,w)
        now=(day-1)*DAY_MIN+12*60
        before=self.engine.decisions.router.usage.total_calls
        directive=wishes.next_wish_drive(self.a,self.world,now,'rest')
        self.assertEqual(directive['action'],'move'); self.assertEqual(directive['location'],'park')
        self.assertEqual(w.requirements[0].current,0); self.assertEqual(self.a.state.location,'home_a')
        self.assertEqual(self.engine.decisions.router.usage.total_calls,before)

    def test_social_and_money_drives_do_not_mutate_state(self):
        target=self.world.agents['ange']; target.state.location=self.a.state.location
        social=drive_wish(self.a,'social-drive','friendship',target.id,40)
        day=passing_day(self.a,social); now=(day-1)*DAY_MIN+12*60
        friendship=self.a.rel(target.id).friendship
        self.assertEqual(wishes.next_wish_drive(self.a,self.world,now,'rest')['action'],'talk_bias')
        self.assertEqual(self.a.rel(target.id).friendship,friendship)
        social.status='completed'; self.a.wishes=[]
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a'),RoutineEntry(600,'work','office')])
        self.a.profile.daily_wage=20
        money=drive_wish(self.a,'money-drive','money_gain','',10)
        day=passing_day(self.a,money); now=(day-1)*DAY_MIN+12*60
        wallet=self.a.state.money
        directive=wishes.next_wish_drive(self.a,self.world,now,'rest')
        self.assertIn(directive['action'],('move','work')); self.assertEqual(self.a.state.money,wallet)

    def test_hard_context_and_daily_cap(self):
        w=drive_wish(self.a,'cap-drive','location_visits','park')
        day=passing_day(self.a,w); now=(day-1)*DAY_MIN+12*60
        self.assertIsNone(wishes.next_wish_drive(self.a,self.world,now,'work'))
        self.assertIsNotNone(wishes.next_wish_drive(self.a,self.world,now,'rest'))
        self.assertIsNone(wishes.next_wish_drive(self.a,self.world,now+30,'rest'))
        self.assertEqual(w.drive_state['daily_attempts'],1)

    def test_multiple_requirements_rotate_reproducibly_and_major_wins(self):
        major=drive_wish(self.a,'multi-drive','location_visits','park')
        major.requirements.append(wishes.Requirement('location_visits',2,target='market'))
        small=drive_wish(self.a,'small-drive','location_visits','cafe',scale='small')
        day=passing_day(self.a,major,0); now=(day-1)*DAY_MIN+12*60
        first=wishes.next_wish_drive(self.a,self.world,now,'rest')
        self.assertEqual(first['wish_id'],major.id); self.assertEqual(first['requirement_index'],0)
        # The daily cap permits the second unmet major requirement; small cannot steal it.
        if wishes._drive_roll(self.a.id,major.id,day,1)<wishes.DRIVE_MAJOR_PROBABILITY:
            second=wishes.next_wish_drive(self.a,self.world,now+10,'rest')
            self.assertEqual((second['wish_id'],second['requirement_index']),(major.id,1))

    def test_blocked_days_are_distinct_consecutive_and_private(self):
        w=drive_wish(self.a,'blocked-drive','location_visits','park')
        w.requirements.append(wishes.Requirement('action_count',2,target='work'))
        marker='PRIVATE_TITLE_STATEMENT_MOTIVATION'; w.title=marker; w.statement=marker; w.motivation=marker
        self.world.active_effects=[{'type':'rain','location':'','until_minute':3*DAY_MIN}]
        with mock.patch.object(wishes,'_drive_roll',return_value=0):
            wishes.next_wish_drive(self.a,self.world,12*60,'rest')
            wishes.next_wish_drive(self.a,self.world,12*60+30,'rest')
        self.assertEqual(w.drive_state['blocked_days'],1)
        self.assertFalse(any('frustrating' in m.text for m in self.a.memory.items))
        with mock.patch.object(wishes,'_drive_roll',return_value=0):
            wishes.next_wish_drive(self.a,self.world,DAY_MIN+12*60,'rest')
            wishes.next_wish_drive(self.a,self.world,DAY_MIN+12*60+30,'rest')
        notes=[m for m in self.a.memory.items if 'frustrating' in m.text]
        self.assertEqual(len(notes),1); self.assertNotIn(marker,notes[0].text)
        self.assertEqual(notes[0].kind,'reflection'); self.assertEqual(notes[0].tags,[f'wish:{w.id}'])
        self.assertEqual(w.drive_state['blocked_days'],0)

    def test_success_resets_blocked_streak_and_gap_does_not_extend_it(self):
        w=drive_wish(self.a,'reset-drive','location_visits','park')
        wishes._blocked_memory(self.a,w,12*60)
        wishes._blocked_memory(self.a,w,2*DAY_MIN+12*60)
        self.assertEqual(w.drive_state['blocked_days'],1)
        day=next(d for d in range(4,80) if wishes._drive_roll(self.a.id,w.id,d,0)<wishes.DRIVE_MAJOR_PROBABILITY)
        now=(day-1)*DAY_MIN+12*60
        directive=wishes.next_wish_drive(self.a,self.world,now,'rest')
        self.assertIsNotNone(directive); self.assertEqual(w.drive_state['blocked_days'],0)
        wishes._blocked_memory(self.a,w,now+2*DAY_MIN)
        self.assertEqual(w.drive_state['blocked_days'],1)

    def test_blocked_day_roundtrip_prevents_same_day_double_count(self):
        w=drive_wish(self.a,'roundtrip-blocked','location_visits','park')
        now=12*60; wishes._blocked_memory(self.a,w,now)
        payload=snapshot.capture(self.engine,self.world,self.engine.decisions)
        w2,e2=env(); snapshot.restore(payload,e2,w2,e2.decisions)
        restored=w2.agents[self.a.id].wishes[0]
        wishes._blocked_memory(w2.agents[self.a.id],restored,now+30)
        self.assertEqual(restored.drive_state['blocked_days'],1)

    def test_v13_and_malformed_blocked_day_restore_safely(self):
        w=drive_wish(self.a,'old-drive','location_visits','park')
        wishes._blocked_memory(self.a,w,12*60)
        payload=snapshot.capture(self.engine,self.world,self.engine.decisions)
        payload['schema_version']=13
        del payload['agents'][self.a.id]['wishes'][0]['drive_state']['last_blocked_day']
        w2,e2=env(); snapshot.restore(payload,e2,w2,e2.decisions)
        self.assertEqual(w2.agents[self.a.id].wishes[0].drive_state['last_blocked_day'],-1)
        payload=snapshot.capture(self.engine,self.world,self.engine.decisions)
        payload['agents'][self.a.id]['wishes'][0]['drive_state']['last_blocked_day']='bad'
        snapshot.restore(payload,e2,w2,e2.decisions)
        self.assertEqual(w2.agents[self.a.id].wishes[0].drive_state['last_blocked_day'],-1)

    def test_action_work_moves_then_work_event_advances_without_llm(self):
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a'),RoutineEntry(600,'work','office')])
        w=drive_wish(self.a,'work-drive','action_count','work',1)
        day=passing_day(self.a,w); now=(day-1)*DAY_MIN+12*60
        before=self.engine.decisions.router.usage.total_calls
        move=wishes.next_wish_drive(self.a,self.world,now,'rest')
        self.assertEqual((move['action'],move['location']),('move','office'))
        self.assertEqual(w.requirements[0].current,0)
        arrived=self.world.execute(self.a,'move','office',now,10)
        self.assertEqual(arrived['verb'],'arrive'); self.assertEqual(w.requirements[0].current,0)
        work=wishes.next_wish_drive(self.a,self.world,now+20,'rest')
        self.assertEqual(work['action'],'work'); self.assertEqual(w.requirements[0].current,0)
        result=self.world.execute(self.a,'work',None,now+20,30)
        event=Event(now+20,'action',result['verb'],actor=self.a.id,location=result['location'])
        self.assertTrue(wishes.update_from_event(w,self.a,event,day))
        self.assertEqual(w.requirements[0].current,1)
        self.assertEqual(self.engine.decisions.router.usage.total_calls,before)

    def test_action_work_blocks_when_workplace_is_closed(self):
        self.a.routine=Routine([RoutineEntry(0,'rest','home_a'),RoutineEntry(600,'work','cafe')])
        w=drive_wish(self.a,'closed-work','action_count','work',1)
        self.world.locations['cafe'].closed_days=[0]
        now=12*60
        with mock.patch.object(wishes,'_drive_roll',return_value=0):
            self.assertIsNone(wishes.next_wish_drive(self.a,self.world,now,'rest'))
        self.assertEqual(w.drive_state['blocked_days'],1)
        self.assertEqual(w.requirements[0].current,0)

if __name__ == "__main__": unittest.main()
