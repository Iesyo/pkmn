from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from ledger_replay import ReplayEvidenceError, build_replay, export, load_trace_context


def _pilot_fixture(directory: Path) -> tuple[dict, Path]:
    def event(seq, kind, slot=None, actor_id=None, **kwargs):
        return {"seq": seq, "kind": kind, "status": "consistent", "slot": slot,
                "actor_id": actor_id, **kwargs}

    battle = {
        "battle_index": 0, "first_frame": 1, "last_frame": 12, "issues": [],
        "actors": {
            "p1-one": {"species": "Blaziken"}, "p1-two": {"species": "Indeedee-F"},
            "p2-one": {"species": "Garchomp"}, "p2-two": {"species": "Sneasler"},
        },
        "events": [
            event(1, "switch", "p1a", "p1-one", species="Blaziken", health="156/156"),
            event(2, "switch", "p1b", "p1-two", species="Indeedee-F", health="177/177"),
            event(3, "switch", "p2a", "p2-one", species="Garchomp", health="100/100"),
            event(4, "switch", "p2b", "p2-two", species="Sneasler", health="100/100"),
            event(5, "turn", turn=1),
            event(6, "move", "p1a", "p1-one", move="Rock Tomb", target_slot="p2b"),
            event(7, "damage", "p2b", "p2-two", health="0/100", before="100/100",
                  after="0/100", cause=6),
            event(8, "faint", "p2b", "p2-two"),
            event(9, "battle_end", frame=10, value="The battle has ended due to a forfeit."),
        ],
    }
    rows = []
    for frame in range(1, 13):
        rows.append({"battle_index": 0, "frame": frame,
                     "detections": {"players": {"p1": "Roku", "p2": "Benji"}},
                     "ocr": [{"text": "You defeated Benji!", "confidence": .98}] if frame == 12 else []})
    diagnostic = directory / "champions-diagnostics-fixture.zip"
    with zipfile.ZipFile(diagnostic, "w") as archive:
        archive.writestr("job.json", json.dumps({
            "id": "fixture", "created_at": "2026-09-27T18:00:00+00:00",
            "context": {"format": "gen9championsvgc2026regmc"}}))
        archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
        # A replay previo podría declarar ganador, pero nunca debe ser fuente.
        archive.writestr("output/replay-001.log", "|win|Roku\n")
    return battle, diagnostic


class LedgerReplayTest(unittest.TestCase):
    def test_exporta_secuencia_y_resultado_con_evidencia(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            battle, zip_path = _pilot_fixture(root)
            ledger = root / "battle-01.json"
            ledger.write_text(json.dumps(battle), encoding="utf-8")
            log_path, json_path, html_path = export(ledger, zip_path, root / "replay-001")
            lines = log_path.read_text().splitlines()
            self.assertEqual(lines[-1], "|win|Roku")
            self.assertLess(lines.index("|turn|1"), lines.index("|move|p1a: Blaziken|Rock Tomb|p2b: Sneasler"))
            self.assertLess(lines.index("|-damage|p2b: Sneasler|0/100"), lines.index("|faint|p2b: Sneasler"))
            result = json.loads(json_path.read_text())
            self.assertEqual(result["ledger_source"]["winner_evidence"]["frame"], 12)
            self.assertEqual(result["source_battle_index"], 0)
            self.assertIn('class="battle-log-data"', html_path.read_text())

    def test_replay_archivado_no_sustituye_prueba_del_ganador(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            battle, zip_path = _pilot_fixture(root)
            with zipfile.ZipFile(zip_path) as source:
                trace = source.read("output/ocr.trace.jsonl").decode().splitlines()
                job = source.read("job.json")
                old_replay = source.read("output/replay-001.log")
            trace[-1] = json.dumps({**json.loads(trace[-1]), "ocr": []})
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(trace))
                archive.writestr("output/replay-001.log", old_replay)
            with self.assertRaisesRegex(ReplayEvidenceError, "ganador único"):
                load_trace_context(zip_path, battle)

    def test_rechaza_ps_discontinuos_y_avisos_abiertos(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            broken = copy.deepcopy(battle)
            broken["events"][6]["before"] = "50/100"
            with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos"):
                build_replay(broken, context)
            broken = copy.deepcopy(battle)
            broken["issues"] = [{"code": "hp_unconfirmed"}]
            with self.assertRaisesRegex(ReplayEvidenceError, "avisos abiertos"):
                build_replay(broken, context)

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_SIXTH") and os.getenv("CHAMPIONS_LEDGER_PILOT"),
                         "requiere el diagnóstico y el JSON real de f7af/01")
    def test_partida_real_f7af_01(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_PILOT"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_SIXTH"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertEqual(document["ledger_source"]["consistent_events"], 30)
        self.assertEqual(document["ledger_source"]["winner_evidence"]["frame"], 605)
        self.assertEqual([line for line in lines if line.startswith("|turn|")], ["|turn|1", "|turn|2"])
        self.assertEqual(sum(line.startswith("|faint|") for line in lines), 2)
        self.assertIn("|detailschange|p2b: Charizard|Charizard-Mega-Y, L50|100/100", lines)
        self.assertEqual(lines[-1], "|win|Roku")


if __name__ == "__main__":
    unittest.main()
