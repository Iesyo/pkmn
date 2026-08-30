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

function setFrom(base, species, ability, move) {
  return {
    ...base,
    species,
    nickname: species,
    ability,
    item: "",
    nature: "Adamant",
    evs: "252 Atk",
    moves: [{ name: move, type: null, damaging: true, usage: 0 }],
  };
}

test("partner Ruin abilities apply only from the battle side that owns them", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await vite.ssrLoadModule("/lib/damage-calculator.ts");
  const { emptyPokemon } = await vite.ssrLoadModule("/lib/team-builder.ts");
  const left = createDamageDraft(setFrom(emptyPokemon(1), "Garchomp", "Rough Skin", "Dragon Claw"));
  const right = createDamageDraft(setFrom(emptyPokemon(2), "Dragonite", "Inner Focus", "Dragon Claw"));
  const field = defaultDamageField();

  const baseline = calculateDamage("gen9", left, right, "Dragon Claw", field);
  field.left.swordOfRuin = true;
  const alliedSword = calculateDamage("gen9", left, right, "Dragon Claw", field);
  assert.ok(alliedSword.max > baseline.max);

  field.left.swordOfRuin = false;
  field.right.swordOfRuin = true;
  const enemySword = calculateDamage("gen9", left, right, "Dragon Claw", field);
  assert.equal(enemySword.max, baseline.max);

  const incomingBaseline = calculateDamage("gen9", right, left, "Dragon Claw", defaultDamageField(), true);
  const reverseField = defaultDamageField();
  reverseField.right.swordOfRuin = true;
  const incomingSword = calculateDamage("gen9", right, left, "Dragon Claw", reverseField, true);
  assert.ok(incomingSword.max > incomingBaseline.max);
});

test("partner Tablets of Ruin reduces the opposing physical attacker only", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await vite.ssrLoadModule("/lib/damage-calculator.ts");
  const { emptyPokemon } = await vite.ssrLoadModule("/lib/team-builder.ts");
  const left = createDamageDraft(setFrom(emptyPokemon(1), "Garchomp", "Rough Skin", "Dragon Claw"));
  const right = createDamageDraft(setFrom(emptyPokemon(2), "Dragonite", "Inner Focus", "Dragon Claw"));
  const field = defaultDamageField();

  const baseline = calculateDamage("gen9", left, right, "Dragon Claw", field);
  field.right.tabletsOfRuin = true;
  const guarded = calculateDamage("gen9", left, right, "Dragon Claw", field);
  assert.ok(guarded.max < baseline.max);

  field.right.tabletsOfRuin = false;
  field.left.tabletsOfRuin = true;
  const ownTablets = calculateDamage("gen9", left, right, "Dragon Claw", field);
  assert.equal(ownTablets.max, baseline.max);
});
