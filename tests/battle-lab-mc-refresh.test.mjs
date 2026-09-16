import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("periodic refresh preserves data partitions, snapshots, champion identity and incremental collection", () => {
  execFileSync("python3", ["-m", "unittest", "discover", "-s", "tests", "-p", "mc_refresh_checks.py", "-v"],
    { cwd: root, encoding: "utf8", timeout: 30000 });
});

test("evaluation recovery loads the fix and routes only to recovery, preserving normal resume", () => {
  execFileSync("python3", ["-c", String.raw`
import json, re, sys, tempfile
from pathlib import Path
nb = json.loads(Path('colab/Battle_Lab_MC_Refresh.ipynb').read_text())
bootstrap = ''.join(nb['cells'][2]['source']).split('source_ref = PKMN_REF', 1)[1].split('# El repositorio', 1)[0]
bootstrap = 'source_ref = PKMN_REF' + bootstrap
with tempfile.TemporaryDirectory() as temp:
    refresh = Path(temp)
    run = refresh/'runs'/'saved-run'
    run.mkdir(parents=True)
    (refresh/'active_run.json').write_text(json.dumps({'runId': 'saved-run'}))
    (run/'config.json').write_text(json.dumps({'codeSha': 'training-sha'}))
    (run/'status.json').write_text(json.dumps({'state': 'failed'}))
    for action in ('auto', 'resume', 'recover_evaluation'):
        for selected in ('', 'saved-run'):
            scope = dict(json=json, re=re, REFRESH=refresh, RUN_ID=selected,
                         RUN_ACTION=action, PKMN_REF='fixed-branch')
            exec(bootstrap, scope)
            assert scope['source_ref'] == ('fixed-branch' if action == 'recover_evaluation' else 'training-sha')
    calls = []
    scope = dict(sys=sys, ROOT=refresh, REPO=refresh, RUN_ID='saved-run',
                 RUN_ACTION='recover_evaluation', run=lambda command, cwd: calls.append(command))
    # No training settings are supplied: the recovery must use the frozen run.
    exec(''.join(nb['cells'][5]['source']), scope)
    assert calls == [[sys.executable, '-u', '-m', 'battle_lab.mc_refresh', '--root', refresh,
                      '--recover-evaluation', '--run-id', 'saved-run']]
`], { cwd: root, encoding: "utf8" });
});

test("unified Colab has executable Python cells, clean outputs and conservative defaults", () => {
  const notebookPath = path.join(root, "colab/Battle_Lab_MC_Refresh.ipynb");
  const notebook = JSON.parse(fs.readFileSync(notebookPath, "utf8"));
  assert.equal(notebook.nbformat, 4);
  for (const cell of notebook.cells.filter(c => c.cell_type === "code")) {
    assert.deepEqual(cell.outputs, []);
    assert.equal(cell.execution_count, null);
  }
  execFileSync("python3", ["-c", String.raw`
import json
from pathlib import Path
nb = json.loads(Path('colab/Battle_Lab_MC_Refresh.ipynb').read_text())
for i, cell in enumerate(nb['cells']):
    if cell['cell_type'] == 'code':
        compile(''.join(cell['source']), f'colab-cell-{i}', 'exec')
config = {}
exec(''.join(nb['cells'][1]['source']), config)
assert config['RUN_ACTION'] == 'auto' and config['RUN_MODE'] == 'LIGHT'
assert config['BATTLES_PER_CONTROL'] == 500
assert config['PKMN_REF'] == 'battle-lab-mc-refresh-001'
assert ''.join(nb['cells'][-1]['source']).startswith('#@title')
`], { cwd: root, encoding: "utf8" });
});
