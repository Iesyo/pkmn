import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const bridge = path.join(root, "battle_lab", "nana_self_critic.py");
const nursery = path.join(root, "battle_lab", "nana_nursery.py");
const report = path.join(root, "battle_lab", "nana_nursery_report.py");

test("Nana self-critic bridges generation 6 to 8 only through audited benign skip 7", () => {
  const script = String.raw`
from battle_lab.nana_self_critic import extract_observations

teacher = {
    'key': 'teacher-1',
    'format': 'regmc',
    'checkpointSha256': 'abc',
}
before = {
    'turn': 5,
    'ownTeam': [{'species':'Human','hp':100,'fainted':False,'status':None}],
    'opponentTeam': [{'species':'Nana','hp':100,'fainted':False,'status':None}],
    'ownActive': [{'species':'Human'}],
    'opponentActive': [{'species':'Nana'}],
}
after = {
    **before,
    'turn': 6,
    'ownTeam': [{'species':'Human','hp':70,'fainted':False,'status':None}],
}
nana_action = {
    'first': {'kind':'move','value':'woodhammer','target':1,'flags':[]},
    'second': {'kind':'move','value':'muddywater','target':0,'flags':[]},
}
light = {'value': 0.1, 'branches': []}
base_events = [
    {'timestamp':'2026-09-16T00:07:48Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':5,'generation':6,'state':before,'modelAction':nana_action,
        'modelActor':'nana','teacher':teacher,'light':light,
    }},
    {'timestamp':'2026-09-16T00:08:23Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':6,'generation':8,'state':after,'modelAction':{'bridge':'light'},
        'modelActor':'light','teacher':teacher,'light':light,
    }},
]

assert extract_observations(base_events, actor_filter='nana') == []

bridged = [
    base_events[0],
    {'timestamp':'2026-09-16T00:08:00Z','sessionId':'s','type':'nana_nursery_skip','payload':{
        'turn':5,
        'reason':'model-only-force-switch-no-human-prompt',
        'promotionBlocking':False,
        'details':{'generation':7,'phase':'resolving'},
    }},
    base_events[1],
]
observations = extract_observations(bridged, actor_filter='nana')
assert len(observations) == 1
assert observations[0]['generation'] == 6
assert observations[0]['continuity']['kind'] == 'benign-skip-bridge'
assert observations[0]['continuity']['skippedGenerations'] == [7]
assert observations[0]['continuity']['skipReasons'] == ['model-only-force-switch-no-human-prompt']
assert observations[0]['delta'] > 0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana self-critic rejects unexplained or non-bridgeable generation gaps", () => {
  const script = String.raw`
from battle_lab.nana_self_critic import extract_observations

state = {
    'turn': 5,
    'ownTeam': [{'species':'Human','hp':100,'fainted':False,'status':None}],
    'opponentTeam': [{'species':'Nana','hp':100,'fainted':False,'status':None}],
    'ownActive': [{'species':'Human'}],
    'opponentActive': [{'species':'Nana'}],
}
events = [
    {'timestamp':'2026-09-16T00:00:00Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':5,'generation':6,'state':state,'modelAction':{'first':{'kind':'move','value':'x'}},'modelActor':'nana','light':{},
    }},
    {'timestamp':'2026-09-16T00:00:01Z','sessionId':'s','type':'nana_nursery_skip','payload':{
        'turn':5,'reason':'prechoice-sync-timeout','promotionBlocking':False,'details':{'generation':7},
    }},
    {'timestamp':'2026-09-16T00:00:02Z','sessionId':'s','type':'turn_choice','payload':{
        'turn':6,'generation':8,'state':{**state,'turn':6},'modelAction':{'first':{'kind':'move','value':'y'}},'modelActor':'light','light':{},
    }},
]
assert extract_observations(events, actor_filter='nana') == []
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nursery and report are wired to skip-aware self critic and compile", () => {
  const nurserySource = readFileSync(nursery, "utf8");
  const reportSource = readFileSync(report, "utf8");
  assert.match(nurserySource, /from battle_lab\.nana_self_critic import build_actor_summary, extract_observations/);
  assert.match(reportSource, /from battle_lab\.nana_self_critic import extract_observations/);
  assert.match(reportSource, /bridgedObservations/);
  for (const filename of [bridge, nursery, report]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
