import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const calibrated = path.join(root, "battle_lab", "nana_stage1_calibrated_runtime.py");
const calibrationReport = path.join(root, "battle_lab", "nana_stage1_calibration_report.py");

test("Nana 1 v2 preserves ranking but shrinks probabilities after readiness", () => {
  const script = String.raw`
from battle_lab.nana_predictor import NanaPredictor
from battle_lab.nana_stage1_calibrated_runtime import CalibratedNanaPredictor

state = {
    "turn": 1,
    "ownActive": [{"species": "Incineroar", "hp": 100}, {"species": "Rillaboom", "hp": 100}],
    "opponentActive": [{"species": "Miraidon", "hp": 100}, {"species": "Farigiraf", "hp": 100}],
    "ownTeam": [], "opponentTeam": [], "weather": [], "fields": [],
}
legal = [
    {"id": "1:0", "first": {"kind": "move", "value": "fakeout", "target": 2, "flags": []}, "second": {"kind": "move", "value": "woodhammer", "target": 1, "flags": []}},
    {"id": "1:1", "first": {"kind": "move", "value": "protect", "target": 0, "flags": []}, "second": {"kind": "switch", "value": "Amoonguss", "target": 0, "flags": []}},
]
v1 = NanaPredictor()
v2 = CalibratedNanaPredictor()
for _ in range(24):
    v1.observe(state, legal[0])
    v2.observe(state, legal[0])
old = v1.predict(state, legal)
new = v2.predict(state, legal)
assert old["ready"] is True
assert new["ready"] is True
assert old["top"][0]["id"] == new["top"][0]["id"] == "1:0"
assert new["top"][0]["probability"] < old["top"][0]["probability"]
assert 0.5 < new["top"][0]["probability"] < 0.8
assert 0.15 < new["calibrationStrength"] < 0.30
assert new["confidence"] < old["confidence"]
assert new["modelVersion"] == "contextual-bayes-v2"
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 1 v2 remains observational and version-marks sessions", () => {
  const source = readFileSync(calibrated, "utf8");
  assert.match(source, /stage1\.NanaPredictor = CalibratedNanaPredictor/);
  assert.match(source, /"nana_predictor_version"/);
  assert.match(source, /"influence": 0\.0/);
  assert.match(source, /MODEL_VERSION = "contextual-bayes-v2"/);
  assert.doesNotMatch(source, /def choose_move\(/);
  assert.doesNotMatch(source, /self\.runtime\.policy\s*=/);
});

test("Nana 1 v2 modules are syntactically valid", () => {
  for (const filename of [calibrated, calibrationReport]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
