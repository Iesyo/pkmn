import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const audit = path.join(root, "battle_lab", "auto_lab_audit.py");

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
  execFileSync("python", ["-m", "py_compile", audit], { cwd: root, encoding: "utf8" });
});
