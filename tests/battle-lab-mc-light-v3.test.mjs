import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_rl_light_v3.py");

test("LIGHT v3 preserves embedding cardinality and reuses a VGC-impossible slot", () => {
  const source = fs.readFileSync(script, "utf8");
  assert.match(source, /MC_ABILITY_SLOT_ALIASES = \{"auraguard": "mountaineer"\}/);
  assert.match(source, /before_len = len\(utils\.abilities\)/);
  assert.match(source, /utils\.abilities\[slot\] = mc_value/);
  assert.match(source, /len\(utils\.abilities\) != before_len/);
  assert.match(source, /v2\.extend_mc_runtime_catalogs = alias_mc_runtime_catalogs/);
  execFileSync("python", ["-m", "py_compile", script], { cwd: root, encoding: "utf8" });
});

test("Aura Guard alias is length-preserving, index-stable for other abilities, and idempotent", () => {
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
mc.working_directory = wd
pkg = types.ModuleType('battle_lab')
pkg.__path__ = []
sys.modules['battle_lab'] = pkg
sys.modules['battle_lab.mc_training'] = mc

v2 = types.ModuleType('battle_lab.mc_rl_light_v2')
v2.main = lambda argv=None: 0
sys.modules['battle_lab.mc_rl_light_v2'] = v2
pkg.mc_rl_light_v2 = v2

spec = importlib.util.spec_from_file_location('battle_lab.mc_rl_light_v3', r'${script.replaceAll("\\", "\\\\")}')
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

root = pathlib.Path(tempfile.mkdtemp())
checkout = root / 'vgc'
(checkout / 'vgc_bench' / 'src').mkdir(parents=True)
(checkout / 'vgc_bench' / '__init__.py').write_text('')
(checkout / 'vgc_bench' / 'src' / '__init__.py').write_text('')
(checkout / 'vgc_bench' / 'src' / 'utils.py').write_text(
    "abilities=['null','aerilate','mountaineer','sharpness']\n"
)

before_path = list(sys.path)
result = mod.alias_mc_runtime_catalogs(checkout)
utils = __import__('vgc_bench.src.utils', fromlist=['x'])
assert len(utils.abilities) == 4
assert utils.abilities == ['null','aerilate','auraguard','sharpness']
assert result['abilities'] == ['auraguard@2<-mountaineer']
result2 = mod.alias_mc_runtime_catalogs(checkout)
assert len(utils.abilities) == 4
assert utils.abilities == ['null','aerilate','auraguard','sharpness']
assert result2['abilities'] == ['auraguard@2']
sys.path[:] = before_path
print('ok')
`;
  const output = execFileSync("python", ["-c", program], { cwd: root, encoding: "utf8" });
  assert.match(output, /ok/);
});
