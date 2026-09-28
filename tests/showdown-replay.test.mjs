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

test("imports a replay reconstructed from Pokémon Champions video", async () => {
  const { importShowdownReplay, normalizeShowdownReplayDocument } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const rawReplay = JSON.parse(
    await readFile(new URL("../backend/tests/data/champions_replay.json", import.meta.url), "utf8"),
  );
  const replay = normalizeShowdownReplayDocument(rawReplay);
  const match = importShowdownReplay(replay, {
    replayUrl: "",
    showdownNames: ["iesyo"],
    teamSpecies: p1Team,
    origin: "champions",
    replayArtifact: replay,
  });

  assert.equal(match.replayUrl, "");
  assert.equal(match.origin, "champions");
  assert.deepEqual(match.replayArtifact, replay);
  assert.equal(match.result, "win");
  assert.equal(match.playerName, "IesYo");
  assert.deepEqual(match.selected, ["Kleavor", "Pelipper", "Sinistcha", "Venusaur"]);
  assert.deepEqual(match.lead, ["Kleavor", "Pelipper"]);
  assert.deepEqual(match.opponentSelected, p2Team);
  assert.deepEqual(match.opponentPicks, ["Miraidon", "Amoonguss", "Incineroar", "Rillaboom"]);
  assert.deepEqual(match.movesUsed, {
    Kleavor: ["Stone Axe"],
    Pelipper: ["Tailwind"],
    Sinistcha: ["Matcha Gotcha"],
    Venusaur: ["Sludge Bomb"],
  });
  assert.equal(match.format, "gen9championsvgc2026regmc");
  assert.match(match.warnings.join(" "), /rating final/);
});

const megaOwnTeam = ["Indeedee-F", "Gardevoir", "Basculegion", "Rillaboom", "Blaziken", "Kingambit"];
const megaRivalTeam = ["Indeedee", "Armarouge", "Raichu", "Arcanine-Hisui", "Sneasler", "Absol"];
const megaReentryLog = [
  "|player|p1|Roku",
  "|player|p2|Rival",
  ...megaOwnTeam.map((species) => `|poke|p1|${species}, L50|`),
  ...megaRivalTeam.map((species) => `|poke|p2|${species}, L50|`),
  "|start",
  "|switch|p1a: Blaze|Blaziken, L50|156/156",
  "|switch|p1b: Support|Indeedee-F, L50|177/177",
  "|switch|p2a: Chu|Raichu, L50|100/100",
  "|switch|p2b: Armor|Armarouge, L50|100/100",
  "|turn|1",
  "|move|p1a: Blaze|Close Combat|p2a: Chu",
  "|detailschange|p1a: Blaze|Blaziken-Mega, L50",
  "|-mega|p1a: Blaze|Blazikenite",
  "|detailschange|p2a: Chu|Raichu-Mega-Y, L50",
  "|move|p1a: Blaze|Detect|p1a: Blaze",
  "|move|p2a: Chu|Protect|p2a: Chu",
  "|turn|2",
  "|switch|p1a: King|Kingambit, L50|177/177",
  "|switch|p2a: Support|Indeedee, L50|100/100",
  "|turn|3",
  "|switch|p1b: Blaze|Blaziken-Mega, L50|156/156",
  "|switch|p2b: Chu|Raichu-Mega-Y, L50|100/100",
  "|move|p1b: Blaze|Rock Tomb|p2a: Support",
  "|move|p2b: Chu|Zap Cannon|p1a: King",
  "|turn|4",
  "|switch|p1b: Fish|Basculegion, L50|219/219",
  "|switch|p2b: Dog|Arcanine-Hisui, L50|100/100",
  "|move|p1b: Fish|Wave Crash|p2b: Dog",
  "|move|p2b: Dog|Head Smash|p1b: Fish",
  "|win|Rival",
].join("\n");

for (const ownSide of ["p1", "p2"]) {
  test(`keeps four distinct picks and all moves after Mega reentry, viewed from ${ownSide}`, async () => {
    const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
    const picks = {
      p1: ["Blaziken", "Indeedee-F", "Kingambit", "Basculegion"],
      p2: ["Raichu", "Armarouge", "Indeedee", "Arcanine-Hisui"],
    };
    const moves = {
      p1: { Blaziken: ["Close Combat", "Detect", "Rock Tomb"], "Indeedee-F": [], Kingambit: [], Basculegion: ["Wave Crash"] },
      p2: { Raichu: ["Protect", "Zap Cannon"], Armarouge: [], Indeedee: [], "Arcanine-Hisui": ["Head Smash"] },
    };
    const otherSide = ownSide === "p1" ? "p2" : "p1";
    const document = { log: megaReentryLog, inputlog: "" };
    const match = importShowdownReplay(document, {
      replayUrl: "",
      showdownNames: [ownSide === "p1" ? "Roku" : "Rival"],
      teamSpecies: ownSide === "p1" ? megaOwnTeam : megaRivalTeam,
      origin: "champions",
      replayArtifact: document,
    });

    assert.deepEqual(match.selected, picks[ownSide]);
    assert.deepEqual(match.lead, picks[ownSide].slice(0, 2));
    assert.deepEqual(match.movesUsed, moves[ownSide]);
    assert.deepEqual(match.opponentPicks, picks[otherSide]);
    assert.deepEqual(match.opponentSelected, ownSide === "p1" ? megaRivalTeam : megaOwnTeam);
    assert.equal(match.replayArtifact.log, megaReentryLog);
  });
}

test("normalizes Mega X, Y and Z while preserving gender and regional forms", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  for (const [base, mega, otherForm] of [
    ["Raichu", "Raichu-Mega-X", "Raichu-Alola"],
    ["Raichu", "Raichu-Mega-Y", "Raichu-Alola"],
    ["Garchomp", "Garchomp-Mega-Z", "Arcanine-Hisui"],
    ["Meowstic-M", "Meowstic-M-Mega", "Meowstic-F"],
    ["Meganium", "Meganium-Mega", "Indeedee-F"],
  ]) {
    const team = [base, otherForm, "Kingambit", "Basculegion", "Rillaboom", "Gardevoir"];
    const match = importShowdownReplay({ log: [
      "|player|p1|Roku", "|player|p2|Rival",
      // Some public previews already name the Mega form.
      ...[mega, ...team.slice(1)].map((species) => `|poke|p1|${species}, L50|`),
      "|start",
      `|switch|p1a: Lead|${base}, L50|100/100`,
      `|switch|p1b: Partner|${otherForm}, L50|100/100`,
      "|turn|1",
      "|switch|p1a: King|Kingambit, L50|100/100",
      `|drag|p1b: Lead|${mega}, L50|100/100`,
      "|switch|p1a: Fish|Basculegion, L50|100/100",
      "|win|Roku",
    ].join("\n") }, { replayUrl: "", showdownNames: ["Roku"], teamSpecies: team });

    assert.deepEqual(match.selected, team.slice(0, 4), mega);
    assert.deepEqual(match.lead, team.slice(0, 2), mega);
    assert.deepEqual(Object.keys(match.movesUsed), team.slice(0, 4), mega);
  }
});

test("preserves explicit team choices and merges moves across Mega reentries", async () => {
  const { importShowdownReplay } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const match = importShowdownReplay({
    log: megaReentryLog,
    inputlog: ">p1 team 5163\n>p2 team 3214",
  }, { replayUrl: "", showdownNames: ["Roku"], teamSpecies: megaOwnTeam });

  assert.deepEqual(match.selected, ["Blaziken", "Indeedee-F", "Kingambit", "Basculegion"]);
  assert.deepEqual(match.opponentPicks, ["Raichu", "Armarouge", "Indeedee", "Arcanine-Hisui"]);
  assert.deepEqual(match.movesUsed.Blaziken, ["Close Combat", "Detect", "Rock Tomb"]);
  assert.deepEqual(match.movesUsed.Basculegion, ["Wave Crash"]);
});

test("surfaces the backend's review_capture issues on match.issues, structured", async () => {
  // Roku, revisión del tercer corte, 26 sep: aplanar a texto perdía
  // severidad/alternativas -esto confirma que `match.issues` conserva la
  // estructura completa, visible antes de confirmar la partida, sin que
  // nada la haya aplicado sola.
  const { importShowdownReplay, normalizeShowdownReplayDocument } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const rawReplay = JSON.parse(
    await readFile(new URL("../backend/tests/data/champions_replay.json", import.meta.url), "utf8"),
  );
  rawReplay.issues = [
    {
      severity: "warning",
      message: "p2a Pelipper entra con una lectura de HP sospechosa.",
      frame: 5549,
      alternatives: ["74/100"],
      proposed_change: "100/100",
    },
  ];
  const replay = normalizeShowdownReplayDocument(rawReplay);
  const match = importShowdownReplay(replay, {
    replayUrl: "",
    showdownNames: ["iesyo"],
    teamSpecies: p1Team,
    origin: "champions",
    replayArtifact: replay,
  });

  assert.deepEqual(replay.issues, rawReplay.issues);
  assert.deepEqual(match.issues, rawReplay.issues);
  assert.equal(match.warnings.some((warning) => warning.includes("lectura de HP sospechosa")), false);
});

test("hasBlockingIssues flags a blocking severity and nothing less", async () => {
  const { hasBlockingIssues, normalizeShowdownReplayDocument } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const rawReplay = JSON.parse(
    await readFile(new URL("../backend/tests/data/champions_replay.json", import.meta.url), "utf8"),
  );

  rawReplay.issues = [{ severity: "warning", message: "Aviso menor." }];
  assert.equal(hasBlockingIssues(normalizeShowdownReplayDocument(rawReplay)), false);

  rawReplay.issues = [{ severity: "blocking", message: "Selección incompleta." }];
  assert.equal(hasBlockingIssues(normalizeShowdownReplayDocument(rawReplay)), true);

  assert.equal(hasBlockingIssues(null), false);
  assert.equal(hasBlockingIssues(undefined), false);
});

test("validates reconstructed replay documents before importing them", async () => {
  const { normalizeShowdownReplayDocument } = await vite.ssrLoadModule("/lib/showdown-replay.ts");

  assert.equal(normalizeShowdownReplayDocument({ log: ["|start", "|win|IesYo"] }).log, "|start\n|win|IesYo");
  assert.throws(() => normalizeShowdownReplayDocument({}), /no contiene un registro/);
  assert.throws(() => normalizeShowdownReplayDocument("not-json"), /documento JSON válido/);
});

test("renders a reconstructed replay as safe Showdown-compatible HTML", async () => {
  const { renderShowdownReplayHtml } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const html = renderShowdownReplayHtml({
    format: "Champions <M-C>",
    p1: "IesYo",
    p2: "Rival",
    log: "|start\n|message|</script><script>alert(1)</script>\n|win|IesYo",
  }, "match-123");

  assert.match(html, /class="battle-log-data"/);
  assert.match(html, /replay-embed\.js/);
  assert.match(html, /upgrade-insecure-requests/);
  assert.match(html, /Champions &lt;M-C&gt;: IesYo vs\. Rival/);
  assert.ok(html.includes("|message|<\\/script><script>alert(1)<\\/script>"));
  assert.ok(!html.includes("|message|</script><script>alert(1)</script>"));
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
  assert.match(match.warnings.join(" "), /3\/4 picks propios/);
  assert.match(match.warnings.join(" "), /2\/4 picks rivales/);
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
