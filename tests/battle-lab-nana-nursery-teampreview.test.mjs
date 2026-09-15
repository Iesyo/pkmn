import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";
const lanRuntime = path.join(
  root,
  "battle_lab",
  "nana_stage2_nursery_lan_runtime.py",
);

test("Nursery LAN keeps VGC-Bench Team Preview synchronous and normal turns awaitable", () => {
  const script = String.raw`
import asyncio
import inspect
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def append_event(self, *args): pass

class ParentPlayer:
    def __init__(self, *args, **kwargs): pass
    async def _handle_battle_request(self, battle, maybe_default_order=False): return None
    async def choose_move(self, current): return 'PARENT-ASYNC'
    def _raw_light_choose(self, current): return 'LIGHT'
    def teampreview(self, current):
        first = self.choose_move(current)
        assert not inspect.isawaitable(first), 'Team Preview received an Awaitable'
        second = self.choose_move(current)
        assert not inspect.isawaitable(second), 'Team Preview received an Awaitable'
        return f'{first}|{second}'

class Runtime:
    player_class = ParentPlayer

class Service:
    def __init__(self):
        self.runtime = Runtime()
        self.active_session = None
        self.nana = Nana()
        self._nana_teacher = {'key':'K'}
        self._nana_nursery_model_generation = {}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service = Service()
    await service.ensure_ready()
    player = service.runtime.player_class()

    preview = SimpleNamespace(teampreview=True, turn=0)
    result = player.teampreview(preview)
    assert result == 'LIGHT|LIGHT'

    normal = SimpleNamespace(teampreview=False, turn=1, force_switch=[])
    pending = player.choose_move(normal)
    assert inspect.isawaitable(pending), 'Normal Nursery turn must remain awaitable'
    assert await pending == 'PARENT-ASYNC'

asyncio.run(main())
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("LAN dispatcher routes preview before creating the live coroutine", () => {
  const source = readFileSync(lanRuntime, "utf8");
  const choose = source.indexOf("def choose_move(self, current: Any):");
  const preview = source.indexOf('getattr(current, "teampreview", False)', choose);
  const rawLight = source.indexOf("return self._raw_light_choose(current)", preview);
  const live = source.indexOf("return self._choose_move_live(current)", rawLight);
  const asyncLive = source.indexOf("async def _choose_move_live(self, current: Any):", live);
  assert.ok(choose >= 0 && preview > choose && rawLight > preview && live > rawLight && asyncLive > live);
});
