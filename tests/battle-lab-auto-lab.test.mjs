import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const core = path.join(root, "battle_lab", "auto_lab.py");
const service = path.join(root, "battle_lab", "auto_lab_service.py");
const runtime = path.join(root, "battle_lab", "local_runtime.py");
const proxy = path.join(root, "app", "api", "battle-lab", "[...path]", "route.ts");
const variants = path.join(root, "lib", "war-room-auto-lab.ts");
const panel = path.join(root, "components", "vgc", "war-room-auto-lab.tsx");
const sparring = path.join(root, "components", "vgc", "war-room-sparring.tsx");

test("Auto Lab keeps LIGHT fixed and compares identical opponent/side schedules", () => {
  const source = fs.readFileSync(core, "utf8");
  assert.match(source, /LIGHT-vs-LIGHT gauntlet/);
  assert.match(source, /battles_per_opponent % 2/);
  assert.match(source, /battle_index % 2 == 0/);
  assert.match(source, /run_vgc_bench_battles/);
  assert.match(source, /compare_with_baseline/);
  assert.match(source, /not presented as a ladder/);
  execFileSync("python", ["-m", "py_compile", core, service], { cwd: root, encoding: "utf8" });
});

test("Auto Lab strips Nana wrappers instead of benchmarking the adaptive layer", () => {
  const source = fs.readFileSync(service, "utf8");
  assert.match(source, /candidate\.__name__ == "BattleLabPolicyPlayer"/);
  assert.match(source, /"adaptiveLayer": False/);
  assert.match(source, /_frozen_light_runtime\(self\.runtime\)/);
  assert.match(source, /Ya existe un Gauntlet Auto Lab activo/);
});

test("local runtime and loopback proxy expose Auto Lab without changing Sparring routes", () => {
  assert.match(fs.readFileSync(runtime, "utf8"), /install_auto_lab_service\(\)/);
  const route = fs.readFileSync(proxy, "utf8");
  assert.match(route, /auto-lab/);
  assert.match(route, /sparring/);
  assert.match(route, /model-info/);
});

test("War Room generates attributable one-field variants before battle validation", () => {
  const source = fs.readFileSync(variants, "utf8");
  assert.match(source, /MAX_AUTO_LAB_VARIANTS = 4/);
  assert.match(source, /applySingleChange/);
  assert.match(source, /change\.key\.startsWith\("move-"\)/);
  assert.match(source, /statPoints/);
  assert.doesNotMatch(source, /species:\s*change\.suggested/);
});

test("War Room surfaces Auto Lab with progress, ETA and A\/B results", () => {
  const ui = fs.readFileSync(panel, "utf8");
  assert.match(ui, /Auto Lab · Auditar \+ Optimizar \+ Gauntlet/);
  assert.match(ui, /\/api\/battle-lab\/auto-lab/);
  assert.match(ui, /etaSeconds/);
  assert.match(ui, /deltaPercentagePoints/);
  assert.match(ui, /Nana no participa/);
  const adapter = fs.readFileSync(sparring, "utf8");
  assert.match(adapter, /Auto Lab · Auditar \+ Optimizar/);
  assert.match(adapter, /WarRoomAutoLab/);
});
