import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";

test("Nana transition acts separate teacher behavior from Nana policy identity", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_transition import build_transition, write_transition_act

same={'key':'W','weightsKey':'W','behaviorKey':'B1','nanaPolicyKey':'N1','executionKey':'E1'}
assert build_transition(same, dict(same), timestamp='2026-01-01T00:00:00+00:00')['record'] is False

teacher={**same,'behaviorKey':'B2','executionKey':'E2'}
t=build_transition(same, teacher, timestamp='2026-01-01T00:00:01+00:00')
assert t['kind'] == 'teacher-behavior-change'
assert t['requiresProbation'] is True
assert t['changes']['weights'] is False

policy={**same,'nanaPolicyKey':'N2','executionKey':'E3'}
p=build_transition(same, policy, timestamp='2026-01-01T00:00:02+00:00')
assert p['kind'] == 'nana-policy-change'
assert p['requiresPolicyRevalidation'] is True
assert p['requiresProbation'] is False

legacy={'key':'W'}
l=build_transition(legacy, same, timestamp='2026-01-01T00:00:03+00:00')
assert l['kind'] == 'legacy-contract-migration'
assert l['liveBehaviorChangedByM0'] is False
with TemporaryDirectory() as tmp:
    act=write_transition_act(Path(tmp), l)
    assert act.is_file()
    assert act.parent.name == 'transitions'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
