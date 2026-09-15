import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const launcher = path.join(root, "battle_lab", "nana_stage2_shadow_v21_lan_runtime.py");
const viewer = path.join(root, "battle_lab", "showdown_native_viewer.py");
const adapter = path.join(root, "components", "vgc", "war-room-sparring.tsx");
const warRoom = path.join(root, "components", "vgc", "war-room.tsx");

test("direct LAN runtime keeps Nana v2.1 canonical and exposes only transport ports", () => {
  const source = readFileSync(launcher, "utf8");
  assert.match(source, /STAGE2_MODEL_VERSION/);
  assert.match(source, /install_nana_stage2_shadow_v2_service/);
  assert.match(source, /host="0\.0\.0\.0"/);
  assert.match(source, /--bind[\s\S]*0\.0\.0\.0/);
  assert.match(source, /exports\.bindaddress = '0\.0\.0\.0'/);
  assert.match(source, /Private networks only|redes Privadas/);
  assert.doesNotMatch(source, /lan_tunnel/);
});

test("native viewer follows the host that actually served it", () => {
  const source = readFileSync(viewer, "utf8");
  assert.match(source, /location\.hostname \+ ':8765'/);
  assert.doesNotMatch(source, /var api = 'http:\/\/127\.0\.0\.1:8765'/);
});

test("War Room transport adapter rewrites native Showdown host for LAN browsers", () => {
  assert.equal(existsSync(adapter), true);
  const source = readFileSync(adapter, "utf8");
  const warRoomSource = readFileSync(warRoom, "utf8");
  assert.match(warRoomSource, /from "@\/components\/vgc\/war-room-sparring"/);
  assert.match(source, /window\.location\.hostname/);
  assert.match(source, /127\.0\.0\.1:8767/);
  assert.match(source, /~~127\.0\.0\.1:8766/);
  assert.match(source, /~~\$\{host\}:8766/);
});

test("direct LAN Python modules compile", () => {
  for (const filename of [launcher, viewer]) {
    execFileSync(python, ["-m", "py_compile", filename], {
      cwd: root,
      encoding: "utf8",
    });
  }
});
