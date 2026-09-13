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

function record(index, overrides = {}) {
  return {
    id: `team-${index}`,
    name: `Paste ${index}`,
    creator: index % 2 ? "lenVGC" : "Riopaser",
    format: index % 2 ? "Champions M-C" : "Champions M-B",
    sourceUrl: "https://pokepast.es/0123456789abcdef",
    sourceLabel: "PokéPaste",
    notes: index % 3 === 0 ? "Zoroark reference" : "",
    createdAt: "2026-09-12T00:00:00.000Z",
    updatedAt: `2026-09-12T00:${String(index).padStart(2, "0")}:00.000Z`,
    pokemon: ["Aerodactyl-Mega", "Sneasler", "Sinistcha", "Incineroar", "Milotic", "Kingambit"],
    ...overrides,
  };
}

test("paginates the private paste library and filters a Pokemon core with AND semantics", async () => {
  const { buildScoutingPasteLibraryResponse } = await vite.ssrLoadModule("/lib/scouting-paste-library.ts");
  const records = Array.from({ length: 30 }, (_, index) => record(index + 1, index % 2 ? {} : { pokemon: ["Aerodactyl", "Sneasler", "Indeedee-F", "Incineroar", "Milotic", "Kingambit"] }));

  const pageTwo = buildScoutingPasteLibraryResponse(records, { page: 2, pageSize: 12 });
  assert.equal(pageTwo.pagination.totalAvailable, 30);
  assert.equal(pageTwo.pagination.totalPages, 3);
  assert.equal(pageTwo.items.length, 12);

  const filtered = buildScoutingPasteLibraryResponse(records, { pokemon: ["Aerodactyl-Mega", "Sneasler", "Indeedee-F"], pageSize: 24 });
  assert.deepEqual(filtered.query.pokemon, ["Aerodactyl", "Sneasler", "Indeedee-F"]);
  assert.equal(filtered.pagination.totalItems, 15);
  assert.equal(filtered.pagination.totalPages, 1);
  assert.equal(filtered.items.length, 15);
  assert.ok(filtered.items.every((item) => item.pokemon.some((species) => species === "Aerodactyl") && item.pokemon.includes("Sneasler") && item.pokemon.includes("Indeedee-F")));
});

test("treats Mega forms as the base species in options and deduplicates impossible cores", async () => {
  const { buildScoutingPasteLibraryResponse, normalizeScoutingPastePokemonFilters } = await vite.ssrLoadModule("/lib/scouting-paste-library.ts");
  assert.deepEqual(normalizeScoutingPastePokemonFilters(["Aerodactyl-Mega", "Aerodactyl", "Sneasler"]), ["Aerodactyl", "Sneasler"]);
  const response = buildScoutingPasteLibraryResponse([
    record(1),
    record(2, { pokemon: ["Aerodactyl", "Sneasler", "Indeedee-F", "Incineroar", "Milotic", "Kingambit"] }),
  ]);
  assert.equal(response.pokemonOptions.filter((species) => species === "Aerodactyl").length, 1);
  assert.equal(response.pokemonOptions.includes("Aerodactyl-Mega"), false);
});

test("filters private pastes by creator/source text and exact format", async () => {
  const { buildScoutingPasteLibraryResponse } = await vite.ssrLoadModule("/lib/scouting-paste-library.ts");
  const records = [record(1), record(2), record(3, { creator: "Other", sourceLabel: "VGCPastes", format: "Champions M-C" })];
  const creator = buildScoutingPasteLibraryResponse(records, { search: "lenVGC" });
  assert.equal(creator.pagination.totalItems, 1);
  assert.equal(creator.items[0].creator, "lenVGC");
  const source = buildScoutingPasteLibraryResponse(records, { search: "VGCPastes", format: "Champions M-C" });
  assert.equal(source.pagination.totalItems, 1);
  assert.equal(source.items[0].creator, "Other");
});

test("migrates known creator folders into flat Scouting records without copying teams", async () => {
  const migration = await readFile(new URL("../drizzle/0010_scouting_library.sql", import.meta.url), "utf8");
  assert.match(migration, /ADD `scope` text DEFAULT 'owned' NOT NULL/);
  assert.match(migration, /'RIOPASER', 'LENVGC'/);
  assert.match(migration, /SET\s+`scope` = 'scouting'/);
  assert.match(migration, /SET `folder_id` = NULL/);
  assert.doesNotMatch(migration, /INSERT INTO `teams`/);
});

test("connects Scouting v4 to a flat Mis pastes library and save-from-VGCPastes action", async () => {
  const scoutingView = await readFile(new URL("../components/vgc/scouting-view.tsx", import.meta.url), "utf8");
  const library = await readFile(new URL("../components/vgc/scouting-paste-library.tsx", import.meta.url), "utf8");
  const publicBrowser = await readFile(new URL("../components/vgc/vgcpastes-scouting-browser.tsx", import.meta.url), "utf8");
  const teamsApi = await readFile(new URL("../app/api/teams/route.ts", import.meta.url), "utf8");

  assert.match(scoutingView, /Mis pastes/);
  assert.match(scoutingView, /ScoutingPasteLibrary/);
  assert.match(library, /Guardar paste/);
  assert.match(library, /\/api\/pokepaste-import/);
  assert.match(library, /\/api\/scouting-pastes/);
  assert.match(library, /Importar al Builder/);
  assert.match(publicBrowser, /Guardar/);
  assert.match(publicBrowser, /sourceLabel: "VGCPastes"/);
  assert.match(teamsApi, /listOwnedTeamIds/);
});
