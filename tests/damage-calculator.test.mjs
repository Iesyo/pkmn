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

test("inverse scouting keeps the real offensive Stat Point inside its compatible interval", async () => {
  const { calculateDamage, createDamageDraft, defaultDamageField } = await damageModule();
  const { analyzeScoutingEvidence } = await vite.ssrLoadModule("/lib/scouting-analysis.ts");
  const { emptyPokemon } = await teamBuilderModule();
  const ownDefender = configureSet(emptyPokemon(1), {
    species: "Abomasnow",
    ability: "Snow Warning",
    item: "Leftovers",
    nature: "Serious",
    evs: "12 HP / 10 Def",
    moves: ["Wood Hammer", "Ice Shard", "Earthquake", "Protect"],
  });
  const opponent = configureSet(emptyPokemon(2), {
    species: "Kleavor",
    ability: "Sharpness",
    item: "",
    nature: "Adamant",
    evs: "20 Atk",
    moves: ["Stone Axe", "X-Scissor", "Close Combat", "Protect"],
  });
  const roll = calculateDamage("champions", createDamageDraft(opponent), createDamageDraft(ownDefender), "Stone Axe", defaultDamageField());
  const observed = Number(((roll.minPercent + roll.maxPercent) / 2).toFixed(2));
  const result = analyzeScoutingEvidence(
    {
      playerName: "IesYo",
      opponentName: "Rival",
      pokemon: [{ species: "Kleavor", brought: true, moves: ["Stone Axe"], item: null, ability: "Sharpness", teraType: null }],
      observations: [{ turn: 1, attacker: "Kleavor", defender: "Abomasnow", move: "Stone Axe", direction: "incoming", damagePercent: observed, tolerance: 0.5, critical: false }],
    },
    { replayUrl: "https://replay.pokemonshowdown.com/test-1", format: "champions", ownTeam: [ownDefender] },
  );
  const attack = result.inferences.find((entry) => entry.species === "Kleavor" && entry.stat === "Atk");

  assert.ok(attack);
  assert.ok(attack.minimum <= 20);
  assert.ok(attack.maximum >= 20);
  assert.match(result.observedPaste, /Kleavor/);
  assert.match(result.observedPaste, /Stone Axe/);
});

test("uses the integrated calculator as the only Team Builder editor", async () => {
  const [builderSource, calculatorSource, statEditorSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/pokemon-stat-editor.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(builderSource, /DamageCalculatorView/);
  assert.match(builderSource, /data-team-calculator/);
  assert.doesNotMatch(builderSource, /proMode|Modo Pro|Volver al modo normal/);
  assert.doesNotMatch(builderSource, /DamageCalculatorDialog/);
  assert.doesNotMatch(calculatorSource, /components\/ui\/dialog/);
  assert.doesNotMatch(builderSource, /<PokemonStatEditor|<Combobox/);
  assert.match(calculatorSource, /<PokemonStatEditor/);
  assert.match(statEditorSource, /<Slider/);
  assert.match(statEditorSource, /calculateStat/);
  assert.match(calculatorSource, /onBoostChange=/);
  assert.match(statEditorSource, /BoostableStat/);
  assert.match(statEditorSource, />Boost<\/span>/);
  assert.match(statEditorSource, /6 - index/);
  assert.doesNotMatch(calculatorSource, /<Label>Boosts<\/Label>/);
});

test("keeps calculator drafts per Pokémon with one page scroll and inline damage", async () => {
  const [builderSource, calculatorSource, statEditorSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/pokemon-stat-editor.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(builderSource, /calculatorSessions/);
  assert.match(builderSource, /const calculatorSessionKey = `\$\{selected\.id\}:\$\{format\}:\$\{slotRevisions\[selectedSlot\]\}`/);
  assert.match(builderSource, /session=\{calculatorSessions\[calculatorSessionKey\]\}/);
  assert.match(builderSource, /onSessionChange=/);
  assert.match(builderSource, /\.\.\.nextSession\.left\.set/);
  assert.match(calculatorSource, /export type DamageCalculatorSession/);
  assert.doesNotMatch(calculatorSource, /Modo Pro · Calculadora de daño/);
  assert.doesNotMatch(calculatorSource, /Vista integrada/);
  assert.ok(calculatorSource.lastIndexOf("<CalculatorPokemonPanel") < calculatorSource.lastIndexOf("<OutcomeList"));
  assert.ok(calculatorSource.lastIndexOf("<OutcomeList") < calculatorSource.lastIndexOf("Motor oficial de Pokémon Showdown"));
  assert.match(builderSource, /data-team-calculator/);
  assert.match(builderSource, /data-team-calculator className="min-h-\[44rem\]"/);
  assert.doesNotMatch(builderSource, /data-team-calculator className="[^"]*overflow-y-auto/);
  assert.match(calculatorSource, /stableHeight/);
  assert.match(statEditorSource, /stableHeight && "min-h-\[23rem\]"/);
  assert.match(calculatorSource, /grid min-h-64 content-start gap-2/);
  assert.doesNotMatch(calculatorSource, /grid h-64 content-start gap-2 overflow-y-auto/);
  assert.match(calculatorSource, /function InlineDamageRange/);
  assert.match(calculatorSource, /grid-cols-\[minmax\(0,1fr\)_62px_88px\]/);
  assert.match(calculatorSource, /`\$\{outcome\.minPercent\}–\$\{outcome\.maxPercent\}%`/);
  assert.match(calculatorSource, /outcomes=\{leftOutcomes\}/);
  assert.match(calculatorSource, /outcomes=\{rightOutcomes\}/);
  assert.match(calculatorSource, /mechanics\.includes\("tera"\)/);
  assert.match(calculatorSource, /<Label>Tipo Tera<\/Label>/);
  assert.match(calculatorSource, /Forma Gigantamax/);
  assert.ok(calculatorSource.indexOf("<Label>Estado</Label>") < calculatorSource.indexOf("<Label>Nivel</Label>"));
  assert.ok(calculatorSource.indexOf("<Label>Nivel</Label>") < calculatorSource.indexOf("<Label>HP actual</Label>"));
});
