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

function team(id, firstPokemon) {
  return {
    id,
    description: "",
    playerName: `Player ${id}`,
    owner: `owner${id}`,
    pokepasteUrl: `https://pokepast.es/${id.padStart(16, "0")}`,
    hasEvs: true,
    replicaCode: `CODE${id}`,
    dateShared: "12 Sep 2026",
    tournament: "Example Event",
    rank: "",
    sourceUrl: "https://example.com/source",
    pokemon: [firstPokemon, "Sneasler", "Indeedee-F", "Incineroar", "Rillaboom", "Gholdengo"],
  };
}

test("collapses Mega forms to the base species for Scouting core identity", async () => {
  const scouting = await vite.ssrLoadModule("/lib/vgcpastes-scouting.ts");
  const search = await vite.ssrLoadModule("/lib/vgcpastes-scouting-search.ts");
  const format = scouting.getVgcPastesFormat("champions-m-c");
  assert.ok(format);

  assert.equal(search.getVgcPastesScoutingSpeciesIdentity("Aerodactyl-Mega"), "Aerodactyl");
  assert.equal(search.getVgcPastesScoutingSpeciesIdentity("Charizard-Mega-X"), "Charizard");
  assert.equal(search.getVgcPastesScoutingSpeciesIdentity("Garchomp-Mega-Z"), "Garchomp");
  assert.equal(search.getVgcPastesScoutingSpeciesIdentity("Floette-Eternal-Mega"), "Floette-Eternal");
  assert.equal(search.getVgcPastesScoutingSpeciesIdentity("Urshifu-Rapid-Strike"), "Urshifu-Rapid-Strike");

  assert.deepEqual(
    search.normalizeVgcPastesScoutingSpeciesFilters([
      "Aerodactyl-Mega",
      "Aerodactyl",
      "Charizard-Mega-X",
      "Charizard",
    ]),
    ["Aerodactyl", "Charizard"],
  );

  const teams = [
    team("1", "Aerodactyl-Mega"),
    team("2", "Aerodactyl"),
    team("3", "Charizard-Mega-X"),
  ];
  const response = search.buildVgcPastesScoutingSearchResponse(format, teams, {
    pokemon: ["Aerodactyl-Mega", "Aerodactyl"],
    page: 1,
    pageSize: 12,
    fetchedAt: "2026-09-13T01:30:00.000Z",
  });

  assert.deepEqual(response.query.pokemon, ["Aerodactyl"]);
  assert.equal(response.pagination.totalItems, 2);
  assert.deepEqual(response.pokemonOptions, ["Aerodactyl", "Charizard", "Gholdengo", "Incineroar", "Indeedee-F", "Rillaboom", "Sneasler"]);
  assert.equal(response.pokemonOptions.includes("Aerodactyl-Mega"), false);
  assert.equal(response.pokemonOptions.includes("Charizard-Mega-X"), false);
  assert.equal(response.teams[0].pokemon[0], "Aerodactyl-Mega", "cards must retain the exact VGCPastes form label");
  assert.equal(response.teams[1].pokemon[0], "Aerodactyl");
});

test("the public API route uses the Mega-aware search projection", async () => {
  const route = await readFile(new URL("../app/api/vgcpastes-scouting/route.ts", import.meta.url), "utf8");
  assert.match(route, /buildVgcPastesScoutingSearchResponse/);
  assert.match(route, /normalizeVgcPastesScoutingSpeciesFilters/);
});
