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

test("replay import keeps rival Team Preview separate from the four rival picks", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const ownTeam = ["Kleavor", "Pelipper", "Venusaur", "Sinistcha", "Archaludon", "Luxray"];
  const rivalTeam = ["Incineroar", "Rillaboom", "Urshifu-Rapid-Strike", "Farigiraf", "Miraidon", "Amoonguss"];
  const preview = [
    ...ownTeam.map((species) => `|poke|p1|${species}, L50|item`),
    ...rivalTeam.map((species) => `|poke|p2|${species}, L50|item`),
  ].join("\n");
  const match = importShowdownReplay(
    {
      log: `|player|p1|IesYo|1|1400\n|player|p2|Rival|2|1400\n${preview}\n|teampreview\n|start\n|switch|p1a: Kleavor|Kleavor, L50|100/100\n|switch|p1b: Pelipper|Pelipper, L50|100/100\n|switch|p2a: Miraidon|Miraidon, L50|100/100\n|switch|p2b: Amoonguss|Amoonguss, L50|100/100\n|turn|1\n|win|IesYo`,
      inputlog: ">p1 team 1243\n>p2 team 5612",
    },
    {
      replayUrl: "https://replay.pokemonshowdown.com/gen9vgc-test",
      showdownNames: ["IesYo"],
      teamSpecies: ownTeam,
    },
  );

  assert.deepEqual(match.opponentSelected, rivalTeam);
  assert.deepEqual(match.opponentPicks, ["Miraidon", "Amoonguss", "Incineroar", "Rillaboom"]);
});

test("recent history renders rival picks between rival preview and own picks", async () => {
  const source = await readFile(new URL("../components/vgc/match-history.tsx", import.meta.url), "utf8");
  const previewHeader = source.indexOf('isChampions ? "Pokémon rival" : "Equipo rival"');
  const rivalPicksHeader = source.indexOf(">Picks rival</TableHead>");
  const ownPicksHeader = source.indexOf(">Tus picks</TableHead>");

  assert.ok(previewHeader >= 0 && rivalPicksHeader > previewHeader && ownPicksHeader > rivalPicksHeader);
  assert.match(source, /species=\{match\.opponentPicks \?\? \[\]\}/);
  assert.match(source, /label="Picks rival" limit=\{4\}/);
});

test("persistence adds an independent rival-picks column and backfills only evidence-backed history", async () => {
  const [migration, backfill, teamsRoute, matchRoute] = await Promise.all([
    readFile(new URL("../drizzle/0007_opponent_picks.sql", import.meta.url), "utf8"),
    readFile(new URL("../app/api/replays/backfill/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/teams/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/matches/route.ts", import.meta.url), "utf8"),
  ]);

  assert.match(migration, /ADD COLUMN opponent_picks_json TEXT NOT NULL DEFAULT '\[\]'/);
  assert.match(migration, /WHERE replay_url = ''/);
  assert.match(migration, /format = 'champions'/);
  assert.match(backfill, /listOpponentPicksBackfillCandidates/);
  assert.match(backfill, /saveBackfilledOpponentPicks/);
  assert.match(backfill, /opponentPicks: match\.opponentPicks/);
  assert.match(teamsRoute, /enrichTeamsWithOpponentPicks/);
  assert.match(matchRoute, /saveOpponentPicks/);
  assert.match(matchRoute, /payload\.opponentSelected\?\.length === 4/);
});
