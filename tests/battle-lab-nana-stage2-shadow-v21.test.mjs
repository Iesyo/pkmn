import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";

function py(script) {
  return execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
}

test("Nana 2 v2.1 resolves damaging spread metadata from poke-env", () => {
  py(String.raw`
from battle_lab.nana_stage2_shadow_common import _move_metadata

target, bp = _move_metadata("dazzlinggleam")
assert target == "ALL_ADJACENT_FOES", (target, bp)
assert bp > 0, (target, bp)

target, bp = _move_metadata("earthquake")
assert target == "ALL_ADJACENT", (target, bp)
assert bp > 0, (target, bp)
`);
});

test("Nana 2 v2.1 treats Protect against spread as partial evidence", () => {
  py(String.raw`
from battle_lab.nana_stage2_shadow_common import response_utility

human_protect = {
    "first": {"kind":"move","value":"protect","target":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
model_spread = {
    "first": {"kind":"move","value":"dazzlinggleam","target":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
result = response_utility(model_spread, human_protect)
assert result["relevant"] == 1, result
assert abs(result["score"] + 0.5) < 1e-12, result
assert result["components"][0]["rule"] == "predicted-human-protect-vs-spread"

human_spread = {
    "first": {"kind":"move","value":"earthquake","target":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
model_protect = {
    "first": {"kind":"move","value":"protect","target":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
result = response_utility(model_protect, human_spread)
assert result["relevant"] == 1, result
assert abs(result["score"] - 0.5) < 1e-12, result
assert result["components"][0]["rule"] == "predicted-human-spread-vs-protect"
`);
});

test("Nana 2 v2.1 keeps status spread-like metadata out of the proxy", () => {
  py(String.raw`
from battle_lab.nana_stage2_shadow_common import response_utility

human_protect = {
    "first": {"kind":"move","value":"protect","target":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
status_like = {
    "first": {"kind":"move","value":"fake-status","target":0,"targetType":"ALL_ADJACENT_FOES","basePower":0,"flags":[]},
    "second": {"kind":"move","value":"tailwind","target":0,"flags":[]},
}
result = response_utility(status_like, human_protect)
assert result["relevant"] == 0, result
assert result["score"] == 0.0, result
`);
});

test("Nana 2 v2.1 entrypoints are version-isolated and compile", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_v21_runtime import STAGE2_MODEL_VERSION as runtime_version
from battle_lab.nana_stage2_shadow_v21_report import STAGE2_MODEL_VERSION as report_version
assert runtime_version == "nana2-shadow-v2.1-spread-aware"
assert report_version == runtime_version
`;
  py(script);

  for (const filename of [
    "battle_lab/nana_stage2_shadow_common.py",
    "battle_lab/nana_stage2_shadow_v21_runtime.py",
    "battle_lab/nana_stage2_shadow_v21_report.py",
  ]) {
    execFileSync(python, ["-m", "py_compile", path.join(root, filename)], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
