import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";

function runPython(script) {
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
}

test("guard serializes even different rqid handlers", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def append_event(self, *args): pass

class ParentPlayer:
    active=0
    max_active=0
    async def _handle_battle_request(self, battle, maybe_default_order=False):
        ParentPlayer.active += 1
        ParentPlayer.max_active=max(ParentPlayer.max_active,ParentPlayer.active)
        try:
            await asyncio.sleep(0.02)
        finally:
            ParentPlayer.active -= 1
    async def choose_move(self, current): return 'nursery'
    def _raw_light_choose(self, current): return 'LIGHT'

class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(id='s1',generation=0,phase='resolving')
        self._nana_nursery_model_generation={'s1':0}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    a=SimpleNamespace(last_request={'rqid':1},battle_tag='b',turn=1,_wait=False)
    b=SimpleNamespace(last_request={'rqid':2},battle_tag='b',turn=1,_wait=False)
    await asyncio.gather(player._handle_battle_request(a),player._handle_battle_request(b))
    assert ParentPlayer.max_active == 1

asyncio.run(main())
`);
});

test("maybe_default_order retry bypasses normal dedupe", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def __init__(self): self.events=[]
    def append_event(self,*args): self.events.append(args)
class ParentPlayer:
    calls=0
    async def _handle_battle_request(self,battle,maybe_default_order=False): ParentPlayer.calls += 1
    async def choose_move(self,current): return 'nursery'
    def _raw_light_choose(self,current): return 'LIGHT'
class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(id='s1',generation=0,phase='resolving')
        self._nana_nursery_model_generation={'s1':0}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    battle=SimpleNamespace(last_request={'rqid':7},battle_tag='b',turn=1,_wait=False)
    await player._handle_battle_request(battle)
    await player._handle_battle_request(battle,maybe_default_order=True)
    assert ParentPlayer.calls == 2

asyncio.run(main())
`);
});

test("already-consumed human prompt fails closed without entering Nursery barrier", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def __init__(self): self.events=[]
    def append_event(self,sid,typ,payload): self.events.append((typ,payload))
class ParentPlayer:
    async def _handle_battle_request(self,battle,maybe_default_order=False): pass
    async def choose_move(self,current): raise AssertionError('barrier must not run')
    def _raw_light_choose(self,current): return 'LIGHT'
class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(id='s1',generation=3,phase='resolving')
        self._nana_nursery_model_generation={'s1':2}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    result=await player.choose_move(SimpleNamespace(turn=4,force_switch=[False,False]))
    assert result == 'LIGHT'
    assert service._nana_nursery_model_generation['s1'] == 3
    assert service.nana.events[-1][1]['reason'] == 'human-prompt-already-consumed'

asyncio.run(main())
`);
});

test("both-side forced switch accepts a human prompt arriving at 40ms", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def __init__(self): self.events=[]
    def append_event(self,sid,typ,payload): self.events.append((typ,payload))
class ParentPlayer:
    async def _handle_battle_request(self,battle,maybe_default_order=False): pass
    async def choose_move(self,current): return 'nursery'
    def _raw_light_choose(self,current): return 'LIGHT'
class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(
            id='s1',generation=2,phase='resolving',
            battle_state={'turn':3},legal_actions=[]
        )
        self._nana_nursery_model_generation={'s1':2}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    async def human_prompt():
        await asyncio.sleep(0.04)
        service.active_session.generation=3
        service.active_session.battle_state={'turn':4}
        service.active_session.legal_actions=[{'id':'3:0'}]
        service.active_session.phase='waiting-choice'
    task=asyncio.create_task(human_prompt())
    result=await player.choose_move(SimpleNamespace(turn=4,force_switch=[True,False]))
    await task
    assert result == 'nursery'
    assert not any(p.get('reason')=='model-only-force-switch-no-human-prompt' for _,p in service.nana.events)

asyncio.run(main())
`);
});

test("residual prechoice timeout is a benign skip, never nursery_error", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
import battle_lab.nana_stage2_nursery_lan_runtime as guarded

class Nana:
    def __init__(self): self.events=[]
    def append_event(self,sid,typ,payload): self.events.append((typ,payload))
class ParentPlayer:
    async def _handle_battle_request(self,battle,maybe_default_order=False): pass
    async def choose_move(self,current): raise AssertionError('base barrier must not run')
    def _raw_light_choose(self,current): return 'LIGHT'
class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(id='s1',generation=2,phase='resolving')
        self._nana_nursery_model_generation={'s1':2}
    async def ensure_ready(self): pass

async def main():
    guarded.PRECHOICE_TIMEOUT_SECONDS=0.02
    guarded.install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    result=await player.choose_move(SimpleNamespace(turn=5,force_switch=[False,False]))
    assert result == 'LIGHT'
    types=[t for t,_ in service.nana.events]
    assert 'nana_nursery_error' not in types
    skips=[p for t,p in service.nana.events if t=='nana_nursery_skip']
    assert skips[-1]['reason'] == 'prechoice-sync-timeout'
    assert skips[-1]['promotionBlocking'] is False

asyncio.run(main())
`);
});

test("battle wait does not poison rqid dedupe", () => {
  runPython(String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import install_nursery_lan_request_guard

class Nana:
    def __init__(self): self.events=[]
    def append_event(self,*args): self.events.append(args)
class ParentPlayer:
    calls=0
    async def _handle_battle_request(self,battle,maybe_default_order=False): ParentPlayer.calls += 1
    async def choose_move(self,current): return 'nursery'
    def _raw_light_choose(self,current): return 'LIGHT'
class Runtime: player_class=ParentPlayer
class Service:
    def __init__(self):
        self.runtime=Runtime(); self.nana=Nana(); self._nana_teacher={'key':'K'}
        self.active_session=SimpleNamespace(id='s1',generation=0,phase='resolving')
        self._nana_nursery_model_generation={'s1':0}
    async def ensure_ready(self): pass

async def main():
    install_nursery_lan_request_guard(Service)
    service=Service(); await service.ensure_ready(); player=service.runtime.player_class()
    battle=SimpleNamespace(last_request={'rqid':9},battle_tag='b',turn=1,_wait=True)
    await player._handle_battle_request(battle)
    battle._wait=False
    await player._handle_battle_request(battle)
    assert ParentPlayer.calls == 2
    assert not any(len(e)>1 and e[1]=='nana_nursery_skip' for e in service.nana.events)

asyncio.run(main())
`);
});
