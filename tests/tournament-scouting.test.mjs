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
  return ["Charizard", "Garchomp", "Whimsicott", "Floette", "Incineroar", "Basculegion-M"]
    .map((species, index) => `${species} @ Leftovers\nAbility: Pressure\nLevel: 50\n${index === 0 ? "Timid Nature\n" : ""}- Protect\n- Tackle\n- Growl\n- Substitute`)
    .join("\n\n");
}

function battleData() {
  return {
    rows: [
      { category: "stat_alignment", rank: 1, name: "Modest", percentage_value: 20 },
      { category: "stat_alignment", rank: 8, name: "Bold", percentage_value: 80 },
      {
        category: "stat_points",
        rank: 1,
        percentage_value: 20,
        hp_points: 2,
        attack_points: 0,
        defense_points: 0,
        sp_atk_points: 32,
        sp_def_points: 0,
        speed_points: 32,
      },
      {
        category: "stat_points",
        rank: 9,
        percentage_value: 80,
        hp_points: 29,
        attack_points: 0,
        defense_points: 21,
        sp_atk_points: 0,
        sp_def_points: 16,
        speed_points: 0,
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
  assert.equal(result.archive.storage, "bundled");
  assert.equal(result.archive.sourceFile, "tournamentTeams-regM-B.json");
});

test("rejects snapshots without any complete, safe PokéPaste teams", async () => {
  const { buildTournamentScoutingResponse } = await vite.ssrLoadModule("/lib/tournament-scouting.ts");
  assert.throws(() => buildTournamentScoutingResponse({ compositions: [{ clusters: [{ teams: [team({ pokepasteUrl: "javascript:alert(1)" })] }] }] }), /no contiene equipos/);
  assert.throws(() => buildTournamentScoutingResponse({ nope: true }), /formato esperado/);
});

test("serves the last known tournament snapshot without runtime network access", async () => {
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
    assert.equal(typeof payload.stale, "boolean");
    assert.equal(payload.archive.sourceFile, "tournamentTeams-regM-B.json");
    assert.equal(response.headers.get("cache-control"), "no-store");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("discovers the newest regulation and validates the GitHub blob before building it", async () => {
  const { fetchLatestTournamentScoutingSnapshot, TOURNAMENT_DATA_DIRECTORY_API } = await vite.ssrLoadModule("/lib/tournament-scouting-refresh.ts");
  const oldSha = "a".repeat(40);
  const currentSha = "b".repeat(40);
  const source = JSON.stringify({
    ...snapshot(),
    regulation: "N-A",
    generatedAt: "2026-09-08",
  });
  const requested = [];
  const fetcher = async (input, init) => {
    requested.push({ url: String(input), redirect: init?.redirect });
    if (String(input) === TOURNAMENT_DATA_DIRECTORY_API) {
      return Response.json([
        { name: "tournamentTeams-regM-B.json", sha: oldSha, size: 100, type: "file" },
        { name: "notes.json", sha: oldSha, size: 100, type: "file" },
        { name: "tournamentTeams-regN-A.json", sha: currentSha, size: Buffer.byteLength(source), type: "file" },
      ]);
    }
    return Response.json({
      sha: currentSha,
      encoding: "base64",
      content: Buffer.from(source).toString("base64"),
      size: Buffer.byteLength(source),
    });
  };

  const result = await fetchLatestTournamentScoutingSnapshot(fetcher, "2026-09-08T18:30:00.000Z");

  assert.equal(result.regulation, "N-A");
  assert.equal(result.archive.sourceFile, "tournamentTeams-regN-A.json");
  assert.equal(result.archive.sourceRevision, currentSha);
  assert.equal(result.archive.storage, "persisted");
  assert.equal(result.archive.checkedAt, "2026-09-08T18:30:00.000Z");
  assert.match(result.snapshotSource.url, /tournamentTeams-regN-A\.json$/);
  assert.deepEqual(requested, [
    { url: TOURNAMENT_DATA_DIRECTORY_API, redirect: "manual" },
    { url: `https://api.github.com/repos/Pocolip/vs-recorder/git/blobs/${currentSha}`, redirect: "manual" },
  ]);
});

test("rejects a same-regulation refresh that loses most of the last known teams", async () => {
  const { assertSafeTournamentSnapshotUpdate } = await vite.ssrLoadModule("/lib/tournament-scouting-refresh.ts");
  const { buildTournamentScoutingResponse } = await vite.ssrLoadModule("/lib/tournament-scouting.ts");
  const previous = buildTournamentScoutingResponse(snapshot(), { retrievedAt: "2026-09-08T00:00:00.000Z" });
  const expanded = {
    ...previous,
    tournaments: Array.from({ length: 10 }, (_, tournamentIndex) => ({
      ...previous.tournaments[0],
      id: `previous-${tournamentIndex}`,
      teams: Array.from({ length: 4 }, (_, teamIndex) => ({
        ...previous.tournaments[0].teams[0],
        id: `previous-${tournamentIndex}-${teamIndex}`,
      })),
    })),
  };
  const candidate = {
    ...previous,
    archive: { ...previous.archive, storage: "persisted" },
    tournaments: previous.tournaments.slice(0, 1),
  };

  assert.throws(() => assertSafeTournamentSnapshotUpdate(candidate, expanded), /perdió demasiados equipos/);
  assert.doesNotThrow(() => assertSafeTournamentSnapshotUpdate({ ...candidate, regulation: "N-A" }, expanded));
});

test("turns upstream transport failures into a safe refresh error", async () => {
  const { fetchLatestTournamentScoutingSnapshot } = await vite.ssrLoadModule("/lib/tournament-scouting-refresh.ts");
  await assert.rejects(
    () => fetchLatestTournamentScoutingSnapshot(async () => {
      throw new Error("internal resolver reference 123");
    }),
    (error) => error instanceof Error
      && error.message === "El catálogo de VS Recorder no está disponible"
      && !error.message.includes("reference 123"),
  );
});

test("downloads only a known tournament PokéPaste for direct import", async () => {
  const { POST } = await vite.ssrLoadModule("/app/api/tournament-team-import/route.ts");
  const originalFetch = globalThis.fetch;
  const requestedUrls = [];
  let requestedRedirect = "";
  let fetchCalls = 0;
  globalThis.fetch = async (input, init) => {
    fetchCalls += 1;
    const url = String(input);
    requestedUrls.push(url);
    if (url.includes("pokepast.es")) {
      requestedRedirect = init?.redirect ?? "";
      return new Response(showdownPaste(), { headers: { "content-type": "text/plain" } });
    }
    return new Response(JSON.stringify(battleData()), { headers: { "content-type": "application/json" } });
  };

  try {
    const response = await POST(new Request("http://localhost/api/tournament-team-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ teamId: "team-6016921c41817086" }),
    }));
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal((payload.paste.match(/^EVs: 29 HP \/ 21 Def \/ 16 SpD$/gm) ?? []).length, 6);
    assert.equal((payload.paste.match(/^Bold Nature$/gm) ?? []).length, 5);
    assert.equal((payload.paste.match(/^Timid Nature$/gm) ?? []).length, 1);
    assert.match(payload.paste, /Charizard @ Leftovers\nAbility: Pressure/);
    assert.deepEqual(payload.estimates, { nature: 5, statPoints: 6 });
    assert.equal(payload.team.id, "team-6016921c41817086");
    assert.equal(requestedUrls[0], "https://pokepast.es/6016921c41817086/raw");
    assert.ok(requestedUrls.includes("https://championsbattledata.com/api/battle/Doubles/basculegionm"));
    assert.equal(requestedRedirect, "manual");
    assert.equal(fetchCalls, 7);

    const missingResponse = await POST(new Request("http://localhost/api/tournament-team-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ teamId: "team-not-in-snapshot" }),
    }));
    assert.equal(missingResponse.status, 404);
    assert.equal(fetchCalls, 7);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("connects persistent tournament scouting to a new editable Team Builder draft", async () => {
  const [scouting, tournamentBrowser, teamBuilder, dashboard, route, storage, importer] = await Promise.all([
    readFile(new URL("../components/vgc/scouting-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/tournament-scouting-browser.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/vgc-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/tournament-scouting/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../db/tournament-snapshot.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/tournament-team-import/route.ts", import.meta.url), "utf8"),
  ]);

  assert.match(scouting, /<TournamentScoutingBrowser onImportTeam=\{onTournamentTeamImport\}/);
  assert.match(scouting, />Torneos/);
  assert.match(tournamentBrowser, /\/api\/tournament-scouting/);
  assert.match(tournamentBrowser, /method: "POST"/);
  assert.match(tournamentBrowser, /Actualizar torneos/);
  assert.match(tournamentBrowser, /Actualización guardada/);
  assert.match(route, /saveStoredTournamentScoutingSnapshot/);
  assert.match(route, /assertSafeTournamentSnapshotUpdate/);
  assert.match(storage, /tournament_scouting_snapshot_gzip_base64_v1/);
  assert.match(storage, /CompressionStream\("gzip"\)/);
  assert.match(storage, /app_settings/);
  assert.match(importer, /loadCurrentTournamentScoutingSnapshot/);
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
