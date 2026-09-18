import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python";
const modulePath = path.join(root, "battle_lab", "nana_legal_orders.py");

test("LegalOrderSource enumerates compatible orders and SafetyGate fails closed", () => {
  const script = String.raw`
import numpy as np
from types import SimpleNamespace
from poke_env.environment import DoublesEnv
from poke_env.player.battle_order import SingleBattleOrder
from battle_lab.nana_legal_orders import LegalOrderSource, SafetyGate, LegalOrderSourceError

first=[
    SingleBattleOrder("/choose move a", mega=True),
    SingleBattleOrder("/choose move b"),
]
second=[
    SingleBattleOrder("/choose move c", mega=True),
    SingleBattleOrder("/choose move d"),
]
battle=SimpleNamespace(
    valid_orders=[first,second],
    battle_tag="battle-test",
    turn=3,
    _wait=False,
    teampreview=False,
)
mapping={}
reverse={}
counter=1
from poke_env.player.battle_order import DoubleBattleOrder
for order in DoubleBattleOrder.join_orders(first,second):
    arr=np.asarray([counter,counter+20],dtype=np.int64)
    mapping[str(order)]=arr
    reverse[tuple(arr.tolist())]=order
    counter+=1

old_o2a=DoublesEnv.order_to_action
old_a2o=DoublesEnv.action_to_order
try:
    DoublesEnv.order_to_action=staticmethod(lambda order,battle,fake=False,strict=True: mapping[str(order)])
    DoublesEnv.action_to_order=staticmethod(lambda action,battle,fake=False,strict=True: reverse[tuple(np.asarray(action).tolist())])
    result=LegalOrderSource().enumerate(battle)
finally:
    DoublesEnv.order_to_action=old_o2a
    DoublesEnv.action_to_order=old_a2o

# 4 Cartesian combinations minus the illegal double-Mega pair.
assert result.resolved is True
assert len(result.candidates) == 3
assert result.joined_count == 3
assert result.individual_counts == (2,2)
assert len(result.keys) == 3
gate=SafetyGate(result)
chosen=gate.authorize_key(result.candidates[0].key)
assert chosen.key == result.candidates[0].key
try:
    gate.authorize_key("order:v1:not-real")
except LegalOrderSourceError:
    pass
else:
    raise AssertionError("SafetyGate must reject unknown orders")
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LegalOrderSource fails closed on round-trip divergence", () => {
  const script = String.raw`
import numpy as np
from types import SimpleNamespace
from poke_env.environment import DoublesEnv
from poke_env.player.battle_order import SingleBattleOrder, DoubleBattleOrder
from battle_lab.nana_legal_orders import LegalOrderSource, LegalOrderSourceError

first=[SingleBattleOrder("/choose move a")]
second=[SingleBattleOrder("/choose move b")]
battle=SimpleNamespace(valid_orders=[first,second],battle_tag="x",turn=1,_wait=False,teampreview=False)
joined=DoubleBattleOrder.join_orders(first,second)[0]
other=DoubleBattleOrder(SingleBattleOrder("/choose move x"),SingleBattleOrder("/choose move y"))
old_o2a=DoublesEnv.order_to_action
old_a2o=DoublesEnv.action_to_order
try:
    DoublesEnv.order_to_action=staticmethod(lambda order,battle,fake=False,strict=True: np.asarray([1,2],dtype=np.int64))
    DoublesEnv.action_to_order=staticmethod(lambda action,battle,fake=False,strict=True: other)
    try:
        LegalOrderSource().enumerate(battle)
    except LegalOrderSourceError:
        pass
    else:
        raise AssertionError("round-trip divergence must fail closed")
finally:
    DoublesEnv.order_to_action=old_o2a
    DoublesEnv.action_to_order=old_a2o
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LegalOrderSource explicitly defers wait and Team Preview", () => {
  const script = String.raw`
from types import SimpleNamespace
from battle_lab.nana_legal_orders import LegalOrderSource
source=LegalOrderSource()
wait=source.enumerate(SimpleNamespace(_wait=True,teampreview=False,battle_tag='w',turn=1))
preview=source.enumerate(SimpleNamespace(_wait=False,teampreview=True,battle_tag='p',turn=0))
assert wait.resolved is False and wait.reason == 'showdown-wait'
assert preview.resolved is False and preview.reason == 'team-preview-not-in-n4-order-source'
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LegalOrderSource has no teacher-policy dependency and compiles", () => {
  const source = readFileSync(modulePath, "utf8");
  assert.doesNotMatch(source, /vgc_bench/);
  assert.doesNotMatch(source, /action_map/);
  assert.doesNotMatch(source, /get_logits/);
  assert.match(source, /battle\.valid_orders/);
  assert.match(source, /DoubleBattleOrder\.join_orders/);
  assert.match(source, /DoublesEnv\.order_to_action/);
  assert.match(source, /DoublesEnv\.action_to_order/);
  execFileSync(python, ["-m", "py_compile", modulePath], { cwd: root, encoding: "utf8" });
});
