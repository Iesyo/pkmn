import { mkdir, writeFile } from "node:fs/promises";
import vm from "node:vm";
import { gzipSync } from "node:zlib";

const SOURCES = {
  pokedex: "https://play.pokemonshowdown.com/data/pokedex.json",
  moves: "https://play.pokemonshowdown.com/data/moves.json",
  items: "https://play.pokemonshowdown.com/data/items.js",
  teambuilder: "https://play.pokemonshowdown.com/data/teambuilder-tables.js",
};

const FORMAT_TABLES = {
  champions: "champions",
  gen9: "gen9vgc",
  gen8: "gen8vgc",
  gen7: "gen7vgc",
  gen6: "gen6vgc",
};

function toId(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

async function download(url) {
  const response = await fetch(url, {
    headers: { "user-agent": "Iesyo-pkmn-data-builder/1.0" },
  });
  if (!response.ok) throw new Error(`No se pudo descargar ${url}: ${response.status}`);
  return response.text();
}

function readTeambuilderTable(source) {
  if (!source.startsWith("// DO NOT EDIT - automatically built with build-tools/build-indexes")) {
    throw new Error("La tabla de Showdown no tiene el encabezado esperado.");
  }
  const sandbox = { exports: {}, JSON };
  vm.runInNewContext(source, sandbox, { timeout: 10_000 });
  if (!sandbox.exports.BattleTeambuilderTable?.champions) {
    throw new Error("La tabla de Showdown no contiene el formato Champions.");
  }
  return sandbox.exports.BattleTeambuilderTable;
}

function readItemTable(source) {
  const sandbox = { exports: {} };
  vm.runInNewContext(source, sandbox, { timeout: 10_000 });
  if (!sandbox.exports.BattleItems) {
    throw new Error("El catálogo de Showdown no contiene objetos.");
  }
  return sandbox.exports.BattleItems;
}

function inheritedLearnset(table, pokedex, speciesId) {
  let current = speciesId;
  const visited = new Set();
  while (current && !visited.has(current)) {
    visited.add(current);
    if (table[current]) return table[current];
    const species = pokedex[current];
    current = toId(species?.changesFrom || species?.baseSpecies);
  }
  return {};
}

const [pokedexSource, movesSource, itemsSource, teambuilderSource] = await Promise.all([
  download(SOURCES.pokedex),
  download(SOURCES.moves),
  download(SOURCES.items),
  download(SOURCES.teambuilder),
]);

const pokedex = JSON.parse(pokedexSource);
const moves = JSON.parse(movesSource);
const itemData = readItemTable(itemsSource);
const tables = readTeambuilderTable(teambuilderSource);
const champions = tables.champions;
const speciesIds = new Set();
const formats = {};

for (const [format, tableName] of Object.entries(FORMAT_TABLES)) {
  const table = tables[tableName];
  const ids = table.tiers
    .filter((entry) => typeof entry === "string")
    .filter((id) => {
      if (!pokedex[id]) return false;
      if (format !== "champions") return true;
      return Object.keys(inheritedLearnset(champions.learnsets, pokedex, id)).length > 0;
    });
  formats[format] = ids;
  ids.forEach((id) => speciesIds.add(id));
}

formats.custom = [...new Set(Object.values(formats).flat())];
formats.custom.forEach((id) => speciesIds.add(id));

const itemTableByFormat = {
  champions: champions.items,
  gen9: tables.items,
  gen8: tables.gen8.items,
  gen7: tables.gen7.items,
  gen6: tables.gen6.items,
};
const itemFormats = Object.fromEntries(
  Object.entries(itemTableByFormat).map(([format, entries]) => [
    format,
    entries.filter((entry) => typeof entry === "string" && itemData[entry]),
  ]),
);
itemFormats.custom = [...new Set(Object.values(itemFormats).flat())];

const referencedItemIds = new Set(Object.values(itemFormats).flat());
const itemCatalog = Object.fromEntries(
  [...referencedItemIds]
    .sort()
    .map((id) => [id, { name: itemData[id].name }]),
);

const referencedMoveIds = new Set();
const species = {};

for (const id of [...speciesIds].sort()) {
  const raw = pokedex[id];
  const standardLearnset = inheritedLearnset(tables.learnsets, pokedex, id);
  const championsLearnset = inheritedLearnset(champions.learnsets, pokedex, id);
  Object.keys(standardLearnset).forEach((moveId) => referencedMoveIds.add(moveId));
  Object.keys(championsLearnset).forEach((moveId) => referencedMoveIds.add(moveId));

  species[id] = {
    name: raw.name,
    types: raw.types,
    baseStats: raw.baseStats,
    abilities: [...new Set(Object.values(raw.abilities ?? {}))],
    baseSpecies: raw.baseSpecies ? toId(raw.baseSpecies) : undefined,
    learnset: tables.learnsets[id] ?? {},
    championsMoves: Object.keys(champions.learnsets[id] ?? {}),
  };
}

const moveCatalog = {};
for (const id of [...referencedMoveIds].sort()) {
  const raw = moves[id];
  if (!raw) continue;
  const override = champions.overrideMoveData?.[id] ?? {};
  moveCatalog[id] = {
    name: raw.name,
    type: override.type ?? raw.type,
    category: raw.category,
  };
}

const snapshot = {
  metadata: {
    source: "Pokémon Showdown / Smogon",
    captured: new Date().toISOString().slice(0, 10),
    format: "champions + VGC Gen 6-9",
    urls: SOURCES,
  },
  formats,
  itemFormats,
  species,
  moves: moveCatalog,
  items: itemCatalog,
};

await mkdir(new URL("../public/data/", import.meta.url), { recursive: true });
await writeFile(
  new URL("../public/data/showdown-dex.json.gz", import.meta.url),
  gzipSync(`${JSON.stringify(snapshot)}\n`, { level: 9 }),
);

console.log(
  `Snapshot creado: ${Object.keys(species).length} especies, ${Object.keys(moveCatalog).length} movimientos y ${Object.keys(itemCatalog).length} objetos.`,
);
