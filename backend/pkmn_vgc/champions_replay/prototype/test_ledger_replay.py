from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from dataclasses import replace
from pathlib import Path

from ledger_replay import ReplayEvidenceError, build_replay, export, load_trace_context, build_trace_context


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
    def test_export_blocks_a_saved_roster_missing_a_confirmed_participant(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, path = _pilot_fixture(Path(directory)); context = load_trace_context(path, battle)
            for side, absent in (("p1", "Blaziken"), ("p2", "Garchomp")):
                team = tuple("Different Species" if s == absent else s for s in context.teams[side])
                with self.subTest(side=side), self.assertRaisesRegex(ReplayEvidenceError, "contradice las especies"):
                    build_replay(battle, replace(context, teams={**context.teams, side: team}))

    def test_illusion_export_uses_catalogue_ability_instead_of_species_name(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, path = _pilot_fixture(Path(directory)); context = load_trace_context(path, battle)
            battle["actors"]["p2-one"]["species"] = "Catalogue Illusion User"
            battle["events"][2].update(species="Catalogue Illusion User", display_species="Charizard")
            context = replace(context, teams={**context.teams, "p2": ("Catalogue Illusion User", *context.teams["p2"][1:])})
            with patch("ledger_replay.species_abilities", return_value={"Catalogue Illusion User": {"Illusion"}}):
                self.assertIn("|switch|p2a: Charizard", build_replay(battle, context)["log"])
            with patch("ledger_replay.species_abilities", return_value={"Catalogue Illusion User": {"Pressure"}}):
                with self.assertRaisesRegex(ReplayEvidenceError, "Ilusión no acreditada"):
                    build_replay(battle, context)

    def test_repeated_own_preview_overrides_a_different_saved_team(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, path = _pilot_fixture(Path(directory))
            with zipfile.ZipFile(path) as archive:
                job = json.loads(archive.read("job.json"))
                rows = [json.loads(x) for x in archive.read("output/ocr.trace.jsonl").splitlines()]
            job["context"]["teams"]["p1"][4] = "Whimsicott"
            for row in rows[:2]: row["detections"]["teams"]["p1"][4] = "Volcarona"
            context = build_trace_context(job, rows, battle)
            self.assertIn("Volcarona", context.teams["p1"])
            self.assertNotIn("Whimsicott", context.teams["p1"])
            self.assertEqual(context.team_evidence["p1"]["source"], "team_preview")
            rows[1]["detections"]["teams"]["p1"][4] = "Rillaboom"
            with self.assertRaises(ReplayEvidenceError): build_trace_context(job, rows, battle)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_TRICK_ROOM"), "Requiere ZIP de Trick Room")
    def test_137129_replay_preserves_room_and_observed_sources(self):
        from champions_automaton import BattleAutomaton, read_diagnostic, read_diagnostic_context

        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_TRICK_ROOM"])
        rows, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, [r for r in rows if r["battle_index"] == 0],
                                 read_diagnostic_context(path)).run()
        context = load_trace_context(path, ledger)
        replay = build_replay(ledger, context)
        with zipfile.ZipFile(path) as archive:
            old = archive.read("output/replay.log").decode().splitlines()
            old_ledger = json.loads(archive.read("output/ledger-battle-001.json"))
        with self.assertRaisesRegex(ReplayEvidenceError, "Inicio duplicado de Trick Room"):
            build_replay(old_ledger, context)
        expected = []
        starts = 0
        sources = {
            "|-fieldstart|move: Psychic Terrain": "|[from] ability: Psychic Surge|[of] p1a: Indeedee-F",
            "|-fieldstart|move: Trick Room": "|[of] p1a: Indeedee-F",
            "|-enditem|p1a: Indeedee-F|Sitrus Berry": "|[eat]",
            "|-heal|p1a: Indeedee-F|76/177": "|[from] item: Sitrus Berry",
        }
        for line in old:
            if line == "|-fieldstart|move: Trick Room":
                starts += 1
                if starts > 1:
                    continue
            if line == "|-damage|p1b: Kingambit|1/177":
                expected.append("|-crit|p1b: Kingambit")
            expected.append(line + sources.get(line, ""))
        self.assertEqual(starts, 3)
        self.assertEqual(replay["log"].splitlines(), expected)

    def test_trick_room_state_rejects_duplicates_and_accepts_reactivation(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)

            def with_room(transitions):
                changed = copy.deepcopy(battle)
                changed["events"][-1]["seq"] += len(transitions)
                changed["events"][-1:-1] = [
                    {"seq": 9 + i, "kind": kind, "value": value, "status": "consistent"}
                    for i, (kind, value) in enumerate(transitions)]
                return changed

            result = build_replay(with_room([
                ("fieldstart", "Trick Room"), ("fieldend", "move: Trick Room"),
                ("fieldstart", "move: Trick Room"), ("fieldend", "Trick Room"),
            ]), context)
            lines = [line for line in result["log"].splitlines() if "Trick Room" in line]
            self.assertEqual(lines, ["|-fieldstart|move: Trick Room", "|-fieldend|move: Trick Room"] * 2)
            for transitions in [
                [("fieldstart", "move: Trick Room"), ("fieldstart", "Trick Room")],
                [("fieldend", "move: Trick Room")],
                [("fieldstart", "Trick Room"), ("fieldend", "Trick Room"), ("fieldend", "Trick Room")],
            ]:
                with self.subTest(transitions=transitions), self.assertRaisesRegex(ReplayEvidenceError, "Trick Room"):
                    build_replay(with_room(transitions), context)
            # No automatic end at the winner: only observed transitions are emitted.
            active = build_replay(with_room([("fieldstart", "Trick Room")]), context)
            self.assertNotIn("|-fieldend|", active["log"])

    def test_hp_checkpoint_verifies_current_state_without_protocol_line(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            for item in battle["events"][4:]:
                item["seq"] += 1
                if isinstance(item.get("cause"), int):
                    item["cause"] += 1
            battle["events"].insert(4, {
                "seq": 5, "kind": "hp_checkpoint", "status": "consistent", "slot": "p2a",
                "actor_id": "p2-one", "health": "100/100", "hp_state": "confirmed", "frame": 4,
                "hp_support": {"state": "confirmed", "evidence": [{"frame": 4, "text": "100%"}]},
            })
            result = build_replay(battle, context)
            self.assertNotIn(5, [entry["ledger_seq"] for entry in
                                 result["ledger_source"]["protocol_lines"]])
            self.assertEqual(sum(line.startswith("|-damage|") for line in result["log"].splitlines()), 1)
            for field, value in (("health", "80/100"), ("hp_support", {"evidence": []})):
                broken = copy.deepcopy(battle)
                broken["events"][4][field] = value
                with self.assertRaisesRegex(ReplayEvidenceError, "Comprobación de PS contradictoria"):
                    build_replay(broken, context)

    def test_tailwind_exige_origen_y_cierre_del_mismo_lado(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            for event in battle["events"][5:]:
                event["seq"] += 2
                if isinstance(event.get("cause"), int):
                    event["cause"] += 2
            battle["events"][5:5] = [
                {"seq": 6, "kind": "move", "status": "consistent", "slot": "p2a",
                 "actor_id": "p2-one", "move": "Tailwind", "frame": 6, "turn": 1},
                {"seq": 7, "kind": "sidestart", "status": "consistent", "slot": "p2a",
                 "actor_id": "p2-one", "value": "move: Tailwind", "frame": 7, "turn": 1},
            ]
            battle["events"][-1]["seq"] = 12
            battle["events"][-1:-1] = [
                {"seq": 11, "kind": "sideend", "status": "consistent", "slot": "p2b",
                 "actor_id": None, "value": "move: Tailwind", "frame": 9, "turn": 1},
            ]
            lines = build_replay(battle, context)["log"].splitlines()
            self.assertLess(lines.index("|move|p2a: Garchomp|Tailwind|"),
                            lines.index("|-sidestart|p2: Benji|move: Tailwind"))
            self.assertIn("|-sideend|p2: Benji|move: Tailwind", lines)

            for field, value, message in [
                ("move", "Reflect", "sin movimiento acreditado"),
                ("slot", "p1a", "Actor fuera de su slot"),
                ("frame", 5, "sin movimiento acreditado"),
            ]:
                broken = copy.deepcopy(battle)
                broken["events"][5 if field == "move" else 6][field] = value
                with self.assertRaisesRegex(ReplayEvidenceError, message):
                    build_replay(broken, context)
            for slot, value in [("p1a", "move: Tailwind"),
                                ("p2b", "move: Reflect")]:
                broken = copy.deepcopy(battle)
                broken["events"][-2].update(slot=slot, value=value)
                with self.assertRaisesRegex(ReplayEvidenceError, "sin inicio acreditado"):
                    build_replay(broken, context)

            partner_slot = copy.deepcopy(battle)
            partner_slot["events"][5].update(slot="p2b", actor_id="p2-two")
            partner_slot["events"][6]["frame"] = 14
            partner_slot["events"][-2].update(slot="p2a", actor_id="p2-one", frame=15)
            partner_lines = build_replay(partner_slot, context)["log"].splitlines()
            self.assertLess(partner_lines.index("|move|p2b: Sneasler|Tailwind|"),
                            partner_lines.index("|-sidestart|p2: Benji|move: Tailwind"))
            self.assertIn("|-sideend|p2: Benji|move: Tailwind", partner_lines)
            broken = copy.deepcopy(partner_slot)
            broken["events"][6]["frame"] = 27
            with self.assertRaisesRegex(ReplayEvidenceError, "sin movimiento acreditado"):
                build_replay(broken, context)
            broken = copy.deepcopy(partner_slot)
            broken["events"][-2]["actor_id"] = "p2-two"
            with self.assertRaisesRegex(ReplayEvidenceError, "Actor fuera de su slot"):
                build_replay(broken, context)

    def test_tailwind_on_vacant_partner_slot_uses_side_of_confirmed_move(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            battle["events"][-1]["seq"] = 11
            battle["events"][-1:-1] = [
                {"seq": 9, "kind": "move", "status": "consistent", "slot": "p2a",
                 "actor_id": "p2-one", "move": "Tailwind", "frame": 8, "turn": 1},
                {"seq": 10, "kind": "sidestart", "status": "consistent", "slot": "p2b",
                 "actor_id": None, "value": "move: Tailwind", "frame": 9, "turn": 1},
            ]
            log = build_replay(battle, context)["log"].splitlines()
            self.assertIn("|-sidestart|p2: Benji|move: Tailwind", log)
            self.assertEqual(sum(line.startswith("|switch|p2b:") for line in log), 1)

            for field, value, expected in [
                ("slot", "p1b", "sin movimiento acreditado"),
                ("actor_id", "p2-one", "Actor fuera de su slot"),
                ("frame", 29, "sin movimiento acreditado"),
            ]:
                with self.subTest(field=field):
                    broken = copy.deepcopy(battle)
                    broken["events"][-2][field] = value
                    with self.assertRaisesRegex(ReplayEvidenceError, expected):
                        build_replay(broken, context)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_9EE3"), "Requiere diagnóstico 9ee3")
    def test_9ee3_tailwind_after_faint_exports_without_restoring_actor(self):
        from champions_automaton import BattleAutomaton, read_diagnostic, read_diagnostic_context

        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_9EE3"])
        frames, _ = read_diagnostic(path)
        battle = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertEqual(battle["issues"], [])
        condition = next(e for e in battle["events"] if e["kind"] == "sidestart")
        self.assertEqual((condition["seq"], condition["slot"], condition["actor_id"]),
                         (22, "p2a", None))
        log = build_replay(battle, load_trace_context(path, battle))["log"].splitlines()
        self.assertIn("|move|p2b: Pelipper|Tailwind|", log)
        self.assertIn("|-sidestart|p2: Angel|move: Tailwind", log)
        self.assertIn("|-sideend|p2: Angel|move: Tailwind", log)
        self.assertEqual(log[-1], "|win|Roku")

    def test_illusion_replace_preserves_actor_and_hp_without_a_switch(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            context = replace(context, teams={**context.teams, "p2": (
                "Zoroark-Hisui", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Excadrill")})
            battle["actors"]["p2-one"]["species"] = "Zoroark-Hisui"
            battle["events"][2].update(species="Zoroark-Hisui", display_species="Excadrill")
            battle["events"][5].update(slot="p2a", actor_id="p2-one", move="Shadow Ball", target_slot="p1a")
            battle["events"][6].update(slot="p2a", actor_id="p2-one", health="1/100",
                                       before="100/100", after="1/100")
            battle["events"][7].update(kind="illusion_reveal", slot="p2a", actor_id="p2-one",
                                       species="Zoroark-Hisui", health="1/100", before="1/100",
                                       after="1/100", hp_state="confirmed",
                                       narration=["The opposing Zoroark's illusion wore off!"])
            lines = build_replay(battle, context)["log"].splitlines()
            self.assertIn("|switch|p2a: Excadrill|Excadrill, L50|100/100", lines)
            self.assertIn("|-damage|p2a: Excadrill|1/100", lines)
            self.assertIn("|replace|p2a: Zoroark-Hisui|Zoroark-Hisui, L50|1/100", lines)
            self.assertEqual(sum(line.startswith("|switch|p2a:") for line in lines), 1)

            broken = copy.deepcopy(battle)
            broken["events"][7]["before"] = "100/100"
            with self.assertRaisesRegex(ReplayEvidenceError, "Revelación de Ilusión contradictoria"):
                build_replay(broken, context)
            broken = copy.deepcopy(battle)
            broken["events"][2]["display_species"] = "Garchomp"
            with self.assertRaisesRegex(ReplayEvidenceError, "Ilusión no acreditada"):
                build_replay(broken, context)

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
            context = load_trace_context(zip_path, battle)
            self.assertEqual(context.teams["p2"], ("Garchomp", "Sneasler"))
            self.assertFalse(context.team_evidence["p2"]["complete"])
            lines = build_replay(battle, context)["log"].splitlines()
            self.assertEqual(sum(line.startswith("|poke|p2|") for line in lines), 2)
            self.assertIn("|teamsize|p2|2", lines)
            self.assertNotIn("|teampreview", lines)
            self.assertEqual(lines[-1], "|win|Roku")

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

    def test_result_name_resolves_repeated_i_l_ocr_ambiguity(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            with zipfile.ZipFile(zip_path) as source:
                job = source.read("job.json")
                rows = [json.loads(raw) for raw in source.read("output/ocr.trace.jsonl").splitlines()]
            for row in rows:
                row["detections"]["players"]["p2"] = "lvannn"
            for row in rows[1:4]:
                row["detections"]["players"]["p2"] = "Ivannn"
            rows[-1]["ocr"][0]["text"] = "You defeated Ivannn!"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            context = load_trace_context(zip_path, battle)
            self.assertEqual((context.p2, context.winner), ("Ivannn", "Roku"))

            for row in rows[2:4]:
                row["detections"]["players"]["p2"] = "lvannn"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
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

    def test_reentrada_mega_y_sucesos_de_objeto_y_retroceso(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            battle["actors"]["p1-three"] = {"species": "Kingambit"}
            battle["events"][-1]["seq"] = 14
            battle["events"][-1]["frame"] = 12
            battle["events"][-1:-1] = [
                {"seq": 9, "kind": "enditem", "status": "consistent", "slot": "p1b",
                 "actor_id": "p1-two", "value": "Sitrus Berry"},
                {"seq": 10, "kind": "cant", "status": "consistent", "slot": "p1a",
                 "actor_id": "p1-one", "value": "flinch"},
                {"seq": 11, "kind": "mega", "status": "consistent", "slot": "p1a",
                 "actor_id": "p1-one", "species": "Blaziken-Mega", "value": "Blazikenite"},
                {"seq": 12, "kind": "switch", "status": "consistent", "slot": "p1a",
                 "actor_id": "p1-three", "species": "Kingambit", "health": "100/100"},
                {"seq": 13, "kind": "switch", "status": "consistent", "slot": "p1a",
                 "actor_id": "p1-one", "species": "Blaziken-Mega", "health": None},
            ]
            document = build_replay(battle, context)
            lines = document["log"].splitlines()
            self.assertIn("|-enditem|p1b: Indeedee-F|Sitrus Berry", lines)
            self.assertIn("|cant|p1a: Blaziken|flinch", lines)
            self.assertIn("|switch|p1a: Blaziken|Blaziken-Mega, L50|156/156", lines)
            self.assertEqual(document["ledger_source"]["inferred_entry_health"], [
                {"ledger_seq": 13, "source": "last_known_health", "health": "156/156",
                 "ledger_last_confirmed_health": None}])

    def test_primera_entrada_inferida_exige_hud_confirmado_al_maximo(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            context = load_trace_context(zip_path, battle)
            battle["events"][0].update(health=None, hp_state="inferred")
            battle["events"][-1]["seq"] = 10
            battle["events"].insert(-1, {
                "seq": 9, "kind": "damage", "status": "consistent", "slot": "p1a",
                "actor_id": "p1-one", "before": "156/156", "after": "100/156",
                "health": "100/156", "hp_state": "confirmed", "frame": 9,
                "hp_baseline": {"state": "confirmed", "evidence": [
                    {"frame": 8, "text": "156/156", "nickname": "blaziken"}]},
            })
            document = build_replay(battle, context)
            self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|156/156", document["log"])
            self.assertEqual(document["ledger_source"]["inferred_entry_health"][0]["hp_ledger_seq"], 9)
            battle["events"][-2]["hp_baseline"]["state"] = "unconfirmed"
            with self.assertRaisesRegex(ReplayEvidenceError, "cambio 1 no tiene PS completos"):
                build_replay(battle, context)

    def test_trainer_generico_en_el_anuncio_del_resultado(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            with zipfile.ZipFile(zip_path) as source:
                job = source.read("job.json")
                rows = [json.loads(raw) for raw in source.read("output/ocr.trace.jsonl").splitlines()]
            for row in rows:
                row["detections"]["players"]["p2"] = "Trainer"
            rows[-1]["ocr"] = [{"text": "You lost to the Trainer!", "confidence": .999}]
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            context = load_trace_context(zip_path, battle)
            self.assertEqual(context.winner, "Trainer")
            self.assertTrue(build_replay(battle, context)["log"].endswith("|win|Trainer"))
            rows[-1]["ocr"][0]["text"] = "You lost to the Rival!"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ReplayEvidenceError, "ganador único"):
                load_trace_context(zip_path, battle)

    def test_alias_corrobora_hud_intermedio_del_primer_golpe(self):
        with tempfile.TemporaryDirectory() as temp:
            battle, zip_path = _pilot_fixture(Path(temp))
            with zipfile.ZipFile(zip_path) as source:
                job = source.read("job.json")
                rows = [json.loads(raw) for raw in source.read("output/ocr.trace.jsonl").splitlines()]
            for row in rows:
                row["resolved_aliases"] = {"p1": {"hotbird": "Blaziken"}, "p2": {}}
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(map(json.dumps, rows)))
            battle["events"][0].update(health=None, hp_state="inferred", frame=1, logical_frame=1)
            battle["events"][5].update(slot="p2a", actor_id="p2-one", target_slot="p1a", frame=6)
            battle["events"][6].update(
                slot="p1a", actor_id="p1-one", species="Blaziken", health="81/156",
                before="92/156", after="81/156", frame=8, hp_state="confirmed",
                hp_baseline={"state": "confirmed", "evidence": [
                    {"frame": 7, "text": "92/156", "nickname": "hotbird"}]})
            battle["events"].pop(7)
            battle["events"][-1]["seq"] = 8
            context = load_trace_context(zip_path, battle)
            document = build_replay(battle, context)
            self.assertEqual(document["ledger_source"]["inferred_entry_health"][0]["health"], "156/156")
            self.assertEqual(document["ledger_source"]["intermediate_baselines"][0]["hud_frame"], 7)
            self.assertIn("|-damage|p1a: Blaziken|81/156", document["log"])

            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("job.json", job)
                archive.writestr("output/ocr.trace.jsonl", "\n".join(json.dumps({
                    **row, "resolved_aliases": {"p1": {}, "p2": {}}}) for row in rows))
            without_alias = load_trace_context(zip_path, battle)
            with self.assertRaisesRegex(ReplayEvidenceError, "cambio 1 no tiene PS completos"):
                build_replay(battle, without_alias)

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_8499") and os.getenv("CHAMPIONS_LEDGER_8499"),
                         "requiere diagnóstico y Ledger 8499")
    def test_tailwind_del_diagnostico_real(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_8499"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_8499"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual(document["ledger_source"]["consistent_events"], 96)
        self.assertEqual((sum(line.startswith("|poke|p1|") for line in lines),
                          sum(line.startswith("|poke|p2|") for line in lines)), (6, 6))
        self.assertEqual((sum(line.startswith("|turn|") for line in lines),
                          sum(line.startswith("|move|") for line in lines),
                          sum(line.startswith("|faint|") for line in lines)), (9, 26, 7))
        self.assertLess(lines.index("|move|p2a: Pelipper|Tailwind|"),
                        lines.index("|-sidestart|p2: 3st|move: Tailwind"))
        self.assertLess(lines.index("|-sidestart|p2: 3st|move: Tailwind"),
                        lines.index("|-sideend|p2: 3st|move: Tailwind"))
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_44FF") and os.getenv("CHAMPIONS_LEDGER_44FF"),
                         "requiere diagnóstico y Ledger 44ff")
    def test_replay_real_con_texto_de_menu_terrain_pulse(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_44FF"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_44FF"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual(document["ledger_source"]["consistent_events"], 44)
        self.assertEqual((sum(line.startswith("|poke|p1|") for line in lines),
                          sum(line.startswith("|poke|p2|") for line in lines)), (6, 6))
        self.assertEqual((sum(line.startswith("|turn|") for line in lines),
                          sum(line.startswith("|move|") for line in lines),
                          sum(line.startswith("|faint|") for line in lines)), (5, 15, 1))
        self.assertNotIn("Torrain Pulse", document["log"])
        self.assertEqual(sum("|Terrain Pulse|" in line for line in lines), 1)
        self.assertEqual(document["ledger_source"]["winner_evidence"]["frame"], 611)
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_AD28") and os.getenv("CHAMPIONS_LEDGER_AD28"),
                         "requiere el diagnóstico y Ledger real de ad28")
    def test_floette_real_no_depende_del_replay_archivado(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_AD28"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_AD28"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual(document["ledger_source"]["consistent_events"], 103)
        self.assertIn("|detailschange|p2b: Floette-Eternal|Floette-Mega, L50|100/100", lines)
        self.assertEqual(document["ledger_source"]["winner_evidence"]["text"],
                         "You lost to the Trainer!")
        self.assertEqual(lines[-1], "|win|Trainer")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_CC5298") and os.getenv("CHAMPIONS_LEDGER_CC5298"),
                         "requiere el diagnóstico rechazado por producción y Ledger cc5298")
    def test_replay_de_batalla_rechazada_por_parser_productivo(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_CC5298"]).read_text())
        diagnostic = Path(os.environ["CHAMPIONS_DIAGNOSTIC_CC5298"])
        with zipfile.ZipFile(diagnostic) as archive:
            self.assertFalse(any(name.startswith("output/replay-") for name in archive.namelist()))
        document = build_replay(battle, load_trace_context(diagnostic, battle))
        lines = document["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual((document["log"].count("|poke|p1|"),
                          document["log"].count("|poke|p2|")), (6, 6))
        self.assertIn("|-ability|p2b: Incineroar|Intimidate", lines)
        self.assertEqual(sum(line.startswith("|faint|") for line in lines), 1)
        self.assertEqual(document["ledger_source"]["winner_evidence"]["text"],
                         "You defeated ゆぐりか!")
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_472247") and os.getenv("CHAMPIONS_LEDGER_472247"),
                         "requiere diagnóstico y Ledger 472247")
    def test_replay_real_con_ilusion_y_hud_desplazado(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_472247"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_472247"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertEqual(battle["issues"], [])
        self.assertEqual((document["log"].count("|poke|p1|"),
                          document["log"].count("|poke|p2|")), (6, 6))
        self.assertIn("|switch|p2a: Excadrill|Excadrill, L50|100/100", lines)
        self.assertIn("|replace|p2a: Zoroark-Hisui|Zoroark-Hisui, L50|1/100", lines)
        self.assertLess(lines.index("|-damage|p2a: Excadrill|1/100"),
                        lines.index("|replace|p2a: Zoroark-Hisui|Zoroark-Hisui, L50|1/100"))
        self.assertIn("|-damage|p2a: Excadrill|68/100", lines)
        self.assertNotIn("|-damage|p2a: Excadrill|0/100\n|-heal|p2a: Excadrill|100/100", document["log"])
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_0F4F") and os.getenv("CHAMPIONS_LEDGER_0F4F"),
                         "requiere diagnóstico y Ledger 0f4f")
    def test_toxtricity_forme_requires_unique_team_and_preimpact_hud(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_0F4F"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_0F4F"]), battle)
        document = build_replay(battle, context)
        self.assertFalse(battle["issues"])
        self.assertEqual(document["ledger_source"]["intermediate_baselines"], [{
            "ledger_seq": 24, "causing_move_seq": 22, "inferred_entry": "100/100",
            "intermediate": "94/100", "final": "81/100", "hud_frame": 390,
            "identity_support": {"source": "unique_team_form", "hud_name": "toxtricity",
                                 "species": "Toxtricity-Low-Key"},
        }])
        lines = document["log"].splitlines()
        self.assertEqual((sum(s.startswith("|poke|p1|") for s in lines),
                          sum(s.startswith("|poke|p2|") for s in lines)), (6, 6))
        self.assertEqual((sum(s.startswith("|turn|") for s in lines),
                          sum(s.startswith("|move|") for s in lines),
                          sum(s.startswith("|faint|") for s in lines)), (7, 22, 7))
        self.assertIn("|-damage|p2a: Toxtricity-Low-Key|81/100", lines)
        self.assertFalse(any(s == "|-damage|p2a: Toxtricity-Low-Key|94/100" for s in lines))
        self.assertEqual(lines[-1], "|win|Roku")

        ambiguous = replace(context, teams={**context.teams, "p2":
                             context.teams["p2"][:-1] + ("Toxtricity-Amped",)})
        with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos en suceso 24"):
            build_replay(battle, ambiguous)
        bad_hud = copy.deepcopy(battle)
        bad_hud["events"][23]["hp_baseline"]["evidence"][0]["text"] = "95%"
        with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos en suceso 24"):
            build_replay(bad_hud, context)
        late_action = copy.deepcopy(battle)
        late_action["events"][21]["frame"] = 391
        with self.assertRaisesRegex(ReplayEvidenceError, "PS no continuos en suceso 24"):
            build_replay(late_action, context)

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

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_B3F7") and os.getenv("CHAMPIONS_LEDGER_B3F7"),
                         "requiere diagnóstico y Ledger b3f7")
    def test_replay_b3f7_sin_entrada_falsa_y_con_tailwind(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_B3F7"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_B3F7"]), battle)
        document = build_replay(battle, context)
        lines = document["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual(document["ledger_source"]["consistent_events"], 102)
        self.assertEqual((sum(line.startswith("|poke|p1|") for line in lines),
                          sum(line.startswith("|poke|p2|") for line in lines)), (6, 6))
        self.assertEqual((sum(line.startswith("|turn|") for line in lines),
                          sum(line.startswith("|move|") for line in lines),
                          sum(line.startswith("|faint|") for line in lines)), (9, 27, 7))
        self.assertEqual(sum(line.startswith("|switch|p2a: Archaludon|") for line in lines), 1)
        self.assertIn("|-heal|p2a: Archaludon|12/100", lines)
        self.assertLess(lines.index("|move|p2b: Pelipper|Tailwind|"),
                        lines.index(f"|-sidestart|p2: {context.p2}|move: Tailwind"))
        self.assertEqual(lines[-1], "|win|Roku")

    @unittest.skipUnless(os.getenv("CHAMPIONS_DIAGNOSTIC_7F41") and os.getenv("CHAMPIONS_LEDGER_7F41"),
                         "requiere diagnóstico y Ledger 7f41")
    def test_replay_7f41_preserves_lethal_damage_status_and_miss(self):
        battle = json.loads(Path(os.environ["CHAMPIONS_LEDGER_7F41"]).read_text())
        context = load_trace_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_7F41"]), battle)
        lines = build_replay(battle, context)["log"].splitlines()
        self.assertFalse(battle["issues"])
        self.assertEqual(context.p2, "Ivannn")
        self.assertLess(lines.index("|-damage|p2b: Indeedee|0/100"),
                        lines.index("|faint|p2b: Indeedee"))
        self.assertIn("|-status|p1a: Indeedee-F|slp", lines)
        self.assertIn("|-curestatus|p1a: Indeedee-F|slp", lines)
        self.assertIn("|move|p2a: Tyranitar|Rock Slide|p1b: Rillaboom", lines)
        self.assertIn("|-miss|p2a: Tyranitar|p1b: Rillaboom", lines)
        self.assertEqual(lines[-1], "|win|Ivannn")


class MissingDetectorContextTest(unittest.TestCase):
    def fixture(self, directory):
        battle, path = _pilot_fixture(Path(directory))
        with zipfile.ZipFile(path) as archive:
            job = json.loads(archive.read("job.json"))
            rows = [json.loads(line) for line in archive.read("output/ocr.trace.jsonl").splitlines()]
        for event in battle["events"][:4]: event["frame"] = 4
        for row in rows: row["detections"]["players"] = {"p1": None, "p2": None}
        for row in rows[:2]:
            row["ocr"] = [{"text": name, "confidence": .999, "left": left, "top": .91}
                          for name, left in (("Alba", .3), ("Nadir", .6))]
        for row in rows[2:4]:
            row["ocr"] = [{"text": "Nadir sent out Garchomp and Sneasler!", "confidence": .999}]
        rows[-1]["ocr"][0]["text"] = "You defeated Nadir!"
        return battle, job, rows

    def test_repeated_player_pair_and_entry_recover_empty_detector_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, job, rows = self.fixture(directory)
            context = build_trace_context(job, rows, battle)
            self.assertEqual((context.p1, context.p2, context.winner), ("Alba", "Nadir", "Alba"))
            self.assertEqual(context.player_evidence["p1"]["frames"], [1, 2])
            self.assertEqual(context.player_evidence["p2"]["rival_entry_frames"], [3, 4])
            replay = build_replay(battle, context)
            self.assertEqual(replay["ledger_source"]["players"], context.player_evidence)
            self.assertEqual(replay["log"].splitlines()[-1], "|win|Alba")

    def test_names_require_repetition_position_same_battle_and_rival_entry(self):
        for problem in ("single_pair", "weak_name", "wrong_position", "different_rival",
                        "single_announcement", "foreign_battle", "outside_bounds", "time_gap",
                        "duplicate_label", "different_own_name", "unaccepted_entry"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                battle, job, rows = self.fixture(directory)
                if problem == "single_pair": rows[1]["ocr"] = []
                if problem == "weak_name": rows[1]["ocr"][0]["confidence"] = .6
                if problem == "wrong_position": rows[1]["ocr"][0]["top"] = .5
                if problem == "different_rival": rows[1]["ocr"][1]["text"] = "Other Rival"
                if problem == "single_announcement": rows[3]["ocr"] = []
                if problem == "foreign_battle": rows[1]["battle_index"] = 1
                if problem == "outside_bounds": battle["first_frame"] = 2
                if problem == "time_gap":
                    rows[0]["timestamp_ms"] = 0; rows[1]["timestamp_ms"] = 10000
                if problem == "duplicate_label": rows[1]["ocr"].append(dict(rows[1]["ocr"][0]))
                if problem == "different_own_name": rows[1]["ocr"][0]["text"] = "Someone Else"
                if problem == "unaccepted_entry":
                    for event in battle["events"][2:4]: event["status"] = "suppressed"
                with self.assertRaisesRegex(ReplayEvidenceError, "La traza no confirma"):
                    build_trace_context(job, rows, battle)

    def test_disagreeing_confirmed_player_and_ambiguous_pairs_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, job, rows = self.fixture(directory)
            for row in rows: row["detections"]["players"]["p2"] = "Another Rival"
            with self.assertRaisesRegex(ReplayEvidenceError, "contradicen"):
                build_trace_context(job, rows, battle)
            for row in rows: row["detections"]["players"]["p2"] = None
            for event in battle["events"][:4]: event["frame"] = 7
            rows[4]["ocr"] = copy.deepcopy(rows[0]["ocr"])
            rows[5]["ocr"] = copy.deepcopy(rows[0]["ocr"])
            for row in rows[4:6]: row["ocr"][0]["text"] = "Other Player"
            with self.assertRaisesRegex(ReplayEvidenceError, "La traza no confirma"):
                build_trace_context(job, rows, battle)

    def test_partial_participants_preserve_real_identity_and_require_exact_members(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, job, rows = self.fixture(directory)
            for row in rows:
                row["detections"]["team_preview"] = False
                row["detections"]["teams"] = {}
            context = build_trace_context(job, rows, battle)
            self.assertEqual(context.teams["p2"], ("Garchomp", "Sneasler"))
            self.assertEqual(context.team_evidence["p2"]["source"], "ledger_participants")
            evidence = context.team_evidence["p2"]["participants"]
            self.assertEqual([item["actor_id"] for item in evidence], ["p2-one", "p2-two"])
            lines = build_replay(battle, context)["log"].splitlines()
            self.assertIn("|teamsize|p2|2", lines)
            self.assertNotIn("|teampreview", lines)
            # Extra unseen members and omitted real members cannot be labelled observed.
            for roster in (("Garchomp",), ("Garchomp", "Sneasler", "Charizard")):
                with self.subTest(roster=roster), self.assertRaises(ReplayEvidenceError):
                    build_replay(battle, replace(context, teams={**context.teams, "p2": roster}))
            battle["actors"]["p2-one"]["species"] = "Zoroark-Hisui"
            battle["events"][2].update(species="Zoroark-Hisui", display_species="Charizard")
            context = build_trace_context(job, rows, battle)
            self.assertEqual(context.teams["p2"][0], "Zoroark-Hisui")
            with self.assertRaisesRegex(ReplayEvidenceError, "Ilusión no acreditada"):
                build_replay(battle, context)
            battle["events"][2]["display_species"] = "Sneasler"
            self.assertIn("|switch|p2a: Sneasler", build_replay(battle, context)["log"])

    def test_partial_team_needs_accepted_actors_and_cannot_bypass_conflicting_preview(self):
        for problem in ("no_participants", "missing_actor", "five_participants", "single_preview"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                battle, path = _pilot_fixture(Path(directory))
                with zipfile.ZipFile(path) as archive:
                    job = json.loads(archive.read("job.json"))
                    rows = [json.loads(line) for line in archive.read("output/ocr.trace.jsonl").splitlines()]
                for row in rows[:2]: row["detections"]["teams"]["p2"] = []
                if problem == "no_participants":
                    for event in battle["events"][2:4]: event["status"] = "suppressed"
                if problem == "missing_actor": del battle["actors"]["p2-one"]
                if problem == "five_participants":
                    for number, species in enumerate(("Charizard", "Whimsicott", "Pelipper"), 10):
                        battle["actors"][str(number)] = {"species": species}
                        battle["events"].append({"seq": number, "kind": "switch", "slot": "p2a",
                                                 "actor_id": str(number), "status": "consistent"})
                if problem == "single_preview":
                    rows[0]["detections"]["teams"]["p2"] = [
                        "Garchomp", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Pelipper"]
                with self.assertRaises(ReplayEvidenceError): build_trace_context(job, rows, battle)


class PreviewSpriteEvidenceTest(unittest.TestCase):
    def fixture(self, directory):
        battle, path = _pilot_fixture(Path(directory))
        with zipfile.ZipFile(path) as archive:
            job = json.loads(archive.read("job.json"))
            rows = [json.loads(line) for line in archive.read("output/ocr.trace.jsonl").splitlines()]
        for row in rows[:2]:
            row["preview_identity_evidence"] = {side: [{"species_votes": {s: 2}}
                                                         for s in row["detections"]["teams"][side]]
                                                for side in ("p1", "p2")}
            row["detections"]["teams"] = {}
        return battle, job, rows

    def test_independent_sprite_votes_confirm_roster_without_repeated_flush(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, job, rows = self.fixture(directory)
            context = build_trace_context(job, rows, battle)
            self.assertEqual(context.team_evidence["p2"]["source"], "preview_sprite_votes")
            self.assertEqual(context.team_evidence["p2"]["votes"], 2)
            self.assertEqual(context.team_evidence["p2"]["frames"], [1])
            self.assertIn("|poke|p2|Garchomp", build_replay(battle, context)["log"])

    def test_copies_of_a_single_sprite_vote_do_not_confirm_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            battle, job, rows = self.fixture(directory)
            for row in rows[:2]:
                for cards in row["preview_identity_evidence"].values():
                    for card in cards:
                        card["species_votes"] = {s: 1 for s in card["species_votes"]}
            with self.assertRaisesRegex(ReplayEvidenceError, "Falta el equipo completo.*p2"):
                build_trace_context(job, rows, battle)

    def test_conflicting_or_partial_cards_cannot_choose_a_roster(self):
        for problem in ("competing_vote", "missing_card", "different_snapshot"):
            with tempfile.TemporaryDirectory() as directory:
                battle, job, rows = self.fixture(directory)
                for row in rows[:2]:
                    cards = row["preview_identity_evidence"]["p2"]
                    if problem == "competing_vote": cards[0]["species_votes"]["Kingambit"] = 1
                    if problem == "missing_card": cards.pop()
                if problem == "different_snapshot":
                    rows[1]["preview_identity_evidence"]["p2"][0]["species_votes"] = {"Kingambit": 2}
                with self.subTest(problem=problem), self.assertRaises(ReplayEvidenceError):
                    build_trace_context(job, rows, battle)

if __name__ == "__main__":
    unittest.main()
