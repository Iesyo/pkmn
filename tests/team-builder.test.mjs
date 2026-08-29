import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gunzipSync } from "node:zlib";
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

async function readSnapshot() {
  const compressed = await readFile(new URL("../public/data/showdown-dex.json.gz", import.meta.url));
  return JSON.parse(gunzipSync(compressed).toString("utf8"));
}

after(async () => {
  await vite.close();
});

test("reproduces the Pokémon Champions Stat Points formula", async () => {
  const { calculateStat } = await vite.ssrLoadModule("/lib/team-builder.ts");
  const baseStats = { hp: 80, atk: 160, def: 80, spa: 130, spd: 80, spe: 100 };

  assert.equal(calculateStat(baseStats, "HP", 2, 50, "Adamant", "champions"), 157);
  assert.equal(calculateStat(baseStats, "Atk", 31, 50, "Adamant", "champions"), 232);
  assert.equal(calculateStat(baseStats, "Def", 1, 50, "Adamant", "champions"), 101);
  assert.equal(calculateStat(baseStats, "SpA", 0, 50, "Adamant", "champions"), 135);
  assert.equal(calculateStat(baseStats, "SpD", 0, 50, "Adamant", "champions"), 100);
  assert.equal(calculateStat(baseStats, "Spe", 32, 50, "Adamant", "champions"), 152);
});

test("uses the official Showdown Champions types, abilities and learnsets", async () => {
  const snapshot = await readSnapshot();
  const { getLegalAbilities, getLegalMoves, getSpecies } = await vite.ssrLoadModule("/lib/showdown-data.ts");

  const charizard = getSpecies(snapshot, "Charizard");
  assert.deepEqual(charizard.types, ["Fire", "Flying"]);
  assert.deepEqual(charizard.baseStats, { hp: 78, atk: 84, def: 78, spa: 109, spd: 85, spe: 100 });
  assert.deepEqual(getLegalAbilities(snapshot, "Charizard"), ["Blaze", "Solar Power"]);
  assert.ok(getLegalMoves(snapshot, "Charizard", "champions").includes("Dragon Claw"));
});

test("inherits a Champions learnset for Mega formes", async () => {
  const snapshot = await readSnapshot();
  const { getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");

  assert.ok(getLegalMoves(snapshot, "Charizard-Mega-X", "champions").includes("Dragon Claw"));
});

test("uses Showdown's complete static sprite catalog for modern species and forms", async () => {
  const { getSpriteUrl } = await vite.ssrLoadModule("/lib/pokemon-data.ts");

  assert.equal(
    getSpriteUrl("Miraidon"),
    "https://play.pokemonshowdown.com/sprites/gen5/miraidon.png",
  );
  assert.equal(
    getSpriteUrl("Ogerpon-Wellspring"),
    "https://play.pokemonshowdown.com/sprites/gen5/ogerpon-wellspring.png",
  );
  assert.equal(
    getSpriteUrl("Flutter Mane"),
    "https://play.pokemonshowdown.com/sprites/gen5/fluttermane.png",
  );
  assert.equal(
    getSpriteUrl("Chi-Yu"),
    "https://play.pokemonshowdown.com/sprites/gen5/chiyu.png",
  );
});

test("filters held items by the selected Showdown format", async () => {
  const snapshot = await readSnapshot();
  const { getLegalItems, isItemLegal } = await vite.ssrLoadModule("/lib/showdown-data.ts");

  assert.ok(getLegalItems(snapshot, "champions").includes("Charizardite X"));
  assert.equal(isItemLegal(snapshot, "Charizardite X", "champions"), true);
  assert.equal(isItemLegal(snapshot, "Firium Z", "gen7"), true);
  assert.equal(isItemLegal(snapshot, "Firium Z", "gen9"), false);
  assert.equal(isItemLegal(snapshot, "Booster Energy", "gen9"), true);
  assert.equal(isItemLegal(snapshot, "Booster Energy", "champions"), false);
  assert.equal(isItemLegal(snapshot, "", "champions"), true);
});

test("starts a wide calculator-only Team Builder in Champions with fixed Pokémon cards", async () => {
  const { DEFAULT_BATTLE_FORMAT, DEFAULT_BATTLE_MECHANICS } = await vite.ssrLoadModule("/lib/team-builder.ts");
  const [dashboardSource, builderSource] = await Promise.all([
    readFile(new URL("../app/vgc-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
  ]);

  assert.equal(DEFAULT_BATTLE_FORMAT, "champions");
  assert.deepEqual(DEFAULT_BATTLE_MECHANICS, ["mega"]);
  assert.match(dashboardSource, /const \[builderVersionId, setBuilderVersionId\] = useState\(""\)/);
  assert.match(dashboardSource, /initialVersion=\{versions\.find\(\(version\) => version\.id === builderVersionId\)\}/);
  assert.doesNotMatch(dashboardSource, /initialVersion=.*\?\? left/);
  assert.match(dashboardSource, /w-full max-w-none/);
  assert.match(builderSource, /xl:grid-cols-\[270px_minmax\(0,1fr\)\]/);
  assert.match(builderSource, /data-team-calculator/);
  assert.match(builderSource, /h-40 min-w-0 overflow-hidden/);
  assert.doesNotMatch(builderSource, /group min-h-40/);
  assert.match(builderSource, /w-full min-w-0 truncate text-sm font-black/);
  assert.match(builderSource, /mt-auto flex min-h-0 items-end justify-between/);
  assert.match(builderSource, /h-20 w-20 shrink-0 translate-y-2/);
  assert.match(builderSource, /size-20 object-contain/);
  assert.match(builderSource, /text-sm font-black text-white/);
  assert.match(builderSource, /text-\[9px\].*>\{type\}<\/TypeBadge>/);
  assert.match(builderSource, /text-\[11px\].*\{pokemon\.item \|\| "Sin objeto"\}/);
});
