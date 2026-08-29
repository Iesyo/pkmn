import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
    moves: values.moves.map((name) => ({
      name,
      type: null,
      damaging: true,
      usage: 0,
    })),
  };
}

test("maps Pokémon Champions to the official generation zero engine", async () => {
  const { generationForFormat } = await damageModule();

  assert.equal(generationForFormat("champions"), 0);
  assert.equal(generationForFormat("gen9"), 9);
  assert.equal(generationForFormat("gen6"), 6);
});

test("keeps calculator edits isolated from the Team Builder set", async () => {
  const { createDamageDraft } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const source = configureSet(emptyPokemon(1), {
    species: "Kleavor",
    ability: "Sharpness",
    item: "Choice Scarf",
    nature: "Adamant",
    evs: "31 Atk / 11 SpD / 20 Spe",
    moves: ["Stone Axe", "X-Scissor", "Close Combat", "U-turn"],
  });

  const draft = createDamageDraft(source);
  draft.set.moves[0].name = "Protect";
  draft.set.mechanics.megaEvolution = true;
  draft.set.performance.games = 99;

  assert.equal(source.moves[0].name, "Stone Axe");
  assert.equal(source.mechanics.megaEvolution, false);
  assert.equal(source.performance.games, 0);
});

test("calculates a Champions damage range with Stat Points", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();

  const kleavor = configureSet(emptyPokemon(1), {
    species: "Kleavor",
    ability: "Sharpness",
    item: "Choice Scarf",
    nature: "Adamant",
    evs: "31 Atk / 11 SpD / 20 Spe",
    moves: ["Stone Axe", "X-Scissor", "Close Combat", "U-turn"],
  });
  const abomasnow = configureSet(emptyPokemon(2), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "Leftovers",
    nature: "Serious",
    evs: "",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Swords Dance"],
  });

  const outcome = calculateDamage(
    "champions",
    createDamageDraft(kleavor),
    createDamageDraft(abomasnow),
    "Stone Axe",
    defaultDamageField(),
  );

  assert.equal(outcome.error, undefined);
  assert.ok(outcome.min > 0);
  assert.ok(outcome.max >= outcome.min);
  assert.ok(outcome.minPercent > 0);
  assert.match(outcome.description, /Kleavor Stone Axe vs\..*Abomasnow/);
  assert.ok(outcome.rolls.length > 1);
});

test("maps base Aegislash to its attacking and defending Champions stances", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const aegislash = configureSet(emptyPokemon(1), {
    species: "Aegislash",
    ability: "Stance Change",
    item: "",
    nature: "Serious",
    evs: "",
    moves: ["Brick Break", "Sacred Sword", "Shadow Sneak", "King's Shield"],
  });
  const aerodactyl = configureSet(emptyPokemon(2), {
    species: "Aerodactyl",
    ability: "Rock Head",
    item: "",
    nature: "Serious",
    evs: "",
    moves: ["Rock Slide", "Earthquake", "Crunch", "Protect"],
  });

  const outgoing = calculateDamage("champions", createDamageDraft(aegislash), createDamageDraft(aerodactyl), "Brick Break", defaultDamageField());
  const incoming = calculateDamage("champions", createDamageDraft(aerodactyl), createDamageDraft(aegislash), "Crunch", defaultDamageField(), true);

  assert.equal(outgoing.error, undefined);
  assert.equal(incoming.error, undefined);
  assert.ok(outgoing.max > 0);
  assert.ok(incoming.max > 0);
});

test("reverses side conditions when calculating incoming damage", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { emptyPokemon } = await teamBuilderModule();
  const attacker = configureSet(emptyPokemon(1), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "",
    nature: "Adamant",
    evs: "32 Atk",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Swords Dance"],
  });
  const defender = configureSet(emptyPokemon(2), {
    species: "Kleavor",
    ability: "Sharpness",
    item: "",
    nature: "Serious",
    evs: "32 HP / 32 Def",
    moves: ["Stone Axe", "X-Scissor", "Close Combat", "U-turn"],
  });
  const field = defaultDamageField();
  const unguarded = calculateDamage("champions", createDamageDraft(attacker), createDamageDraft(defender), "Wood Hammer", field, true);
  field.left.reflect = true;
  const guarded = calculateDamage("champions", createDamageDraft(attacker), createDamageDraft(defender), "Wood Hammer", field, true);

  assert.equal(unguarded.error, undefined);
  assert.equal(guarded.error, undefined);
  assert.ok(guarded.max < unguarded.max);
});

test("renders the calculator as an inline Pro mode instead of a floating dialog", async () => {
  const [builderSource, calculatorSource, statEditorSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/pokemon-stat-editor.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(builderSource, /proMode/);
  assert.match(builderSource, /DamageCalculatorView/);
  assert.match(builderSource, /Volver al modo normal/);
  assert.doesNotMatch(builderSource, /DamageCalculatorDialog/);
  assert.doesNotMatch(calculatorSource, /components\/ui\/dialog/);
  assert.match(builderSource, /<PokemonStatEditor/);
  assert.match(calculatorSource, /<PokemonStatEditor/);
  assert.match(statEditorSource, /<Slider/);
  assert.match(statEditorSource, /calculateStat/);
  assert.match(calculatorSource, /onBoostChange=/);
  assert.match(statEditorSource, /BoostableStat/);
  assert.match(statEditorSource, />Boost<\/span>/);
  assert.match(statEditorSource, /6 - index/);
  assert.doesNotMatch(calculatorSource, /<Label>Boosts<\/Label>/);
});

test("keeps Pro drafts per Pokémon and prevents result-driven layout shifts", async () => {
  const [builderSource, calculatorSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(builderSource, /proSessions/);
  assert.match(builderSource, /const proSessionKey = `\$\{selected\.id\}:\$\{format\}`/);
  assert.match(builderSource, /session=\{proSessions\[proSessionKey\]\}/);
  assert.match(builderSource, /onSessionChange=/);
  assert.match(builderSource, /\.\.\.nextSession\.left\.set/);
  assert.match(builderSource, /disabled=\{!dex\}/);
  assert.match(calculatorSource, /export type DamageCalculatorSession/);
  assert.doesNotMatch(calculatorSource, /Modo Pro · Calculadora de daño/);
  assert.doesNotMatch(calculatorSource, /Vista integrada/);
  assert.ok(calculatorSource.lastIndexOf("<CalculatorPokemonPanel") < calculatorSource.lastIndexOf("<OutcomeList"));
  assert.ok(calculatorSource.lastIndexOf("<OutcomeList") < calculatorSource.lastIndexOf("Motor oficial de Pokémon Showdown"));
  assert.match(calculatorSource, /grid min-h-64/);
  assert.ok(calculatorSource.indexOf("<Label>Estado</Label>") < calculatorSource.indexOf("<Label>Nivel</Label>"));
  assert.ok(calculatorSource.indexOf("<Label>Nivel</Label>") < calculatorSource.indexOf("<Label>HP actual</Label>"));
});
