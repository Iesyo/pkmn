import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const nativeBridge = path.join(root, "battle_lab", "native_showdown_controls.py");

test("native Showdown room id survives missing transient battle_tag", () => {
  const script = String.raw`
from types import SimpleNamespace
from battle_lab.native_showdown_controls import _room_tag_for_battle, _snapshot_with_room_tag

class Battle:
    battle_tag = ""
    _battle_tag = ""
    turn = 0
    finished = False
    won = False
    lost = False
    weather = {}
    fields = {}
    team = {}
    opponent_team = {}

battle = Battle()
player = SimpleNamespace(battles={"battle-gen9vgc2026regi-123": battle})
session = SimpleNamespace(room_tag="", battle_state={})

assert _room_tag_for_battle(player, battle) == "battle-gen9vgc2026regi-123"
snapshot = _snapshot_with_room_tag(session, player, battle)
assert snapshot["tag"] == "battle-gen9vgc2026regi-123"
assert session.room_tag == "battle-gen9vgc2026regi-123"

player.battles = {}
next_battle = Battle()
next_snapshot = _snapshot_with_room_tag(session, player, next_battle)
assert next_snapshot["tag"] == "battle-gen9vgc2026regi-123"
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("native player publishes the room id at battle creation and reuses it in snapshots", () => {
  const source = readFileSync(nativeBridge, "utf8");
  assert.match(source, /async def _create_battle\(self, split_message: list\[str\]\)/);
  assert.match(source, /target\.room_tag = room_tag/);
  assert.match(source, /_snapshot_with_room_tag\(target, self, current_battle\)/);
  assert.match(source, /if isinstance\(battle, dict\) and not battle\.get\("tag"\)/);
});
