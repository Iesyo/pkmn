import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("CoachMemory persists literal user advice, rebuilds, and revokes append-only", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_recorder import NanaRecorder
from battle_lab.nana_coach_memory import CoachMemory

with TemporaryDirectory() as tmp:
    recorder=NanaRecorder(Path(tmp),profile_id='ies')
    store=CoachMemory(recorder)
    advice=store.create_species_mechanic(
        text='Con Garchomp, si Mega es legal, priorizar Mega salvo razón táctica fuerte.',
        species='Garchomp',
        mechanic='Mega',
    )
    assert advice['text'].startswith('Con Garchomp')
    assert advice['scope']=={'level':'species','species':'garchomp'}
    assert advice['effect']['target']['flag']=='Mega'
    assert advice['effect']['mode']=='soft'
    assert len(store.active())==1

    rebuilt=CoachMemory(recorder)
    assert rebuilt.active()[0]['adviceId']==advice['adviceId']
    assert rebuilt.active()[0]['text']==advice['text']

    rebuilt.revoke(advice['adviceId'])
    assert rebuilt.active()==[]
    events=[e for e in recorder.iter_events() if e['type'].startswith('coach_advice_')]
    assert [e['type'] for e in events]==['coach_advice_created','coach_advice_revoked']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Garchomp Mega tip becomes a bounded coach term only when the legal condition is true", () => {
  const script = String.raw`
from battle_lab.nana_coach_memory import coach_context, coach_terms_for_action

advice={
 'adviceId':'tip1','status':'active','scope':{'level':'species','species':'garchomp'},
 'condition':{'kind':'mechanic-legal-for-active-species','mechanic':'Mega'},
 'effect':{'kind':'prefer','target':{'slotSpecies':'garchomp','flag':'Mega'},'strength':0.20,'mode':'soft'},
 'priority':1.0,'confidence':0.50,
}
state={'ownActive':[{'species':'Garchomp'},{'species':'Incineroar'}]}
normal={'first':{'kind':'move','value':'earthquake','flags':[]},'second':{'kind':'move','value':'fakeout','flags':[]}}
mega={'first':{'kind':'move','value':'earthquake','flags':['Mega']},'second':{'kind':'move','value':'fakeout','flags':[]}}
ctx=coach_context({'advices':[advice]},model_state=state,legal_actions=[normal,mega])
assert [a['adviceId'] for a in ctx['applicable']]==['tip1']
terms, ids=coach_terms_for_action(ctx,model_state=state,action=mega)
assert ids==['tip1'] and len(terms)==1
assert terms[0].name=='coach:tip1'
assert abs(terms[0].value-0.20)<1e-9
assert terms[0].score_space=='board-delta-v1'
terms, ids=coach_terms_for_action(ctx,model_state=state,action=normal)
assert terms==[] and ids==[]

ctx=coach_context({'advices':[advice]},model_state={'ownActive':[{'species':'Salamence'}]},legal_actions=[mega])
assert ctx['applicable']==[]
assert [a['adviceId'] for a in ctx['conditionFalse']]==['tip1']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("CoachMemory can move N4 away from the LIGHT reference but cannot invent an illegal candidate", () => {
  const script = String.raw`
from battle_lab.nana_contracts import order_key
from battle_lab.nana_counter_calibration import CounterCalibration
from battle_lab.nana_legal_orders import LegalOrderSet, NormalizedCandidate
from battle_lab.nana_n4_shadow import build_n4_shadow_plan

ref_action={'first':{'kind':'move','value':'protect','target':0,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
mega_action={'first':{'kind':'move','value':'earthquake','target':1,'flags':['Mega']},'second':{'kind':'move','value':'fakeout','target':1,'flags':[]}}
ref_key=order_key(ref_action); mega_key=order_key(mega_action)
legal=LegalOrderSet(True,'ok','x',1,(
    NormalizedCandidate(ref_key,ref_action,'ref',(1,1),object()),
    NormalizedCandidate(mega_key,mega_action,'mega',(2,1),object()),
),(2,2),2,2)
light={'jointScores':[{'selectedByLight':True,'action':ref_action}]}
cal=CounterCalibration(1,'x','response-utility-v1','board-delta-v1',False,'nope',0,0,0,0,0,'x')
coach={'advices':[{
 'adviceId':'tip1','status':'active','scope':{'level':'species','species':'garchomp'},
 'condition':{'kind':'mechanic-legal-for-active-species','mechanic':'Mega'},
 'effect':{'kind':'prefer','target':{'slotSpecies':'garchomp','flag':'Mega'},'strength':0.20,'mode':'soft'},
 'priority':1.0,'confidence':0.50,
}]}
plan=build_n4_shadow_plan(
    legal_orders=legal,light=light,prediction={'confidence':0.0,'candidates':[]},
    model_state={'turn':1,'ownActive':[{'species':'Garchomp'},{'species':'Incineroar'}]},
    self_summary={'buckets':{}},counter_calibration=cal,coach_memory=coach,
)
assert plan['eligible'] is True
assert plan['selectedKey']==mega_key
assert plan['wouldChange'] is True
assert plan['coach']['matchedSelected']==['tip1']
assert set(item['orderKey'] for item in plan['top']).issubset({ref_key,mega_key})
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("CoachMemory routes and UI are wired without exposing model-native indices", () => {
  const service = readFileSync(path.join(root, "battle_lab", "local_sparring_service.py"), "utf8");
  const proxy = readFileSync(path.join(root, "app", "api", "battle-lab", "[...path]", "route.ts"), "utf8");
  const runtime = readFileSync(path.join(root, "battle_lab", "nana_stage2_nursery_runtime.py"), "utf8");
  const ui = readFileSync(path.join(root, "components", "vgc", "war-room-sparring", "index.tsx"), "utf8");

  assert.match(service, /@app\.get\("\/nana\/advice"\)/);
  assert.match(service, /@app\.post\("\/nana\/advice"\)/);
  assert.match(service, /@app\.post\("\/nana\/advice\/\{advice_id\}\/revoke"\)/);
  assert.match(proxy, /nana\\\/advice/);
  assert.match(runtime, /CoachMemory\(self\.nana\)/);
  assert.match(runtime, /coach_memory=service\.nana_coach_memory\.list\(\)/);
  assert.match(runtime, /coach_advice_applied/);
  assert.match(runtime, /coach_advice_skipped/);
  assert.match(ui, /CoachMemory/);
  assert.match(ui, /Confirmar y guardar tip/);
  assert.match(ui, /Regla ejecutable/);
  assert.doesNotMatch(readFileSync(path.join(root, "battle_lab", "nana_coach_memory.py"), "utf8"), /action_map|get_logits/);
});
