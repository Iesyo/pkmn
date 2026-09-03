import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const calculatorUrl = new URL("../components/vgc/damage-calculator.tsx", import.meta.url);
const cardUrl = new URL("../components/vgc/pokemon-card.tsx", import.meta.url);
const hintUrl = new URL("../components/vgc/showdown-tech-hint.tsx", import.meta.url);

test("Team Builder surfaces rich Showdown move and ability metadata", async () => {
  const [calculator, card, hint] = await Promise.all([
    readFile(calculatorUrl, "utf8"),
    readFile(cardUrl, "utf8"),
    readFile(hintUrl, "utf8"),
  ]);

  assert.match(calculator, /getAbilityData/);
  assert.match(calculator, /getMoveData/);
  assert.match(calculator, /formatMoveAccuracy/);
  assert.match(calculator, /abilityInfo\?\.shortDesc/);
  assert.match(calculator, /technical\.basePower/);
  assert.match(calculator, /technical\.accuracy/);
  assert.match(calculator, /technical\.pp/);
  assert.match(calculator, /technical\.priority/);
  assert.match(calculator, /Datos Showdown \+ resultados en vivo/);

  assert.match(card, /ShowdownTechHint kind="ability"/);
  assert.match(card, /ShowdownTechHint kind="move"/);
  assert.match(hint, /title=\{hint \|\| undefined\}/);
});
