import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));

test("parses replay evidence and preserves mirrored audit semantics", () => {
  const source = String.raw`
from battle_lab.audit_benchmark import build_audit, parse_replay_html

target_battle = {
    "battleTag": "battle-test-2",
    "winner": "Baseline",
    "winnerSide": "alpha",
    "winnerAgent": "baseline",
    "turns": 3,
    "pairing": {
        "id": "MC182--vs--MC183",
        "alphaTeamId": "MC183",
        "betaTeamId": "MC182",
    },
    "benchmark": {
        "baselineId": "simple-heuristics",
        "scheduleIndex": 1,
        "vgcBenchSide": "beta",
    },
}
target_replay = """
<script type="text/plain" class="battle-log-data">
|player|p1|Baseline|1
|player|p2|VGC|1
|poke|p2|Gardevoir, L50
|poke|p2|Indeedee-F, L50
|poke|p2|Armarouge, L50
|poke|p2|Sneasler, L50
|teampreview|4
|start
|switch|p2a: Gardevoir|Gardevoir, L50|100/100
|switch|p2b: Indeedee|Indeedee-F, L50|100/100
|turn|1
|move|p2b: Indeedee|Fake Out|p1b: Gholdengo
|-immune|p1b: Gholdengo
|faint|p2b: Indeedee
|turn|2
|move|p2a: Gardevoir|Protect|p2a: Gardevoir
|turn|3
|move|p2a: Gardevoir|Protect|p2a: Gardevoir
|-fail|p2a: Gardevoir
|win|Baseline
</script>
"""
facts = parse_replay_html(target_replay, target_battle)
assert facts.protocol_side == "p2"
assert facts.leads == ["Gardevoir", "Indeedee-F"]
assert facts.immunity_events == 1
assert facts.failed_move_events == 1
assert facts.consecutive_protection == 1
assert facts.first_faint_conceded is True

mirror_battle = {
    "battleTag": "battle-test-1",
    "winner": "VGC",
    "winnerSide": "alpha",
    "winnerAgent": "vgcBench",
    "turns": 4,
    "pairing": target_battle["pairing"],
    "benchmark": {
        "baselineId": "simple-heuristics",
        "scheduleIndex": 0,
        "vgcBenchSide": "alpha",
    },
}
mirror_replay = """
<script type="text/plain" class="battle-log-data">
|player|p1|VGC|1
|player|p2|Baseline|1
|poke|p1|Salamence, L50
|poke|p1|Tyranitar, L50
|start
|switch|p1a: Salamence|Salamence, L50|100/100
|switch|p1b: Tyranitar|Tyranitar, L50|100/100
|turn|1
|move|p1a: Salamence|Protect|p1a: Salamence
|turn|4
|win|VGC
</script>
"""
result = {
    "schemaVersion": 4,
    "mode": "benchmark",
    "runId": "unit-run",
    "createdAt": "2026-09-14T00:00:00Z",
    "projectCommit": "abc",
    "teams": {
        "items": [
            {"id": "MC182", "player": "Tester", "roster": ["Gardevoir"]},
            {"id": "MC183", "player": "Opponent", "roster": ["Salamence"]},
        ]
    },
    "benchmark": {
        "schedule": {"sha256": "deadbeef"},
        "opponents": {
            "simple-heuristics": {
                "id": "simple-heuristics",
                "label": "Simple Heuristics",
            }
        },
    },
    "battles": {"items": [mirror_battle, target_battle]},
}
audit = build_audit(
    result,
    {"battle-test-1": mirror_replay, "battle-test-2": target_replay},
    team_id="MC182",
    baseline_id="simple-heuristics",
    sample_size=1,
)
assert audit["record"] == {
    "games": 1, "wins": 0, "losses": 1, "ties": 0, "winPercent": 0.0
}
assert audit["pairedEvidence"]["lossMirrorWin"] == 1
assert audit["pairedEvidence"]["lossMirrorWinPercent"] == 100.0
assert audit["selectedCases"][0]["evidenceFlags"][0] == "espejo-favorable"
`;
  const result = spawnSync("python3", ["-c", source], {
    cwd: projectRoot,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stdout + "\n" + result.stderr);
});

test("the canonical Colab can audit the latest run without replaying 1500 battles", async () => {
  const [notebookText, auditor] = await Promise.all([
    readFile(new URL("../colab/Battle_Lab.ipynb", import.meta.url), "utf8"),
    readFile(new URL("../battle_lab/audit_benchmark.py", import.meta.url), "utf8"),
  ]);
  const notebook = JSON.parse(notebookText);
  const source = notebook.cells.flatMap((cell) => cell.source).join("");

  assert.match(source, /RUN_EVALUATION = False/);
  assert.match(source, /RUN_AUDIT = True/);
  assert.match(source, /AUDIT_TEAM_ID = "MC182"/);
  assert.match(source, /AUDIT_BASELINE = "simple-heuristics"/);
  assert.match(source, /battle_lab\/audit_benchmark\.py/);
  assert.match(source, /report\.html/);
  assert.match(source, /cases\.csv/);
  assert.match(auditor, /paired-mirrored-replay-audit/);
  assert.match(auditor, /Los indicadores automáticos son hechos observables/);
  assert.match(auditor, /El replay no conserva logits/);
  assert.match(auditor, /select_representative_cases/);
});
