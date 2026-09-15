import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "nana_policy.py");

test("Nana introspection is read-only and preserves LIGHT sequential semantics", () => {
  const source = readFileSync(script, "utf8");
  assert.match(source, /def inspect_light_decision/);
  assert.match(source, /policy\.get_logits\(obs_dict, actor_grad=False\)/);
  assert.match(source, /get_actions\(deterministic=True\)/);
  assert.match(source, /branch2ConditionedOnFirst/);
  assert.match(source, /selectionRule\": \"sequential-greedy/);
  assert.match(source, /selectedByLight/);
  assert.doesNotMatch(source, /load_state_dict/);
  assert.doesNotMatch(source, /optimizer/);
  execFileSync(process.env.PYTHON ?? "python3", ["-m", "py_compile", script], {
    cwd: root,
    encoding: "utf8",
  });
});
