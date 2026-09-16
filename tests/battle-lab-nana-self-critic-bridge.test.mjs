import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const critic = path.join(root, "battle_lab", "nana_self_critic.py");
const nursery = path.join(root, "battle_lab", "nana_nursery.py");
const report = path.join(root, "battle_lab", "nana_nursery_report.py");

function run(script) {
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
}

test("Nana self-critic uses generation+1 human-only prompt as the immediate after-state", () => {
  const script = String.raw`
from battle_lab.nana_self_critic import extract_observations

teacher = {'key':'teacher-1','format':'regmc','checkpointSha256':'abc'}
before = {
    'turn': 5,
    'ownTeam': [{'species':'Human','hp':100,'fainted':False,'status':None}],
    'opponentTeam': [{'species':'Nana','hp':100,'fainted':False,'status':None}],
    'ownActive': [{'species':'Human'}],
    'opponentActive': [{'species':'Nana'}],
}
after = {
    **before,
    'turn': 5,
    'ownTeam': [{'species':'Human','hp':70,'fainted':False,'status':None}],
}
nana_action = {
    'first': {'kind':'move','value':'woodhammer','target':1,'flags':[]},
    'second': {'kind':'move','value':'muddywater','target':0,'flags':[]},
}
events = [
    {'timestamp':'2026-09-16T00:07:48.579Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':5,'generation':6,'state':before,'modelAction':nana_action,
        'modelActor':'nana','teacher':teacher,'light':{},
    }},
    # Generation 7 is a human-only same-turn prompt. It has no model turn_choice,
    # but human_choice_observed carries the pre-choice state we need.
    {'timestamp':'2026-09-16T00:08:12.098Z','sessionId':'s','type':'human_choice_observed','payload':{
        'turn':5,'generation':7,'state':after,'legalActions':[{'id':'7:1'}],
        'action':{'id':'7:1'},'prediction':{},
    }},
    {'timestamp':'2026-09-16T00:08:23.439Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':6,'generation':8,'state':{**after,'turn':6},
        'modelAction':{'first':{'kind':'move','value':'light'}},
        'modelActor':'light','teacher':teacher,'light':{},
    }},
]
obs = extract_observations(events, actor_filter='nana')
assert len(obs) == 1
assert obs[0]['generation'] == 6
assert obs[0]['continuity']['kind'] == 'next-human-prompt'
assert obs[0]['continuity']['fromGeneration'] == 6
assert obs[0]['continuity']['toGeneration'] == 7
assert obs[0]['continuity']['fromTurn'] == 5
assert obs[0]['continuity']['toTurn'] == 5
assert obs[0]['delta'] > 0
`;
  run(script);
});

test("Nana self-critic refuses to jump from generation 6 directly to generation 8", () => {
  const script = String.raw`
from battle_lab.nana_self_critic import extract_observations

state = {
    'turn':5,
    'ownTeam':[{'species':'Human','hp':100,'fainted':False,'status':None}],
    'opponentTeam':[{'species':'Nana','hp':100,'fainted':False,'status':None}],
    'ownActive':[{'species':'Human'}],
    'opponentActive':[{'species':'Nana'}],
}
events = [
    {'timestamp':'2026-09-16T00:00:00Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':5,'generation':6,'state':state,
        'modelAction':{'first':{'kind':'move','value':'x'}},'modelActor':'nana','light':{},
    }},
    {'timestamp':'2026-09-16T00:00:02Z','sessionId':'s','type':'human_choice_observed','payload':{
        'turn':6,'generation':8,'state':{**state,'turn':6},'action':{'id':'8:1'},'prediction':{},
    }},
    {'timestamp':'2026-09-16T00:00:03Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':6,'generation':8,'state':{**state,'turn':6},
        'modelAction':{'first':{'kind':'move','value':'y'}},'modelActor':'light','light':{},
    }},
]
assert extract_observations(events, actor_filter='nana') == []
`;
  run(script);
});

test("Nana self-critic may use session_end after the real same-generation human observation", () => {
  const script = String.raw`
from battle_lab.nana_self_critic import extract_observations

before = {
    'turn':9,
    'ownTeam':[{'species':'Human','hp':40,'fainted':False,'status':None}],
    'opponentTeam':[{'species':'Nana','hp':100,'fainted':False,'status':None}],
    'ownActive':[{'species':'Human'}],
    'opponentActive':[{'species':'Nana'}],
}
final = {
    **before,
    'turn':9,
    'ownTeam':[{'species':'Human','hp':0,'fainted':True,'status':None}],
    'finished':True,
    'won':False,
    'lost':True,
}
events = [
    {'timestamp':'2026-09-16T00:00:00Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':9,'generation':12,'state':before,
        'modelAction':{'first':{'kind':'move','value':'finish'}},'modelActor':'nana','light':{},
    }},
    # Real runtime ordering: the same generation's observed human choice is
    # appended after the model turn_choice and must not block terminal learning.
    {'timestamp':'2026-09-16T00:00:00.500Z','sessionId':'s','type':'human_choice_observed','payload':{
        'turn':9,'generation':12,'state':before,'action':{'id':'12:1'},'prediction':{},
    }},
    {'timestamp':'2026-09-16T00:00:01Z','sessionId':'s','type':'session_end','payload':{
        'finalState':final,'result':{'winner':'model'},
    }},
]
obs = extract_observations(events, actor_filter='nana')
assert len(obs) == 1
assert obs[0]['terminal'] is True
assert obs[0]['continuity']['kind'] == 'session-end'
assert obs[0]['delta'] > 0
`;
  run(script);
});

test("Nursery/report use prompt-aware self critic and modules compile", () => {
  const nurserySource = readFileSync(nursery, "utf8");
  const reportSource = readFileSync(report, "utf8");
  const criticSource = readFileSync(critic, "utf8");
  assert.match(nurserySource, /from battle_lab\.nana_self_critic import build_actor_summary, extract_observations/);
  assert.match(reportSource, /from battle_lab\.nana_self_critic import extract_observations/);
  assert.match(reportSource, /promptLinkedObservations/);
  assert.match(criticSource, /human_choice_observed/);
  assert.match(criticSource, /next-human-prompt/);
  assert.doesNotMatch(criticSource, /BRIDGEABLE_SKIP_REASONS/);
  for (const filename of [critic, nursery, report]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
