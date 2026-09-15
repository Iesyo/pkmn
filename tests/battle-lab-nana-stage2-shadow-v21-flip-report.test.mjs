import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const report = path.join(root, "battle_lab", "nana_stage2_shadow_v21_flip_report.py");

test("Nana 2 v2.1 flip diagnostic computes required lambda cap without mutating plans", () => {
  const script = String.raw`
from copy import deepcopy
from battle_lab.nana_stage2_shadow_v21_flip_report import analyze_plan

plan = {
    "eligible": True,
    "confidenceScale": 0.5,
    "canonical": {
        "selectedByLight": True,
        "expectedCounter": 0.0,
        "lightRegretLog": 0.0,
        "indices": [1, 1],
    },
    "candidatePool": [
        {
            "selectedByLight": True,
            "expectedCounter": 0.0,
            "lightRegretLog": 0.0,
            "indices": [1, 1],
        },
        {
            "selectedByLight": False,
            "expectedCounter": 0.4,
            "lightRegretLog": -0.03,
            "firstRegretLog": -0.03,
            "secondRegretLog": 0.0,
            "indices": [2, 1],
        },
        {
            "selectedByLight": False,
            "expectedCounter": -0.2,
            "lightRegretLog": -0.01,
            "firstRegretLog": -0.01,
            "secondRegretLog": 0.0,
            "indices": [3, 1],
        },
    ],
}
before = deepcopy(plan)
result = analyze_plan(plan)
assert plan == before
assert result["eligible"] is True
assert result["positiveAlternativeCount"] == 1
assert abs(result["best"]["requiredEffectiveLambda"] - 0.075) < 1e-12
assert abs(result["best"]["requiredLambdaCap"] - 0.15) < 1e-12
assert result["best"]["marginAtCurrentCap"] > 0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 v2.1 flip diagnostic distinguishes no pool alternative from no positive proxy signal", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_v21_flip_report import analyze_plan

base = {
    "eligible": True,
    "confidenceScale": 0.5,
    "canonical": {"selectedByLight": True, "expectedCounter": 0.2, "lightRegretLog": 0.0},
}
only_light = {**base, "candidatePool": [{"selectedByLight": True, "expectedCounter": 0.2, "lightRegretLog": 0.0}]}
assert analyze_plan(only_light)["reason"] == "no-alternative-in-pool"

flat = {
    **base,
    "candidatePool": [
        {"selectedByLight": True, "expectedCounter": 0.2, "lightRegretLog": 0.0},
        {"selectedByLight": False, "expectedCounter": 0.2, "lightRegretLog": -0.01},
    ],
}
assert analyze_plan(flat)["reason"] == "no-positive-proxy-advantage"
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 v2.1 flip report module compiles", () => {
  execFileSync(python, ["-m", "py_compile", report], { cwd: root, encoding: "utf8" });
});
