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
