import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";

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
    moves: values.moves.map((name) => ({ name, type: null, damaging: true, usage: 0 })),
  };
}

test("Mega Stones grant access without auto-activating Mega Evolution", async () => {
  const { createDamageDraft, getMegaForm, resolveBattleSpeciesName } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const venusaur = configureSet(emptyPokemon(1), {
    species: "Venusaur",
    ability: "Chlorophyll",
    item: "Venusaurite",
    nature: "Modest",
    evs: "32 HP / 30 SpA / 2 Def / 2 Spe",
    moves: ["Sludge Bomb", "Giga Drain", "Earth Power", "Protect"],
  });

  const draft = createDamageDraft(venusaur);
  assert.equal(getMegaForm(venusaur), "Venusaur-Mega");
  assert.equal(draft.megaActive, false);
  assert.equal(resolveBattleSpeciesName("champions", draft), "Venusaur");

  draft.megaActive = true;
  assert.equal(resolveBattleSpeciesName("champions", draft), "Venusaur-Mega");
  assert.equal(draft.set.species, "Venusaur");
  assert.equal(draft.set.mechanics.megaEvolution, false);
});

test("Mega Stone mapping distinguishes split Mega forms", async () => {
  const { getMegaForm } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const charizard = configureSet(emptyPokemon(1), {
    species: "Charizard",
    ability: "Blaze",
    item: "Charizardite X",
    nature: "Jolly",
    evs: "",
    moves: ["Flare Blitz", "Dragon Claw", "Earthquake", "Protect"],
  });

  assert.equal(getMegaForm(charizard), "Charizard-Mega-X");
  assert.equal(getMegaForm({ ...charizard, item: "Charizardite Y" }), "Charizard-Mega-Y");
  assert.equal(getMegaForm({ ...charizard, item: "Venusaurite" }), null);
});

test("vendored engine exposes representative Legends ZA Mega mappings", async () => {
  const { getMegaForm } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();

  const setFor = (species, item) => configureSet(emptyPokemon(1), {
    species,
    ability: "",
    item,
    nature: "Serious",
    evs: "",
    moves: ["Protect", "Protect", "Protect", "Protect"],
  });

  assert.equal(getMegaForm(setFor("Dragonite", "Dragoninite")), "Dragonite-Mega");
  assert.equal(getMegaForm(setFor("Greninja", "Greninjite")), "Greninja-Mega");
  assert.equal(getMegaForm(setFor("Malamar", "Malamarite")), "Malamar-Mega");
  assert.equal(getMegaForm(setFor("Raichu", "Raichunite X")), "Raichu-Mega-X");
  assert.equal(getMegaForm(setFor("Raichu", "Raichunite Y")), "Raichu-Mega-Y");
  assert.equal(getMegaForm(setFor("Baxcalibur", "Baxcalibrite")), "Baxcalibur-Mega");
  assert.equal(getMegaForm(setFor("Zeraora", "Zeraorite")), "Zeraora-Mega");
  assert.equal(getMegaForm(setFor("Lucario", "Lucarionite Z")), "Lucario-Mega-Z");
  assert.equal(getMegaForm(setFor("Garchomp", "Garchompite Z")), "Garchomp-Mega-Z");
});

test("Rayquaza is the no-stone exception and requires Dragon Ascent", async () => {
  const { createDamageDraft, getMegaForm, resolveBattleSpeciesName } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const rayquaza = configureSet(emptyPokemon(1), {
    species: "Rayquaza",
    ability: "Air Lock",
    item: "Life Orb",
    nature: "Jolly",
    evs: "32 Atk / 32 Spe",
    moves: ["Dragon Ascent", "Extreme Speed", "Earthquake", "Protect"],
  });

  assert.equal(getMegaForm(rayquaza), "Rayquaza-Mega");
  assert.equal(getMegaForm({ ...rayquaza, moves: rayquaza.moves.map((move, index) => index === 0 ? { ...move, name: "Dragon Claw" } : move) }), null);

  const draft = createDamageDraft(rayquaza);
  assert.equal(resolveBattleSpeciesName("gen7", draft), "Rayquaza");
  draft.megaActive = true;
  assert.equal(resolveBattleSpeciesName("gen7", draft), "Rayquaza-Mega");
});

test("Mega toggle changes the actual calculated form instead of only the UI", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const venusaur = configureSet(emptyPokemon(1), {
    species: "Venusaur",
    ability: "Chlorophyll",
    item: "Venusaurite",
    nature: "Modest",
    evs: "30 SpA",
    moves: ["Sludge Bomb", "Giga Drain", "Earth Power", "Protect"],
  });
  const abomasnow = configureSet(emptyPokemon(2), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "Leftovers",
    nature: "Serious",
    evs: "",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Protect"],
  });

  const baseDraft = createDamageDraft(venusaur);
  const megaDraft = createDamageDraft(venusaur);
  megaDraft.megaActive = true;
  const defender = createDamageDraft(abomasnow);
  const base = calculateDamage("champions", baseDraft, defender, "Sludge Bomb", defaultDamageField());
  const mega = calculateDamage("champions", megaDraft, defender, "Sludge Bomb", defaultDamageField());

  assert.equal(base.error, undefined);
  assert.equal(mega.error, undefined);
  assert.ok(mega.max > base.max);
  assert.doesNotMatch(base.description, /Venusaur-Mega|Mega Venusaur/);
  assert.match(mega.description, /Venusaur-Mega|Mega Venusaur/);
});

test("calculator renders the Mega switch in the stat footer and swaps battle presentation", async () => {
  const source = await readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8");

  assert.match(source, /const megaForm = mechanics\.includes\("mega"\) \? getMegaForm\(set\) : null/);
  assert.match(source, /battleSpeciesName/);
  assert.match(source, /getSpriteUrl\(battleSpeciesName\)/);
  assert.match(source, /baseStats=\{battleSpecies\?\.baseStats \?\? null\}/);
  assert.match(source, /disabled=\{megaActive\}/);
  assert.match(source, /absolute bottom-3 right-3/);
  assert.match(source, /<span>Mega<\/span>/);
  assert.match(source, /checked=\{megaActive\}/);
  assert.match(source, /megaActive: checked/);
});
