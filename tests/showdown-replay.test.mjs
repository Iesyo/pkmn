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

const p1Team = ["Kleavor", "Sinistcha", "Archaludon", "Pelipper", "Venusaur", "Luxray"];
const p2Team = ["Incineroar", "Rillaboom", "Urshifu-Rapid-Strike", "Farigiraf", "Miraidon", "Amoonguss"];
const teamPreview = [...p1Team.map((species) => `|poke|p1|${species}, L50|item`), ...p2Team.map((species) => `|poke|p2|${species}, L50|item`)].join("\n");

const log = `|player|p1|IesYo|1|1401
|player|p2|Opponent|2|1390
${teamPreview}
|teampreview
|start
|switch|p1a: Kleavor|Kleavor, L50|145/145
|switch|p1b: Pelipper|Pelipper, L50|135/135
|switch|p2a: Miraidon|Miraidon, L50|100/100
|switch|p2b: Amoonguss|Amoonguss, L50|100/100
|turn|1
|move|p1a: Kleavor|Stone Axe|p2a: Miraidon
|move|p1a: Kleavor|Stone Axe|p2a: Miraidon
|move|p1b: Pelipper|Tailwind|p1b: Pelipper
|switch|p1a: Sinistcha|Sinistcha, L50|100/100
|move|p1a: Sinistcha|Matcha Gotcha|p2a: Miraidon
|raw|IesYo's rating: 1401 &rarr; <strong>1428</strong><br />(+27 for winning)
|raw|Opponent's rating: 1390 &rarr; <strong>1363</strong><br />(-27 for losing)
|win|IesYo`;

test("imports a VGC replay without manual match data", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const match = importShowdownReplay(
    {
      log,
      inputlog: ">p1 team 1452\n>p2 team 5612",
      uploadtime: 1_788_000_000,
      format: "gen9championsvgc2026regma",
    },
    {
      replayUrl: "https://replay.pokemonshowdown.com/gen9championsvgc2026regma-123",
      showdownNames: ["iesyo"],
      teamSpecies: p1Team,
    },
  );

  assert.equal(match.result, "win");
  assert.equal(match.playerName, "IesYo");
  assert.equal(match.opponentName, "Opponent");
  assert.deepEqual(match.selected, ["Kleavor", "Pelipper", "Venusaur", "Sinistcha"]);
  assert.deepEqual(match.lead, ["Kleavor", "Pelipper"]);
  assert.deepEqual(match.opponentSelected, p2Team);
  assert.deepEqual(match.movesUsed, {
    Kleavor: ["Stone Axe"],
    Pelipper: ["Tailwind"],
    Venusaur: [],
    Sinistcha: ["Matcha Gotcha"],
  });
  assert.equal(match.rating, 1428);
  assert.equal(match.format, "gen9championsvgc2026regma");
  assert.deepEqual(match.warnings, []);
});

test("falls back to the saved roster and public switches when inputlog is absent", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const match = importShowdownReplay(
    { log, uploadtime: 1_788_000_000 },
    {
      replayUrl: "https://replay.pokemonshowdown.com/gen9vgc-456",
      showdownNames: [],
      teamSpecies: p1Team,
    },
  );

  assert.equal(match.playerName, "IesYo");
  assert.deepEqual(match.selected, ["Kleavor", "Pelipper", "Sinistcha"]);
  assert.deepEqual(match.lead, ["Kleavor", "Pelipper"]);
  assert.deepEqual(match.movesUsed, {
    Kleavor: ["Stone Axe"],
    Pelipper: ["Tailwind"],
    Sinistcha: ["Matcha Gotcha"],
  });
  assert.match(match.warnings.join(" "), /cuatro picks/);
});

test("imports the trainer on p2 and removes Showdown's hidden-form marker", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const match = importShowdownReplay(
    {
      format: "[Gen 9] VGC 2026 Reg I",
      log: [
        "|player|p1|Rival|avatar|1500",
        "|player|p2|MiCuenta|avatar|1428",
        "|poke|p1|Zamazenta-*, L50|",
        "|poke|p1|Kyogre, L50|",
        "|poke|p2|Kleavor, L50|",
        "|poke|p2|Pelipper, L50|",
        "|poke|p2|Venusaur, L50|",
        "|poke|p2|Sinistcha, L50|",
        "|poke|p2|Archaludon, L50|",
        "|poke|p2|Luxray, L50|",
        "|start|",
        "|switch|p2a: Kleavor|Kleavor, L50|100/100",
        "|switch|p2b: Pelipper|Pelipper, L50|100/100",
        "|turn|1",
        "|switch|p2a: Venusaur|Venusaur, L50|100/100",
        "|switch|p2b: Sinistcha|Sinistcha, L50|100/100",
        "|win|Rival",
        "|raw|MiCuenta's rating: 1428 &rarr; <strong>1411</strong>",
      ].join("\n"),
    },
    {
      replayUrl: "https://replay.pokemonshowdown.com/gen9vgc-789",
      showdownNames: ["micuenta"],
      teamSpecies: ["Kleavor", "Pelipper", "Venusaur", "Sinistcha", "Archaludon", "Luxray"],
    },
  );

  assert.equal(match.result, "loss");
  assert.equal(match.playerName, "MiCuenta");
  assert.equal(match.rating, 1411);
  assert.deepEqual(match.opponentSelected, ["Zamazenta", "Kyogre"]);
});

test("accepts only canonical public Showdown replay URLs", async () => {
  const { normalizeShowdownReplayUrl } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  assert.deepEqual(
    normalizeShowdownReplayUrl("https://replay.pokemonshowdown.com/gen9vgc-123.json?ignored=1"),
    {
      replayId: "gen9vgc-123",
      replayUrl: "https://replay.pokemonshowdown.com/gen9vgc-123",
      jsonUrl: "https://replay.pokemonshowdown.com/gen9vgc-123.json",
    },
  );
  assert.throws(() => normalizeShowdownReplayUrl("https://example.com/gen9vgc-123"), /debe pertenecer/);
});

test("extracts rival reveals and direct damage evidence for scouting", async () => {
  const { collectScoutingReplayEvidence } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const evidence = collectScoutingReplayEvidence(
    {
      log: `${log.replace("|win|IesYo", "")}
|turn|2
|-terastallize|p2a: Miraidon|Electric
|-ability|p2a: Miraidon|Hadron Engine
|-item|p2a: Miraidon|Choice Specs
|move|p1b: Pelipper|Hurricane|p2a: Miraidon
|-damage|p2a: Miraidon|65/100
|move|p2a: Miraidon|Electro Drift|p1b: Pelipper
|-damage|p1b: Pelipper|90/135
|-crit|p1b: Pelipper
|win|IesYo`,
    },
    { showdownNames: ["IesYo"], teamSpecies: p1Team },
  );

  assert.equal(evidence.opponentName, "Opponent");
  const miraidon = evidence.pokemon.find((pokemon) => pokemon.species === "Miraidon");
  assert.equal(miraidon.item, "Choice Specs");
  assert.equal(miraidon.ability, "Hadron Engine");
  assert.equal(miraidon.teraType, "Electric");
  assert.deepEqual(miraidon.moves, ["Electro Drift"]);
  assert.deepEqual(evidence.observations.slice(-2), [
    {
      turn: 2,
      attacker: "Pelipper",
      defender: "Miraidon",
      move: "Hurricane",
      direction: "outgoing",
      damagePercent: 35,
      tolerance: 1.25,
      critical: false,
    },
    {
      turn: 2,
      attacker: "Miraidon",
      defender: "Pelipper",
      move: "Electro Drift",
      direction: "incoming",
      damagePercent: 33.33,
      tolerance: 0.74,
      critical: true,
    },
  ]);
});
