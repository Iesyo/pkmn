import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
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

const completePaste = `Swampert @ Swampertite
Ability: Damp
Level: 50
EVs: 14 HP / 30 Atk / 22 Spe
Adamant Nature
- Wave Crash
- Ice Punch
- Earthquake
- Protect

Archaludon @ Leftovers
Ability: Stamina
Level: 50
EVs: 32 HP / 1 SpA / 29 SpD / 4 Spe
Modest Nature
- Electro Shot
- Dragon Pulse
- Flash Cannon
- Protect

Golisopod @ Golisopite
Ability: Emergency Exit
Level: 50
EVs: 16 HP / 32 Atk / 9 SpD / 9 Spe
Adamant Nature
- Iron Head
- Leech Life
- Close Combat
- Protect

Pelipper @ Focus Sash
Ability: Drizzle
Level: 50
EVs: 2 HP / 1 Def / 30 SpA / 1 SpD / 32 Spe
Timid Nature
- Tailwind
- Weather Ball
- Hurricane
- Protect

Grimmsnarl @ Light Clay
Ability: Prankster
Level: 50
EVs: 32 HP / 20 Def / 14 SpD
Calm Nature
- Parting Shot
- Spirit Break
- Reflect
- Light Screen

Sinistcha @ Sitrus Berry
Ability: Hospitality
Level: 50
EVs: 32 HP / 4 Def / 30 SpD
Relaxed Nature
- Matcha Gotcha
- Rage Powder
- Trick Room
- Protect`;

test("sparring accepts only explicit six-set battle-ready pastes", async () => {
  const { inspectBattleReadyPaste, isBattleReadyPaste } = await vite.ssrLoadModule("/lib/war-room-sparring.ts");

  const report = inspectBattleReadyPaste(completePaste);
  assert.equal(report.ready, true);
  assert.equal(report.blocks, 6);
  assert.deepEqual(report.issues, []);

  assert.equal(isBattleReadyPaste(completePaste.replace("EVs: 32 HP / 20 Def / 14 SpD\n", "")), false);
  assert.equal(isBattleReadyPaste(completePaste.replace("- Protect\n\nArchaludon", "\nArchaludon")), false);
  assert.equal(isBattleReadyPaste(completePaste.replace(" @ Focus Sash", "")), false);
});

test("sparring pool is limited to retrievable VGCPastes and Mis pastes sources", async () => {
  const { sparringCorpusCandidates } = await vite.ssrLoadModule("/lib/war-room-sparring.ts");
  const base = {
    playerName: "Player",
    tournament: "",
    rank: "",
    dateShared: "",
    pokemon: ["A", "B", "C", "D", "E", "F"],
    formatId: "champions-m-c",
    formatLabel: "Champions M-C",
    regulationWeight: 1,
    historical: false,
    setEvidenceEligible: true,
  };
  const teams = [
    { ...base, id: "vgc", source: "vgcpastes", savedPasteId: "", pokepasteUrl: "https://pokepast.es/aaaa" },
    { ...base, id: "mine", source: "scouting-library", savedPasteId: "paste-1", pokepasteUrl: "" },
    { ...base, id: "tournament", source: "tournament", savedPasteId: "", pokepasteUrl: "https://pokepast.es/bbbb" },
    { ...base, id: "missing", source: "vgcpastes", savedPasteId: "", pokepasteUrl: "" },
  ];

  assert.deepEqual(sparringCorpusCandidates(teams).map((team) => team.id), ["vgc", "mine"]);
});

test("local sparring service is syntactically valid and loopback-only", async () => {
  const sourcePath = fileURLToPath(new URL("../battle_lab/local_sparring_service.py", import.meta.url));
  const source = await readFile(sourcePath, "utf8");
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /strict_complete_team/);
  assert.match(source, /DoubleBattleOrder\.join_orders/);
  assert.match(source, /@app\.post\("\/sparring\/\{session_id\}\/choice"\)/);

  const python = spawnSync(process.env.PYTHON ?? "python3", ["-m", "py_compile", sourcePath], { encoding: "utf8" });
  assert.equal(python.status, 0, python.stderr || python.stdout);
});

test("sparring UI presents both teams and staged human battle controls", async () => {
  const sourcePath = fileURLToPath(new URL("../components/vgc/war-room-sparring.tsx", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /parseShowdownPaste/);
  assert.match(source, /Equipo rival/);
  assert.match(source, /Elige objetivo/);
  assert.match(source, /Confirmar turno/);
  assert.match(source, /Pokémon izquierdo/);
  assert.match(source, /Pokémon derecho/);
  assert.match(source, /const LOCAL_SERVICE = "\/api\/battle-lab"/);
  assert.doesNotMatch(source, />\{action\.label\}<\/button>/);
});

test("web app proxies only the Battle Lab loopback endpoints used by Sparring", async () => {
  const sourcePath = fileURLToPath(new URL("../app/api/battle-lab/[...path]/route.ts", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /127\.0\.0\.1:8765/);
  assert.match(source, /ALLOWED_PATH/);
  assert.match(source, /health\|model-info\|sparring/);
  assert.match(source, /team-preview\|choice/);
  assert.match(source, /export async function GET/);
  assert.match(source, /export async function POST/);
});
