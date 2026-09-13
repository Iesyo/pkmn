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

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function csvRow(values) {
  return values.map(csvEscape).join(",");
}

function vgcpastesCsv(count = 30) {
  const intro = Array(45).fill("");
  const header = Array(45).fill("");
  header[0] = "Team ID";
  header[1] = "Team Description";
  header[3] = "Full Name";
  header[24] = "Pokepaste";
  header[25] = "EVs";
  header[28] = "Replica Code\n(Click text for image)";
  header[29] = "Date Shared";
  header[30] = "Tournament / Event";
  header[31] = "Rank";
  header[32] = "Link to Source";
  header[35] = "Owner";
  header[37] = "Pokemon Text for Copypasta";
  header[44] = "Team ID";

  const species = ["Sneasler", "Indeedee-F", "Rillaboom", "Gholdengo", "Incineroar", "Salamence-Mega"];
  const rows = Array.from({ length: count }, (_, index) => {
    const row = Array(45).fill("");
    const id = `MC${String(count - index).padStart(3, "0")}`;
    row[0] = id;
    row[1] = `Player ${index + 1}'s team`;
    row[3] = `Player ${index + 1}`;
    row[24] = index % 5 === 4 ? "" : `https://pokepast.es/${String(index + 1).padStart(16, "0")}`;
    row[25] = index % 2 === 0 ? "Yes" : "No";
    row[28] = index % 3 === 0 ? `CODE${index + 1}` : "None";
    row[29] = index < 10 ? "12 Sep 2026" : "10 Sep 2026";
    row[30] = index % 2 === 0 ? "Tera Square Offline Meetup #2" : "-";
    row[31] = index === 0 ? "Top 4" : index === 1 ? "Top 8" : "";
    row[32] = "https://x.com/example/status/2098723565273280848";
    row[35] = `owner${index + 1}`;
    species.forEach((name, pokemonIndex) => {
      row[37 + pokemonIndex] = index === count - 1 && pokemonIndex === 0 ? "Garchomp-Mega-Z" : name;
    });
    if (index % 2 === 1) row[38] = "Farigiraf";
    row[44] = id;
    return row;
  });
  return [intro, intro, header, ...rows].map(csvRow).join("\r\n");
}

test("parses the public VGCPastes Champions column layout", async () => {
  const { parseVgcPastesTeams } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const teams = parseVgcPastesTeams(vgcpastesCsv(2));

  assert.equal(teams.length, 2);
  assert.equal(teams[0].id, "MC002");
  assert.equal(teams[0].playerName, "Player 1");
  assert.equal(teams[0].pokepasteUrl, "https://pokepast.es/0000000000000001");
  assert.equal(teams[0].hasEvs, true);
  assert.equal(teams[0].replicaCode, "CODE1");
  assert.equal(teams[0].tournament, "Tera Square Offline Meetup #2");
  assert.equal(teams[0].rank, "Top 4");
  assert.deepEqual(teams[0].pokemon, ["Sneasler", "Indeedee-F", "Rillaboom", "Gholdengo", "Incineroar", "Salamence-Mega"]);
});

test("uses the verified sheet ids and a deterministic A:AS public CSV query", async () => {
  const { buildVgcPastesCsvUrl, buildVgcPastesSheetUrl, VGCPASTES_FORMATS } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  assert.deepEqual(VGCPASTES_FORMATS.map(({ label, gid }) => [label, gid]), [
    ["Champions M-C", "2001945654"],
    ["Champions M-B", "1458357160"],
    ["Champions M-A", "791705272"],
    ["SV Regulation I", "972834435"],
  ]);

  const csvUrl = new URL(buildVgcPastesCsvUrl(VGCPASTES_FORMATS[0]));
  assert.equal(csvUrl.hostname, "docs.google.com");
  assert.equal(csvUrl.searchParams.get("gid"), "2001945654");
  assert.equal(csvUrl.searchParams.get("headers"), "3");
  assert.equal(csvUrl.searchParams.get("range"), "A:AS");
  assert.equal(csvUrl.searchParams.get("tqx"), "out:csv");
  assert.match(buildVgcPastesSheetUrl(VGCPASTES_FORMATS[0]), /gid=2001945654#gid=2001945654$/);
});

test("normalizes at most three unique Pokemon filters", async () => {
  const { MAX_VGCPASTES_POKEMON_FILTERS, normalizeVgcPastesPokemonFilters } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  assert.equal(MAX_VGCPASTES_POKEMON_FILTERS, 3);
  assert.deepEqual(
    normalizeVgcPastesPokemonFilters([" Sneasler ", "sneasler", "Indeedee-F", "Rillaboom", "Gholdengo"]),
    ["Sneasler", "Indeedee-F", "Rillaboom"],
  );
});

test("filters by a Pokemon core with AND semantics and paginates before sending cards to the client", async () => {
  const { buildVgcPastesScoutingResponse, getVgcPastesFormat, isVgcPastesScoutingResponse, parseVgcPastesTeams } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const teams = parseVgcPastesTeams(vgcpastesCsv(30));
  const format = getVgcPastesFormat("champions-m-c");
  assert.ok(format);

  const pageTwo = buildVgcPastesScoutingResponse(format, teams, { page: 2, pageSize: 12, fetchedAt: "2026-09-12T23:00:00.000Z" });
  assert.equal(pageTwo.pagination.totalAvailable, 30);
  assert.equal(pageTwo.pagination.totalItems, 30);
  assert.equal(pageTwo.pagination.totalPages, 3);
  assert.equal(pageTwo.teams.length, 12);
  assert.equal(pageTwo.teams[0].id, "MC018");
  assert.equal(pageTwo.source.url, "https://docs.google.com/spreadsheets/d/1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw/htmlview?gid=2001945654#gid=2001945654");
  assert.deepEqual(pageTwo.query.pokemon, []);
  assert.equal(pageTwo.query.hasEvs, false);
  assert.equal(pageTwo.query.hasPaste, false);
  assert.equal(pageTwo.query.hasReplica, false);
  assert.equal(isVgcPastesScoutingResponse(pageTwo), true);

  const filtered = buildVgcPastesScoutingResponse(format, teams, { pokemon: ["Sneasler", "Indeedee-F"], page: 1, pageSize: 12 });
  assert.deepEqual(filtered.query.pokemon, ["Sneasler", "Indeedee-F"]);
  assert.equal(filtered.pagination.totalItems, 15);
  assert.equal(filtered.pagination.totalPages, 2);
  assert.ok(filtered.teams.every((team) => team.pokemon.includes("Sneasler") && team.pokemon.includes("Indeedee-F")));

  const threePokemonCore = buildVgcPastesScoutingResponse(format, teams, { pokemon: ["Sneasler", "Indeedee-F", "Rillaboom"], page: 1, pageSize: 24 });
  assert.equal(threePokemonCore.pagination.totalItems, 15);
  assert.ok(threePokemonCore.teams.every((team) => ["Sneasler", "Indeedee-F", "Rillaboom"].every((species) => team.pokemon.includes(species))));

  const invalid = structuredClone(pageTwo);
  invalid.query.pokemon = ["Sneasler", "Indeedee-F", "Rillaboom", "Gholdengo"];
  assert.equal(isVgcPastesScoutingResponse(invalid), false);
});

test("combines competitive filters with the Pokemon core before pagination", async () => {
  const { buildVgcPastesScoutingResponse, getVgcPastesFormat, parseVgcPastesTeams } = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const teams = parseVgcPastesTeams(vgcpastesCsv(30));
  const format = getVgcPastesFormat("champions-m-c");
  assert.ok(format);

  const filtered = buildVgcPastesScoutingResponse(format, teams, {
    pokemon: ["Sneasler", "Indeedee-F"],
    player: "owner1",
    event: "Tera Square",
    rank: "Top 4",
    date: "12 Sep",
    hasEvs: true,
    hasPaste: true,
    hasReplica: true,
    page: 1,
    pageSize: 24,
  });

  assert.equal(filtered.pagination.totalItems, 1);
  assert.equal(filtered.teams.length, 1);
  assert.equal(filtered.teams[0].playerName, "Player 1");
  assert.equal(filtered.query.player, "owner1");
  assert.equal(filtered.query.event, "Tera Square");
  assert.equal(filtered.query.rank, "Top 4");
  assert.equal(filtered.query.date, "12 Sep");
  assert.equal(filtered.query.hasEvs, true);
  assert.equal(filtered.query.hasPaste, true);
  assert.equal(filtered.query.hasReplica, true);
});

test("serves repeated Pokemon params and bounded competitive filters from one cached source", async () => {
  const serverModule = await vite.ssrLoadModule("/lib/vgcpastes-scouting-server.ts");
  serverModule.clearVgcPastesScoutingCache();
  const { GET } = await vite.ssrLoadModule("/app/api/vgcpastes-scouting/route.ts");
  const originalFetch = globalThis.fetch;
  const requested = [];
  globalThis.fetch = async (input) => {
    requested.push(String(input));
    return new Response(vgcpastesCsv(30), { headers: { "content-type": "text/csv" } });
  };

  try {
    const response = await GET(new Request("http://localhost/api/vgcpastes-scouting?format=champions-m-c&pokemon=Sneasler&pokemon=Indeedee-F&player=owner1&event=Tera%20Square&rank=Top%204&date=12%20Sep&hasEvs=1&hasPaste=1&hasReplica=1&page=1&pageSize=12"));
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.format.label, "Champions M-C");
    assert.deepEqual(payload.query.pokemon, ["Sneasler", "Indeedee-F"]);
    assert.equal(payload.query.player, "owner1");
    assert.equal(payload.query.hasEvs, true);
    assert.equal(payload.query.hasPaste, true);
    assert.equal(payload.query.hasReplica, true);
    assert.equal(payload.pagination.totalItems, 1);
    assert.equal(payload.teams.length, 1);
    assert.equal(payload.teams[0].playerName, "Player 1");

    const upstream = new URL(requested[0]);
    assert.equal(upstream.hostname, "docs.google.com");
    assert.equal(upstream.searchParams.get("gid"), "2001945654");
    assert.equal(upstream.searchParams.get("headers"), "3");
    assert.equal(upstream.searchParams.get("range"), "A:AS");

    const boundedResponse = await GET(new Request("http://localhost/api/vgcpastes-scouting?format=champions-m-c&pageSize=999999"));
    const boundedPayload = await boundedResponse.json();
    assert.equal(boundedResponse.status, 200);
    assert.equal(boundedPayload.query.pageSize, 24);
    assert.equal(boundedPayload.teams.length, 24);
    assert.equal(requested.length, 1, "same-format filtering and pagination should reuse the in-process source cache");
  } finally {
    globalThis.fetch = originalFetch;
    serverModule.clearVgcPastesScoutingCache();
  }
});

test("rejects redirects from the fixed VGCPastes source instead of following them", async () => {
  const serverModule = await vite.ssrLoadModule("/lib/vgcpastes-scouting-server.ts");
  serverModule.clearVgcPastesScoutingCache();
  await assert.rejects(
    () => serverModule.loadVgcPastesFormat("champions-m-c", {
      fetcher: async () => new Response(null, { status: 302, headers: { location: "https://example.com/elsewhere" } }),
    }),
    /intentó redirigir/,
  );
});

test("connects Scouting v3 to competitive filters and an on-demand Pokepaste inspector", async () => {
  const browser = await readFile(new URL("../components/vgc/vgcpastes-scouting-browser.tsx", import.meta.url), "utf8");
  const scouting = await readFile(new URL("../components/vgc/scouting-view.tsx", import.meta.url), "utf8");

  assert.match(browser, /Buscar core · hasta/);
  assert.match(browser, /AND · deben aparecer todos/);
  assert.match(browser, /Filtros competitivos/);
  assert.match(browser, /Jugador \/ owner/);
  assert.match(browser, /Torneo \/ evento/);
  assert.match(browser, /Rank \/ placement/);
  assert.match(browser, /Con EVs/);
  assert.match(browser, /Con PokéPaste/);
  assert.match(browser, /Con Replica Code/);
  assert.match(browser, /params\.set\("hasEvs", "1"\)/);
  assert.match(browser, /Team Inspector/);
  assert.match(browser, /parseShowdownPaste\(paste\)/);
  assert.match(browser, /Copiar Paste/);
  assert.match(browser, /Importar al Builder/);
  assert.match(browser, /\/api\/pokepaste-import/);
  assert.match(browser, /pasteCache/);
  assert.match(browser, /Página \$\{data\.pagination\.page\} de/);
  assert.match(scouting, /VgcPastesScoutingBrowser/);
  assert.match(scouting, />Equipos/);
});
