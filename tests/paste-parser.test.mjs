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
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const paste = `Kleavor @ Choice Scarf
Ability: Sharpness
Tera Type: Water
Adamant Nature
- Stone Axe

Miraidon @ Choice Specs
Ability: Hadron Engine
Modest Nature
- Electro Drift

Incineroar @ Safety Goggles
Ability: Intimidate
Careful Nature
- Fake Out

Ogerpon-Wellspring @ Wellspring Mask
Ability: Water Absorb
Jolly Nature
- Ivy Cudgel

Farigiraf @ Throat Spray
Ability: Armor Tail
Modest Nature
- Trick Room

Urshifu-Rapid-Strike @ Mystic Water
Ability: Unseen Fist
Adamant Nature
- Surging Strikes`;

test("parses an exact six-Pokémon Showdown paste", async () => {
  const { parseShowdownPaste } = await vite.ssrLoadModule("/lib/paste.ts");
  const team = parseShowdownPaste(paste);

  assert.equal(team.length, 6);
  assert.equal(team[0].species, "Kleavor");
  assert.equal(team[0].moves[0].type, "Rock");
  assert.deepEqual(team[3].types, ["Grass", "Water"]);
});

test("rejects a partial paste", async () => {
  const { parseShowdownPaste } = await vite.ssrLoadModule("/lib/paste.ts");

  assert.throws(() => parseShowdownPaste(paste.split("\n\n", 1)[0]), /exactamente 6/);
});

test("computes base and Tera defensive views separately", async () => {
  const { parseShowdownPaste } = await vite.ssrLoadModule("/lib/paste.ts");
  const { analyzeTypes } = await vite.ssrLoadModule("/lib/team-stats.ts");
  const team = parseShowdownPaste(paste);

  const base = analyzeTypes(team, false);
  const tera = analyzeTypes(team, true);
  assert.notDeepEqual(base.defense, tera.defense);
  assert.ok(base.coverage.some((entry) => entry.count > 0));
});

test("formats major and minor team versions without decimal ambiguity", async () => {
  const { formatVersion } = await vite.ssrLoadModule("/lib/team-builder.ts");
  assert.equal(formatVersion({ version: 1, minorVersion: 0 }), "1");
  assert.equal(formatVersion({ version: 1, minorVersion: 1 }), "1.01");
  assert.equal(formatVersion({ version: 1, minorVersion: 10 }), "1.10");
});

test("computes matchup and attendance stats from opposing Pokémon", async () => {
  const { calculateOpponentPokemonStats } = await vite.ssrLoadModule("/lib/team-stats.ts");
  const matches = [
    { result: "win", opponentSelected: ["Rillaboom", "Incineroar"] },
    { result: "loss", opponentSelected: ["Rillaboom", "Rillaboom", "Calyrex-Ice"] },
  ];

  const stats = calculateOpponentPokemonStats(matches);
  const rillaboom = stats.find((entry) => entry.species === "Rillaboom");
  const incineroar = stats.find((entry) => entry.species === "Incineroar");

  assert.deepEqual(rillaboom, {
    species: "Rillaboom",
    games: 2,
    wins: 1,
    winRate: 50,
    attendanceRate: 100,
  });
  assert.equal(incineroar.attendanceRate, 50);
});

test("computes move usage from replay telemetry instead of dividing 100 by four", async () => {
  const { parseShowdownPaste } = await vite.ssrLoadModule("/lib/paste.ts");
  const { decoratePokemonPerformance } = await vite.ssrLoadModule("/lib/team-stats.ts");
  const parsed = parseShowdownPaste(paste);
  const team = parsed.map((set) => set.species === "Kleavor"
    ? {
        ...set,
        moves: [
          set.moves[0],
          { name: "X-Scissor", type: "Bug", damaging: true, usage: 0 },
        ],
      }
    : set);
  const baseMatch = {
    result: "win",
    opponentSelected: [],
    selected: ["Kleavor", "Miraidon", "Incineroar", "Farigiraf"],
    lead: ["Kleavor", "Miraidon"],
  };
  const matches = [
    { ...baseMatch, movesUsed: { Kleavor: ["Stone Axe"] } },
    { ...baseMatch, result: "loss", movesUsed: { Kleavor: ["Stone Axe", "X-Scissor"] } },
    { ...baseMatch, movesUsed: { Kleavor: [] } },
    { ...baseMatch, movesUsed: null },
  ];

  const decorated = decoratePokemonPerformance(team, matches);
  const kleavor = decorated.find((set) => set.species === "Kleavor");
  const miraidon = decorated.find((set) => set.species === "Miraidon");

  assert.equal(kleavor.moves.find((move) => move.name === "Stone Axe").usage, 66.7);
  assert.equal(kleavor.moves.find((move) => move.name === "X-Scissor").usage, 33.3);
  assert.equal(kleavor.performance.games, 4);
  assert.equal(miraidon.moves[0].usage, null);
});
