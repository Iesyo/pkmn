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
  assert.match(source, /if len\(opponents\) > 100/);
  execFileSync("python", ["-m", "py_compile", core, audit, service, nanaRuntime], { cwd: root, encoding: "utf8" });
});

test("Audit runs baseline-only while Optimize owns contextual set proposals", () => {
  const api = fs.readFileSync(service, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");
  const room = fs.readFileSync(warRoom, "utf8");

  assert.match(api, /variants: list\[AutoLabTeamPayload\] = Field\(default_factory=list/);
  assert.match(api, /MAX_OPPONENTS = 100/);
  assert.doesNotMatch(ui, /buildAutoLabVariants|optimizeTeam|Paquete de set completo|Copiar set candidato/);
  assert.match(ui, /Los paquetes de set se quedaron en/);
  assert.match(ui, /Optimizar o construir/);
  assert.doesNotMatch(ui, /variants:/);
  assert.match(room, /function SetSuggestionCard/);
  assert.match(room, /Set search/);
  assert.match(room, /result\.sets\.map\(\(suggestion\) => <SetSuggestionCard/);
});

test("Auto Lab replay audit exposes the requested empirical dimensions with cautious wording", () => {
  const source = fs.readFileSync(audit, "utf8");
  for (const key of [
    "goodMatchups",
    "badMatchups",
    "problematicOpponents",
    "leadPerformance",
    "selectionUsage",
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

test("Auto Lab canonicalizes Showdown ids and transient Mega forms to exactly six roster identities", () => {
  const source = fs.readFileSync(audit, "utf8");
  assert.match(source, /_canonical_candidate_species/);
  assert.match(source, /DYNAMIC_FORM_SUFFIXES/);
  assert.match(source, /identity not in canonical_preview/);
  const script = `
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.auto_lab_audit import _canonical_candidate_species, build_auto_lab_audit

roster = ["Farigiraf", "Garchomp", "Incineroar", "Mawile", "Milotic", "Rillaboom"]
assert _canonical_candidate_species("farigiraf", roster) == "Farigiraf"
assert _canonical_candidate_species("mawilemega", roster) == "Mawile"
assert _canonical_candidate_species("Mawile-Mega", roster) == "Mawile"

summaries = [
    {
        "battleTag": "missing-1",
        "pairing": {"alphaTeamId": "baseline", "betaTeamId": "opp-1"},
        "winnerSide": "alpha",
        "teamPreview": {"alpha": ["farigiraf", "garchomp", "incineroar", "mawilemega"]},
    },
    {
        "battleTag": "missing-2",
        "pairing": {"alphaTeamId": "baseline", "betaTeamId": "opp-2"},
        "winnerSide": "alpha",
        "teamPreview": {"alpha": ["milotic", "rillaboom", "mawile", "farigiraf"]},
    },
]
with TemporaryDirectory() as tmp:
    result = build_auto_lab_audit(
        candidate_id="baseline",
        candidate_roster=roster,
        summaries=summaries,
        candidate_report={"scorePercent": 100.0, "byOpponent": {}},
        opponents={},
        replay_root=Path(tmp),
    )
rows = result["selectionUsage"]
assert [row["pokemon"] for row in rows] == roster, rows
assert len(rows) == 6, rows
counts = {row["pokemon"]: row["selectedGames"] for row in rows}
assert counts == {"Farigiraf": 2, "Garchomp": 1, "Incineroar": 1, "Mawile": 2, "Milotic": 1, "Rillaboom": 1}, counts
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

test("War Room keeps full-set package generation available for Optimize", () => {
  const source = fs.readFileSync(variants, "utf8");
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

test("Deep audit prioritizes the newest current VGCPastes while quick and normal keep broad coverage", () => {
  const source = fs.readFileSync(variants, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");

  assert.match(source, /export function selectAutoLabRecentVgcPastesCandidates/);
  assert.match(source, /team\.source === "vgcpastes"/);
  assert.match(source, /Date\.parse\(value\.trim\(\)\)/);
  assert.match(source, /right\.sharedAt - left\.sharedAt/);
  assert.match(ui, /opponents: 18, battlesPerOpponent: 12/);
  assert.match(ui, /opponents: 24, battlesPerOpponent: 20/);
  assert.match(ui, /opponents: 100, battlesPerOpponent: 10/);
  assert.match(ui, /preset === "deep"[\s\S]*selectAutoLabRecentVgcPastesCandidates/);
  assert.match(ui, /Profundo necesita \$\{spec\.opponents\} VGCPastes M-C recientes validados por Showdown/);
  assert.match(ui, /Solo los VGCPastes M-C más recientes por Date Shared/);
});

test("Auto Lab preflight skips invalid source pastes before starting the gauntlet", () => {
  const api = fs.readFileSync(service, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(api, /class ValidateAutoLabTeamsRequest/);
  assert.match(api, /validate_auto_lab_payloads/);
  assert.match(api, /@app\.post\("\/auto-lab\/validate"\)/);
  assert.match(ui, /validateBattleReadyBatch/);
  assert.match(ui, /\/api\/battle-lab\/auto-lab\/validate/);
  assert.match(ui, /verdict\?\.valid/);
  assert.match(ui, /rejected \+= 1/);
  assert.match(ui, /loaded\.length < spec\.opponents/);
  assert.match(ui, /Preflight Showdown M-C/);

  const script = `
import asyncio
from pathlib import Path
from unittest.mock import patch
from battle_lab.auto_lab_service import AutoLabTeamPayload, validate_auto_lab_payloads

block = """Pikachu @ Light Ball
Ability: Static
Level: 50
EVs: 4 HP / 252 SpA / 252 Spe
Timid Nature
- Thunderbolt
- Protect
- Volt Switch
- Fake Tears"""
team = "\\n\\n".join([block] * 6)
payloads = [
    AutoLabTeamPayload(id="bad", label="Archaludon inválido", teamPaste=team),
    AutoLabTeamPayload(id="good", label="Siguiente paste reciente", teamPaste=team),
]

async def main():
    calls = iter([RuntimeError("Archaludon's move Precipice Blades does not exist in Gen 9."), "ok"])
    def fake_validate(*args, **kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    with patch("battle_lab.auto_lab_service.validate_team", side_effect=fake_validate):
        results = await validate_auto_lab_payloads(Path("."), payloads)
    assert results[0]["valid"] is False, results
    assert "Precipice Blades" in results[0]["error"], results
    assert results[1]["valid"] is True, results

asyncio.run(main())
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Audit renders visual evidence while Sparring stays manual", () => {
  assert.match(fs.readFileSync(panel, "utf8"), /war-room-auto-lab-v2/);
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(ui, /Auto Lab · auditoría empírica/);
  assert.match(ui, /getSpriteUrl/);
  assert.match(ui, /SpriteStrip/);
  assert.match(ui, /Matchups favorables/);
  assert.match(ui, /Matchups duros/);
  assert.match(ui, /Ranking de rivales problemáticos/);
  assert.match(ui, /Leads propios/);
  assert.match(ui, /Uso del roster/);
  assert.match(ui, /Pokémon rivales ligados a derrotas/);
  assert.match(ui, /Cores \/ leads rivales ligados a derrotas/);
  assert.match(ui, /Moves que merecen revisión/);
  assert.match(ui, /Patrones recurrentes en derrotas/);
  assert.match(ui, /Rendimiento por arquetipo/);

  const room = fs.readFileSync(warRoom, "utf8");
  assert.match(room, /import \{ WarRoomAutoLab \} from "@\/components\/vgc\/war-room-auto-lab"/);
  assert.match(room, /mode === "audit"[\s\S]*<AuditView result=\{audit\} \/>[\s\S]*<WarRoomAutoLab team=\{workingTeam\}/);
  const adapter = fs.readFileSync(sparring, "utf8");
  assert.doesNotMatch(adapter, /WarRoomAutoLab/);
  assert.match(adapter, /LocalWarRoomSparring/);
});
