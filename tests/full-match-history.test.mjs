import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const baseMatch = {
  id: "match",
  result: "win",
  opponentName: "Rival",
  opponentPaste: "",
  replayUrl: "",
  selected: [],
  opponentSelected: [],
  opponentPicks: [],
  lead: [],
  rating: null,
  notes: "",
  playedAt: "2026-08-30T12:00:00.000Z",
};

test("classifies manual Champions matches separately from Showdown replays", async () => {
  const { getMatchOrigin, filterMatchesByOrigin, countMatchesByOrigin } = await vite.ssrLoadModule("/lib/match-history.ts");
  const champions = { ...baseMatch, id: "champions", replayUrl: "" };
  const showdown = { ...baseMatch, id: "showdown", replayUrl: "https://replay.pokemonshowdown.com/gen9vgc-test" };
  const matches = [showdown, champions];

  assert.equal(getMatchOrigin(champions), "champions");
  assert.equal(getMatchOrigin(showdown), "showdown");
  assert.deepEqual(filterMatchesByOrigin(matches, "all").map((match) => match.id), ["showdown", "champions"]);
  assert.deepEqual(filterMatchesByOrigin(matches, "champions").map((match) => match.id), ["champions"]);
  assert.deepEqual(filterMatchesByOrigin(matches, "showdown").map((match) => match.id), ["showdown"]);
  assert.deepEqual(countMatchesByOrigin(matches), { champions: 1, showdown: 1 });
});

test("recent history stays capped at five while exposing the complete filtered history", async () => {
  const source = await readFile(new URL("../components/vgc/match-history.tsx", import.meta.url), "utf8");

  assert.match(source, /matches=\{matches\.slice\(0, 5\)\}/);
  assert.match(source, /Historial completo/);
  assert.match(source, /Todas las partidas guardadas de esta versión/);
  assert.match(source, /value: "all", label: "Todos"/);
  assert.match(source, /value: "champions", label: "Champions"/);
  assert.match(source, /value: "showdown", label: "Showdown"/);
  assert.match(source, /filterMatchesByOrigin\(matches, historyFilter\)/);
  assert.match(source, /<OriginBadge match=\{match\} \/>/);
});
