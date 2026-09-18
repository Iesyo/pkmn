import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";
const autonomy = path.join(root, "battle_lab", "nana_autonomy.py");
const scorer = path.join(root, "battle_lab", "nana_scorer.py");
const nurseryRuntime = path.join(root, "battle_lab", "nana_stage2_nursery_runtime.py");

test("M3 N2 envelope exactly matches current Nursery and promotion remains manual", () => {
  const script = String.raw`
from battle_lab.nana_autonomy import envelope, live_nursery_contract, assert_live_nursery_matches_n2
from battle_lab.nana_nursery import NURSERY_LAMBDA_CAP, MAX_INTERVENTIONS_PER_BATTLE
n2=envelope('N2')
assert n2.lambda_cap == 0.15
assert n2.max_interventions_per_battle == 1
assert n2.automatic_promotion is False
contract=live_nursery_contract(lambda_cap=NURSERY_LAMBDA_CAP,max_interventions_per_battle=MAX_INTERVENTIONS_PER_BATTLE)
assert contract['level'] == 'N2'
assert contract['matchesCanonicalN2'] is True
assert contract['automaticPromotion'] is False
assert_live_nursery_matches_n2(lambda_cap=NURSERY_LAMBDA_CAP,max_interventions_per_battle=MAX_INTERVENTIONS_PER_BATTLE)
try:
    assert_live_nursery_matches_n2(lambda_cap=0.20,max_interventions_per_battle=1)
except RuntimeError:
    pass
else:
    raise AssertionError('ungated autonomy drift must fail')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("M3 scorer refuses to add raw values from different score spaces", () => {
  const script = String.raw`
from battle_lab.nana_scorer import ScoreTerm, combine_common_terms, COMMON_SCORE_SPACE
ok=combine_common_terms([
    ScoreTerm('experience',0.5,0.8,COMMON_SCORE_SPACE),
    ScoreTerm('heuristicFloor',0.2,0.2,COMMON_SCORE_SPACE),
])
assert ok['scoreSpace'] == COMMON_SCORE_SPACE
assert ok['effectiveWeight'] > 0
try:
    combine_common_terms([
        ScoreTerm('experience',0.5,0.8,COMMON_SCORE_SPACE),
        ScoreTerm('counter',1.0,1.0,'response-utility-v1'),
    ])
except ValueError:
    pass
else:
    raise AssertionError('cross-space arithmetic must be rejected')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("live Nursery exposes N2 contract without changing its decision constants", () => {
  const source = readFileSync(nurseryRuntime, "utf8");
  assert.match(source, /assert_live_nursery_matches_n2/);
  assert.match(source, /live_nursery_contract/);
  assert.match(source, /NURSERY_LAMBDA_CAP/);
  assert.match(source, /MAX_INTERVENTIONS_PER_BATTLE/);
});

test("M3 modules compile", () => {
  for (const file of [autonomy, scorer]) {
    execFileSync(python, ["-m", "py_compile", file], { cwd: root, encoding: "utf8" });
  }
});
