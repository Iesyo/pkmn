import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import ts from "typescript";
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

test("persistent War Room refreshes the Sparring corpus when it becomes active again", async () => {
  const sourcePath = fileURLToPath(new URL("../components/vgc/war-room-sparring.tsx", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /fetch\(`\/api\/war-room\?format=\$\{WAR_ROOM_FORMAT_ID\}`/);
  assert.match(source, /isWarRoomCorpusResponse\(payload\)/);
  assert.match(source, /root\.closest<HTMLElement>\('\[role="tabpanel"\]'\)/);
  assert.match(source, /attributeFilter: \["data-state", "hidden"\]/);
  assert.match(source, /window\.addEventListener\("focus", refreshWhenActive\)/);
  assert.match(source, /document\.addEventListener\("visibilitychange", onVisibilityChange\)/);
  assert.match(source, /corpusTeams=\{liveCorpusTeams\}/);
  assert.doesNotMatch(source, /refresh=1/);
  assert.doesNotMatch(source, /WarRoomAutoLab/);
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

test("local runtime provisions an unmodified pinned classic Showdown viewer", async () => {
  const sourcePath = fileURLToPath(new URL("../battle_lab/local_runtime.py", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /pokemon-showdown-client\.git/);
  assert.match(source, /e47b8be4103b5e027cd191a024e383be88f37bfe/);
  assert.match(source, /testclient-old\.html/);
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /DEFAULT_VIEWER_PORT = 8767/);
  assert.match(source, /AGPLv3/);

  const python = spawnSync(process.env.PYTHON ?? "python3", ["-m", "py_compile", sourcePath], { encoding: "utf8" });
  assert.equal(python.status, 0, python.stderr || python.stdout);
});

test("sparring UI embeds the real classic Showdown battle room and keeps staged legal controls", async () => {
  const sourcePath = fileURLToPath(new URL("../components/vgc/war-room-sparring/index.tsx", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /parseShowdownPaste/);
  assert.match(source, /Equipo rival/);
  assert.match(source, /Confirmar turno/);
  assert.match(source, /Pokémon izquierdo/);
  assert.match(source, /Pokémon derecho/);
  assert.match(source, /const LOCAL_SERVICE = "\/api\/battle-lab"/);
  assert.match(source, /testclient-old\.html\?~~127\.0\.0\.1:8766/);
  assert.match(source, /#\$\{session\.battle\.tag\}/);
  assert.match(source, /<iframe/);
  assert.match(source, /Pokémon Showdown · batalla real/);
  assert.match(source, /Telemetría Nana/);
  assert.match(source, /Full Amiibo N4/);
  assert.match(source, /Rueditas OFF/);
  assert.match(source, /Gate \{gateOk \? "OK" : "fallback"\}/);
  assert.match(source, /Mem \{exactSamples\}\/3/);
  assert.match(source, /Diagnóstico/);
  assert.match(source, /group-open:hidden/);
  assert.match(source, /Cobertura teacher/);
  assert.match(source, /Counter R²/);
  assert.match(source, /TeamMemory/);
  assert.match(source, /LIGHT sigue disponible como advisor\/fallback/);
  assert.match(source, /xl:grid-cols-\[minmax\(0,1fr\)_360px\]/);
  assert.match(source, /2xl:grid-cols-\[minmax\(0,1fr\)_400px\]/);
  const panelStart = source.indexOf("function TelemetryTrust");
  const panelEnd = source.indexOf("export function WarRoomSparring", panelStart);
  const panel = source.slice(panelStart, panelEnd);
  assert.ok(panelStart >= 0 && panelEnd > panelStart);
  assert.doesNotMatch(panel, /text-\[(?:7|8|9|10)px\]/);
  assert.match(panel, /rounded-\[22px\]/);
  assert.doesNotMatch(source, /Battle log/);
});

test("War Room Sparring TSX has no TypeScript/JSX parse diagnostics", async () => {
  const sourcePath = fileURLToPath(new URL("../components/vgc/war-room-sparring/index.tsx", import.meta.url));
  const source = await readFile(sourcePath, "utf8");
  const result = ts.transpileModule(source, {
    fileName: sourcePath,
    reportDiagnostics: true,
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  const diagnostics = (result.diagnostics ?? []).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  );
  assert.equal(
    diagnostics.length,
    0,
    diagnostics.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n")).join("\n"),
  );
});

test("sparring move target picker survives identical polling snapshots", async () => {
  const sourcePath = fileURLToPath(new URL("../components/vgc/war-room-sparring/index.tsx", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /const actionSignature = unique\.map\(actionKey\)\.sort\(\)\.join\("\|\|"\)/);
  assert.match(source, /useEffect\(\(\) => setPendingMove\(""\), \[actionSignature, effectiveMechanic\]\)/);
  assert.doesNotMatch(source, /setPendingMove\(""\), \[actions, effectiveMechanic\]/);
});

test("web app proxies only the Battle Lab loopback endpoints used by Sparring and Auto Lab", async () => {
  const sourcePath = fileURLToPath(new URL("../app/api/battle-lab/[...path]/route.ts", import.meta.url));
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /127\.0\.0\.1:8765/);
  assert.match(source, /ALLOWED_PATH/);
  assert.match(source, /health\|model-info\|sparring/);
  assert.match(source, /auto-lab/);
  assert.match(source, /team-preview\|choice/);
  assert.match(source, /export async function GET/);
  assert.match(source, /export async function POST/);
});
