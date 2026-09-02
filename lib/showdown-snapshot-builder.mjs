export const SHOWDOWN_SOURCES = {
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

async function download(url, fetcher) {
  const response = await fetcher(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`Pokémon Showdown respondió ${response.status} al descargar ${url}.`);
  return response.text();
}

function decodeEscapedSingleQuotedString(value) {
  let result = "";
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (char !== "\\") {
      result += char;
      continue;
    }
    const next = value[index + 1];
    if (next !== "\\" && next !== "'") {
      throw new Error("La tabla de Showdown contiene una secuencia escapada inesperada.");
    }
    result += next;
    index += 1;
  }
  return result;
}

export function parseTeambuilderSource(source) {
  if (!source.startsWith("// DO NOT EDIT - automatically built with build-tools/build-indexes")) {
    throw new Error("La tabla de Showdown no tiene el encabezado esperado.");
  }
  const marker = "exports.BattleTeambuilderTable = JSON.parse('";
  const start = source.indexOf(marker);
  const end = source.lastIndexOf("');");
  if (start < 0 || end <= start + marker.length) {
    throw new Error("La tabla de Showdown no tiene el formato serializado esperado.");
  }
  const encoded = source.slice(start + marker.length, end);
  const table = JSON.parse(decodeEscapedSingleQuotedString(encoded));
  if (!table?.champions?.tiers || !table?.champions?.learnsets) {
    throw new Error("La tabla de Showdown no contiene el formato Champions.");
  }
  return table;
}

function quoteEs3ObjectKeys(source) {
  let result = "";
  let inString = false;
  let escaped = false;

  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    if (inString) {
      result += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }

    if (char === '"') {
      inString = true;
      result += char;
      continue;
    }

    result += char;
    if (char !== "{" && char !== ",") continue;

    let cursor = index + 1;
    const first = source[cursor];
    if (!first || !/[A-Za-z]/.test(first)) continue;
    cursor += 1;
    while (cursor < source.length && /[A-Za-z0-9]/.test(source[cursor])) cursor += 1;
    if (source[cursor] !== ":") continue;

    const key = source.slice(index + 1, cursor);
    result += `"${key}":`;
    index = cursor;
  }

  return result;
}

export function parseEs3Export(source, exportName) {
  const marker = `exports.${exportName} = `;
  const start = source.indexOf(marker);
  if (start < 0) throw new Error(`El catálogo de Showdown no contiene ${exportName}.`);
  let expression = source.slice(start + marker.length).trim();
  if (expression.endsWith(";")) expression = expression.slice(0, -1);
  return JSON.parse(quoteEs3ObjectKeys(expression));
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

export async function buildShowdownSnapshot(fetcher = fetch) {
  const [pokedexSource, movesSource, itemsSource, teambuilderSource] = await Promise.all([
    download(SHOWDOWN_SOURCES.pokedex, fetcher),
    download(SHOWDOWN_SOURCES.moves, fetcher),
    download(SHOWDOWN_SOURCES.items, fetcher),
    download(SHOWDOWN_SOURCES.teambuilder, fetcher),
  ]);

  const pokedex = JSON.parse(pokedexSource);
  const moves = JSON.parse(movesSource);
  const itemData = parseEs3Export(itemsSource, "BattleItems");
  const tables = parseTeambuilderSource(teambuilderSource);
  const champions = tables.champions;
  const speciesIds = new Set();
  const formats = {};

  for (const [format, tableName] of Object.entries(FORMAT_TABLES)) {
    const table = tables[tableName];
    if (!Array.isArray(table?.tiers)) throw new Error(`Showdown no contiene la tabla ${tableName}.`);
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
    gen8: tables.gen8?.items,
    gen7: tables.gen7?.items,
    gen6: tables.gen6?.items,
  };
  const itemFormats = Object.fromEntries(
    Object.entries(itemTableByFormat).map(([format, entries]) => {
      if (!Array.isArray(entries)) throw new Error(`Showdown no contiene objetos para ${format}.`);
      return [format, entries.filter((entry) => typeof entry === "string" && itemData[entry])];
    }),
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
    if (!raw) continue;
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

  return {
    metadata: {
      source: "Pokémon Showdown / Smogon",
      captured: new Date().toISOString().slice(0, 10),
      format: "champions + VGC Gen 6-9",
      urls: SHOWDOWN_SOURCES,
    },
    formats,
    itemFormats,
    species,
    moves: moveCatalog,
    items: itemCatalog,
  };
}
