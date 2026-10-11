"""Evidence-based continuity across OCR confidence and corrected HP baselines."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from champions_automaton import BattleAutomaton, read_diagnostic, read_diagnostic_context
from test_champions_automaton import delayed_damage_endpoint_trace, event, frame


class NarrationEpisodeTests(unittest.TestCase):
    def trace(self, confidence=.999, effect="restoration", nickname="Rillaboom"):
        ending = {"restoration": "had its HP restored.", "burn": "was hurt by its burn!",
                  "recoil": "was damaged by the recoil!"}[effect]
        clean = f"The opposing {nickname} {ending}"
        damaged = f"The opposing {nickname[:-1]} {ending}"
        rows = [frame(n) for n in range(1, 9)]
        rows[0] = frame(1, [event("switch", "p2a", "Rillaboom", "50/100"), event("turn", turn=1)])
        rows[2] = frame(3, [event("heal" if effect == "restoration" else "damage", "p2a", "Rillaboom",
                                "56/100" if effect == "restoration" else "44/100")])
        rows[3] = frame(4, [event("message", value=clean)], [clean])
        rows[4] = frame(5, [{**event("message", value=damaged), "confidence": confidence}], [damaged])
        rows[4]["ocr"][0]["confidence"] = confidence
        rows[5] = frame(6, texts=[damaged])
        rows[6] = frame(7, [event("message", value=clean)], [clean])
        rows[7] = frame(8, [event("move", "p2a", "Rillaboom", move="Protect")])
        if nickname != "Rillaboom":
            for row in rows:
                row["resolved_aliases"]["p2"][nickname.casefold()] = "Rillaboom"
        return rows

    def test_one_episode_survives_for_all_hp_effects_even_with_high_ocr_confidence(self):
        for effect in ("restoration", "burn", "recoil"):
            for confidence in (.6, .98961, 1.0):
                with self.subTest(effect=effect, confidence=confidence):
                    rows = self.trace(confidence, effect)
                    ledger = BattleAutomaton(0, rows).run()
                    self.assertEqual(ledger["issues"], [])
                    bad = next(link for link in ledger["narration_links"] if link["frame"] == 5)
                    self.assertEqual(bad["status"], "superseded")
                    self.assertEqual(bad["event_seq"], ledger["narration_links"][0]["event_seq"])
                    self.assertEqual(bad["confidence"], confidence)
                    self.assertIn("Rillaboo ", bad["text"])
                    self.assertEqual(len([e for e in ledger["events"] if e["kind"] in {"damage", "heal"}]), 1)

    def test_nickname_variant_is_local_to_the_confirmed_episode(self):
        ledger = BattleAutomaton(0, self.trace(nickname="Northstar")).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(ledger["narration_links"][1]["status"], "superseded")
        self.assertIn("Northsta ", ledger["narration_links"][1]["text"])

    def test_two_compatible_actors_do_not_select_a_path(self):
        rows = self.trace()
        rows[0] = frame(1, [event("switch", "p2a", "Rillaboom", "50/100"),
                             event("switch", "p2b", "Farigiraf", "50/100"), event("turn", turn=1)])
        rows[2] = frame(3, [event("heal", "p2a", "Rillaboom", "56/100"),
                             event("heal", "p2b", "Farigiraf", "56/100")])
        second = "The opposing Rillaboon had its HP restored."
        for row in rows: row["resolved_aliases"]["p2"]["rillaboon"] = "Farigiraf"
        for number in (4, 7):
            rows[number - 1]["detections"]["events"].append(event("message", value=second))
            rows[number - 1]["ocr"].append({"text": second, "top": .75, "confidence": .999})
        rows[4]["detections"]["events"][0]["value"] = "The opposing Rillabor had its HP restored."
        rows[4]["ocr"][0]["text"] = rows[4]["detections"]["events"][0]["value"]
        ledger = BattleAutomaton(0, rows).run()
        self.assertTrue(any(i["code"] == "hp_narration_unmatched" and i["frame"] == 5 for i in ledger["issues"]))

    def test_incomplete_or_conflicting_paths_remain_open(self):
        for problem in ("single_anchor", "weak_anchor", "different_name", "known_nickname", "wrong_side",
                        "wrong_effect", "gap", "new_action", "raw_action", "turn", "replacement"):
            with self.subTest(problem=problem):
                rows = self.trace()
                bad = rows[4]["detections"]["events"][0]
                if problem == "single_anchor": rows[6] = frame(7)
                if problem == "weak_anchor": rows[6]["ocr"][0]["confidence"] = .6
                if problem == "different_name": bad["value"] = "The opposing Unrelated had its HP restored."
                if problem == "known_nickname":
                    for row in rows: row["resolved_aliases"]["p2"]["rillaboo"] = "Farigiraf"
                if problem == "wrong_side": bad["value"] = bad["value"].removeprefix("The opposing ")
                if problem == "wrong_effect": bad["value"] = "The opposing Rillaboo was hurt by its burn!"
                if problem == "gap": rows[5]["timestamp_ms"] += 2000
                if problem == "new_action": rows[5] = frame(6, [event("move", "p2a", "Rillaboom", move="Protect")])
                if problem == "raw_action": rows[5]["ocr"].append({"text": "The opposing Rillaboom used Protect!", "top": .75, "confidence": .999})
                if problem == "turn": rows[5] = frame(6, [event("turn", turn=2)])
                if problem == "replacement": rows[5] = frame(6, [event("switch", "p2a", "Farigiraf", "100/100")])
                rows[4]["ocr"][0]["text"] = bad["value"]
                ledger = BattleAutomaton(0, rows).run()
                self.assertTrue(any(i["code"] == "hp_narration_unmatched" and i["frame"] == 5 for i in ledger["issues"]))


class CorrectedBaselineTests(unittest.TestCase):
    def test_late_endpoint_becomes_an_observation_and_next_real_damage_survives(self):
        rows = delayed_damage_endpoint_trace()
        rows[31] = frame(32, [event("heal", "p1a", "Pikachu", "132/200")])
        rows[31]["ocr"].append({"text": "Pikachu", "left": .08, "top": .86, "confidence": .999})
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual(ledger["issues"], [])
        original = next(e for e in ledger["events"] if e["frame"] == 23)
        endpoint = next(e for e in ledger["events"] if e["frame"] == 32)
        damage = next(e for e in ledger["events"] if e["frame"] == 35)
        self.assertEqual((original["before"], original["after"]), ("120/200", "132/200"))
        self.assertEqual((endpoint["before"], endpoint["after"], endpoint["status"]), ("132/200", "132/200", "suppressed"))
        self.assertEqual(endpoint["original_transition"], {"before": "125/200", "after": "132/200"})
        self.assertEqual(endpoint["resolution"]["event_seq"], original["seq"])
        self.assertEqual((damage["before"], damage["after"], damage["status"]), ("132/200", "100/200", "consistent"))

    def test_a_second_real_heal_is_preserved(self):
        rows = delayed_damage_endpoint_trace()
        rows[31] = frame(32, [event("heal", "p1a", "Pikachu", "140/200")])
        ledger = BattleAutomaton(0, rows).run()
        healing = next(e for e in ledger["events"] if e["frame"] == 32)
        self.assertEqual((healing["before"], healing["after"], healing["status"]), ("132/200", "140/200", "consistent"))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_99A0"), "diagnóstico opcional 99a0")
    def test_both_automata_export_from_the_same_archived_ocr(self):
        from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_99A0"])
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as directory:
            trace_bytes = archive.read("output/ocr.trace.jsonl")
            original = json.loads(archive.read("output/ledger-battle-001.json"))
            job = json.loads(archive.read("job.json"))
            root = Path(directory); trace = root / "ocr.trace.jsonl"
            trace.write_bytes(trace_bytes)
            docs = documents_from_trace(trace, job, root / "output")
            self.assertEqual(hashlib.sha256(trace.read_bytes()).digest(), hashlib.sha256(trace_bytes).digest())
            self.assertEqual(len(docs), 1)
            self.assertEqual((docs[0].p1, docs[0].p2), ("Roku", "TC3"))
            self.assertTrue(docs[0].log.endswith("|win|Roku"))
            ledger = json.loads((root / "output/ledger-battle-001.json").read_text())
            self.assertEqual(ledger["issues"], [])
            # Every candidate stays auditable. Only the zero-change HP
            # transition is removed from the accepted battle sequence.
            fields = ("kind", "actor_id", "slot", "species", "move", "before", "after")
            self.assertEqual([{k: e.get(k) for k in fields} for e in original["events"]],
                             [{k: e.get(k) for k in fields} for e in ledger["events"]])
            self.assertEqual([(a["seq"], a["status"], b["status"]) for a, b in zip(original["events"], ledger["events"])
                              if a["status"] != b["status"]], [(32, "consistent", "suppressed")])


if __name__ == "__main__":
    unittest.main()
