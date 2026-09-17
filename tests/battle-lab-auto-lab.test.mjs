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
  assert.match(source, /preview_index_offset \+ index/);
  assert.match(source, /_preview_seed\(opponent_id, preview_index\)/);
  assert.match(source, /summary\["teamPreview"\]/);
  assert.match(source, /build_auto_lab_audit/);
  assert.match(source, /"schemaVersion": 3/);
  assert.match(source, /if len\(opponents\) > 100/);
  execFileSync("python", ["-m", "py_compile", core, audit, service, nanaRuntime], { cwd: root, encoding: "utf8" });
});

test("Audit stays baseline-only while Optimize can compare its captured draft", () => {
  const api = fs.readFileSync(service, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");
  const room = fs.readFileSync(warRoom, "utf8");

  assert.match(api, /variants: list\[AutoLabTeamPayload\] = Field\(default_factory=list/);
  assert.match(api, /MAX_OPPONENTS = 100/);
  assert.doesNotMatch(ui, /buildAutoLabVariants|optimizeTeam|Paquete de set completo|Copiar set candidato/);
  assert.match(ui, /serializeShowdownPaste\(team\.pokemon/);
  assert.match(ui, /id:\s*"baseline-current"/);
  assert.match(ui, /teamPaste:\s*baselinePaste/);
  assert.match(ui, /Optimizar o construir/);
  assert.match(ui, /variants:\s*comparisonMode && comparisonTeam/);
  assert.match(ui, /id:\s*"variant-optimized"/);
  assert.match(ui, /Iniciar 2,000 batallas/);
  assert.match(ui, /Mejora confirmada/);
  assert.match(room, /Comparar con original/);
  assert.match(room, /comparisonTeam=\{optimizationComparison\.optimized\}/);
  assert.match(room, /team=\{workingTeam\} corpusTeams=\{resources\.corpus\.teams\}/);
  assert.match(room, /function SetSuggestionCard/);
  assert.match(room, /Set search/);
  assert.match(room, /result\.sets\.map\([\s\S]*SetSuggestionCard/);
});

test("Optimize comparison requires interval-backed evidence before promotion", () => {
  const source = fs.readFileSync(core, "utf8");
  assert.match(source, /def _score_delta_confidence95/);
  assert.match(source, /confirmed-improvement/);
  assert.match(source, /criticalOpponentsImproved/);
  assert.match(source, /evidence == "confirmed-improvement"/);

  const script = `
from battle_lab.auto_lab import compare_with_baseline

def report(score, wins, losses, screening, combined):
    return {
        "poolEstimate": {"scorePercent": score, "games": wins + losses, "wins": wins, "losses": losses, "ties": 0},
        "screeningByOpponent": screening,
        "byOpponent": combined,
    }

baseline = report(
    40, 40, 60,
    {"rain": {"scorePercent": 25}, "balance": {"scorePercent": 50}},
    {"rain": {"scorePercent": 30, "deepDive": True}, "balance": {"scorePercent": 50, "deepDive": False}},
)
optimized = report(
    70, 70, 30,
    {"rain": {"scorePercent": 75}, "balance": {"scorePercent": 60}},
    {"rain": {"scorePercent": 70, "deepDive": True}, "balance": {"scorePercent": 60, "deepDive": False}},
)
comparison = compare_with_baseline(baseline, optimized)
assert comparison["evidence"] == "confirmed-improvement", comparison
assert comparison["deltaPercentagePoints"] == 30, comparison
assert comparison["delta95"]["low"] > 0, comparison
assert comparison["criticalOpponentsImproved"] == 1, comparison
assert comparison["promotion"] == "candidate", comparison

noisy = report(
    51, 51, 49,
    {"rain": {"scorePercent": 62.5}, "balance": {"scorePercent": 50}},
    {"rain": {"scorePercent": 62.5, "deepDive": True}, "balance": {"scorePercent": 50, "deepDive": False}},
)
directional = compare_with_baseline(report(
    50, 50, 50,
    {"rain": {"scorePercent": 50}, "balance": {"scorePercent": 50}},
    {"rain": {"scorePercent": 50, "deepDive": True}, "balance": {"scorePercent": 50, "deepDive": False}},
), noisy)
assert directional["evidence"] == "directional-improvement", directional
assert directional["delta95"]["low"] < 0, directional
assert directional["promotion"] == "hold", directional
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
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
  assert.match(route, /auto-lab\(\?:\\\/validate\|\\\/\[a-f0-9\]\{16\}/);
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
  assert.match(source, /"tournament"[\s\S]*"scouting-library"[\s\S]*"vgcpastes"/);
  assert.match(source, /selectAutoLabOpponentCandidates/);
});

test("Adaptive audit preserves each budget while widening and deduplicating the opponent pool", () => {
  const source = fs.readFileSync(variants, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");

  assert.match(source, /export function selectAutoLabRecentVgcPastesCandidates/);
  assert.match(source, /team\.source === "vgcpastes"/);
  assert.match(source, /Date\.parse\(value\.trim\(\)\)/);
  assert.match(source, /right\.sharedAt - left\.sharedAt/);
  assert.match(source, /selectRosterDiverse/);
  assert.match(source, /rosterSignature/);
  assert.match(ui, /opponents:\s*18,\s*initialBattlesPerOpponent:\s*8,\s*deepDiveOpponents:\s*6,\s*additionalBattlesPerDeepDive:\s*12/);
  assert.match(ui, /opponents:\s*40,\s*initialBattlesPerOpponent:\s*8,\s*deepDiveOpponents:\s*10,\s*additionalBattlesPerDeepDive:\s*16/);
  assert.match(ui, /opponents:\s*100,\s*initialBattlesPerOpponent:\s*6,\s*deepDiveOpponents:\s*20,\s*additionalBattlesPerDeepDive:\s*20/);
  assert.match(ui, /activePreset === "deep"[\s\S]*selectAutoLabRecentVgcPastesCandidates/);
  assert.match(ui, /Profundo necesita \$\{spec\.opponents\} VGCPastes M-C actuales, recientes y validados por Showdown/);
  assert.match(ui, /100 × 6 del meta reciente \+ 20 × 20 de confirmación/);
});

test("Adaptive sampling ranks recurrent risk, adds confidence and never reuses preview seeds", () => {
  const source = fs.readFileSync(core, "utf8");
  const api = fs.readFileSync(service, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(source, /class AdaptiveSamplingPlan/);
  assert.match(source, /select_deep_dive_opponents/);
  assert.match(source, /prevalence \* severity \* repeatability \* confidence/);
  assert.match(source, /preview_index_offset=sampling_plan\.initial_battles_per_opponent/);
  assert.match(api, /initialBattlesPerOpponent/);
  assert.match(api, /deepDiveOpponents/);
  assert.match(api, /additionalBattlesPerDeepDive/);
  assert.match(ui, /Cobertura primero, confirmación después/);
  assert.match(ui, /IC95%/);

  const script = `
from battle_lab.auto_lab import AdaptiveSamplingPlan, _preview_seed, select_deep_dive_opponents

plan = AdaptiveSamplingPlan(8, 6, 12)
assert plan.battles_per_candidate(18) == 216
assert AdaptiveSamplingPlan(8, 10, 16).battles_per_candidate(40) == 480
assert AdaptiveSamplingPlan(6, 20, 20).battles_per_candidate(100) == 1000
assert _preview_seed("rain", 0) != _preview_seed("rain", 8)

report = {"scorePercent": 50, "byOpponent": {
    "rain-a": {"games": 8, "wins": 0, "losses": 8, "ties": 0, "scorePercent": 0, "confidence95": {"low": 0, "high": 32.4, "width": 32.4}},
    "rain-b": {"games": 8, "wins": 1, "losses": 7, "ties": 0, "scorePercent": 12.5, "confidence95": {"low": 2.2, "high": 47.1, "width": 44.9}},
    "tr-a": {"games": 8, "wins": 2, "losses": 6, "ties": 0, "scorePercent": 25, "confidence95": {"low": 7.1, "high": 59.1, "width": 52}},
    "tailwind-a": {"games": 8, "wins": 7, "losses": 1, "ties": 0, "scorePercent": 87.5, "confidence95": {"low": 52.9, "high": 97.8, "width": 44.9}},
}}
opponents = {
    "rain-a": {"label": "Rain A", "archetypes": ["Rain"]},
    "rain-b": {"label": "Rain B", "archetypes": ["Rain"]},
    "tr-a": {"label": "TR", "archetypes": ["Trick Room"]},
    "tailwind-a": {"label": "TW", "archetypes": ["Tailwind"]},
}
selected = select_deep_dive_opponents(report, opponents, 2)
assert [row["id"] for row in selected] == ["rain-a", "tr-a"], selected
assert selected[0]["components"]["repeatability"] == 1.0, selected
assert selected[0]["screening"]["confidence95"]["high"] == 32.4, selected
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Adaptive confirmation does not bias the broad pool or archetype estimate", () => {
  const script = `
from pathlib import Path
from tempfile import TemporaryDirectory
from battle_lab.auto_lab_audit import build_auto_lab_audit

def battle(tag, opponent, winner, stage):
    return {
        "battleTag": tag,
        "pairing": {"alphaTeamId": "baseline", "betaTeamId": opponent},
        "winnerSide": winner,
        "samplingStage": stage,
        "teamPreview": {"alpha": []},
    }

summaries = [
    battle("screen-hard", "hard", "beta", "screening"),
    battle("screen-good", "good", "alpha", "screening"),
    battle("deep-hard-1", "hard", "beta", "deepening"),
    battle("deep-hard-2", "hard", "beta", "deepening"),
]
candidate_report = {
    "scorePercent": 25,
    "poolEstimate": {"games": 2, "wins": 1, "losses": 1, "ties": 0, "scorePercent": 50},
    "byOpponent": {
        "hard": {"games": 3, "wins": 0, "losses": 3, "ties": 0, "scorePercent": 0, "confidence95": {"low": 0, "high": 56.15, "width": 56.15}, "deepDive": True, "evidenceLevel": "confirmed"},
        "good": {"games": 1, "wins": 1, "losses": 0, "ties": 0, "scorePercent": 100, "confidence95": {"low": 20.65, "high": 100, "width": 79.35}, "deepDive": False, "evidenceLevel": "screening"},
    },
}
opponents = {
    "hard": {"label": "Hard Rain", "roster": ["A", "B", "C", "D", "E", "F"], "archetypes": ["Rain"]},
    "good": {"label": "Good Rain", "roster": ["G", "H", "I", "J", "K", "L"], "archetypes": ["Rain"]},
}
with TemporaryDirectory() as tmp:
    result = build_auto_lab_audit(
        candidate_id="baseline",
        candidate_roster=["One", "Two", "Three", "Four", "Five", "Six"],
        summaries=summaries,
        candidate_report=candidate_report,
        opponents=opponents,
        replay_root=Path(tmp),
        sampling={"deepDive": {"opponents": 1}},
    )
rain = result["archetypePerformance"][0]
assert rain["scorePercent"] == 50, rain
assert rain["adaptiveCombined"]["scorePercent"] == 25, rain
assert result["dataQuality"]["screeningGames"] == 2, result["dataQuality"]
assert result["dataQuality"]["deepeningGames"] == 2, result["dataQuality"]
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Evidence-aware heuristics compare with/without, smooth small samples and expose move opportunities", () => {
  const source = fs.readFileSync(audit, "utf8");
  const coreSource = fs.readFileSync(core, "utf8");
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(source, /def _compare_presence/);
  assert.match(source, /def _smoothed_rate_percent/);
  assert.match(source, /candidate_team_text/);
  assert.match(source, /extract_candidate_moves/);
  assert.match(source, /matchedDeltaPercentagePoints/);
  assert.match(source, /appearanceGames/);
  assert.match(source, /archetypeStratum/);
  assert.match(source, /"evidenceSummary"/);
  assert.match(coreSource, /candidate_team_text=candidate_records\[0\]\.team_text/);
  assert.match(ui, /Semáforo de evidencia/);
  assert.match(ui, /Score al elegirlo/);
  assert.match(ui, /apariciones del\s+Pokémon/);
  assert.match(ui, /ajustado por arquetipo\+lado/);

  const script = `
from battle_lab.auto_lab_audit import (
    _compare_presence,
    _smoothed_rate_percent,
    extract_candidate_moves,
)

observations = []
for opponent in ("rain-a", "rain-b", "trick-room"):
    for side in ("alpha", "beta"):
        for present in (True, False):
            for _ in range(6):
                observations.append({
                    "outcome": "losses" if present else "wins",
                    "side": side,
                    "stratum": f"{opponent}|{side}",
                    "present": present,
                })
comparison = _compare_presence(
    observations,
    present=lambda row: row["present"],
    stratum=lambda row: row["stratum"],
    prior_percent=50,
)
assert comparison["matchedDeltaPercentagePoints"] == -100, comparison
assert comparison["matchedStrata"] == 6, comparison
assert comparison["evidence"]["level"] == "robust", comparison

side_sensitive = []
for side in ("alpha", "beta"):
    for present in (True, False):
        for _ in range(6):
            favorable = (side == "alpha" and present) or (side == "beta" and not present)
            side_sensitive.append({
                "outcome": "wins" if favorable else "losses",
                "side": side,
                "stratum": side,
                "present": present,
            })
side_comparison = _compare_presence(
    side_sensitive,
    present=lambda row: row["present"],
    stratum=lambda row: row["stratum"],
    prior_percent=50,
)
assert side_comparison["evidence"]["level"] == "side-sensitive", side_comparison
assert _smoothed_rate_percent(
    {"games": 1, "wins": 1, "losses": 0, "ties": 0},
    prior_percent=50,
) == 57.14

paste = """Ace (Mawile) @ Mawilite
Ability: Intimidate
- Protect
- Play Rough

Farigiraf @ Sitrus Berry
Ability: Armor Tail
- Trick Room"""
assert extract_candidate_moves(paste, ["Mawile", "Farigiraf"]) == {
    "Mawile": ["Protect", "Play Rough"],
    "Farigiraf": ["Trick Room"],
}
`;
  execFileSync("python", ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Opponent selection spends early slots on distinct rosters before duplicate builds", () => {
  const script = `
import assert from "node:assert/strict";
import { selectAutoLabRecentVgcPastesCandidates } from "./lib/war-room-auto-lab.ts";

const make = (id, dateShared, pokemon) => ({
  id,
  source: "vgcpastes",
  savedPasteId: "",
  playerName: id,
  tournament: "",
  rank: "",
  dateShared,
  pokepasteUrl: "https://pokepast.es/example",
  pokemon,
  formatId: "gen9vgc2026regmc",
  formatLabel: "M-C",
  regulationWeight: 1,
  historical: false,
  setEvidenceEligible: true,
});
const rosterA = ["A", "B", "C", "D", "E", "F"];
const selected = selectAutoLabRecentVgcPastesCandidates([
  make("a-new", "2026-09-16", rosterA),
  make("a-copy", "2026-09-15", [...rosterA].reverse()),
  make("b", "2026-09-14", ["G", "H", "I", "J", "K", "L"]),
  make("c", "2026-09-13", ["M", "N", "O", "P", "Q", "R"]),
], 3);
assert.deepEqual(selected.map((team) => team.id), ["a-new", "b", "c"]);
`;
  execFileSync(process.execPath, ["--import", "tsx", "--input-type=module", "-e", script], {
    cwd: root,
    encoding: "utf8",
  });
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

test("Auto Lab keeps results from the previous runtime renderable", () => {
  const ui = fs.readFileSync(panelV2, "utf8");

  assert.match(ui, /evidenceSummary\?:/);
  assert.match(ui, /audit\.evidenceSummary \?/);
  assert.match(ui, /Esta ejecución viene de un runtime anterior/);
  assert.match(ui, /label: "Resultado previo"/);
  assert.match(ui, /function isFiniteNumber\(value: unknown\): value is number/);
  assert.match(ui, /isFiniteNumber\(row\.scoreWhenNotSelected\)/);
  assert.match(ui, /isFiniteNumber\(row\.smoothedLossRate\)/);
  assert.match(ui, /isFiniteNumber\(row\.appearanceGames\)/);
});

test("Audit renders visual evidence while Sparring stays manual", () => {
  assert.match(fs.readFileSync(panel, "utf8"), /war-room-auto-lab-v2/);
  const ui = fs.readFileSync(panelV2, "utf8");
  assert.match(ui, /Auto Lab · auditoría empírica/);
  assert.match(ui, /getSpriteUrl/);
  assert.match(ui, /SpriteStrip/);
  assert.match(ui, /Matchups favorables/);
  assert.match(ui, /Matchups duros/);
  assert.doesNotMatch(ui, /Ranking de rivales problemáticos/);
  assert.match(ui, /Leads propios/);
  assert.match(ui, /Uso del roster/);
  assert.match(ui, /Pokémon rivales ligados a derrotas/);
  assert.match(ui, /Cores \/ leads rivales ligados a derrotas/);
  assert.match(ui, /Moves que merecen revisión/);
  assert.match(ui, /Patrones recurrentes en derrotas/);
  assert.match(ui, /Rendimiento por arquetipo/);

  const room = fs.readFileSync(warRoom, "utf8");
  assert.match(room, /WarRoomAutoLab/);
  assert.match(room, /mode === "audit"[\s\S]*AuditView[\s\S]*WarRoomAutoLab/);
  const adapter = fs.readFileSync(sparring, "utf8");
  assert.doesNotMatch(adapter, /WarRoomAutoLab/);
  assert.match(adapter, /LocalWarRoomSparring/);
});
