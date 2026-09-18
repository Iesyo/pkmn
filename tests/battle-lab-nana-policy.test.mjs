import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const facade = path.join(root, "battle_lab", "nana_policy.py");
const adapter = path.join(root, "battle_lab", "nana_teacher_adapter.py");
const contracts = path.join(root, "battle_lab", "nana_contracts.py");
const teacher = path.join(root, "battle_lab", "nana_teacher.py");
const python = process.env.PYTHON ?? "python";

test("Nana policy is a compatibility facade and model-aware code is isolated in TeacherAdapter", () => {
  const facadeSource = readFileSync(facade, "utf8");
  const adapterSource = readFileSync(adapter, "utf8");

  assert.match(facadeSource, /from battle_lab\.nana_teacher_adapter import inspect_light_decision/);
  assert.doesNotMatch(facadeSource, /action_map/);
  assert.doesNotMatch(facadeSource, /_update_mask/);

  assert.match(adapterSource, /class TeacherAdapter\(Protocol\)/);
  assert.match(adapterSource, /class VgcBenchMaskedActorCriticAdapter/);
  assert.match(adapterSource, /from vgc_bench\.src\.policy import action_map/);
  assert.match(adapterSource, /policy\.get_logits\(obs_dict, actor_grad=False\)/);
  assert.match(adapterSource, /get_actions\(deterministic=True\)/);
  assert.match(adapterSource, /branch2ConditionedOnFirst/);
  assert.match(adapterSource, /SELECTION_RULE = "sequential-greedy"/);
  assert.match(adapterSource, /selectedByLight/);
  assert.match(adapterSource, /Deliberately lazy/);
  assert.doesNotMatch(adapterSource, /load_state_dict/);
  assert.doesNotMatch(adapterSource, /optimizer/);

  for (const script of [facade, adapter, contracts, teacher]) {
    execFileSync(python, ["-m", "py_compile", script], {
      cwd: root,
      encoding: "utf8",
    });
  }
});

test("canonical orderKey ignores formatting noise but keeps semantic action changes", () => {
  const script = String.raw`
from battle_lab.nana_contracts import order_key, build_nana_policy_contract, nana_policy_key, execution_key

a={'first':{'kind':'Move','value':'Earthquake','target':1,'flags':['Mega','Tera']},'second':{'kind':'switch','value':'Incineroar','target':0,'flags':[]}}
b={'first':{'kind':' move ','value':'earthquake','target':'1','flags':['Tera','Mega']},'second':{'kind':'SWITCH','value':'incineroar','target':None,'flags':[]}}
c={'first':{'kind':'move','value':'Earthquake','target':1,'flags':['Mega']},'second':{'kind':'switch','value':'Incineroar','target':0,'flags':[]}}
d={'first':{'kind':'Move','value':'Protect','target':0,'flags':[]},'second':{'kind':'switch','value':'Flutter Mane','target':0,'flags':['Z-Move']}}
e={'first':{'kind':'move','value':'protect','target':None,'flags':[]},'second':{'kind':'SWITCH','value':'flutter-mane','target':0,'flags':['z move']}}
assert order_key(a) == order_key(b)
assert order_key(a) != order_key(c)
assert order_key(d) == order_key(e)

base=build_nana_policy_contract(
    decision_mode='nana2.3-nursery-live-v1',
    scorer_contract='legacy-n2-light-regret-plus-response-utility-v1',
    score_spaces={'teacherPrior':'teacher-log-regret-v1','counter':'response-utility-v1'},
    governor_contract={'level':'N2','lambdaCap':0.15,'maxInterventionsPerBattle':1,'minAllowedLightRegretLog':-0.08},
    memory_contract={'modelVersion':'nana-team-memory-v1','minScopeSamples':3,'maxBlend':0.35},
    legal_order_contract='vgc-bench-indexed-order-v1',
)
policy_key=nana_policy_key(base)
assert policy_key.startswith('nana-policy:v3:')
assert nana_policy_key({**base,'governor':{**base['governor'],'lambdaCap':0.20}}) != policy_key
assert nana_policy_key({**base,'governor':{**base['governor'],'minAllowedLightRegretLog':-0.20}}) != policy_key
assert nana_policy_key({**base,'memory':{**base['memory'],'minScopeSamples':4}}) != policy_key
assert execution_key(teacher_behavior_key='teacher-A', nana_policy_key_value=policy_key) != execution_key(teacher_behavior_key='teacher-B', nana_policy_key_value=policy_key)
assert execution_key(teacher_behavior_key='', nana_policy_key_value=policy_key) == ''
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("teacher behavior identity refuses unresolved fingerprints", () => {
  const teacherSource = readFileSync(teacher, "utf8");
  assert.match(teacherSource, /"key": weights_key/);
  assert.match(teacherSource, /"behaviorKeyResolved": behavior_resolved/);
  assert.match(teacherSource, /"nanaPolicyKeyResolved": policy_resolved/);
  assert.match(teacherSource, /"executionKeyResolved": execution_resolved/);

  const script = String.raw`
from battle_lab.nana_teacher_adapter import teacher_behavior_key

base={
 'fingerprintSpecVersion':1,
 'resolved':True,
 'family':'vgc-bench-masked-actor-critic',
 'format':'fmt',
 'checkpointSha256':'same-weights',
 'actionSpaceId':'actions-v1',
 'featureSchemaId':'features-v1',
 'adapterContractVersion':1,
 'selectionRule':'sequential-greedy',
 'inferenceParams':{'fakeRating':2000,'deterministic':True},
}
changed={**base,'adapterContractVersion':2}
assert teacher_behavior_key(base) != teacher_behavior_key(changed)
assert teacher_behavior_key({**base,'resolved':False}) == ''
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
