import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_rl_light.py");
const notebook = path.join(root, "colab", "Battle_Lab_MC_Light.ipynb");

test("LIGHT runner skips hidden VGC-Bench eval and persists visible progress", () => {
  const source = fs.readFileSync(script, "utf8");
  assert.match(source, /PPO\/self-play iniciado/);
  assert.match(source, /RL M-C \[/);
  assert.match(source, /status\.json/);
  assert.match(source, /step-\{current:09d\}\.zip/);
  assert.match(source, /ACTOR_UNFREEZE_STEP = 98_304/);
  assert.doesNotMatch(source, /\.compare\(/);
  assert.doesNotMatch(source, /evaluate=True/);
  execFileSync("python", ["-m", "py_compile", script], { cwd: root, encoding: "utf8" });
});

test("LIGHT Colab is isolated, resumable, and starts the visible runner", () => {
  const parsed = JSON.parse(fs.readFileSync(notebook, "utf8"));
  const text = parsed.cells.flatMap((cell) => cell.source ?? []).join("");
  assert.match(text, /TOTAL_STEPS = 196_608/);
  assert.match(text, /CHECKPOINT_EVERY = 24_576/);
  assert.match(text, /battle_lab\.mc_rl_light/);
  assert.match(text, /TRAIN_TEAM_DIR/);
  assert.match(text, /running_showdown/);
  assert.match(text, /145/);
  assert.match(text, /holdoutTeams.*36/s);
});
