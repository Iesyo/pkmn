import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const nativeBridge = path.join(root, "battle_lab", "native_showdown_controls.py");
const viewerBridge = path.join(root, "battle_lab", "showdown_native_viewer.py");
const localRuntime = path.join(root, "battle_lab", "local_runtime.py");

test("native Showdown bridge translates canonical client commands without bypassing submit hooks", () => {
  const script = String.raw`
from battle_lab.native_showdown_controls import (
    _native_single_choice,
    _normalize_native_command,
    _team_preview_order,
)

assert _normalize_native_command('/choose move 1 1, move 2 terastallize 2|9') == 'choose move 1 1,move 2 terastallize 2'
assert _team_preview_order('/team 1234|7') == [1, 2, 3, 4]
assert _team_preview_order('/team 1,2,3,4|7') == [1, 2, 3, 4]

class Move:
    id = 'protect'
class Pokemon:
    name = 'Bench'
    species = 'Incineroar'
class Order:
    mega = False
    z_move = False
    dynamax = False
    terastallize = True
    move_target = 2

request = {
    'active': [
        {'moves': [{'move': 'Protect', 'id': 'protect'}]},
        {'moves': [{'move': 'Icy Wind', 'id': 'icywind'}]},
    ],
    'side': {'pokemon': [
        {'ident': 'p1: Lead', 'details': 'Garchomp, L50'},
        {'ident': 'p1: Bench', 'details': 'Incineroar, L50'},
    ]},
}
move_order = Order()
move_order.order = Move()
assert _native_single_choice(move_order, position=0, request=request) == 'move 1 terastallize 2'
switch_order = Order()
switch_order.order = Pokemon()
switch_order.terastallize = False
switch_order.move_target = 0
assert _native_single_choice(switch_order, position=0, request=request) == 'switch 2'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("native service exposes raw Showdown request while preserving Nana submit path", () => {
  const source = readFileSync(nativeBridge, "utf8");
  assert.match(source, /getattr\(current_battle, "last_request"/);
  assert.match(source, /native_choices/);
  assert.match(source, /return await self\.submit_preview\(session_id, order\)/);
  assert.match(source, /return await self\.submit_choice\(session_id, choice_id\)/);
  assert.match(source, /native-team-preview/);
  assert.match(source, /native-waiting-choice/);
  assert.match(source, /\/sparring\/\{session_id\}\/native-choice/);
});

test("viewer bridge keeps vendor client untouched and drives BattleRoom native controls", () => {
  const source = readFileSync(viewerBridge, "utf8");
  assert.match(source, /battle-lab-native-showdown-controls-v1/);
  assert.match(source, /battle-lab-vendor\.html/);
  assert.match(source, /room\.receiveRequest\(request, null\)/);
  assert.equal(source.includes("/^\\/(?:choose |team )/"), true);
  assert.match(source, /\/native-choice/);
  assert.match(source, /like-no-one-ever-was-nana\/0/);
  assert.doesNotMatch(source, /write_text\(.+testclient-old\.html/);
});

test("local runtime installs native controls above the active Nana/plain service", () => {
  const source = readFileSync(localRuntime, "utf8");
  assert.match(source, /install_native_showdown_controls\(\)/);
  assert.match(source, /showdown_native_viewer\.py/);
  assert.match(source, /assets de Pokémon Showdown Client se sirven sin modificar/);
});

test("native Showdown bridge modules compile", () => {
  for (const filename of [nativeBridge, viewerBridge, localRuntime]) {
    execFileSync(python, ["-m", "py_compile", filename], { cwd: root, encoding: "utf8" });
  }
});
