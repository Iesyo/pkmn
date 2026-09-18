import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import url from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("N4 readiness separates runtime evidence from manual activation gate", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_recorder import NanaRecorder
from battle_lab.nana_counter_calibration import CounterCalibration
from battle_lab.nana_n4_shadow import N4_SHADOW_VERSION
import battle_lab.nana_n4_readiness as mod

ready_cal=CounterCalibration(
    version=1,mapper_id='x',source_space='response-utility-v1',
    target_space='board-delta-v1',resolved=True,reason='ok',
    samples=20,slope=0.4,r2=0.5,confidence=0.25,x_energy=1.0,fitted_at='x'
)

with TemporaryDirectory() as tmp:
    rec=NanaRecorder(Path(tmp),profile_id='ies')
    rec.append_event('s','nana_nursery_decision',{
        'legalOrders':{
            'resolved':True,'teacherCoverage':1.0,
            'missingFromTeacher':0,'totalLegal':42,
        },
        'n4Shadow':{
            'version':N4_SHADOW_VERSION,
            'eligible':True,'wouldChange':True,'commonScoreAvailable':4,
            'safetyGate':{'authorized':True,'reason':'ok'},
            'legalOrderMs':4.0,'planningMs':6.0,'totalDecisionMs':10.0,
        },
        'selection':{'reason':'x'},
    })
    old=mod.fit_counter_calibration
    try:
        mod.fit_counter_calibration=lambda events:ready_cal
        r=mod.build_readiness(rec)
    finally:
        mod.fit_counter_calibration=old

assert r['runtimeReady'] is True
assert r['activationReady'] is False
assert r['blockers'] == []
assert r['legalOrderSource']['samples'] == 1
assert r['n4Shadow']['wouldChange'] == 1
assert r['n4Shadow']['safetyGate']['authorized'] == 1
assert r['n4Shadow']['safetyGate']['failures'] == 0
assert r['n4Shadow']['timingMs']['totalMax'] == 10.0
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("N4 readiness stays blocked without live legal-order evidence", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_recorder import NanaRecorder
from battle_lab.nana_counter_calibration import CounterCalibration
import battle_lab.nana_n4_readiness as mod

blocked=CounterCalibration(
    version=1,mapper_id='x',source_space='response-utility-v1',
    target_space='board-delta-v1',resolved=False,reason='insufficient-samples:0/12',
    samples=0,slope=0,r2=0,confidence=0,x_energy=0,fitted_at='x'
)
with TemporaryDirectory() as tmp:
    rec=NanaRecorder(Path(tmp),profile_id='ies')
    old=mod.fit_counter_calibration
    try:
        mod.fit_counter_calibration=lambda events:blocked
        r=mod.build_readiness(rec)
    finally:
        mod.fit_counter_calibration=old
assert r['runtimeReady'] is False
assert any(x.startswith('counter-calibration:') for x in r['blockers'])
assert 'legal-order-source:not-yet-observed-live' in r['blockers']
assert 'n4-shadow:not-yet-observed-live' in r['blockers']
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
