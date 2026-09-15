import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const nursery = path.join(root, "battle_lab", "nana_nursery.py");
const runtime = path.join(root, "battle_lab", "nana_stage2_nursery_runtime.py");
const lanRuntime = path.join(
  root,
  "battle_lab",
  "nana_stage2_nursery_lan_runtime.py",
);
const report = path.join(root, "battle_lab", "nana_nursery_report.py");
const critic = path.join(root, "battle_lab", "nana_light_critic.py");
const teacher = path.join(root, "battle_lab", "nana_teacher.py");

test("Nursery selects only a positive near-LIGHT candidate inside live cap", () => {
  const script = String.raw`
from battle_lab.nana_nursery import choose_candidate
plan={
 'eligible':True,'confidence':0.20,'confidenceScale':1.0,
 'canonical':{'expectedCounter':0.0},
 'candidatePool':[
   {'selectedByLight':True,'expectedCounter':0.0,'lightRegretLog':0.0,'indices':[1,1]},
   {'selectedByLight':False,'expectedCounter':0.5,'lightRegretLog':-0.05,'probability':0.2,'indices':[2,2]},
   {'selectedByLight':False,'expectedCounter':1.0,'lightRegretLog':-0.20,'probability':0.2,'indices':[3,3]},
 ]}
result=choose_candidate(plan, light_trust={'trust':0.9,'confidence':0.1}, self_trust={'trust':0.5,'confidence':0.0})
assert result['intervene'] is True
assert result['candidate']['indices'] == [2,2]
assert result['requiredLambdaCap'] <= 0.15
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("high-confidence LIGHT trust can veto Nana", () => {
  const script = String.raw`
from battle_lab.nana_nursery import choose_candidate
plan={'eligible':True,'confidence':0.5,'confidenceScale':1.0,'canonical':{'expectedCounter':0.0},'candidatePool':[]}
r=choose_candidate(plan, light_trust={'trust':0.99,'confidence':0.8}, self_trust={'trust':0.5,'confidence':0.0})
assert r['intervene'] is False
assert r['reason'] == 'light-critic-high-trust-veto'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana self-critic can veto repeating a bad learned context", () => {
  const script = String.raw`
from battle_lab.nana_nursery import choose_candidate
plan={'eligible':True,'confidence':0.5,'confidenceScale':1.0,'canonical':{'expectedCounter':0.0},'candidatePool':[]}
r=choose_candidate(plan, light_trust={'trust':0.8,'confidence':0.2}, self_trust={'trust':0.2,'confidence':0.5})
assert r['intervene'] is False
assert r['reason'] == 'nana-self-low-trust-veto'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("teacher-aware critic isolates different LIGHT checkpoints", () => {
  const script = String.raw`
from battle_lab.nana_light_critic import build_summary, trust_for, model_perspective

def session(sid, key, lose_light):
  teacher={'key':key,'legacyKey':'legacy','format':'fmt','checkpoint':'x','checkpointSha256':key}
  before={'turn':1,'ownTeam':[{'species':'H','hp':100,'fainted':False,'status':None}], 'opponentTeam':[{'species':'L','hp':100,'fainted':False,'status':None}], 'ownActive':[{'species':'H'}], 'opponentActive':[{'species':'L'}]}
  final={**before,'turn':2}
  if lose_light:
    final['opponentTeam']=[{'species':'L','hp':0,'fainted':True,'status':None}]
  else:
    final['ownTeam']=[{'species':'H','hp':0,'fainted':True,'status':None}]
  light={'canonicalAction':{'labels':['move 1','move 2']},'branches':[{'scores':[{'selected':True,'probability':0.8}]},{'scores':[{'selected':True,'probability':0.8}]}]}
  return [
    {'timestamp':'2026-01-01T00:00:00Z','sessionId':sid,'type':'nana_teacher_version','payload':{'teacher':teacher}},
    {'timestamp':'2026-01-01T00:00:01Z','sessionId':sid,'type':'turn_choice','payload':{'turn':1,'state':before,'modelAction':light['canonicalAction'],'modelActor':'light','teacher':teacher,'light':light}},
    {'timestamp':'2026-01-01T00:00:02Z','sessionId':sid,'type':'session_end','payload':{'finalState':final,'result':{'winner':'human' if lose_light else 'model'}}},
  ]
events=[]
for i in range(8): events += session(f'a{i}','A',True)
for i in range(8): events += session(f'b{i}','B',False)
summary=build_summary(events)
assert 'A' in summary['teachers'] and 'B' in summary['teachers']
state=model_perspective(events[1]['payload']['state'])
light=events[1]['payload']['light']
a=trust_for(summary,state,light['canonicalAction'],light,teacher_key='A')
b=trust_for(summary,state,light['canonicalAction'],light,teacher_key='B')
assert a['trust'] < b['trust']
assert a['teacherSource'] == 'exact' and b['teacherSource'] == 'exact'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("latest teacher is selected by timestamp, never random session filename order", () => {
  const script = String.raw`
from battle_lab.nana_teacher import latest_teacher_from_events

events=[
 {'timestamp':'2026-09-15T12:00:00Z','sessionId':'00ff','type':'nana_teacher_version','payload':{'teacher':{'key':'NEW'}}},
 {'timestamp':'2026-01-01T12:00:00Z','sessionId':'ff00','type':'nana_teacher_version','payload':{'teacher':{'key':'OLD'}}},
]
assert latest_teacher_from_events(events)['key'] == 'NEW'
assert latest_teacher_from_events(list(reversed(events)))['key'] == 'NEW'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("critic rejects multi-turn attribution gaps and accepts same-turn forced-switch adjacency", () => {
  const script = String.raw`
from battle_lab.nana_light_critic import extract_observations

state=lambda turn,hp: {
 'turn':turn,
 'ownTeam':[{'species':'H','hp':100,'fainted':False,'status':None}],
 'opponentTeam':[{'species':'L','hp':hp,'fainted':hp <= 0,'status':None}],
 'ownActive':[{'species':'H'}], 'opponentActive':[{'species':'L'}],
}
action={'first':{'kind':'move','value':'x','target':1,'flags':[]},'second':{'kind':'move','value':'y','target':2,'flags':[]}}
light={'canonicalAction':{'labels':['move 1','move 2']},'branches':[{'scores':[{'selected':True,'probability':0.8}]},{'scores':[{'selected':True,'probability':0.8}]}]}

gap=[
 {'timestamp':'2026-01-01T00:00:01Z','sessionId':'gap','type':'turn_choice','payload':{'turn':1,'state':state(1,100),'modelAction':action,'modelActor':'light','light':light}},
 {'timestamp':'2026-01-01T00:00:03Z','sessionId':'gap','type':'turn_choice','payload':{'turn':3,'state':state(3,0),'modelAction':action,'modelActor':'light','light':light}},
]
assert extract_observations(gap, actor_filter='light') == []

forced=[
 {'timestamp':'2026-01-01T00:00:01Z','sessionId':'forced','type':'turn_choice','payload':{'generation':1,'turn':1,'state':state(1,100),'modelAction':action,'modelActor':'light','light':light}},
 {'timestamp':'2026-01-01T00:00:02Z','sessionId':'forced','type':'turn_choice','payload':{'generation':2,'turn':1,'state':state(1,0),'modelAction':action,'modelActor':'light','light':light}},
 {'timestamp':'2026-01-01T00:00:03Z','sessionId':'forced','type':'session_end','payload':{'finalState':state(2,0),'result':{'winner':'model'}}},
]
obs=extract_observations(forced, actor_filter='light')
assert len(obs) == 2
assert obs[0]['turn'] == 1 and obs[1]['turn'] == 1
assert obs[0]['id'] != obs[1]['id']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("promotion errors are teacher-scoped and an ancient failure does not latch forever", () => {
  const script = String.raw`
from battle_lab.nana_nursery import promotion_status

teacher={'key':'K'}
events=[
 {'timestamp':'2026-01-01T00:00:00Z','sessionId':'old','type':'nana_nursery_error','payload':{'teacher':teacher,'error':'old'}},
]
for i in range(20):
  events.append({'timestamp':f'2026-02-{i+1:02d}T00:00:00Z','sessionId':f's{i}','type':'nana_nursery_decision','payload':{'teacher':teacher,'intervened':True,'selection':{'candidate':{'lightRegretLog':-0.01}}}})
events.append({'timestamp':'2026-03-01T00:00:00Z','sessionId':'x','type':'nana_nursery_error','payload':{'teacher':{'key':'OTHER'},'error':'other'}})
r=promotion_status(events, teacher_key='K')
assert r['errors'] == 0

events.append({'timestamp':'2026-03-02T00:00:00Z','sessionId':'s19','type':'nana_nursery_error','payload':{'teacher':teacher,'error':'recent'}})
r=promotion_status(events, teacher_key='K')
assert r['errors'] == 1
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nursery runtime records actual actor and keeps one-intervention wheels", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /MAX_INTERVENTIONS_PER_BATTLE/);
  assert.match(source, /modelActor/);
  assert.match(source, /nana_nursery_decision/);
  assert.match(source, /fallback.*LIGHT/s);
  assert.match(source, /automaticPromotion.*False/s);
  assert.match(source, /DoublesEnv\.action_to_order/);
});

test("Nursery preview and commit boundary cannot silently relabel a LIGHT fallback as Nana", () => {
  const source = readFileSync(runtime, "utf8");
  const preview = source.indexOf('getattr(current, "teampreview", False)');
  const inspect = source.indexOf("inspect_light_decision(");
  const commit = source.indexOf("# Commit point:");
  const lastLightFallback = source.lastIndexOf("return super().choose_move(current)");
  const budget = source.indexOf("service._nana_nursery_interventions[session.id] = used + 1");
  assert.ok(preview >= 0 && preview < inspect);
  assert.ok(commit >= 0 && lastLightFallback >= 0 && lastLightFallback < commit);
  assert.ok(budget > commit);
  assert.match(source, /nana_nursery_recording_error/);
});

test("LAN launcher requires no client-side install and exposes Nursery live", () => {
  const source = readFileSync(lanRuntime, "utf8");
  assert.match(source, /install_nursery_service/);
  assert.match(source, /1 intervención near-LIGHT/);
  assert.match(source, /segunda PC solo abre la URL Network/);
});

test("Nursery and teacher modules compile", () => {
  for (const filename of [nursery, runtime, lanRuntime, report, critic, teacher]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
