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

async function showdownModule() {
  return vite.ssrLoadModule("/lib/showdown-data.ts");
}

async function teamBuilderModule() {
  return vite.ssrLoadModule("/lib/team-builder.ts");
}

function configureSet(base, values) {
  return {
    ...base,
    ...values,
    mechanics: { ...base.mechanics, ...values.mechanics },
    moves: values.moves.map((name) => ({ name, type: null, damaging: true, usage: 0 })),
  };
}

test("Champions uses the calc-engine ability when a battle form snapshot is stale", async () => {
  const { getLegalAbilities } = await showdownModule();
  const snapshot = {
    metadata: { source: "test", captured: "2026-09-05", format: "champions", schema: 3, urls: {} },
    formats: { champions: ["floettemega"] },
    itemFormats: { champions: [] },
    species: {
      floettemega: {
        name: "Floette-Mega",
        types: ["Fairy"],
        baseStats: { hp: 74, atk: 85, def: 87, spa: 155, spd: 148, spe: 102 },
        abilities: ["Flower Veil"],
        learnset: {},
        championsMoves: [],
      },
    },
    moves: {},
    abilities: {},
    items: {},
  };

  assert.deepEqual(getLegalAbilities(snapshot, "Floette-Mega", "champions"), ["Fairy Aura"]);
});

test("Mega Floette actually calculates with Fairy Aura", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const floette = configureSet(emptyPokemon(1), {
    species: "Floette-Eternal",
    ability: "Flower Veil",
    item: "Floettite",
    nature: "Modest",
    evs: "30 SpA",
    moves: ["Moonblast", "Dazzling Gleam", "Light of Ruin", "Protect"],
  });
  const abomasnow = configureSet(emptyPokemon(2), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "",
    nature: "Serious",
    evs: "",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Protect"],
  });

  const mega = createDamageDraft(floette);
  mega.megaActive = true;
  const outcome = calculateDamage("champions", mega, createDamageDraft(abomasnow), "Moonblast", defaultDamageField());

  assert.equal(outcome.error, undefined);
  assert.ok(outcome.max > 0);
  assert.match(outcome.description, /Fairy Aura/);
});

test("current HP changes KO math while normal damage percentages stay based on max HP", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const attacker = configureSet(emptyPokemon(1), {
    species: "Kleavor",
    ability: "Sharpness",
    item: "",
    nature: "Adamant",
    evs: "20 Atk",
    moves: ["Stone Axe", "X-Scissor", "Close Combat", "Protect"],
  });
  const defender = configureSet(emptyPokemon(2), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "",
    nature: "Serious",
    evs: "32 HP / 32 Def",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Protect"],
  });

  const full = createDamageDraft(defender);
  const wounded = createDamageDraft(defender);
  wounded.hpPercent = 10;

  const fullOutcome = calculateDamage("champions", createDamageDraft(attacker), full, "Stone Axe", defaultDamageField());
  const woundedOutcome = calculateDamage("champions", createDamageDraft(attacker), wounded, "Stone Axe", defaultDamageField());

  assert.equal(fullOutcome.error, undefined);
  assert.equal(woundedOutcome.error, undefined);
  assert.equal(woundedOutcome.min, fullOutcome.min);
  assert.equal(woundedOutcome.max, fullOutcome.max);
  assert.equal(woundedOutcome.minPercent, fullOutcome.minPercent);
  assert.equal(woundedOutcome.maxPercent, fullOutcome.maxPercent);
  assert.ok(woundedOutcome.minPercent > wounded.hpPercent);
  assert.notEqual(woundedOutcome.koChance, fullOutcome.koChance);
  assert.match(woundedOutcome.koChance, /KO/);
});
