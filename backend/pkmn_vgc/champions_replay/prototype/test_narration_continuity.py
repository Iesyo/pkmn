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

    def test_unbound_readings_are_discarded_without_selecting_an_episode(self):
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
                self.assertFalse(any(i["code"] == "hp_narration_unmatched" and i["frame"] == 5 for i in ledger["issues"]))
                link = next(link for link in ledger["narration_links"] if link["frame"] == 5)
                self.assertEqual(link["status"], "discarded")
                self.assertIsNone(link["event_seq"])


class OrphanStateTests(unittest.TestCase):
    def test_discarded_wrong_occupant_does_not_keep_a_blocking_issue(self):
        rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"), event("turn", turn=1)]),
                frame(2, [event("mega", "p1a", "Charizard", value="Charizardite X")]),
                frame(3, [event("move", "p1a", "Pikachu", move="Protect")])]
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual(ledger["issues"], [])
        discarded = next(e for e in ledger["events"] if e["kind"] == "mega")
        self.assertEqual(discarded["status"], "suppressed")
        self.assertIsNone(next(iter(ledger["actors"].values()))["forme"])
        self.assertEqual(ledger["resolved_issues"][0]["resolution"]["to"], "discarded")

    def test_repeated_isolated_text_does_not_acquire_continuity(self):
        text = "An unrelated and incomplete observation"
        rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"), event("turn", turn=1)]),
                frame(2, [event("message", value=text)], [text]),
                frame(3, [event("message", value=text)], [text]),
                frame(4, [event("move", "p1a", "Pikachu", move="Protect")])]
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual(ledger["issues"], [])
        node = next(e for e in ledger["events"] if e["kind"] == "unclassified_text")
        self.assertEqual((node["status"], node["value"]), ("suppressed", text))
        self.assertEqual(node["resolution"]["live_event_seqs"], [])

    def test_named_live_actor_and_competing_episodes_still_require_resolution(self):
        rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"), event("turn", turn=1)]),
                frame(2, [event("message", value="Pikachu had its HP restored.")],
                      ["Pikachu had its HP restored."]),
                frame(3, [event("move", "p1a", "Pikachu", move="Protect")])]
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual([i["code"] for i in ledger["issues"]], ["hp_narration_unmatched"])
        self.assertEqual(ledger["narration_links"][0]["status"], "unmatched")

    def test_a_terminal_battle_state_needs_no_successor(self):
        rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"), event("turn", turn=1)]),
                frame(2, [event("battle_end")])]
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(ledger["events"][-1]["status"], "consistent")

    def test_a_component_of_provisional_hp_states_without_an_actor_is_discarded(self):
        rows = [frame(1, [event("turn", turn=1)]),
                frame(2, [event("damage", "p2a", "Pikachu", "75/100")]),
                frame(3, [event("heal", "p2a", "Pikachu", "80/100")]),
                frame(4, [event("battle_end")])]
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual(ledger["issues"], [])
        hp = [e for e in ledger["events"] if e["kind"] in {"damage", "heal"}]
        self.assertEqual(len(hp), 2)
        self.assertTrue(all(e["status"] == "suppressed" and e["actor_id"] is None for e in hp))
        self.assertTrue(all(i["resolution"]["orphaned"] for i in ledger["resolved_issues"]))
        self.assertEqual(ledger["actors"], {})

    def test_a_live_descendant_keeps_its_unresolved_causal_ancestor(self):
        rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"), event("turn", turn=1)]),
                frame(2, [event("move", "p1a", "Pikachu", move="Protect")]),
                frame(3, [event("damage", "p1a", "Pikachu", "75/100")])]
        machine = BattleAutomaton(0, rows)
        machine.run()
        action = next(e for e in machine.events if e["kind"] == "move")
        action.update(status="review", actor_id=None, evidence=[])
        effect = next(e for e in machine.events if e["kind"] == "damage")
        effect["cause"] = action["seq"]
        machine._issue("actor_mismatch", "Acción pendiente con un efecto vivo.", action["frame"], action["seq"])
        machine._prune_orphaned_states()
        self.assertEqual(action["status"], "review")
        self.assertEqual(machine.issues[0]["event_seq"], action["seq"])
        self.assertEqual(effect["cause"], action["seq"])

    def test_unbound_effect_with_a_real_source_is_connected_and_still_pending(self):
        for source_present in (False, True):
            with self.subTest(source_present=source_present):
                rows = [frame(1, [event("switch", "p1a", "Pikachu", "100/100"),
                                   event("switch", "p2a", "Tyranitar", "100/100"), event("turn", turn=1)]),
                        frame(2, [event("move", "p2a", "Tyranitar",
                                        move="Knock Off" if source_present else "Protect")]),
                        frame(3, [{**event("enditem", "p1b", value="Life Orb"),
                                   "tags": ["[from] move: Knock Off", "[of] p2a: Tyranitar"]}])]
                ledger = BattleAutomaton(0, rows).run()
                effect = next(e for e in ledger["events"] if e["kind"] == "enditem")
                self.assertIsNone(effect["actor_id"])
                self.assertEqual(effect["status"], "review" if source_present else "suppressed")
                self.assertEqual([i["code"] for i in ledger["issues"]],
                                 ["unbound_item_actor"] if source_present else [])
                self.assertFalse(any(actor["item_lost"] for actor in ledger["actors"].values()))


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


class ClippedHpNumeratorTests(unittest.TestCase):
    def trace(self, slot="p1a", stable="77/167", clipped="7/167", nickname="Whimsicott", confidence=.91207):
        rows = [frame(n) for n in range(1, 8)]
        rows[0] = frame(1, [event("switch", slot, "Whimsicott", stable), event("turn", turn=1)])
        rows[3] = frame(4, [event("damage", slot, "Whimsicott", clipped)])
        rows[4] = frame(5, [event("heal", slot, "Whimsicott", stable)])
        rows[6] = frame(7, [event("move", slot, "Whimsicott", move="Protect")])
        hp_left, name_left = (.14, .075) if slot == "p1a" else (.34, .285)
        for row in rows[1:6]:
            short = row["frame"] == 4
            row["ocr"] = [
                {"text": nickname, "confidence": .999, "left": name_left, "right": name_left + .05,
                 "top": .867, "bottom": .905},
                {"text": clipped if short else stable, "confidence": confidence if short else .999,
                 "left": hp_left + (.028 if short else 0), "right": hp_left + .075,
                 "top": .92, "bottom": .98}]
            row["resolved_aliases"]["p1"][nickname.casefold()] = "Whimsicott"
        return rows

    def test_clipped_numerator_is_an_observation_on_either_own_slot_at_any_confidence(self):
        for slot in ("p1a", "p1b"):
            for stable, short in (("77/167", "7/167"), ("133/167", "33/167")):
                for confidence in (.91207, 1):
                    with self.subTest(slot=slot, stable=stable, short=short, confidence=confidence):
                        ledger = BattleAutomaton(0, self.trace(slot, stable, short, "Yuki", confidence)).run()
                        self.assertEqual(ledger["issues"], [])
                        reading = next(e for e in ledger["events"] if e["kind"] == "hp_rejected_reading")
                        self.assertEqual((reading["status"], reading["before"], reading["after"]),
                                         ("suppressed", stable, stable))
                        self.assertEqual(reading["resolution"]["original_health"], short)
                        self.assertEqual([e["frame"] for e in reading["resolution"]["evidence"]], [2, 3, 5, 6, 4])
                        self.assertEqual([o["health"] for o in reading["observations"]], [short, stable])
                        self.assertEqual(ledger["actors"][reading["actor_id"]]["health"], stable)
                        self.assertFalse(any(e["kind"] in {"damage", "heal"} for e in ledger["events"]))

    def test_without_complete_local_proof_the_oscillation_remains_pending(self):
        for problem in ("missing_before", "missing_after", "weak_before", "different_after", "different_name",
                        "gap", "missing_frame", "raw_action", "hp_narration", "not_suffix", "wide_box", "wrong_maximum"):
            with self.subTest(problem=problem):
                rows = self.trace()
                if problem == "missing_before": rows[1]["ocr"] = []
                if problem == "missing_after": rows[5]["ocr"] = []
                if problem == "weak_before": rows[1]["ocr"][1]["confidence"] = .96
                if problem == "different_after": rows[5]["ocr"][1]["text"] = "78/167"
                if problem == "different_name": rows[5]["ocr"][0]["text"] = "Farigiraf"
                if problem == "gap": rows[3]["timestamp_ms"] += 1500
                if problem == "missing_frame": rows.pop(1)
                if problem == "raw_action": rows[3]["ocr"].append({"text": "Whimsicott used Protect!", "top": .75, "confidence": .999})
                if problem == "hp_narration": rows[3]["ocr"].append({"text": "Whimsicott had its HP restored.", "top": .75, "confidence": .999})
                if problem in {"not_suffix", "wrong_maximum"}:
                    short = "8/167" if problem == "not_suffix" else "7/168"
                    rows[3]["ocr"][1]["text"] = short
                    rows[3]["detections"]["events"][0]["health"] = short
                if problem == "wide_box": rows[3]["ocr"][1]["left"] = .14
                ledger = BattleAutomaton(0, rows).run()
                self.assertTrue(any(i["code"] == "hp_oscillation" for i in ledger["issues"]))
                self.assertFalse(any(e.get("resolution", {}).get("confirmed_health") for e in ledger["events"]))

    def test_a_real_action_between_the_readings_keeps_both_hp_changes(self):
        rows = self.trace(confidence=1)
        rows[3]["detections"]["events"].insert(0, event("move", "p1a", "Whimsicott", move="Substitute"))
        ledger = BattleAutomaton(0, rows).run()
        changes = [e for e in ledger["events"] if e["kind"] in {"damage", "heal"}]
        self.assertEqual([(e["before"], e["after"]) for e in changes], [("77/167", "7/167"), ("7/167", "77/167")])
        self.assertFalse(any(e.get("resolution", {}).get("confirmed_health") for e in ledger["events"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_15D9"), "diagnóstico opcional 15d9")
    def test_real_cropped_hp_exports_from_the_identical_archived_ocr(self):
        from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_15D9"])
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as directory:
            trace_bytes = archive.read("output/ocr.trace.jsonl")
            original = json.loads(archive.read("output/ledger-battle-001.json"))
            job = json.loads(archive.read("job.json"))
            root = Path(directory); trace = root / "ocr.trace.jsonl"; trace.write_bytes(trace_bytes)
            docs = documents_from_trace(trace, job, root / "output")
            self.assertEqual(hashlib.sha256(trace.read_bytes()).digest(), hashlib.sha256(trace_bytes).digest())
            self.assertEqual(len(docs), 1)
            self.assertEqual((docs[0].p1, docs[0].p2), ("Roku", "DecoLipe2"))
            self.assertTrue(docs[0].log.endswith("|win|Roku"))
            ledger = json.loads((root / "output/ledger-battle-001.json").read_text())
            report = json.loads((root / "output/ledger-report.json").read_text())
            self.assertEqual((ledger["issues"], report["blocked_battles"]), ([], 0))
            self.assertEqual(ledger["actors"], original["actors"])
            self.assertEqual(len(ledger["events"]), len(original["events"]))
            fields = ("kind", "status", "actor_id", "slot", "species", "move", "value", "before", "after", "cause")
            changed = [a["seq"] for a, b in zip(original["events"], ledger["events"])
                       if any(a.get(k) != b.get(k) for k in fields)]
            self.assertEqual(changed, [48])
            rejected = ledger["events"][47]
            self.assertEqual((rejected["kind"], rejected["status"]), ("hp_rejected_reading", "suppressed"))
            self.assertEqual(rejected["resolution"]["original_health"], "7/167")
            self.assertNotIn("|7/167", docs[0].log)


class SideEpisodeReachabilityTests(unittest.TestCase):
    def trace(self):
        return [frame(1, [event("switch", "p1a", "Whimsicott", "167/167"),
                          event("switch", "p1b", "Kingambit", "177/177"),
                          event("turn", turn=1)]),
                frame(2, [event("move", "p1a", "Whimsicott", move="Tailwind")]),
                frame(3, [event("sidestart", "p1a", value="move: Tailwind")]),
                frame(4, [event("sideend", "p1b", value="move: Tailwind")]),
                frame(5, [event("sideend", "p1a", value="move: Tailwind")])]

    def test_adjacent_closes_without_repeated_text_cannot_close_twice(self):
        ledger = BattleAutomaton(0, self.trace()).run()
        ends = [e for e in ledger["events"] if e["kind"] == "sideend"]
        self.assertEqual([e["status"] for e in ends], ["consistent", "suppressed"])
        discarded = ends[1]["resolution"]
        self.assertTrue(discarded["orphaned"])
        self.assertEqual(discarded["previous_end_seq"], ends[0]["seq"])
        self.assertEqual(discarded["raw_event"]["confidence"], 1)
        self.assertEqual(ledger["counts"]["sideend"], 1)
        self.assertEqual(ledger["issues"], [])

    def test_an_actor_and_a_later_opening_do_not_revive_an_earlier_close(self):
        rows = self.trace()
        rows[1] = frame(2, [event("sideend", "p1a", value="move: Tailwind")])
        rows[2] = frame(3, [event("move", "p1a", "Whimsicott", move="Tailwind")])
        rows[3] = frame(4, [event("sidestart", "p1b", value="move: Tailwind")])
        ledger = BattleAutomaton(0, rows).run()
        ends = [e for e in ledger["events"] if e["kind"] == "sideend"]
        self.assertEqual([e["status"] for e in ends], ["suppressed", "consistent"])
        self.assertIsNone(ends[0]["resolution"]["previous_start_seq"])
        self.assertTrue(ends[0]["actor_id"])
        self.assertEqual(ledger["actors"][ends[0]["actor_id"]]["health"], "167/167")

    def test_a_new_opening_allows_another_real_close(self):
        rows = self.trace()
        rows[4] = frame(5, [event("move", "p1a", "Whimsicott", move="Tailwind")])
        rows.extend([frame(6, [event("sidestart", "p1b", value="move: Tailwind")]),
                     frame(7, [event("sideend", "p1a", value="move: Tailwind")])])
        ledger = BattleAutomaton(0, rows).run()
        self.assertEqual([e["status"] for e in ledger["events"] if e["kind"] == "sideend"],
                         ["consistent", "consistent"])
        self.assertEqual(ledger["issues"], [])

    def test_side_and_effect_require_their_own_opening(self):
        for slot, effect in (("p2a", "Tailwind"), ("p1b", "Reflect")):
            with self.subTest(slot=slot, effect=effect):
                rows = self.trace()
                rows[3] = frame(4, [event("sideend", slot, value="move: " + effect)])
                ledger = BattleAutomaton(0, rows).run()
                ends = [e for e in ledger["events"] if e["kind"] == "sideend"]
                self.assertEqual([e["status"] for e in ends], ["suppressed", "consistent"])

    def test_a_provisional_opening_keeps_its_connected_uncertainty(self):
        class ProvisionalOpening(BattleAutomaton):
            def _reconcile_side_condition_readings(self):
                super()._reconcile_side_condition_readings()
                start = next(e for e in self.events if e["kind"] == "sidestart")
                start["status"] = "review"
                self._issue("side_transition", "Inicio pendiente de resolver.", start["frame"], start["seq"])

        ledger = ProvisionalOpening(0, self.trace()[:-1]).run()
        start = next(e for e in ledger["events"] if e["kind"] == "sidestart")
        end = next(e for e in ledger["events"] if e["kind"] == "sideend")
        self.assertEqual((start["status"], end["status"]), ("review", "consistent"))
        self.assertTrue(any(i["code"] == "side_transition" for i in ledger["issues"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_355F"), "diagnóstico opcional 355f")
    def test_real_duplicate_close_is_pruned_before_production_export(self):
        from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_355F"])
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as directory:
            trace_bytes = archive.read("output/ocr.trace.jsonl")
            original = json.loads(archive.read("output/ledger-battle-001.json"))
            job = json.loads(archive.read("job.json"))
            root = Path(directory); trace = root / "ocr.trace.jsonl"
            trace.write_bytes(trace_bytes)
            docs = documents_from_trace(trace, job, root / "output")
            self.assertEqual(hashlib.sha256(trace.read_bytes()).digest(), hashlib.sha256(trace_bytes).digest())
            self.assertEqual(len(docs), 1)
            self.assertEqual((docs[0].p1, docs[0].p2), ("Roku", "zid99"))
            self.assertTrue(docs[0].log.endswith("|win|Roku"))
            ledger = json.loads((root / "output/ledger-battle-001.json").read_text())
            report = json.loads((root / "output/ledger-report.json").read_text())
            self.assertEqual((ledger["issues"], report["blocked_battles"]), ([], 0))
            self.assertEqual(ledger["actors"], original["actors"])
            self.assertEqual([(a["seq"], a["status"], b["status"]) for a, b in zip(original["events"], ledger["events"])
                              if a["status"] != b["status"]], [(48, "consistent", "suppressed")])
            ends = [e for e in ledger["events"] if e["kind"] == "sideend"]
            self.assertEqual([(e["seq"], e["status"]) for e in ends],
                             [(46, "consistent"), (47, "consistent"), (48, "suppressed")])
            self.assertEqual(ends[-1]["resolution"]["previous_end_seq"], 47)
            self.assertIn("Your side's tailwind petered out!",
                          [e["text"] for e in ends[-1]["resolution"]["evidence"]])
            self.assertEqual(docs[0].log.count("|-sideend|p1:"), 1)
            self.assertEqual(docs[0].log.count("|-sideend|p2:"), 1)


if __name__ == "__main__":
    unittest.main()
