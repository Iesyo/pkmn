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
const python = process.env.PYTHON ?? "python3";

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
from battle_lab.nana_contracts import order_key, current_nana_policy_contract, nana_policy_key, execution_key

a={'first':{'kind':'Move','value':'Earthquake','target':1,'flags':['Mega','Tera']},'second':{'kind':'switch','value':'Incineroar','target':0,'flags':[]}}
b={'first':{'kind':' move ','value':'earthquake','target':'1','flags':['Tera','Mega']},'second':{'kind':'SWITCH','value':'incineroar','target':None,'flags':[]}}
c={'first':{'kind':'move','value':'Earthquake','target':1,'flags':['Mega']},'second':{'kind':'switch','value':'Incineroar','target':0,'flags':[]}}
d={'first':{'kind':'Move','value':'Protect','target':0,'flags':[]},'second':{'kind':'switch','value':'Flutter Mane','target':0,'flags':['Z-Move']}}
e={'first':{'kind':'move','value':'protect','target':None,'flags':[]},'second':{'kind':'SWITCH','value':'flutter-mane','target':0,'flags':['z move']}}
assert order_key(a) == order_key(b)
assert order_key(a) != order_key(c)
assert order_key(d) == order_key(e)
policy_key=nana_policy_key(current_nana_policy_contract())
assert policy_key.startswith('nana-policy:v1:')
assert execution_key(teacher_behavior_key='teacher-A', nana_policy_key_value=policy_key) != execution_key(teacher_behavior_key='teacher-B', nana_policy_key_value=policy_key)
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("teacher behavior identity and Nana policy identity are distinct contracts", () => {
  const teacherSource = readFileSync(teacher, "utf8");
  assert.match(teacherSource, /"key": weights_key/);
  assert.match(teacherSource, /"behaviorKey": behavior_key/);
  assert.match(teacherSource, /"nanaPolicyKey": policy_key/);
  assert.match(teacherSource, /"executionKey": execution_key/);

  const script = String.raw`
from battle_lab.nana_teacher_adapter import teacher_behavior_key

base={
 'fingerprintSpecVersion':1,
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
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
