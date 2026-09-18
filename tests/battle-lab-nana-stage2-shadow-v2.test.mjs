import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const runtime = path.join(root, "battle_lab", "nana_stage2_shadow_v2_runtime.py");
const report = path.join(root, "battle_lab", "nana_stage2_shadow_v2_report.py");

test("Nana 2 v2 returns LIGHT even when shadow instrumentation raises", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_v2_runtime import _safe_after_light
calls = []
def light():
    calls.append("light")
    return "LIGHT-ORDER"
def shadow(_order):
    calls.append("shadow")
    raise RuntimeError("boom")
order, error = _safe_after_light(light, shadow)
assert order == "LIGHT-ORDER"
assert calls == ["light", "shadow"]
assert "RuntimeError" in error
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 v2 requires pre-choice prediction and exact turn match", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_v2_runtime import shadow_rerank_v2
assert shadow_rerank_v2({}, {}, prediction_source="submit-fallback", prediction_generation=1, turn_matched=True)["reason"] == "prediction-not-prechoice"
assert shadow_rerank_v2({}, {}, prediction_source="snapshot-cache", prediction_generation=1, turn_matched=False)["reason"] == "light-turn-not-matched"
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 v2 executes lambda zero as an invariant control", () => {
  const script = String.raw`
import math
from battle_lab.nana_stage2_shadow_v2_runtime import shadow_rerank_v2
canonical_action = {
    "first": {"kind":"move","value":"Thunderbolt","target":1,"flags":[]},
    "second": {"kind":"move","value":"Tailwind","target":0,"flags":[]},
}
alternative_action = {
    "first": {"kind":"move","value":"Feint","target":1,"flags":[]},
    "second": {"kind":"move","value":"Tailwind","target":0,"flags":[]},
}
light = {
    "waiting": False,
    "canonicalAction": {"indices":[1,1]},
    "branches": [{"scores":[
        {"index":1,"probability":0.51},
        {"index":2,"probability":0.49},
    ]}],
    "jointScores": [
        {"indices":[1,1],"probability":0.51,"selectedByLight":True,"action":canonical_action},
        {"indices":[2,1],"probability":0.49,"selectedByLight":False,"action":alternative_action},
    ],
}
prediction = {
    "ready": True,
    "confidence": 0.15,
    "candidates": [{
        "probability": 1.0,
        "signature":"x",
        "first":{"kind":"move","value":"Protect","target":0,"flags":[]},
        "second":{"kind":"move","value":"Tailwind","target":0,"flags":[]},
    }],
}
plan = shadow_rerank_v2(light, prediction, prediction_source="snapshot-cache", prediction_generation=1, turn_matched=True)
assert plan["eligible"] is True
zero = [s for s in plan["sweeps"] if abs(s["lambdaCap"]) < 1e-12][0]
assert zero["changed"] is False
assert zero["recommended"]["selectedByLight"] is True
assert abs(plan["canonical"]["lightRegretLog"]) < 1e-12
assert plan["diagnostics"]["jointTotal"] == 2
assert plan["diagnostics"]["jointStructured"] == 2
assert plan["diagnostics"]["branchRegretPassed"] >= 1
assert plan["diagnostics"]["poolSize"] >= 1
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 v2 modules compile", () => {
  for (const filename of [runtime, report]) {
    execFileSync(python, ["-m", "py_compile", filename], { cwd: root, encoding: "utf8" });
  }
});
