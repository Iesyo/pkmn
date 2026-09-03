export const SHOWDOWN_SOURCES = {
  pokedex: "https://play.pokemonshowdown.com/data/pokedex.json",
  moves: "https://play.pokemonshowdown.com/data/moves.json",
  abilities: "https://play.pokemonshowdown.com/data/abilities.js",
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
    let whitespace = "";
    while (cursor < source.length && /\s/.test(source[cursor])) {
      whitespace += source[cursor];
      cursor += 1;
    }
    const first = source[cursor];
    if (!first || !/[A-Za-z_$]/.test(first)) continue;
    let keyEnd = cursor + 1;
    while (keyEnd < source.length && /[A-Za-z0-9_$]/.test(source[keyEnd])) keyEnd += 1;
    if (source[keyEnd] !== ":") continue;

    const key = source.slice(cursor, keyEnd);
    result += `${whitespace}"${key}":`;
    index = keyEnd;
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

function finiteNumber(value, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function copyRecord(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
}

function remainingDetails(source, excludedKeys) {
  return Object.fromEntries(Object.entries(source ?? {}).filter(([key]) => !excludedKeys.has(key)));
}

const MOVE_TOP_LEVEL_KEYS = new Set([
  "name", "type", "category", "basePower", "accuracy", "pp", "priority", "target", "flags", "desc", "shortDesc",
]);
const ABILITY_TOP_LEVEL_KEYS = new Set(["name", "desc", "shortDesc", "rating", "num"]);
const ITEM_TOP_LEVEL_KEYS = new Set(["name", "desc", "shortDesc"]);

function normalizeMoveData(raw, override = {}) {
  // Keep the historical Champions-effective top-level values used by the app,
  // but also preserve the raw override separately so format-aware consumers can distinguish it.
  const merged = { ...raw, ...override };
  const effects = {};
  const structuredKeys = ["boosts", "secondary", "secondaries", "self", "condition", "zMove", "maxMove"];
  for (const key of structuredKeys) {
    if (merged[key] !== undefined) effects[key] = merged[key];
  }
  const scalarKeys = [
    "status",
    "volatileStatus",
    "drain",
    "recoil",
    "multihit",
    "critRatio",
    "willCrit",
    "breaksProtect",
    "hasCrashDamage",
    "mindBlownRecoil",
    "struggleRecoil",
    "basePowerCallback",
    "damageCallback",
    "damage",
    "callsMove",
    "forceSwitch",
    "selfSwitch",
    "stealsBoosts",
    "thawsTarget",
  ];
  for (const key of scalarKeys) {
    if (merged[key] !== undefined) effects[key] = merged[key];
  }

  const accuracy = merged.accuracy === true
    ? true
    : typeof merged.accuracy === "number"
      ? merged.accuracy
      : null;

  return {
    name: merged.name,
    type: merged.type,
    category: merged.category,
    basePower: finiteNumber(merged.basePower),
    accuracy,
    pp: finiteNumber(merged.pp),
    priority: finiteNumber(merged.priority),
    target: typeof merged.target === "string" ? merged.target : "normal",
    flags: Object.keys(copyRecord(merged.flags) ?? {}).filter((flag) => Boolean(merged.flags[flag])).sort(),
    desc: typeof merged.desc === "string" ? merged.desc : "",
    shortDesc: typeof merged.shortDesc === "string" ? merged.shortDesc : (typeof merged.desc === "string" ? merged.desc : ""),
    effects,
    details: remainingDetails(merged, MOVE_TOP_LEVEL_KEYS),
    championsOverride: Object.keys(override).length ? override : undefined,
  };
}

function normalizeAbilityData(raw, fallbackName, override = {}) {
  const base = raw ?? {};
  return {
    name: typeof base.name === "string" ? base.name : fallbackName,
    desc: typeof base.desc === "string" ? base.desc : "",
    shortDesc: typeof base.shortDesc === "string" ? base.shortDesc : (typeof base.desc === "string" ? base.desc : ""),
    rating: typeof base.rating === "number" ? base.rating : null,
    num: typeof base.num === "number" ? base.num : null,
    details: remainingDetails(base, ABILITY_TOP_LEVEL_KEYS),
    championsOverride: Object.keys(override).length ? override : undefined,
  };
}

function normalizeItemData(raw, override = {}) {
  const base = raw ?? {};
  return {
    name: base.name,
    desc: typeof base.desc === "string" ? base.desc : "",
    shortDesc: typeof base.shortDesc === "string" ? base.shortDesc : (typeof base.desc === "string" ? base.desc : ""),
    details: remainingDetails(base, ITEM_TOP_LEVEL_KEYS),
    championsOverride: Object.keys(override).length ? override : undefined,
  };
}

export async function buildShowdownSnapshot(fetcher = fetch) {
  const [pokedexSource, movesSource, abilitiesSource, itemsSource, teambuilderSource] = await Promise.all([
    download(SHOWDOWN_SOURCES.pokedex, fetcher),
    download(SHOWDOWN_SOURCES.moves, fetcher),
    download(SHOWDOWN_SOURCES.abilities, fetcher),
    download(SHOWDOWN_SOURCES.items, fetcher),
    download(SHOWDOWN_SOURCES.teambuilder, fetcher),
  ]);

  const pokedex = JSON.parse(pokedexSource);
  const moves = JSON.parse(movesSource);
  const abilityData = parseEs3Export(abilitiesSource, "BattleAbilities");
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
      .map((id) => [id, normalizeItemData(itemData[id], champions.overrideItemData?.[id] ?? {})]),
  );

  const referencedMoveIds = new Set();
  const referencedAbilityIds = new Set();
  const abilityNamesById = new Map();
  const species = {};

  for (const id of [...speciesIds].sort()) {
    const raw = pokedex[id];
    if (!raw) continue;
    const standardLearnset = inheritedLearnset(tables.learnsets, pokedex, id);
    const championsLearnset = inheritedLearnset(champions.learnsets, pokedex, id);
    Object.keys(standardLearnset).forEach((moveId) => referencedMoveIds.add(moveId));
    Object.keys(championsLearnset).forEach((moveId) => referencedMoveIds.add(moveId));

    const abilities = [...new Set(Object.values(raw.abilities ?? {}).filter((ability) => typeof ability === "string"))];
    const speciesOverride = champions.overrideSpeciesData?.[id] ?? {};
    const championAbilities = Object.values(speciesOverride.abilities ?? {}).filter((ability) => typeof ability === "string");
    for (const abilityName of [...abilities, ...championAbilities]) {
      const abilityId = toId(abilityName);
      referencedAbilityIds.add(abilityId);
      abilityNamesById.set(abilityId, abilityName);
    }

    species[id] = {
      name: raw.name,
      types: raw.types,
      baseStats: raw.baseStats,
      abilities,
      baseSpecies: raw.baseSpecies ? toId(raw.baseSpecies) : undefined,
      learnset: tables.learnsets[id] ?? {},
      championsMoves: Object.keys(champions.learnsets[id] ?? {}),
      championsOverride: Object.keys(speciesOverride).length ? speciesOverride : undefined,
    };
  }

  const moveCatalog = {};
  for (const id of [...referencedMoveIds].sort()) {
    const raw = moves[id];
    if (!raw) continue;
    const override = champions.overrideMoveData?.[id] ?? {};
    moveCatalog[id] = normalizeMoveData(raw, override);
  }

  const abilityCatalog = {};
  for (const id of [...referencedAbilityIds].sort()) {
    abilityCatalog[id] = normalizeAbilityData(
      abilityData[id],
      abilityNamesById.get(id) ?? id,
      champions.overrideAbilityData?.[id] ?? {},
    );
  }

  return {
    metadata: {
      source: "Pokémon Showdown / Smogon",
      captured: new Date().toISOString().slice(0, 10),
      format: "champions + VGC Gen 6-9",
      schema: 3,
      urls: SHOWDOWN_SOURCES,
    },
    formats,
    itemFormats,
    species,
    moves: moveCatalog,
    abilities: abilityCatalog,
    items: itemCatalog,
  };
}
