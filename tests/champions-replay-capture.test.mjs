import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const quickEntry = await readFile(new URL("../components/vgc/match-quick-entry.tsx", import.meta.url), "utf8");
const replayRoute = await readFile(new URL("../app/api/replays/route.ts", import.meta.url), "utf8");
const pyproject = await readFile(new URL("../backend/pyproject.toml", import.meta.url), "utf8");

test("offers reconstructed Champions replay files next to manual entry", () => {
  assert.match(quickEntry, /accept="\.json,application\/json"/);
  assert.match(quickEntry, /Replay Champions/);
  assert.match(quickEntry, /readReplay\(\{ replay \}\)/);
});

test("accepts either a public replay URL or a reconstructed document", () => {
  assert.match(replayRoute, /replay\?: unknown/);
  assert.match(replayRoute, /normalizeShowdownReplayDocument\(payload\.replay\)/);
  assert.match(replayRoute, /replayUrl: ""/);
});

test("registers the local Champions replay companion CLI", () => {
  assert.match(pyproject, /champions-replay = "pkmn_vgc\.champions_replay\.cli:main"/);
});
