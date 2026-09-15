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
const lanRuntime = path.join(root, "battle_lab", "nana_stage2_nursery_lan_runtime.py");
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

test("Nursery runtime records actual actor and keeps one-intervention wheels", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /MAX_INTERVENTIONS_PER_BATTLE/);
  assert.match(source, /modelActor/);
  assert.match(source, /nana_nursery_decision/);
  assert.match(source, /fallback.*LIGHT/s);
  assert.match(source, /automaticPromotion.*False/s);
  assert.match(source, /DoublesEnv\.action_to_order/);
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
