import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const runner = path.join(root, "battle_lab", "mc_holdout_benchmark.py");
const notebook = path.join(root, "colab", "Battle_Lab_MC_Holdout_Benchmark.ipynb");

test("holdout benchmark freezes the 36-team protocol and checkpoint-compatible alias", () => {
  const source = fs.readFileSync(runner, "utf8");
  assert.match(source, /DEFAULT_HOLDOUT_COUNT = 36/);
  assert.match(source, /DEFAULT_BATTLES_PER_BASELINE = 500/);
  assert.match(source, /PRIMARY_BASELINE_ID = ['\"]simple-heuristics['\"]/);
  assert.match(source, /alias_mc_runtime_catalogs/);
  assert.match(source, /signatureLeakage/);
  assert.match(source, /sameScheduleForBothCheckpoints/);
  assert.match(source, /sameScheduleForEveryControl/);
  assert.match(source, /totalBattles/);
  assert.match(source, /chunks/);
  execFileSync("python", ["-m", "py_compile", runner], { cwd: root, encoding: "utf8" });
});

test("holdout Colab runs unbuffered, resumable, and never points at train teams", () => {
  const parsed = JSON.parse(fs.readFileSync(notebook, "utf8"));
  const text = parsed.cells.flatMap((cell) => cell.source ?? []).join("");
  assert.match(text, /BATTLES_PER_CONTROL = 500/);
  assert.match(text, /HOLDOUT_DIR = SPLIT_ROOT \/ \"holdout\"/);
  assert.match(text, /step-000196608\.zip/);
  assert.match(text, /battle_lab\.mc_holdout_benchmark/);
  assert.match(text, /\"-u\"/);
  assert.match(text, /--resume/);
  assert.doesNotMatch(text, /TRAIN_TEAM_DIR/);
});
