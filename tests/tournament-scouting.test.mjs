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

function team(overrides = {}) {
  return {
    name: "Alex",
    placement: 4,
    record: "7-2-0",
    pokemonNames: ["Charizard", "Garchomp", "Whimsicott", "Floette", "Incineroar", "Basculegion ♂"],
    pokepasteUrl: "https://pokepast.es/abc123",
    tournamentName: "Champions Cup",
    ...overrides,
  };
}

function snapshot() {
  return {
    regulation: "M-B",
    generatedAt: "2026-08-17",
    dateRange: { from: "2026-06-18", to: "2026-08-17" },
    compositions: [
      {
        size: 4,
        clusters: [{
          teams: [
            team(),
            team({ name: "Duplicate", placement: 1 }),
            team({ name: "Sam", placement: 1, pokepasteUrl: "https://pokepast.es/def456" }),
            team({ name: "Jo", placement: 2, pokepasteUrl: "https://pokepast.es/ghi789", tournamentName: "Regional One" }),
            team({ pokepasteUrl: "https://example.com/not-a-paste" }),
            team({ pokepasteUrl: "https://pokepast.es/incomplete", pokemonNames: ["Charizard"] }),
          ],
        }],
      },
    ],
  };
}

test("groups real tournament teams, deduplicates pastes and orders by placement", async () => {
  const { buildTournamentScoutingResponse } = await vite.ssrLoadModule("/lib/tournament-scouting.ts");
  const result = buildTournamentScoutingResponse(snapshot(), { retrievedAt: "2026-09-08T00:00:00.000Z" });

  assert.equal(result.regulation, "M-B");
  assert.equal(result.generatedAt, "2026-08-17");
  assert.equal(result.tournaments.length, 2);
  assert.equal(result.tournaments[0].name, "Champions Cup");
  assert.deepEqual(result.tournaments[0].teams.map((entry) => entry.playerName), ["Sam", "Alex"]);
  assert.equal(result.tournaments[0].teams[1].pokemon[5], "Basculegion-M");
  assert.equal(result.tournaments.flatMap((entry) => entry.teams).length, 3);
  assert.equal(result.source.label, "LabMaus");
  assert.equal(result.snapshotSource.label, "VS Recorder");
});

test("rejects snapshots without any complete, safe PokéPaste teams", async () => {
  const { buildTournamentScoutingResponse } = await vite.ssrLoadModule("/lib/tournament-scouting.ts");
  assert.throws(() => buildTournamentScoutingResponse({ compositions: [{ clusters: [{ teams: [team({ pokepasteUrl: "javascript:alert(1)" })] }] }] }), /no contiene equipos/);
  assert.throws(() => buildTournamentScoutingResponse({ nope: true }), /formato esperado/);
});

test("serves the tournament snapshot through a fixed cached backend route", async () => {
  const { GET } = await vite.ssrLoadModule("/app/api/scouting/tournaments/route.ts");
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (input) => {
    requestedUrl = String(input);
    return new Response(JSON.stringify(snapshot()), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    const response = await GET();
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(requestedUrl, "https://raw.githubusercontent.com/Pocolip/vs-recorder/develop/frontend/src/data/tournamentTeams-regM-B.json");
    assert.equal(payload.tournaments[0].teams.length, 2);
    assert.equal(payload.stale, false);
    assert.match(response.headers.get("cache-control"), /s-maxage=43200/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("places the tournament browser in Scouting without coupling it to Team Builder", async () => {
  const [scouting, tournamentBrowser, teamBuilder] = await Promise.all([
    readFile(new URL("../components/vgc/scouting-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/tournament-scouting-browser.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(scouting, /<TournamentScoutingBrowser/);
  assert.match(scouting, />Torneos/);
  assert.match(tournamentBrowser, /\/api\/scouting\/tournaments/);
  assert.match(tournamentBrowser, /Buscar torneo/);
  assert.match(tournamentBrowser, /Cada tarjeta abre el PokéPaste original/);
  assert.doesNotMatch(teamBuilder, /TournamentScoutingBrowser|scouting\/tournaments/);
});
