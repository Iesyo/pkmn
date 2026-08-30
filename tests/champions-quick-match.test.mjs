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

test("shows the quick entry only for Champions while keeping replay entry available", async () => {
  const history = await source("components/vgc/match-history.tsx");

  assert.match(history, /const isChampions = version\.format === "champions"/);
  assert.match(history, /isChampions \? \(/);
  assert.match(history, /<ChampionsQuickMatchDialog version=\{version\} onCreated=\{onMatchCreated\} \/>/);
  assert.match(history, /<ReplayQuickEntry version=\{version\} onCreated=\{onMatchCreated\} \/>/);
  assert.match(history, /\{isChampions \? "Pokémon rival" : "Equipo rival"\}/);
});
