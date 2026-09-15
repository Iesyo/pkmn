import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const critic = path.join(root, "battle_lab", "nana_light_critic.py");
const runtime = path.join(root, "battle_lab", "nana_stage2_shadow_v22_runtime.py");
const lanRuntime = path.join(
  root,
  "battle_lab",
  "nana_stage2_shadow_v22_lan_runtime.py",
);
const report = path.join(root, "battle_lab", "nana_light_critic_report.py");

test("LIGHT critic scores model-perspective board transitions", () => {
  const script = String.raw`
from battle_lab.nana_light_critic import model_perspective, transition_outcome

before_human = {
    'turn': 1,
    'ownTeam': [
        {'species':'Raichu','hp':100,'fainted':False,'status':None},
        {'species':'Milotic','hp':100,'fainted':False,'status':None},
    ],
    'opponentTeam': [
        {'species':'Garchomp','hp':100,'fainted':False,'status':None},
        {'species':'Incineroar','hp':100,'fainted':False,'status':None},
    ],
    'ownActive': [{'species':'Raichu'}],
    'opponentActive': [{'species':'Garchomp'}],
}
after_human = {
    **before_human,
    'turn': 2,
    'ownTeam': [
        {'species':'Raichu','hp':0,'fainted':True,'status':None},
        {'species':'Milotic','hp':75,'fainted':False,'status':None},
    ],
}
before_model = model_perspective(before_human)
after_model = model_perspective(after_human)
assert before_model['ownTeam'][0]['species'] == 'Garchomp'
outcome = transition_outcome(before_model, after_model)
assert outcome['label'] == 'positive'
assert outcome['delta'] > 0
assert outcome['outcomeScore'] == 1.0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LIGHT critic backfills append-only history and keeps causal guardrail", () => {
  const script = String.raw`
from battle_lab.nana_light_critic import build_summary

base = {
    'ownTeam': [{'species':'HumanMon','hp':100,'fainted':False,'status':None}],
    'opponentTeam': [{'species':'LightMon','hp':100,'fainted':False,'status':None}],
    'ownActive': [{'species':'HumanMon'}],
    'opponentActive': [{'species':'LightMon'}],
}
after = {
    **base,
    'turn': 2,
    'ownTeam': [{'species':'HumanMon','hp':0,'fainted':True,'status':None}],
}
light = {
    'value': 0.2,
    'canonicalAction': {'labels':['move 1','move 2']},
    'branches': [
        {'scores':[{'selected':True,'probability':0.8}]},
        {'scores':[{'selected':True,'probability':0.7}]},
    ],
}
events = [
    {'timestamp':'2026-01-01T00:00:00Z','sessionId':'s1','type':'turn_choice','payload':{'turn':1,'state':{**base,'turn':1},'modelAction':light['canonicalAction'],'light':light}},
    {'timestamp':'2026-01-01T00:00:01Z','sessionId':'s1','type':'session_end','payload':{'finalState':after,'result':{'winner':'model'}}},
]
summary = build_summary(events)
assert summary['observations'] == 1
assert summary['labels']['positive'] == 1
assert summary['causalStatus'] == 'observational-only'
assert summary['influence'] == 0.0
assert summary['global']['trust'] > summary['prior']['trust']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("repeated bad LIGHT outcomes lower trust without changing policy", () => {
  const script = String.raw`
from battle_lab.nana_light_critic import PRIOR_TRUST, build_summary

light = {
    'canonicalAction': {'labels':['move 1','move 2']},
    'branches': [
        {'scores':[{'selected':True,'probability':0.8}]},
        {'scores':[{'selected':True,'probability':0.8}]},
    ],
}
events=[]
for i in range(12):
    before={
        'turn':1,
        'ownTeam':[{'species':'HumanMon','hp':100,'fainted':False,'status':None}],
        'opponentTeam':[{'species':'LightMon','hp':100,'fainted':False,'status':None}],
        'ownActive':[{'species':'HumanMon'}],
        'opponentActive':[{'species':'LightMon'}],
    }
    final={
        **before,
        'turn':2,
        'opponentTeam':[{'species':'LightMon','hp':0,'fainted':True,'status':None}],
    }
    sid=f's{i}'
    events.append({'timestamp':f'2026-01-01T00:00:{i:02d}Z','sessionId':sid,'type':'turn_choice','payload':{'turn':1,'state':before,'modelAction':light['canonicalAction'],'light':light}})
    events.append({'timestamp':f'2026-01-01T00:01:{i:02d}Z','sessionId':sid,'type':'session_end','payload':{'finalState':final,'result':{'winner':'human'}}})
summary=build_summary(events)
assert summary['observations'] == 12
assert summary['labels']['negative'] == 12
assert summary['global']['trust'] < PRIOR_TRUST
assert summary['influence'] == 0.0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("v2.2 runtime backfills and rebuilds critic while preserving shadow-only LIGHT", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /nana2-shadow-v2\.2-light-critic/);
  assert.match(source, /rebuild_for_recorder/);
  assert.match(source, /historicalBackfill/);
  assert.match(source, /causalStatus/);
  assert.match(source, /"influence": 0\.0/);
  assert.doesNotMatch(source, /future\.set_result\(.+recommended/);
});

test("LAN entrypoint and critic report stay separate from policy logic", () => {
  const lan = readFileSync(lanRuntime, "utf8");
  const rep = readFileSync(report, "utf8");
  assert.match(lan, /install_light_critic_service/);
  assert.match(lan, /influence=0\.0/);
  assert.match(rep, /observational; not causal proof/);
  assert.match(rep, /recent30/);
});

test("Nana LIGHT critic modules compile", () => {
  for (const filename of [critic, runtime, lanRuntime, report]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
