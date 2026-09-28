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
    p1_team = ["Blaziken", "Indeedee-F", "Gardevoir", "Kingambit", "Rillaboom", "Basculegion"]
    p2_team = ["Garchomp", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Pelipper"]
    rows = []
    for frame in range(1, 13):
        rows.append({"battle_index": 0, "frame": frame,
                     "detections": {"players": {"p1": "Roku", "p2": "Benji"},
                                    "team_preview": frame in (1, 2),
                                    "teams": {"p1": p1_team, "p2": p2_team} if frame in (1, 2) else {}},
                     "ocr": [{"text": "You defeated Benji!", "confidence": .98}] if frame == 12 else []})
    diagnostic = directory / "champions-diagnostics-fixture.zip"
    with zipfile.ZipFile(diagnostic, "w") as archive:
        archive.writestr("job.json", json.dumps({
            "id": "fixture", "created_at": "2026-09-27T18:00:00+00:00",
            "context": {"format": "gen9championsvgc2026regmc", "teams": {"p1": p1_team}}}))
        archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
        # A replay previo podría declarar ganador, pero nunca debe ser fuente.
        archive.writestr("output/replay-001.log", "|poke|p2|Inventado, L50|\n|win|Roku\n")
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
            self.assertEqual(sum(line.startswith("|poke|p1|") for line in lines), 6)
            self.assertEqual(sum(line.startswith("|poke|p2|") for line in lines), 6)
            self.assertEqual(lines[lines.index("|teamsize|p2|6") + 1], "|poke|p2|Garchomp, L50|")
            self.assertLess(lines.index("|teampreview"), lines.index("|start"))
            self.assertLess(lines.index("|start"), lines.index("|switch|p1a: Blaziken|Blaziken, L50|156/156"))
            self.assertLess(lines.index("|turn|1"), lines.index("|move|p1a: Blaziken|Rock Tomb|p2b: Sneasler"))
            self.assertLess(lines.index("|-damage|p2b: Sneasler|0/100"), lines.index("|faint|p2b: Sneasler"))
            result = json.loads(json_path.read_text())
            self.assertEqual(result["ledger_source"]["winner_evidence"]["frame"], 12)
            self.assertEqual(result["source_battle_index"], 0)
            self.assertEqual(result["ledger_source"]["team_preview"]["p2"]["frames"], [1, 2])
            self.assertIn('class="battle-log-data"', html_path.read_text())

    def test_no_inventa_seis_pokemon_sin_roster_estable(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            with zipfile.ZipFile(zip_path) as source:
                job = source.read("job.json")
                rows = [json.loads(line) for line in source.read("output/ocr.trace.jsonl").splitlines()]
            rows[0]["detections"]["teams"]["p2"] = []
            rows[1]["detections"]["teams"]["p2"] = []
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ReplayEvidenceError, "equipo completo de seis"):
                load_trace_context(zip_path, battle)

            rows[0]["detections"]["teams"]["p2"] = [
                "Garchomp", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Pelipper"]
            rows[1]["detections"]["teams"]["p2"] = [
                "Garchomp", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Kingambit"]
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ReplayEvidenceError, "equipo único de seis"):
                load_trace_context(zip_path, battle)

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

    def test_hud_intermedio_exige_accion_previa_y_mote(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            battle["events"][3]["hp_state"] = "inferred"
            battle["events"][5]["frame"] = 6
            damage = battle["events"][6]
            damage.update({"before": "92/100", "frame": 8, "hp_state": "confirmed", "species": "Sneasler",
                           "hp_baseline": {"state": "confirmed", "evidence": [
                               {"frame": 7, "text": "92%", "nickname": "sneasler"}]}})
            result = build_replay(battle, context)
            self.assertEqual(result["ledger_source"]["intermediate_baselines"][0]["hud_frame"], 7)
            self.assertIn("|-damage|p2b: Sneasler|0/100", result["log"])
            damage["hp_baseline"]["evidence"][0]["frame"] = 5
            with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos"):
                build_replay(battle, context)
            damage["hp_baseline"]["evidence"][0].update(frame=7, nickname="garchomp")
            with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos"):
                build_replay(battle, context)

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_SIXTH") and os.getenv("CHAMPIONS_LEDGER_PILOT"),
                         "requiere el diagnóstico y el JSON real de f7af/01")
    def test_partida_real_f7af_01(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_PILOT"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_SIXTH"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertEqual(document["ledger_source"]["consistent_events"], 30)
        self.assertEqual(document["ledger_source"]["winner_evidence"]["frame"], 605)
        self.assertEqual(document["log"].count("|poke|p1|"), 6)
        self.assertEqual(document["log"].count("|poke|p2|"), 6)
        self.assertIn("|poke|p2|Whimsicott, L50|", document["log"])
        self.assertEqual([line for line in lines if line.startswith("|turn|")], ["|turn|1", "|turn|2"])
        self.assertEqual(sum(line.startswith("|faint|") for line in lines), 2)
        self.assertIn("|detailschange|p2b: Charizard|Charizard-Mega-Y, L50|100/100", lines)
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_TENTH") and os.getenv("CHAMPIONS_LEDGER_SECOND"),
                         "requiere el diagnóstico y el JSON real de 79dd/02")
    def test_partida_real_79dd_02(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_SECOND"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_TENTH"]), battle)
        document = build_replay(battle, context)
        self.assertEqual(document["ledger_source"]["consistent_events"], 32)
        self.assertEqual(document["log"].count("|poke|p1|"), 6)
        self.assertEqual(document["log"].count("|poke|p2|"), 6)
        self.assertIn("|poke|p2|Gengar, L50|", document["log"])
        self.assertEqual(document["ledger_source"]["winner_evidence"], {
            "frame": 1314, "text": "You lost to Denton!", "confidence": .99995})
        self.assertEqual(document["ledger_source"]["intermediate_baselines"], [{
            "ledger_seq": 25, "causing_move_seq": 24, "inferred_entry": "100/100",
            "intermediate": "92/100", "final": "81/100", "hud_frame": 1235,
        }])
        self.assertEqual(document["log"].splitlines()[-1], "|win|Denton")


if __name__ == "__main__":
    unittest.main()
