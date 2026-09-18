import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";
const nursery = path.join(root, "battle_lab", "nana_stage2_nursery_runtime.py");

test("Nana transition acts separate teacher behavior from Nana policy identity", () => {
  const script = String.raw`
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.nana_transition import build_transition, write_transition_act

same={
 'key':'W','weightsKey':'W',
 'behaviorKey':'B1','behaviorKeyResolved':True,
 'nanaPolicyKey':'N1','nanaPolicyKeyResolved':True,
 'executionKey':'E1'
}
assert build_transition(same, dict(same), timestamp='2026-01-01T00:00:00Z')['record'] is False

teacher={**same,'behaviorKey':'B2','executionKey':'E2'}
t=build_transition(same, teacher, timestamp='2026-01-01T00:00:01Z')
assert t['kind'] == 'teacher-behavior-change'
assert t['requiresProbation'] is True
assert t['changes']['weights'] is False

policy={**same,'nanaPolicyKey':'N2','executionKey':'E3'}
p=build_transition(same, policy, timestamp='2026-01-01T00:00:02Z')
assert p['kind'] == 'nana-policy-change'
assert p['requiresPolicyRevalidation'] is True
assert p['requiresProbation'] is False

legacy={'key':'W'}
l=build_transition(legacy, same, timestamp='2026-01-01T00:00:03Z')
assert l['kind'] == 'legacy-contract-migration'
assert l['liveBehaviorChangedByM0'] is False
assert l['resolved'] is True

unresolved={**same,'behaviorKey':'','behaviorKeyResolved':False,'executionKey':''}
u=build_transition(same, unresolved, timestamp='2026-01-01T00:00:04Z')
assert u['kind'] == 'identity-unresolved'
assert u['record'] is False
assert u['requiresProbation'] is False

with TemporaryDirectory() as tmp:
    act=write_transition_act(Path(tmp), l)
    assert act.is_file()
    assert act.parent.name == 'transitions'
    assert act.name.startswith('2026-01-01T00-00-03Z-')
    try:
        write_transition_act(Path(tmp), u)
    except ValueError:
        pass
    else:
        raise AssertionError('unresolved transition act should be rejected')
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nursery advances previous teacher only after a resolved identity sync", () => {
  const source = readFileSync(nursery, "utf8");
  assert.match(source, /transition_resolved = False/);
  assert.match(source, /if transition_resolved:/);
  assert.match(source, /_nana_identity_recorded_sessions\.add\(session\.id\)/);
  assert.doesNotMatch(source, /finally:\s*\n\s*self\._nana_previous_teacher = copy\.deepcopy\(self\._nana_teacher\)/);
});
