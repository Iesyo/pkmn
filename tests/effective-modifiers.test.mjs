import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  ssr: { external: ["@smogon/calc"] },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

async function damageModule() {
  return vite.ssrLoadModule("/lib/damage-calculator.ts");
}

async function teamBuilderModule() {
  return vite.ssrLoadModule("/lib/team-builder.ts");
}

function configureSet(base, values) {
  return {
    ...base,
    ...values,
    mechanics: { ...base.mechanics, ...values.mechanics },
    moves: (values.moves ?? []).map((name) => ({
      name,
      type: null,
      damaging: name !== "Protect",
      usage: 0,
    })),
  };
}

async function pair(leftValues, rightValues, format = "gen9") {
  const { createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const left = createDamageDraft(configureSet(emptyPokemon(1), leftValues));
  const right = createDamageDraft(configureSet(emptyPokemon(2), rightValues));
  return { left, right, field: defaultDamageField(), format };
}

test("effective Speed comes from Showdown items, status, field and abilities", async () => {
  const { calculateEffectiveStats } = await damageModule();
  const setup = await pair(
    { species: "Hawlucha", ability: "Unburden", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Close Combat", "Protect"] },
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Dragon Claw", "Protect"] },
  );

  const baseline = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.ok(baseline.left && baseline.right);

  setup.left.set.item = "Choice Scarf";
  const scarf = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(scarf.left.spe, Math.floor(baseline.left.spe * 1.5));

  setup.field.left.tailwind = true;
  const scarfTailwind = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(scarfTailwind.left.spe, baseline.left.spe * 3);

  setup.field.magicRoom = true;
  const magicRoom = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(magicRoom.left.spe, baseline.left.spe * 2);

  setup.field.magicRoom = false;
  setup.field.left.tailwind = false;
  setup.left.set.item = "";
  setup.left.status = "par";
  const paralyzed = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(paralyzed.left.spe, Math.floor(baseline.left.spe / 2));

  setup.left.status = "";
  setup.left.abilityOn = true;
  const unburden = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(unburden.left.spe, baseline.left.spe * 2);
});

test("weather and terrain speed abilities are resolved by the engine", async () => {
  const { calculateEffectiveStats } = await damageModule();
  const setup = await pair(
    { species: "Barraskewda", ability: "Swift Swim", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Liquidation", "Protect"] },
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Dragon Claw", "Protect"] },
  );
  const dry = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  setup.field.weather = "Rain";
  const rain = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(rain.left.spe, dry.left.spe * 2);

  const quark = await pair(
    { species: "Iron Bundle", ability: "Quark Drive", item: "", nature: "Timid", evs: "252 Spe", moves: ["Freeze-Dry", "Protect"] },
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Dragon Claw", "Protect"] },
  );
  const neutral = calculateEffectiveStats(quark.format, quark.left, quark.right, quark.field);
  quark.field.terrain = "Electric";
  const electric = calculateEffectiveStats(quark.format, quark.left, quark.right, quark.field);
  assert.ok(electric.left.spe > neutral.left.spe);
});

test("conditional entry abilities and their counters affect the battle stat snapshot", async () => {
  const { calculateEffectiveStats } = await damageModule();
  const setup = await pair(
    { species: "Landorus-Therian", ability: "Intimidate", item: "", nature: "Jolly", evs: "252 Spe", moves: ["Earthquake", "Protect"] },
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Jolly", evs: "252 Atk / 252 Spe", moves: ["Dragon Claw", "Protect"] },
  );

  const inactive = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  setup.left.abilityOn = true;
  const intimidated = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.ok(intimidated.right.atk < inactive.right.atk);

  setup.right.set.item = "Clear Amulet";
  const blocked = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  assert.equal(blocked.right.atk, inactive.right.atk);
});

test("contextual item and ability modifiers stay in damage calculation instead of fake universal stats", async () => {
  const { calculateDamage, calculateEffectiveStats } = await damageModule();
  const setup = await pair(
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Adamant", evs: "252 Atk", moves: ["Dragon Claw", "Protect"] },
    { species: "Murkrow", ability: "Prankster", item: "", nature: "Bold", evs: "252 HP / 252 Def", moves: ["Foul Play", "Protect"] },
  );

  const noBand = calculateDamage(setup.format, setup.left, setup.right, "Dragon Claw", setup.field);
  setup.left.set.item = "Choice Band";
  const banded = calculateDamage(setup.format, setup.left, setup.right, "Dragon Claw", setup.field);
  assert.ok(banded.max > noBand.max);

  setup.left.set.item = "";
  const noEvioliteStats = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  const noEvioliteDamage = calculateDamage(setup.format, setup.left, setup.right, "Dragon Claw", setup.field);
  setup.right.set.item = "Eviolite";
  const evioliteStats = calculateEffectiveStats(setup.format, setup.left, setup.right, setup.field);
  const evioliteDamage = calculateDamage(setup.format, setup.left, setup.right, "Dragon Claw", setup.field);
  assert.equal(evioliteStats.right.def, noEvioliteStats.right.def);
  assert.ok(evioliteDamage.max < noEvioliteDamage.max);
});

test("Supreme Overlord uses the configured fainted ally count in real damage", async () => {
  const { calculateDamage } = await damageModule();
  const setup = await pair(
    { species: "Kingambit", ability: "Supreme Overlord", item: "", nature: "Adamant", evs: "252 Atk", moves: ["Kowtow Cleave", "Protect"] },
    { species: "Garchomp", ability: "Rough Skin", item: "", nature: "Impish", evs: "252 HP / 252 Def", moves: ["Dragon Claw", "Protect"] },
  );

  const fresh = calculateDamage(setup.format, setup.left, setup.right, "Kowtow Cleave", setup.field);
  setup.left.alliesFainted = 5;
  const powered = calculateDamage(setup.format, setup.left, setup.right, "Kowtow Cleave", setup.field);
  assert.ok(powered.max > fresh.max);
});
