import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import url from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";

test("counter calibration fits positive through-origin mapping and confidence", () => {
  const script = String.raw`
import battle_lab.nana_counter_calibration as mod
pairs=[]
for i,x in enumerate([-1.0,-0.75,-0.5,-0.25,0.25,0.5,0.75,1.0]*3):
    pairs.append({'x':x,'y':0.4*x,'weight':1.0})
old=mod.calibration_pairs
try:
    mod.calibration_pairs=lambda events:pairs
    c=mod.fit_counter_calibration([],min_samples=12,min_r2=0.05)
finally:
    mod.calibration_pairs=old
assert c.resolved is True
assert abs(c.slope-0.4) < 1e-9
assert c.r2 > 0.99
assert c.confidence > 0
assert abs(c.map(0.5)-0.2) < 1e-9
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("counter calibration fails closed for weak or inverted evidence", () => {
  const script = String.raw`
import battle_lab.nana_counter_calibration as mod
old=mod.calibration_pairs
try:
    mod.calibration_pairs=lambda events:[{'x':1.0,'y':-0.5,'weight':1.0} for _ in range(20)]
    c=mod.fit_counter_calibration([],min_samples=12,min_r2=0.05)
    assert c.resolved is False
    assert c.reason.startswith('non-positive-slope')

    mod.calibration_pairs=lambda events:[{'x':1.0,'y':0.5,'weight':1.0} for _ in range(3)]
    c=mod.fit_counter_calibration([],min_samples=12,min_r2=0.05)
    assert c.resolved is False
    assert c.reason.startswith('insufficient-samples')
finally:
    mod.calibration_pairs=old
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
