import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const core = path.join(root, "battle_lab", "auto_lab.py");
const audit = path.join(root, "battle_lab", "auto_lab_audit.py");
const service = path.join(root, "battle_lab", "auto_lab_service.py");
const runtime = path.join(root, "battle_lab", "local_runtime.py");
const nanaRuntime = path.join(root, "battle_lab", "nana_stage2_nursery_lan_runtime.py");
const proxy = path.join(root, "app", "api", "battle-lab", "[...path]", "route.ts");
const variants = path.join(root, "lib", "war-room-auto-lab.ts");
const panel = path.join(root, "components", "vgc", "war-room-auto-lab.tsx");
const panelV2 = path.join(root, "components", "vgc", "war-room-auto-lab-v2.tsx");
const warRoom = path.join(root, "components", "vgc", "war-room.tsx");
const sparring = path.join(root, "components", "vgc", "war-room-sparring.tsx");

test("Auto Lab balances sides, samples only candidate Team Preview and audits the same replays", () => {
  const source = fs.readFileSync(core, "utf8");
  assert.match(source, /battles_per_opponent % 2/);
  assert.match(source, /battle_index % 2 == 0/);
  assert.match(source, /auto_lab_preview_sampling/);
  assert.match(source, /self\.deterministic = False/);
  assert.match(source, /self\.deterministic = previous/);
  assert.match(source, /_preview_seed\(opponent_id, index\)/);
  assert.match(source, /summary\["teamPreview"\]/);
  assert.match(source, /build_auto_lab_audit/);
  assert.match(source, /"schemaVersion": 2/);
  execFileSync("python", ["-m", "py_compile", core, audit, service, nanaRuntime], { cwd: root, encoding: "utf8" });
});

test("empirical audit remains runnable even when no alternate full set exists", () => {
  const source = fs.readFileSync(core, "utf8");
  const api = fs.readFileSync(service, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");

  assert.doesNotMatch(source, /Auto Lab requiere al menos una variante/);
  assert.match(api, /variants: list\[AutoLabTeamPayload\] = Field\(default_factory=list/);
  assert.doesNotMatch(ui, /!preparation\.variants\.length/);
  assert.match(ui, /La auditoría del Team actual sí puede ejecutarse/);
  assert.match(ui, /Esta ronda fue solo auditoría del baseline/);
});

test("Auto Lab replay audit exposes the requested empirical dimensions with cautious wording", () => {
  const source = fs.readFileSync(audit, "utf8");
  for (const key of [
    "goodMatchups",
    "badMatchups",
    "problematicOpponents",
    "leadPerformance",
    "selectionUsage",
    "setSignals",
    "moveSignals",
    "opponentPokemonPressure",
    "opponentCorePressure",
    "archetypePerformance",
    "recurringLossPatterns",
  ]) assert.match(source, new RegExp(`\\"${key}\\"`));
  assert.match(source, /correlaciones de uso, no evidencia causal/);
  const script = `
from battle_lab.auto_lab_audit import classify_archetypes
paste = "Pelipper @ Focus Sash\\nAbility: Drizzle\\n- Tailwind\\n\\nFarigiraf @ Sitrus Berry\\nAbility: Armor Tail\\n- Trick Room"
tags = classify_archetypes(paste)
assert "Rain" in tags and "Tailwind" in tags and "Trick Room" in tags, tags
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Auto Lab strips Nana wrappers instead of benchmarking the adaptive layer", () => {
  const source = fs.readFileSync(service, "utf8");
  assert.match(source, /candidate\.__name__ == "BattleLabPolicyPlayer"/);
  assert.match(source, /"adaptiveLayer": False/);
  assert.match(source, /_frozen_light_runtime\(self\.runtime\)/);
  assert.match(source, /Ya existe un Gauntlet Auto Lab activo/);
});

test("local and Nana runtimes expose Auto Lab before serving LAN traffic", () => {
  assert.match(fs.readFileSync(runtime, "utf8"), /install_auto_lab_service\(\)/);
  const nana = fs.readFileSync(nanaRuntime, "utf8");
  assert.match(nana, /from battle_lab\.auto_lab_service import install_auto_lab_service/);
  assert.match(nana, /install_auto_lab_service\(\)[\s\S]*lan\.install_direct_lan\(local_runtime\)/);
  assert.match(nana, /Auto Lab: rutas \/auto-lab activas/);

  const route = fs.readFileSync(proxy, "utf8");
  assert.match(route, /auto-lab/);
  assert.match(route, /sparring/);
  assert.match(route, /model-info/);
  assert.match(route, /upstream\.status === 404 && relativePath\.startsWith\("auto-lab"\)/);
  assert.match(route, /Auto Lab no está cargado en el runtime local/);
});

test("War Room generates complete contextual set packages instead of isolated moves", () => {
  const source = fs.readFileSync(variants, "utf8");
  assert.match(source, /MAX_AUTO_LAB_VARIANTS = 4/);
  assert.match(source, /applySetPackage/);
  assert.match(source, /suggestion\.proposal\.item/);
  assert.match(source, /suggestion\.proposal\.ability/);
  assert.match(source, /suggestion\.proposal\.nature/);
  assert.match(source, /suggestion\.proposal\.evs/);
  assert.match(source, /suggestion\.proposal\.moves\.forEach/);
  assert.doesNotMatch(source, /applySingleChange/);
  assert.match(source, /"tournament", "scouting-library", "vgcpastes"/);
  assert.match(source, /selectAutoLabOpponentCandidates/);
});

test("War Room surfaces the full empirical audit under Audit while Sparring stays manual", () => {
  assert.match(fs.readFileSync(panel, "utf8"), /war-room-auto-lab-v2/);
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(ui, /Auto Lab · auditoría empírica/);
  assert.match(ui, /Paquete de set completo/);
  assert.match(ui, /Matchups más favorables/);
  assert.match(ui, /Matchups más duros/);
  assert.match(ui, /Ranking de rivales problemáticos/);
  assert.match(ui, /Leads que mejor funcionan/);
  assert.match(ui, /Selección del roster/);
  assert.match(ui, /Pokémon casi nunca seleccionados/);
  assert.match(ui, /Pokémon rivales asociados a derrotas/);
  assert.match(ui, /Cores \/ leads rivales asociados a derrotas/);
  assert.match(ui, /Moves a revisar/);
  assert.match(ui, /Patrones recurrentes en derrotas/);
  assert.match(ui, /Rendimiento por arquetipo/);
  assert.match(ui, /selectAutoLabOpponentCandidates/);
  assert.match(ui, /battlesPerOpponent: 4/);
  assert.match(ui, /battlesPerOpponent: 6/);
  assert.match(ui, /battlesPerOpponent: 10/);

  const room = fs.readFileSync(warRoom, "utf8");
  assert.match(room, /import \{ WarRoomAutoLab \} from "@\/components\/vgc\/war-room-auto-lab"/);
  assert.match(room, /mode === "audit"[\s\S]*<AuditView result=\{audit\} \/>[\s\S]*<WarRoomAutoLab team=\{workingTeam\}/);
  const adapter = fs.readFileSync(sparring, "utf8");
  assert.doesNotMatch(adapter, /WarRoomAutoLab/);
  assert.match(adapter, /LocalWarRoomSparring/);
});
