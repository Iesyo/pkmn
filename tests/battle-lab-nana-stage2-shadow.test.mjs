import assert from "node:assert/strict";
import { execFileSync, readFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const runtime = path.join(root, "battle_lab", "nana_stage2_shadow_runtime.py");
const report = path.join(root, "battle_lab", "nana_stage2_shadow_report.py");

test("Nana 2 shadow response proxy handles Protect conservatively", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_runtime import response_utility

human_protect = {
    "first": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
    "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
}
model_attack = {
    "first": {"kind": "move", "value": "Thunderbolt", "target": -1, "flags": []},
    "second": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
}
model_feint = {
    "first": {"kind": "move", "value": "Feint", "target": -1, "flags": []},
    "second": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
}
assert response_utility(model_attack, human_protect)["score"] == -1.0
assert response_utility(model_feint, human_protect)["score"] == 1.0

human_attack = {
    "first": {"kind": "move", "value": "Thunderbolt", "target": -1, "flags": []},
    "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
}
model_protect = {
    "first": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
    "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
}
assert response_utility(model_protect, human_attack)["score"] == 1.0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 shadow can rerank a LIGHT near-tie but never applies it", () => {
  const script = String.raw`
import math
from battle_lab.nana_stage2_shadow_runtime import shadow_rerank

canonical_action = {
    "first": {"kind": "move", "value": "Thunderbolt", "target": -1, "flags": []},
    "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
}
alternative_action = {
    "first": {"kind": "move", "value": "Thunderbolt", "target": -2, "flags": []},
    "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
}
far_action = {
    "first": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
    "second": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
}
light = {
    "waiting": False,
    "jointScores": [
        {"indices": [1, 1], "labels": ["a", "b"], "probability": 0.50, "logProbability": math.log(0.50), "selectedByLight": True, "action": canonical_action},
        {"indices": [2, 1], "labels": ["c", "b"], "probability": 0.49, "logProbability": math.log(0.49), "selectedByLight": False, "action": alternative_action},
        {"indices": [3, 3], "labels": ["d", "d"], "probability": 0.10, "logProbability": math.log(0.10), "selectedByLight": False, "action": far_action},
    ],
}
prediction = {
    "ready": True,
    "confidence": 0.15,
    "candidates": [
        {
            "probability": 1.0,
            "first": {"kind": "move", "value": "Protect", "target": 0, "flags": []},
            "second": {"kind": "move", "value": "Tailwind", "target": 0, "flags": []},
        }
    ],
}
plan = shadow_rerank(light, prediction)
assert plan["eligible"] is True
assert len(plan["candidatePool"]) == 2
assert all(sweep["changed"] for sweep in plan["sweeps"])
assert all(sweep["recommended"]["indices"] == [2, 1] for sweep in plan["sweeps"])

not_ready = shadow_rerank(light, {**prediction, "ready": False})
assert not_ready["eligible"] is False
assert not_ready["reason"] == "predictor-not-ready"
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 shadow runtime delegates the real move to LIGHT and exposes no recommendation", () => {
  const source = fs.readFileSync(runtime, "utf8");
  assert.match(source, /order = super\(\)\.choose_move\(current\)/);
  assert.match(source, /return order/);
  assert.match(source, /include_joint_scores=True/);
  assert.match(source, /"influence": 0\.0/);
  assert.match(source, /"mode": "shadow"/);
  assert.match(source, /No shadow recommendation is exposed to the human UI/);
  assert.doesNotMatch(source, /load_state_dict/);
  assert.doesNotMatch(source, /self\.runtime\.policy\s*=/);
});

test("Nana 2 shadow modules are syntactically valid", () => {
  for (const filename of [runtime, report]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
