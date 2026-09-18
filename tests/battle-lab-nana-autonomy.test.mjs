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

test("M3 N2 envelope covers the complete live Nursery intervention policy", () => {
  const script = String.raw`
from battle_lab.nana_autonomy import envelope, full_amiibo_contract, live_nursery_contract, assert_live_nursery_matches_n2, assert_level_activation_ready
from battle_lab.nana_nursery import (
    ALLOW_UNREPRESENTED_ORDERS, AUTOMATIC_PROMOTION,
    HIGH_LIGHT_TRUST_CONFIDENCE, HIGH_LIGHT_TRUST_VETO,
    MAX_INTERVENTIONS_PER_BATTLE, MIN_ALLOWED_LIGHT_REGRET_LOG,
    MIN_PREDICTION_CONFIDENCE, NURSERY_LAMBDA_CAP,
    PROMOTION_INTERVENTION_WINDOW, SELF_LOW_TRUST_CONFIDENCE,
    SELF_LOW_TRUST_VETO,
)
values=dict(
    lambda_cap=NURSERY_LAMBDA_CAP,
    max_interventions_per_battle=MAX_INTERVENTIONS_PER_BATTLE,
    min_prediction_confidence=MIN_PREDICTION_CONFIDENCE,
    min_allowed_light_regret_log=MIN_ALLOWED_LIGHT_REGRET_LOG,
    high_light_trust_veto=HIGH_LIGHT_TRUST_VETO,
    high_light_trust_confidence=HIGH_LIGHT_TRUST_CONFIDENCE,
    self_low_trust_veto=SELF_LOW_TRUST_VETO,
    self_low_trust_confidence=SELF_LOW_TRUST_CONFIDENCE,
    promotion_intervention_window=PROMOTION_INTERVENTION_WINDOW,
    allow_unrepresented_orders=ALLOW_UNREPRESENTED_ORDERS,
    automatic_promotion=AUTOMATIC_PROMOTION,
)
n2=envelope('N2')
contract=live_nursery_contract(**values)
assert contract['matchesCanonicalN2'] is True
assert contract['lambdaCap'] == 0.15
assert contract['maxInterventionsPerBattle'] == 1
assert contract['minAllowedLightRegretLog'] == -0.08
assert contract['automaticPromotion'] is False
assert contract['allowUnrepresentedOrders'] is False
assert_live_nursery_matches_n2(**values)
for key, changed in (
    ('lambda_cap',0.20),
    ('min_allowed_light_regret_log',-0.20),
    ('min_prediction_confidence',0.01),
    ('promotion_intervention_window',30),
    ('allow_unrepresented_orders',True),
    ('automatic_promotion',True),
):
    bad={**values,key:changed}
    try:
        assert_live_nursery_matches_n2(**bad)
    except RuntimeError:
        pass
    else:
        raise AssertionError(f'ungated N2 drift must fail: {key}')
assert_level_activation_ready('N2')
assert_level_activation_ready('N4')
n4=full_amiibo_contract()
assert n4['level'] == 'N4'
assert n4['lambdaCap'] is None
assert n4['maxInterventionsPerBattle'] is None
assert n4['safetyGateRequired'] is True
assert n4['teacherRole'] == 'advisor-fallback'
assert n4['activationReady'] is True
try:
    assert_level_activation_ready('N3')
except RuntimeError:
    pass
else:
    raise AssertionError('N3 must remain non-activatable')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("M3 scorer requires explicit mappers and distinguishes no evidence from neutral", () => {
  const script = String.raw`
from battle_lab.nana_scorer import (
    board_delta_term, map_to_board_delta, combine_common_terms,
    assert_common_scorer_ready_for_level,
)
neutral=combine_common_terms([board_delta_term(name='experience',value=0.0,confidence=1.0)])
assert neutral['score'] == 0.0
assert neutral['insufficientEvidence'] is False
empty=combine_common_terms([])
assert empty['score'] is None
assert empty['effectiveWeight'] == 0.0
assert empty['insufficientEvidence'] is True
mapped=map_to_board_delta(
    name='counter', source_value=0.5, confidence=0.8,
    source_space='response-utility-v1', mapper_id='test-linear-v1',
    mapper=lambda value: value * 0.25,
)
result=combine_common_terms([mapped])
assert result['score'] == 0.125
assert result['terms'][0]['mapperId'] == 'test-linear-v1'
try:
    map_to_board_delta(
        name='bad', source_value=float('nan'), confidence=1.0,
        source_space='response-utility-v1', mapper_id='bad',
        mapper=lambda value:value,
    )
except ValueError:
    pass
else:
    raise AssertionError('NaN must be rejected')
assert_common_scorer_ready_for_level('N4')
try:
    assert_common_scorer_ready_for_level('N3')
except RuntimeError:
    pass
else:
    raise AssertionError('N3 must remain on the legacy path')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("live Nursery exposes complete N2 contract without changing choose_candidate", () => {
  const source = readFileSync(nurseryRuntime, "utf8");
  for (const token of [
    "MIN_PREDICTION_CONFIDENCE",
    "MIN_ALLOWED_LIGHT_REGRET_LOG",
    "HIGH_LIGHT_TRUST_VETO",
    "HIGH_LIGHT_TRUST_CONFIDENCE",
    "SELF_LOW_TRUST_VETO",
    "SELF_LOW_TRUST_CONFIDENCE",
    "PROMOTION_INTERVENTION_WINDOW",
    "ALLOW_UNREPRESENTED_ORDERS",
    "AUTOMATIC_PROMOTION",
  ]) {
    assert.match(source, new RegExp(token));
  }
  assert.match(source, /governor_contract=_live_autonomy_contract\(\)/);
  assert.equal(
    (source.match(/live_nursery_contract\(/g) || []).length,
    1,
    "all runtime consumers must use the complete _live_autonomy_contract helper",
  );
  assert.ok(
    (source.match(/"autonomy": _live_autonomy_contract\(\)/g) || []).length >= 2,
    "metadata and snapshot must use the complete autonomy helper",
  );
});

test("M3 modules compile", () => {
  for (const file of [autonomy, scorer]) {
    execFileSync(python, ["-m", "py_compile", file], { cwd: root, encoding: "utf8" });
  }
});
