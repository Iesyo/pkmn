import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import url from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("N4 shadow can prefer a legal non-teacher-filtered order from mapped common-space evidence", () => {
  const script = String.raw`
from battle_lab.nana_counter_calibration import CounterCalibration
from battle_lab.nana_legal_orders import LegalOrderSet, NormalizedCandidate
import battle_lab.nana_n4_shadow as n4

ref_action={'first':{'kind':'move','value':'protect','target':0,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
alt_action={'first':{'kind':'move','value':'feint','target':1,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
from battle_lab.nana_contracts import order_key
ref_key=order_key(ref_action)
alt_key=order_key(alt_action)
legal=LegalOrderSet(
    resolved=True,reason='ok',battle_tag='x',turn=1,
    candidates=(
        NormalizedCandidate(ref_key,ref_action,'ref',(1,1),object()),
        NormalizedCandidate(alt_key,alt_action,'alt',(2,1),object()),
    ),
    individual_counts=(2,2),joined_count=2,deduplicated_count=2,
)
light={'jointScores':[{'selectedByLight':True,'action':ref_action}]}
prediction={'confidence':1.0,'candidates':[{'probability':1.0}]}
cal=CounterCalibration(
    version=1,mapper_id='test-map',source_space='response-utility-v1',
    target_space='board-delta-v1',resolved=True,reason='ok',
    samples=20,slope=0.5,r2=1.0,confidence=1.0,x_energy=1.0,fitted_at='x'
)
old=n4._expected_response_stats
try:
    n4._expected_response_stats=lambda action,human: (
        {'score':0.0,'relevantProbability':1.0}
        if action is ref_action or action==ref_action
        else {'score':1.0,'relevantProbability':1.0}
    )
    plan=n4.build_n4_shadow_plan(
        legal_orders=legal,light=light,prediction=prediction,
        model_state={'turn':1},self_summary={'buckets':{}},
        counter_calibration=cal,
    )
finally:
    n4._expected_response_stats=old
assert plan['eligible'] is True
assert plan['referenceKey'] == ref_key
assert plan['selectedKey'] == alt_key
assert plan['wouldChange'] is True
assert plan['reason'] == 'best-common-score'
assert plan['commonScoreAvailable'] == 2
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("N4 shadow falls back to teacher reference when common-space calibration is unresolved", () => {
  const script = String.raw`
from battle_lab.nana_counter_calibration import CounterCalibration
from battle_lab.nana_legal_orders import LegalOrderSet, NormalizedCandidate
from battle_lab.nana_contracts import order_key
import battle_lab.nana_n4_shadow as n4

ref_action={'first':{'kind':'move','value':'protect','target':0,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
alt_action={'first':{'kind':'move','value':'feint','target':1,'flags':[]},'second':{'kind':'move','value':'protect','target':0,'flags':[]}}
ref_key=order_key(ref_action); alt_key=order_key(alt_action)
legal=LegalOrderSet(True,'ok','x',1,(
    NormalizedCandidate(ref_key,ref_action,'ref',(1,1),object()),
    NormalizedCandidate(alt_key,alt_action,'alt',(2,1),object()),
),(2,2),2,2)
light={'jointScores':[{'selectedByLight':True,'action':ref_action}]}
cal=CounterCalibration(1,'x','response-utility-v1','board-delta-v1',False,'nope',0,0,0,0,0,'x')
plan=n4.build_n4_shadow_plan(
    legal_orders=legal,light=light,prediction={'confidence':1.0,'candidates':[]},
    model_state={'turn':1},self_summary={'buckets':{}},counter_calibration=cal,
)
assert plan['eligible'] is True
assert plan['selectedKey'] == ref_key
assert plan['wouldChange'] is False
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
