import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_rl_light_v2.py");

test("LIGHT v2 appends M-C catalogs without shifting historical IDs", () => {
  const source = fs.readFileSync(script, "utf8");
  assert.match(source, /MC_ABILITY_EXTENSIONS = \("auraguard",\)/);
  assert.match(source, /values\.append\(value\)/);
  assert.match(source, /abilities_before/);
  assert.match(source, /items_before/);
  assert.match(source, /reanudando desde checkpoint/);
  assert.match(source, /traceback=tb\[-12000:\]/);
  assert.match(source, /finalCheckpointSha256/);
  assert.doesNotMatch(source, /finalCheckpointSha257/);
  execFileSync("python", ["-m", "py_compile", script], { cwd: root, encoding: "utf8" });
});

test("catalog extension is append-only and idempotent", () => {
  const program = String.raw`
import contextlib
import importlib.util
import os
import pathlib
import sys
import tempfile
import types

mc = types.ModuleType('battle_lab.mc_training')
@contextlib.contextmanager
def wd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)
mc._resolve_device = lambda value: value
mc.atomic_json = lambda *args, **kwargs: None
mc.download_baseline = lambda path: path
mc.human_seconds = lambda value: str(value)
mc.inject_mc_support = lambda path: None
mc.install_mc_teams = lambda *args, **kwargs: 145
mc.sha256_file = lambda path: 'sha'
mc.working_directory = wd
pkg = types.ModuleType('battle_lab')
pkg.__path__ = []
sys.modules['battle_lab'] = pkg
sys.modules['battle_lab.mc_training'] = mc

spec = importlib.util.spec_from_file_location('battle_lab.mc_rl_light_v2', r'${script.replaceAll("\\", "\\\\")}')
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

root = pathlib.Path(tempfile.mkdtemp())
checkout = root / 'vgc'
(checkout / 'vgc_bench' / 'src').mkdir(parents=True)
(checkout / 'vgc_bench' / 'src' / 'utils.py').write_text(
    "abilities=['null','aerilate','sharpness']\nitems=['null','absolite']\n"
)
before_path = list(sys.path)
result = mod.extend_mc_runtime_catalogs(checkout)
utils = __import__('vgc_bench.src.utils', fromlist=['x'])
assert utils.abilities[:3] == ['null','aerilate','sharpness']
assert utils.abilities[-1] == 'auraguard'
assert result['abilities'] == ['auraguard']
result2 = mod.extend_mc_runtime_catalogs(checkout)
assert result2['abilities'] == []
assert utils.abilities.count('auraguard') == 1
sys.path[:] = before_path
print('ok')
`;
  const output = execFileSync("python", ["-c", program], { cwd: root, encoding: "utf8" });
  assert.match(output, /ok/);
});
