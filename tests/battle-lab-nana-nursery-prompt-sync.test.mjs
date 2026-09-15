import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";

test("LAN guard requires matching turn and legal actions before base Nursery barrier", () => {
  const script = String.raw`
import asyncio
from types import SimpleNamespace
from battle_lab.nana_stage2_nursery_lan_runtime import _wait_for_human_prompt

class Service:
    def __init__(self):
        self._nana_nursery_model_generation={'s1':1}

async def main():
    service=Service()
    session=SimpleNamespace(
        id='s1',
        generation=2,
        phase='waiting-choice',
        battle_state={'turn':4},
        legal_actions=[{'id':'2:0'}],
    )

    # Fresh generation + waiting-choice is not enough if the two websockets are
    # transiently on different turns.
    assert await _wait_for_human_prompt(
        service, session, turn=5, timeout=0.01
    ) is False

    # Matching turn is still incomplete until the human prompt has legal actions.
    session.battle_state={'turn':5}
    session.legal_actions=[]
    assert await _wait_for_human_prompt(
        service, session, turn=5, timeout=0.01
    ) is False

    # A complete prompt published later is accepted inside the same cooperative wait.
    async def publish_complete_prompt():
        await asyncio.sleep(0.01)
        session.generation=3
        session.phase='waiting-choice'
        session.battle_state={'turn':5}
        session.legal_actions=[{'id':'3:0'}]

    publisher=asyncio.create_task(publish_complete_prompt())
    assert await _wait_for_human_prompt(
        service, session, turn=5, timeout=0.1
    ) is True
    await publisher

asyncio.run(main())
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});
