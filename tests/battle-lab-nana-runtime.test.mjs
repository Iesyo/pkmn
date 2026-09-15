import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const runtime = path.join(root, "battle_lab", "nana_runtime.py");

test("Nana 0 observes but delegates the actual move to canonical LIGHT", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /light = inspect_light_decision\(self, current\)/);
  assert.match(source, /order = super\(\)\.choose_move\(current\)/);
  assert.match(source, /return order/);
  assert.match(source, /"influence": 0\.0/);
  assert.match(source, /NanaRecorder/);
  assert.match(source, /record_team_preview/);
  assert.match(source, /record_turn/);
  assert.match(source, /finish_session/);
  assert.doesNotMatch(source, /load_state_dict/);
  assert.doesNotMatch(source, /policy\s*=/);
});

test("Nana safely reuses only the exact pinned static Showdown renderer", () => {
  const source = readFileSync(runtime, "utf8");
  assert.match(source, /_VIEWER_ASSETS/);
  assert.match(source, /testclient-old\.html/);
  assert.match(source, /js\/battle\.js/);
  assert.match(source, /hashlib\.sha256\(local_path\.read_bytes\(\)\)/);
  assert.match(source, /hashlib\.sha256\(remote_bytes\)/);
  assert.match(source, /Nana lo reutilizará sin tomar propiedad del proceso/);
  assert.match(source, /return _BorrowedViewerProcess\(\), _BorrowedViewerLog\(\)/);
  assert.match(source, /def poll\(\) -> int:/);
  assert.match(source, /return 0/);
  assert.match(source, /install_reusable_viewer\(local_runtime\)/);
  assert.doesNotMatch(source, /terminate\(/);
  assert.doesNotMatch(source, /kill\(/);
});

test("Nana modules are syntactically valid", () => {
  for (const filename of ["nana_policy.py", "nana_recorder.py", "nana_runtime.py", "nana_predictor.py", "nana_stage1_runtime.py", "nana_stage1_audit.py"]) {
    execFileSync(process.env.PYTHON ?? "python3", ["-m", "py_compile", path.join(root, "battle_lab", filename)], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
