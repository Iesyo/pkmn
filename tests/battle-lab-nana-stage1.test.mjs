import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const predictor = path.join(root, "battle_lab", "nana_predictor.py");
const runtime = path.join(root, "battle_lab", "nana_stage1_runtime.py");
const audit = path.join(root, "battle_lab", "nana_stage1_audit.py");

test("Nana 1 predictor stays cautious during cold start and learns online", () => {
  const script = String.raw`
from battle_lab.nana_predictor import NanaPredictor

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
p = NanaPredictor()
cold = p.predict(state, legal)
assert cold["historySamples"] == 0
assert cold["ready"] is False
assert cold["confidence"] == 0.0
assert abs(cold["top"][0]["probability"] - 0.5) < 1e-9

for _ in range(6):
    p.observe(state, legal[0])
warm = p.predict(state, legal)
assert warm["historySamples"] == 6
assert warm["ready"] is False
assert warm["confidenceBand"] == "cold-start"
assert warm["top"][0]["id"] == "1:0"
assert 0.5 < warm["top"][0]["probability"] < 0.7

for _ in range(18):
    p.observe(state, legal[0])
ready = p.predict(state, legal)
assert ready["historySamples"] == 24
assert ready["ready"] is True
assert ready["top"][0]["id"] == "1:0"
assert ready["top"][0]["probability"] > 0.9
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 1 history loader never double counts stage-1 observations", () => {
  const script = String.raw`
from battle_lab.nana_predictor import NanaPredictor
state = {"turn": 1, "ownActive": [], "opponentActive": [], "ownTeam": [], "opponentTeam": [], "weather": [], "fields": []}
action = {"id": "1:0", "first": {"kind": "move", "value": "protect", "target": 0, "flags": []}, "second": {"kind": "move", "value": "protect", "target": 0, "flags": []}}
events = [
    {"sessionId": "s1", "type": "turn_choice", "payload": {"state": state, "humanAction": action}},
    {"sessionId": "s1", "type": "human_choice_observed", "payload": {"state": state, "action": action}},
    {"sessionId": "s2", "type": "turn_choice", "payload": {"state": state, "humanAction": action}},
]
p = NanaPredictor.from_events(events)
assert p.samples == 2
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 1 remains observational and does not replace LIGHT move selection", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /install_nana_service\(profile_id=profile_id\)/);
  assert.match(source, /NanaPredictor\.from_events/);
  assert.match(source, /"human_prediction"/);
  assert.match(source, /"human_choice_observed"/);
  assert.match(source, /"influence": 0\.0/);
  assert.match(source, /predictionRecorded/);
  assert.match(source, /Deliberately do not expose predicted actions to the human UI/);
  assert.doesNotMatch(source, /def choose_move\(/);
  assert.doesNotMatch(source, /load_state_dict/);
  assert.doesNotMatch(source, /self\.runtime\.policy\s*=/);
});

test("Nana 1 modules are syntactically valid", () => {
  for (const filename of [predictor, runtime, audit]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
