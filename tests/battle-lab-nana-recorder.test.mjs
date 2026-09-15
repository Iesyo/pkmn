import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "nana_recorder.py");

test("Nana 0 stores append-only history and rebuilds habits", () => {
  const program = String.raw`
import json
import pathlib
import sys
import tempfile
sys.path.insert(0, r'${root.replaceAll("\\", "\\\\")}')
from battle_lab.nana_recorder import NanaRecorder

root = pathlib.Path(tempfile.mkdtemp())
r = NanaRecorder(root, profile_id='Ies local')
r.start_session('session-1', opponent={'id':'vgc-1'}, context={'format':'M-C'})
r.record_team_preview('session-1', side='human', order=[1,2,3,4], state={'turn':0})
r.record_turn(
    'session-1',
    turn=1,
    state={'turn':1},
    legal_actions=[{'id':'1'}],
    human_action={
        'first': {'kind':'move','value':'protect','target':0,'flags':['Tera']},
        'second': {'kind':'switch','value':'Pelipper','target':0,'flags':[]},
    },
    model_action={'indices':[1,2]},
    light={'value':0.2},
)
r.finish_session('session-1', result={'winner':'human'}, final_state={'turn':4})
summary = json.loads((r.profile_root / 'habits.json').read_text())
assert summary['sessions'] == 1
assert summary['completedSessions'] == 1
assert summary['results'] == {'wins':1,'losses':0,'ties':0}
assert summary['turnChoices'] == 1
assert summary['actionKinds'] == {'move':1,'switch':1}
assert summary['moves']['protect'] == 1
assert summary['switches']['Pelipper'] == 1
assert summary['gimmicks']['Tera'] == 1
assert len(list(r.iter_events())) == 4
print('ok')
`;
  const output = execFileSync(process.env.PYTHON ?? "python3", ["-c", program], {
    cwd: root,
    encoding: "utf8",
  });
  assert.match(output, /ok/);
});

test("Nana recorder module is syntactically valid", () => {
  execFileSync(process.env.PYTHON ?? "python3", ["-m", "py_compile", script], {
    cwd: root,
    encoding: "utf8",
  });
});
