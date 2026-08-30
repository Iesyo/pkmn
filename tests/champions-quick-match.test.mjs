import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

async function source(relativePath) {
  return readFile(new URL(relativePath, `file://${root}/`), "utf8");
}

test("registers a minimal Champions match against the exact team version", async () => {
  const [quickMatch, queries] = await Promise.all([
    source("components/vgc/champions-quick-match.tsx"),
    source("db/queries.ts"),
  ]);

  assert.match(quickMatch, /teamVersionId:\s*version\.id/);
  assert.match(quickMatch, /selected\.length !== 4/);
  assert.match(quickMatch, /lead\.length !== 2/);
  assert.match(quickMatch, /rival\.length !== 4/);
  assert.match(quickMatch, /opponentSelected:\s*rival/);
  assert.match(quickMatch, /getSpeciesOptions\(snapshot, "champions"\)/);
  assert.match(queries, /INSERT INTO matches \(id, team_version_id,/);
  assert.match(queries, /match\.id,\s*\n\s*input\.teamVersionId,/);
});

test("uses one adaptive control for Champions quick entry and replay import", async () => {
  const [history, entry, quickMatch] = await Promise.all([
    source("components/vgc/match-history.tsx"),
    source("components/vgc/match-quick-entry.tsx"),
    source("components/vgc/champions-quick-match.tsx"),
  ]);

  assert.match(history, /const isChampions = version\.format === "champions"/);
  assert.match(history, /<MatchQuickEntry version=\{version\} onCreated=\{onMatchCreated\} \/>/);
  assert.doesNotMatch(history, /Registro rápido Champions/);
  assert.doesNotMatch(history, /<ReplayQuickEntry/);
  assert.match(history, /\{isChampions \? "Pokémon rival" : "Equipo rival"\}/);

  assert.match(entry, /const hasReplayInput = replayUrl\.trim\(\)\.length > 0/);
  assert.match(entry, /const championsMode = version\.format === "champions" && !hasReplayInput/);
  assert.match(entry, /championsMode \? "Partida Champions" : "Agregar replay"/);
  assert.match(entry, /if \(!hasReplayInput\)[\s\S]*setChampionsDialogOpen\(true\)/);
  assert.match(entry, /replayUrl:\s*replayUrl\.trim\(\)/);
  assert.match(entry, /open=\{championsDialogOpen\}/);
  assert.match(entry, /hideTrigger/);

  assert.match(quickMatch, /open:\s*controlledOpen/);
  assert.match(quickMatch, /onOpenChange/);
  assert.match(quickMatch, /hideTrigger = false/);
});
