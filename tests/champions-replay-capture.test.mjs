import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const quickEntry = await readFile(new URL("../components/vgc/match-quick-entry.tsx", import.meta.url), "utf8");
const replayRoute = await readFile(new URL("../app/api/replays/route.ts", import.meta.url), "utf8");
const matchDialog = await readFile(new URL("../components/vgc/team-dialogs.tsx", import.meta.url), "utf8");
const replayPageRoute = await readFile(new URL("../app/api/matches/[id]/replay/route.ts", import.meta.url), "utf8");
const replayMigration = await readFile(new URL("../drizzle/0011_replay_artifacts.sql", import.meta.url), "utf8");
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
  assert.match(replayRoute, /replayArtifact: reconstructed \? source\.replay : null/);
});

test("persists reconstructed replay protocol and serves it as inline HTML", () => {
  assert.match(matchDialog, /replayArtifact: initialReplay\?\.replayArtifact/);
  assert.match(matchDialog, /Podrás abrir este replay desde el historial en una pestaña nueva/);
  assert.match(replayMigration, /ADD `origin` text DEFAULT 'champions' NOT NULL/);
  assert.match(replayMigration, /ADD `replay_artifact_json` text/);
  assert.match(replayPageRoute, /renderShowdownReplayHtml/);
  assert.match(replayPageRoute, /content-disposition/);
  assert.match(replayPageRoute, /sandbox allow-scripts/);
});

test("registers the local Champions replay companion CLI", () => {
  assert.match(pyproject, /champions-replay = "pkmn_vgc\.champions_replay\.cli:main"/);
});
