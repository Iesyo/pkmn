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

function showdownPaste() {
  return ["Venusaur", "Charizard", "Garchomp", "Sylveon", "Incineroar", "Farigiraf"]
    .map((species) => `${species} @ Leftovers\nAbility: Pressure\nLevel: 50\nTimid Nature\n- Protect\n- Tackle\n- Growl\n- Substitute`)
    .join("\n\n");
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

test("serves the bundled tournament snapshot without runtime network access", async () => {
  const { GET } = await vite.ssrLoadModule("/app/api/tournament-scouting/route.ts");
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("The bundled route must not fetch at runtime");
  };

  try {
    const response = await GET();
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /application\/json/);
    assert.equal(fetchCalls, 0);
    assert.equal(payload.tournaments.length, 207);
    assert.equal(payload.tournaments.reduce((total, tournament) => total + tournament.teams.length, 0), 621);
    assert.equal(payload.stale, false);
    assert.match(response.headers.get("cache-control"), /s-maxage=43200/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("downloads only a known tournament PokéPaste for direct import", async () => {
  const { POST } = await vite.ssrLoadModule("/app/api/tournament-team-import/route.ts");
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  let requestedRedirect = "";
  let fetchCalls = 0;
  globalThis.fetch = async (input, init) => {
    fetchCalls += 1;
    requestedUrl = String(input);
    requestedRedirect = init?.redirect ?? "";
    return new Response(showdownPaste(), { headers: { "content-type": "text/plain" } });
  };

  try {
    const response = await POST(new Request("http://localhost/api/tournament-team-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ teamId: "team-6016921c41817086" }),
    }));
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.paste, showdownPaste());
    assert.equal(payload.team.id, "team-6016921c41817086");
    assert.equal(requestedUrl, "https://pokepast.es/6016921c41817086/raw");
    assert.equal(requestedRedirect, "manual");
    assert.equal(fetchCalls, 1);

    const missingResponse = await POST(new Request("http://localhost/api/tournament-team-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ teamId: "team-not-in-snapshot" }),
    }));
    assert.equal(missingResponse.status, 404);
    assert.equal(fetchCalls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("connects tournament scouting to a new editable Team Builder draft", async () => {
  const [scouting, tournamentBrowser, teamBuilder, dashboard] = await Promise.all([
    readFile(new URL("../components/vgc/scouting-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/tournament-scouting-browser.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/vgc-dashboard.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(scouting, /<TournamentScoutingBrowser onImportTeam=\{onTournamentTeamImport\}/);
  assert.match(scouting, />Torneos/);
  assert.match(tournamentBrowser, /\/api\/tournament-scouting/);
  assert.match(tournamentBrowser, /\/api\/tournament-team-import/);
  assert.match(tournamentBrowser, /content-type/);
  assert.match(tournamentBrowser, /respondió con una página en lugar del archivo de torneos/);
  assert.match(tournamentBrowser, /Buscar torneo/);
  assert.match(tournamentBrowser, /Importar al Builder/);
  assert.doesNotMatch(tournamentBrowser, />Ver equipo/);
  assert.match(dashboard, /function importTournamentTeam\(request: TournamentTeamBuilderImport\)/);
  assert.match(dashboard, /setActiveView\("builder"\)/);
  assert.match(dashboard, /initialImport=\{builderImport \?\? undefined\}/);
  assert.match(teamBuilder, /initialImport \? "" : initialVersion\?\.demo/);
  assert.match(teamBuilder, /importado como borrador/);
  assert.doesNotMatch(teamBuilder, /<TournamentScoutingBrowser|\/api\/tournament-team-import/);
});
