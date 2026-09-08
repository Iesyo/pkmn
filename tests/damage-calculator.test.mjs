import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";

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

async function opponentMetaModule() {
  return vite.ssrLoadModule("/lib/opponent-meta-presets.ts");
}

function battleDataRow(category, rank, name, percentage, extra = {}) {
  return {
    category,
    rank,
    name,
    percentage: `${percentage}%`,
    percentage_value: percentage,
    ...extra,
  };
}

function pyroarBattleData() {
  return {
    pokemon: "Pyroar",
    rows: [
      battleDataRow("move", 1, "Heat Wave", 97.2),
      battleDataRow("move", 2, "Protect", 95.4),
      battleDataRow("move", 3, "Overheat", 64.8),
      battleDataRow("move", 4, "Solar Beam", 46.3),
      battleDataRow("move", 5, "Hyper Voice", 23.8),
      battleDataRow("move", 6, "Scorching Sands", 20),
      battleDataRow("move", 7, "Flamethrower", 13),
      battleDataRow("held_item", 1, "Pyroarite", 96.1),
      battleDataRow("held_item", 2, "Charcoal", 0.9),
      battleDataRow("ability", 1, "Unnerve", 82),
      battleDataRow("ability", 2, "Moxie", 9.2),
      battleDataRow("stat_alignment", 1, "Timid", 76.6),
      battleDataRow("stat_alignment", 2, "Modest", 21),
      battleDataRow("stat_points", 1, "", 64, {
        hp_points: 2,
        attack_points: 0,
        defense_points: 0,
        sp_atk_points: 32,
        sp_def_points: 0,
        speed_points: 32,
      }),
      battleDataRow("stat_points", 2, "", 5.2, {
        hp_points: 0,
        attack_points: 0,
        defense_points: 2,
        sp_atk_points: 32,
        sp_def_points: 0,
        speed_points: 32,
      }),
    ],
  };
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

test("builds at most three ranked rival presets from Champions marginal usage", async () => {
  const { buildOpponentMetaPresets, MAX_OPPONENT_META_PRESETS } = await opponentMetaModule();
  const presets = buildOpponentMetaPresets(pyroarBattleData());

  assert.equal(MAX_OPPONENT_META_PRESETS, 3);
  assert.equal(presets.length, 3);
  assert.deepEqual(presets[0].moves, ["Heat Wave", "Protect", "Overheat", "Solar Beam"]);
  assert.equal(presets[0].item, "Pyroarite");
  assert.equal(presets[0].ability, "Unnerve");
  assert.equal(presets[0].nature, "Timid");
  assert.equal(presets[0].evs, "2 HP / 32 SpA / 32 Spe");
  assert.deepEqual(presets.map((preset) => preset.id), ["meta-1", "meta-2", "meta-3"]);
  assert.equal(new Set(presets.map((preset) => JSON.stringify(preset))).size, 3);
});

test("maps the live Pyroar preset fields to the local Champions legality snapshot", async () => {
  const { buildOpponentMetaPresets } = await opponentMetaModule();
  const { getLegalAbilities, getLegalItems, getLegalMoves } = await vite.ssrLoadModule("/lib/showdown-data.ts");
  const compressed = await readFile(new URL("../public/data/showdown-dex.json.gz", import.meta.url));
  const snapshot = JSON.parse(gunzipSync(compressed).toString("utf8"));
  const first = buildOpponentMetaPresets(pyroarBattleData())[0];

  assert.ok(getLegalItems(snapshot, "champions").includes(first.item));
  assert.ok(getLegalAbilities(snapshot, "Pyroar", "champions").includes(first.ability));
  assert.ok(first.moves.every((move) => getLegalMoves(snapshot, "Pyroar", "champions").includes(move)));
});

test("does not invent weak or incomplete rival meta variants", async () => {
  const { buildOpponentMetaPresets } = await opponentMetaModule();
  const payload = pyroarBattleData();
  payload.rows = payload.rows.filter((row) => {
    if (row.category === "move") return row.rank <= 4 || row.rank === 7;
    return row.rank === 1;
  });
  const weakMove = payload.rows.find((row) => row.category === "move" && row.rank === 7);
  weakMove.percentage = "1%";
  weakMove.percentage_value = 1;

  assert.equal(buildOpponentMetaPresets(payload).length, 1);
  assert.deepEqual(buildOpponentMetaPresets({ rows: payload.rows.filter((row) => row.category !== "move" || row.rank <= 3) }), []);
  assert.deepEqual(buildOpponentMetaPresets({ rows: [{ category: "move", name: "Heat Wave", percentage_value: "oops" }] }), []);
});

test("serves rival meta through the fixed Battle Data upstream with attribution", async () => {
  const { GET } = await vite.ssrLoadModule("/app/api/opponent-meta/[species]/route.ts");
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (input) => {
    requestedUrl = String(input);
    return new Response(JSON.stringify(pyroarBattleData()), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    const response = await GET(
      new Request("http://localhost/api/opponent-meta/pyroar"),
      { params: Promise.resolve({ species: "Pyroar" }) },
    );
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(requestedUrl, "https://championsbattledata.com/api/battle/Doubles/pyroar");
    assert.equal(payload.methodology, "marginal-frequency-composite");
    assert.equal(payload.source.label, "Pokémon Champions Battle Data");
    assert.equal(payload.presets.length, 3);
    assert.match(response.headers.get("cache-control"), /s-maxage=21600/);

    globalThis.fetch = async () => new Response(null, { status: 404 });
    const unavailableResponse = await GET(
      new Request("http://localhost/api/opponent-meta/unavailabletest"),
      { params: Promise.resolve({ species: "unavailabletest" }) },
    );
    const unavailable = await unavailableResponse.json();
    assert.equal(unavailableResponse.status, 200);
    assert.deepEqual(unavailable.presets, []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uses Showdown stage rounding for displayed effective stats and Tailwind speed", async () => {
  const { emptySideConditions, getBoostedStatValue, getDisplayedEffectiveStat } = await damageModule();

  assert.equal(getBoostedStatValue(126, 1), 189);
  assert.equal(getBoostedStatValue(126, -1), 84);
  assert.equal(getDisplayedEffectiveStat("spe", 126, 1, false), 189);
  assert.equal(getDisplayedEffectiveStat("spe", 126, 1, true), 378);
  assert.equal(getDisplayedEffectiveStat("atk", 182, 2, true), 364);
  assert.equal(emptySideConditions().tailwind, false);
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
  const [builderSource, calculatorSource, statEditorSource, damageSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/pokemon-stat-editor.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/damage-calculator.ts", import.meta.url), "utf8"),
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
  assert.match(statEditorSource, />Efect\.<\/span>/);
  assert.match(statEditorSource, /getDisplayedEffectiveStat/);
  assert.match(statEditorSource, /tailwind\?: boolean/);
  assert.match(calculatorSource, /\["tailwind", "Tailwind"\]/);
  assert.match(calculatorSource, /tailwind=\{field\.left\.tailwind\}/);
  assert.match(calculatorSource, /tailwind=\{field\.right\.tailwind\}/);
  assert.match(damageSource, /isTailwind: side\.tailwind/);
  assert.match(statEditorSource, /6 - index/);
  assert.doesNotMatch(calculatorSource, /<Label>Boosts<\/Label>/);
});

test("keeps own drafts per Pokémon while sharing one fixed rival", async () => {
  const [builderSource, calculatorSource, statEditorSource] = await Promise.all([
    readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/pokemon-stat-editor.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(builderSource, /calculatorSessions/);
  assert.match(builderSource, /const calculatorSessionKey = `\$\{selected\.id\}:\$\{format\}:\$\{slotRevisions\[selectedSlot\]\}`/);
  assert.match(builderSource, /const \[sharedRival, setSharedRival\]/);
  assert.match(builderSource, /session=\{calculatorSessions\[calculatorSessionKey\]\}/);
  assert.match(builderSource, /rivalSession=\{sharedRival\}/);
  assert.match(builderSource, /right: nextSession\.right/);
  assert.match(builderSource, /opponentSetSelectionId: nextSession\.opponentSetSelectionId/);
  assert.match(builderSource, /onSessionChange=/);
  assert.match(builderSource, /\.\.\.nextSession\.left\.set/);
  assert.match(calculatorSource, /export type DamageCalculatorSession/);
  assert.match(calculatorSource, /export type DamageCalculatorRivalSession/);
  assert.match(calculatorSource, /opponentSetSelectionId: string \| null/);
  assert.match(calculatorSource, /const session = rivalSession \? \{ \.\.\.baseSession, \.\.\.rivalSession \} : baseSession/);
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

test("offers three meta presets before saved rival sets in Champions", async () => {
  const [calculatorSource, selectorSource, presetSource, routeSource] = await Promise.all([
    readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/vgc/opponent-meta-set-select.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/opponent-meta-presets.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/opponent-meta/[species]/route.ts", import.meta.url), "utf8"),
  ]);

  assert.match(calculatorSource, /side === "right" && format === "champions"/);
  assert.match(calculatorSource, /<OpponentMetaSetSelect/);
  assert.match(calculatorSource, /<PokemonLibraryVersionSelect/);
  assert.match(calculatorSource, /manualMetaEditCountRef/);
  assert.match(calculatorSource, /autoLoadMetaOnMount={autoLoadOpponentMetaOnMount}/);
  assert.match(calculatorSource, /setSelectionId \? "library" : "manual"/);
  assert.match(calculatorSource, /`meta:\$\{preset\.id\}`/);
  assert.match(calculatorSource, /selectedSetId=\{selectedOpponentSetId\}/);
  assert.match(selectorSource, /onLoadMetaRef\.current\(first, true\)/);
  assert.match(selectorSource, /loadPokemonLibraryEntries\(format\)/);
  assert.match(selectorSource, /right\.version - left\.version/);
  assert.ok(selectorSource.indexOf("Meta estimado") < selectorSource.indexOf("Mis sets guardados"));
  assert.ok(selectorSource.indexOf("presets.map") < selectorSource.indexOf("versions.map"));
  assert.match(selectorSource, /onLoadLibrary\(version\.set, value\)/);
  assert.doesNotMatch(selectorSource, /Estimación estadística/);
  assert.match(calculatorSource, /Sets rivales: estimación estadística de/);
  assert.match(calculatorSource, /Pokémon Champions Battle Data/);
  assert.match(presetSource, /MAX_OPPONENT_META_PRESETS = 3/);
  assert.match(presetSource, /marginal-frequency-composite/);
  assert.match(routeSource, /https:\/\/championsbattledata\.com\/api\/battle\/Doubles/);
  assert.match(routeSource, /FRESH_CACHE_MS/);
  assert.doesNotMatch(`${calculatorSource}\n${selectorSource}\n${presetSource}\n${routeSource}`, /pikalytics/i);
});
