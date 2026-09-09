import { execFile } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { gzipSync } from "node:zlib";

import {
  CHAMPIONS_REGULATION,
  CHAMPIONS_REGULATION_STARTED_AT,
  CHAMPIONS_REQUIRED_ITEM_IDS,
  CHAMPIONS_REQUIRED_SPECIES_IDS,
  CHAMPIONS_SHOWDOWN_COMMIT,
  assertChampionsRegulationSnapshot,
} from "../lib/champions-regulation.mjs";
import { buildShowdownSnapshot } from "../lib/showdown-snapshot-builder.mjs";

const execFileAsync = promisify(execFile);
const require = createRequire(import.meta.url);
const checkout = resolve(process.argv[2] ?? "../pokemon-showdown");

async function git(...args) {
  return (await execFileAsync("git", ["-C", checkout, ...args], { encoding: "utf8" })).stdout.trim();
}

try {
  await execFileAsync("git", ["-C", checkout, "cat-file", "-e", `${CHAMPIONS_SHOWDOWN_COMMIT}^{commit}`]);
} catch {
  throw new Error(`El checkout de Showdown no conoce el commit M-C ${CHAMPIONS_SHOWDOWN_COMMIT}.`);
}

const revision = await git("rev-parse", "HEAD");
const { Dex } = require(resolve(checkout, "dist/sim/dex.js"));
const championsDex = Dex.mod("champions");
const snapshot = await buildShowdownSnapshot();

function toId(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function sortedUnique(values) {
  return [...new Set(values)].sort();
}

function championsMoveIds(speciesId) {
  return sortedUnique(championsDex.species.getFullLearnset(speciesId)
    .flatMap(({ learnset }) => Object.keys(learnset ?? {})));
}

for (const id of CHAMPIONS_REQUIRED_SPECIES_IDS) {
  const species = championsDex.species.get(id);
  if (!species.exists || species.isNonstandard || species.tier === "Illegal") {
    throw new Error(`${id} no es legal en el mod Champions del checkout indicado.`);
  }

  const previous = snapshot.species[id];
  const abilities = sortedUnique(Object.values(species.abilities ?? {}));
  const baseSpecies = toId(species.baseSpecies);
  snapshot.species[id] = {
    name: species.name,
    types: previous?.types ?? [...species.types],
    baseStats: previous?.baseStats ?? { ...species.baseStats },
    abilities: previous?.abilities ?? abilities,
    baseSpecies: previous?.baseSpecies ?? (baseSpecies && baseSpecies !== id ? baseSpecies : undefined),
    learnset: previous?.learnset ?? {},
    championsMoves: championsMoveIds(id),
    championsOverride: {
      ...(previous?.championsOverride ?? {}),
      types: [...species.types],
      baseStats: { ...species.baseStats },
      abilities: { ...species.abilities },
    },
  };

  for (const abilityName of abilities) {
    const abilityId = toId(abilityName);
    if (snapshot.abilities[abilityId]) continue;
    const ability = championsDex.abilities.get(abilityId);
    snapshot.abilities[abilityId] = {
      name: ability.name || abilityName,
      desc: "",
      shortDesc: "",
      rating: typeof ability.rating === "number" ? ability.rating : null,
      num: typeof ability.num === "number" ? ability.num : null,
      details: {},
    };
  }
}

for (const id of CHAMPIONS_REQUIRED_ITEM_IDS) {
  const item = championsDex.items.get(id);
  if (!item.exists || item.isNonstandard) {
    throw new Error(`${id} no es legal en el mod Champions del checkout indicado.`);
  }
  if (!snapshot.items[id]) {
    snapshot.items[id] = {
      name: item.name,
      desc: "",
      shortDesc: "",
      details: {
        num: item.num,
        gen: item.gen,
        megaStone: item.megaStone,
      },
    };
  }
}

snapshot.formats.champions = sortedUnique([
  ...snapshot.formats.champions,
  ...CHAMPIONS_REQUIRED_SPECIES_IDS,
]);
snapshot.formats.custom = sortedUnique([
  ...snapshot.formats.custom,
  ...CHAMPIONS_REQUIRED_SPECIES_IDS,
]);
snapshot.itemFormats.champions = sortedUnique([
  ...snapshot.itemFormats.champions,
  ...CHAMPIONS_REQUIRED_ITEM_IDS,
]);
snapshot.itemFormats.custom = sortedUnique([
  ...snapshot.itemFormats.custom,
  ...CHAMPIONS_REQUIRED_ITEM_IDS,
]);
snapshot.metadata.regulation = CHAMPIONS_REGULATION;
snapshot.metadata.regulationStartedAt = CHAMPIONS_REGULATION_STARTED_AT;
snapshot.metadata.sourceRevision = revision;
snapshot.metadata.urls = {
  ...snapshot.metadata.urls,
  champions: `https://github.com/smogon/pokemon-showdown/tree/${revision}/data/mods/champions`,
};

assertChampionsRegulationSnapshot(snapshot);

await mkdir(new URL("../public/data/", import.meta.url), { recursive: true });
await writeFile(
  new URL("../public/data/showdown-dex.json.gz", import.meta.url),
  gzipSync(`${JSON.stringify(snapshot)}\n`, { level: 9 }),
);

console.log(
  `Snapshot ${CHAMPIONS_REGULATION} creado desde Showdown ${revision.slice(0, 7)}: `
  + `${snapshot.formats.champions.length} entradas Champions y ${snapshot.itemFormats.champions.length} objetos.`,
);
