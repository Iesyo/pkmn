import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const audit = path.join(root, "battle_lab", "auto_lab_audit.py");
const core = path.join(root, "battle_lab", "auto_lab.py");
const service = path.join(root, "battle_lab", "auto_lab_service.py");
const ui = path.join(root, "components", "vgc", "war-room-auto-lab-v2.tsx");

test("Auto Lab indexes replay files once instead of rescanning the tree per battle", () => {
  const source = fs.readFileSync(audit, "utf8");
  assert.match(source, /def _index_replay_paths\(/);
  assert.match(source, /paths = list\(replay_root\.rglob\("\*\.html"\)\)/);
  assert.match(source, /replay_index = _index_replay_paths\(replay_root, battle_tags\)/);
  assert.match(source, /path = replay_index\.get\(tag\) if tag else None/);
  assert.doesNotMatch(source, /rglob\(f"\*\{battle_tag\}\*\.html"\)/);

  const script = `
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.auto_lab_audit import _index_replay_paths

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    tags = [f"battle-gen9championsvgc2026regmc-{index}" for index in range(1000)]
    for index, tag in enumerate(tags):
        folder = root / f"opponent-{index // 10}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{tag}.html").write_text("<html></html>", encoding="utf-8")
    index = _index_replay_paths(root, tags)
    assert len(index) == 1000, len(index)
    assert all(index[tag].name == f"{tag}.html" for tag in tags)
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Deep finalization leaves the event loop free and exposes a finalizing phase", () => {
  const source = fs.readFileSync(core, "utf8");
  const serviceSource = fs.readFileSync(service, "utf8");
  assert.match(source, /"phase": "finalizing"/);
  assert.match(source, /audit = await asyncio\.to_thread\(\s*build_auto_lab_audit,/s);
  assert.match(source, /"opponentId": opponent\.id,[\s\S]*completedBattles/);
  assert.match(serviceSource, /payload_phase in \{"running", "finalizing"\}/);
  assert.match(serviceSource, /Generando informe…/);
  execFileSync("python", ["-m", "py_compile", core, audit, service], { cwd: root, encoding: "utf8" });
});

test("War Room retries transient Auto Lab poll failures instead of abandoning the finished job", () => {
  const source = fs.readFileSync(ui, "utf8");
  assert.match(source, /"finalizing"/);
  assert.match(source, /pollFailuresRef/);
  assert.match(source, /reintentando consulta de estado/);
  assert.match(source, /window\.setTimeout\(\(\) => void poll\(jobId\), delay\)/);
  assert.match(source, /Generando informe de combate/);
});
