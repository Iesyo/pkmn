import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const viewer = path.join(root, "battle_lab", "sparring_speed_viewer_v2.py");
const launcher = path.join(root, "battle_lab", "nana_stage2_nursery_speed_tier_lan_runtime.py");

test("Speed Tier v2 reserves a visible left column instead of inferring Showdown gutter geometry", () => {
  const source = readFileSync(viewer, "utf8");
  assert.match(source, /display:block!important/);
  assert.match(source, /left:328px!important/);
  assert.match(source, /width:calc\(100% - 328px\)!important/);
  assert.doesNotMatch(source, /querySelector\('\.battle'\)/);
});

test("Speed Tier v2 polls Battle Lab independently and surfaces missing backend data", () => {
  const source = readFileSync(viewer, "utf8");
  assert.match(source, /fetch\(api \+ '\/health'/);
  assert.match(source, /fetch\(api \+ '\/sparring\/'/);
  assert.match(source, /snapshot\.battle\.speedTier/);
  assert.match(source, /Esperando datos de Speed Tier/);
  assert.match(source, /Speed Tier sin filas/);
});

test("Speed Tier v2 is the only panel writer after battle completion", () => {
  const script = String.raw`
from battle_lab.sparring_speed_viewer_v2 import BRIDGE_HTML
assert "if (!activeSession) { renderSpeedTier(null); return; }" not in BRIDGE_HTML
assert "renderSpeedTier(snapshot && snapshot.battle ? snapshot.battle.speedTier : null);" not in BRIDGE_HTML
assert "if (!activeSession) return;" in BRIDGE_HTML
assert "Speed Tier v2 owns panel rendering." in BRIDGE_HTML
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Speed Tier launcher installs LAN first, then Speed Tier viewer, then Nana reuse wrapper", () => {
  const source = readFileSync(launcher, "utf8");
  const lanIndex = source.indexOf("lan.install_direct_lan(local_runtime)");
  const speedIndex = source.lastIndexOf("_install_speed_layer_after_lan()");
  const reuseIndex = source.indexOf("install_reusable_viewer(local_runtime)");
  assert.ok(lanIndex >= 0, "LAN bootstrap must be installed");
  assert.ok(speedIndex > lanIndex, "Speed Tier must replace the LAN viewer after LAN bootstrap");
  assert.ok(reuseIndex > speedIndex, "Nana reuse wrapper must wrap the final Speed Tier viewer");
  assert.doesNotMatch(source, /return nursery\.main\(argv\)/);
});

test("existing Speed Tier launcher promotes Nana directly to Full Amiibo N4", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /full_amiibo_live=True/);
  assert.match(source, /Nana Full Amiibo N4 LIVE \+ Speed Tier/);
  assert.match(source, /sin λ cap y sin presupuesto de intervenciones/);
  assert.match(source, /LIGHT es advisor\/fallback/);
});

test("Speed Tier LAN viewer launches as a package module from repo root", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /project_root = Path\(__file__\)\.resolve\(\)\.parents\[1\]/);
  assert.match(source, /"-m",\s*"battle_lab\.sparring_speed_viewer_v2"/s);
  assert.match(source, /cwd=project_root/);
  assert.doesNotMatch(source, /str\(viewer_server\)/);
});

test("Speed Tier LAN viewer binds on the trusted LAN and requires the v4 marker", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /battle-lab-native-showdown-controls-v4-speed-tier/);
  assert.match(source, /"--bind",\s*"0\.0\.0\.0"/s);
  assert.match(source, /local_runtime\.start_viewer_server = _start_speed_viewer/);
  assert.match(source, /local_runtime\.NATIVE_BRIDGE_MARKER = SPEED_VIEWER_MARKER/);
});

test("Speed Tier v2 modules compile", () => {
  for (const filename of [viewer, launcher]) {
    execFileSync(python, ["-m", "py_compile", filename], { cwd: root, encoding: "utf8" });
  }
});
