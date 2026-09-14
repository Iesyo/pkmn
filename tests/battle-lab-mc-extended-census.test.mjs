import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_census_extended.py");
const notebook = path.join(root, "colab", "Battle_Lab_MC_Census_Extended.ipynb");

test("extended census targets BO3 only and preserves manifest aggregation", () => {
  const source = fs.readFileSync(script, "utf8");
  assert.match(source, /DEFAULT_FORMAT_BO3/);
  assert.doesNotMatch(source, /DEFAULT_FORMAT\b(?!_BO3)/);
  assert.match(source, /manifest\.setdefault\("formats", \{\}\)/);
  assert.match(source, /startingAcceptedLogs/);
  assert.match(source, /targetAcceptedLogs/);
  assert.match(source, /sourceExhausted/);
  assert.match(source, /default=10_000/);
  assert.match(source, /default=5_000/);
  execFileSync("python", ["-m", "py_compile", script], { cwd: root, encoding: "utf8" });
});

test("extended census Colab reuses corpus, rebuilds trajectories, and writes native result", () => {
  const parsed = JSON.parse(fs.readFileSync(notebook, "utf8"));
  const text = parsed.cells.flatMap((cell) => cell.source ?? []).join("");
  assert.match(text, /TARGET_BO3_LOGS = 10_000/);
  assert.match(text, /battle_lab\.mc_census_extended/);
  assert.match(text, /build-trajectories/);
  assert.match(text, /battle_lab\.mc_team_split/);
  assert.match(text, /application\/vnd\.google-apps\.document/);
  assert.match(text, /Battle Lab M-C — latest run/);
});
