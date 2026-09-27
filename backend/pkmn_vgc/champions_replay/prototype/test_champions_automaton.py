"""Regresiones pequeñas de la máquina de estados; el diagnóstico real es opcional."""

import os
import json
import unittest
import zipfile
from pathlib import Path

from champions_automaton import BattleAutomaton, compare_baseline, health_ratio, read_diagnostic


def event(kind, slot=None, species=None, health=None, move=None, value=None, turn=None):
    return {"kind": kind, "slot": slot, "species": species, "health": health,
            "move": move, "value": value, "turn": turn, "timestamp_ms": 0,
            "source_frame": 0, "confidence": 1}


def frame(n, events=(), texts=()):
    for item in events:
        item["timestamp_ms"] = n * 500
        item["source_frame"] = n
    return {"frame": n, "timestamp_ms": n * 500, "battle_index": 0,
            "resolved_aliases": {"p1": {}, "p2": {"minemine": "Pelipper"}},
            "resolved_identities": {},
            "ocr": [{"text": text, "top": .75, "confidence": 1} for text in texts],
            "detections": {"events": list(events)}}


class TemporalAutomatonTests(unittest.TestCase):
    def test_hp_ratio_parses_fractions_and_rejects_invalid_values(self):
        self.assertEqual(health_ratio("50/200"), .25)
        self.assertEqual(health_ratio("0/100"), 0)
        self.assertIsNone(health_ratio("50%"))
        self.assertIsNone(health_ratio("50/0"))

    def test_announced_entry_is_before_attack_and_its_late_health_is_unknown(self):
        trace = [
            frame(1, [event("switch", "p1a", "Kingambit", "100/100"), event("turn", turn=1)]),
            frame(2, texts=["Ender sent out MineMine!"]),
            frame(3, [event("move", "p1a", "Kingambit", move="Kowtow Cleave")],
                  texts=["Kingambit used Kowtow Cleave!"]),
            frame(4, [event("switch", "p2a", "Pelipper", "74/100"),
                      event("ability", "p2a", "Pelipper", value="Drizzle")]),
            frame(5, [event("damage", "p2a", "Pelipper", "37/100")]),
        ]
        ledger = BattleAutomaton(0, trace).run()
        entries = [x for x in ledger["events"] if x["kind"] == "switch" and x["slot"] == "p2a"]
        self.assertEqual(len(entries), 1)
        self.assertLess(entries[0]["seq"], next(x["seq"] for x in ledger["events"] if x["kind"] == "move"))
        self.assertIsNone(entries[0]["health"])
        self.assertEqual(entries[0]["logical_frame"], 2)
        self.assertIn("late_switch_health", [x["code"] for x in ledger["issues"]])

    def test_hp_animation_and_repeated_move_become_one_action(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"),
                           event("switch", "p2a", "Kingambit", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Blaziken", move="Flare Blitz")]),
                 frame(3, [event("move", "p1a", "Blaziken", move="Flare Blitz")]),
                 frame(4, [event("damage", "p2a", "Kingambit", "80/100")]),
                 frame(5, [event("damage", "p2a", "Kingambit", "40/100")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["kind"] == "move" and x["status"] == "consistent" for x in ledger["events"]), 1)
        hp = [x for x in ledger["events"] if x["kind"] == "damage"]
        self.assertEqual(len(hp), 1)
        self.assertEqual((hp[0]["before"], hp[0]["after"]), ("100/100", "40/100"))
        self.assertEqual(len(hp[0]["observations"]), 2)

    def test_opposite_hp_readings_before_action_are_flagged_without_heal(self):
        trace = [frame(1, [event("switch", "p1a", "Rillaboom", "88/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Rillaboom", move="Protect")]),
                 frame(3, [event("turn", turn=2)]),
                 frame(4, [event("damage", "p1a", "Rillaboom", "0/100")]),
                 frame(5, [event("heal", "p1a", "Rillaboom", "88/100")])]
        trace[3]["ocr"] = [
            {"text": "88", "confidence": .99, "left": .70, "right": .74, "top": .11, "bottom": .16},
            {"text": "0%", "confidence": .78, "left": .73, "right": .75, "top": .12, "bottom": .16},
            {"text": "Kingambit", "confidence": .99, "left": .83, "right": .90, "top": .04, "bottom": .09},
            {"text": "0%", "confidence": .99, "left": .92, "right": .96, "top": .11, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["kind"] == "heal" for x in ledger["events"]), 0)
        self.assertIn("hp_oscillation", [x["code"] for x in ledger["issues"]])
        self.assertEqual(ledger["actors"][next(iter(ledger["actors"]))]["health"], "88/100")
        oscillation = next(x for x in ledger["events"] if x["kind"] == "hp_oscillation")
        self.assertIn("mismo HUD", oscillation["note"])
        self.assertEqual(oscillation["observations"][0]["competing_ocr"]["stronger"]["text"], "88")

    def test_zero_from_distant_partner_hud_is_separate_evidence(self):
        trace = [frame(1, [event("switch", "p2a", "Rillaboom", "88/100"),
                           event("switch", "p2b", "Kingambit", "0/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Rillaboom", "0/100")]),
                 frame(3, [event("heal", "p2a", "Rillaboom", "88/100")])]
        trace[1]["ocr"] = [
            {"text": "88", "confidence": .99, "left": .70, "right": .74, "top": .11, "bottom": .16},
            {"text": "0%", "confidence": .99, "left": .92, "right": .96, "top": .11, "bottom": .16},
        ]
        oscillation = next(x for x in BattleAutomaton(0, trace).run()["events"]
                           if x["kind"] == "hp_oscillation")
        self.assertNotIn("mismo HUD", oscillation["note"])
        self.assertNotIn("competing_ocr", oscillation["observations"][0])

    def test_fainted_actor_cannot_reenter_from_stale_hud(self):
        trace = [frame(1, [event("switch", "p1a", "Rillaboom", "10/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p1a", "Rillaboom", "0/100")]),
                 frame(3, [event("faint", "p1a", "Rillaboom")]),
                 frame(4, [event("switch", "p1a", "Rillaboom", "0/100")]),
                 frame(5, [event("switch", "p1a", "Blaziken", "100/100")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([x["species"] for x in ledger["events"]
                          if x["kind"] == "switch" and x["status"] == "consistent"],
                         ["Rillaboom", "Blaziken"])
        self.assertIn("ghost_reentry_after_faint", [x["code"] for x in ledger["issues"]])

    def test_isolated_partial_hp_read_does_not_create_damage_or_recovery(self):
        trace = [frame(1, [event("switch", "p2a", "Delphox", "28/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Delphox", move="Protect")]),
                 frame(3, [event("damage", "p2a", "Delphox", "3/100")]),
                 frame(4, [event("heal", "p2a", "Delphox", "28/100")])]
        trace[2]["ocr"] = [
            {"text": "28", "confidence": .999, "left": .70, "right": .74, "top": .11, "bottom": .16},
            {"text": "3%", "confidence": .95, "left": .73, "right": .75, "top": .12, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        self.assertFalse(any(x["kind"] in {"damage", "heal"} and x["status"] == "consistent"
                             for x in ledger["events"]))
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "28/100")
        self.assertEqual(sum(x["code"] == "hp_ocr_conflict" for x in ledger["issues"]), 1)

    def test_mega_from_other_slot_does_not_change_occupant(self):
        trace = [frame(1, [event("switch", "p1a", "Indeedee-F", "100/100"),
                           event("switch", "p2a", "Delphox", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("mega", "p1a", "Delphox", value="Delphoxite"),
                           event("mega", "p2a", "Delphox", value="Delphoxite")])]
        for candidate in trace[1]["detections"]["events"]:
            candidate["forme"] = "Delphox-Mega"
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([x["slot"] for x in ledger["events"]
                          if x["kind"] == "mega" and x["status"] == "consistent"], ["p2a"])
        self.assertIsNone(next(a["forme"] for a in ledger["actors"].values()
                               if a["species"] == "Indeedee-F"))
        self.assertIn("mega_wrong_occupant", [x["code"] for x in ledger["issues"]])

    def test_unresolved_actor_is_reported_before_replay_export(self):
        trace = [frame(1, [event("switch", "p2a", "__champions_actor_p2_0002__", "100/100"),
                           event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "__champions_actor_p2_0002__", move="Protect")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertIn("unresolved_identity", [x["code"] for x in ledger["issues"]])

    def test_zero_hp_during_faint_animation_does_not_revive_actor(self):
        trace = [frame(1, [event("switch", "p2b", "Archaludon", "20/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2b", "Archaludon", move="Protect")]),
                 frame(3, [event("damage", "p2b", "Archaludon", "0/100")]),
                 frame(4, [event("heal", "p2b", "Archaludon", "9/100")]),
                 frame(5, [event("damage", "p2b", "Archaludon", "0/100")]),
                 frame(6, [event("heal", "p2b", "Archaludon", "9/100")]),
                 frame(7, [event("faint", "p2b", "Archaludon")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["kind"] == "hp_zero_rebound" for x in ledger["events"]), 2)
        self.assertFalse(any(x["kind"] == "heal" and x["status"] == "consistent"
                             for x in ledger["events"]))
        self.assertTrue(next(iter(ledger["actors"].values()))["fainted"])

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC"), "Requiere el ZIP original del usuario")
    def test_five_approved_battles_retain_their_core_event_order(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC"]))
        self.assertEqual(len(baselines), 5)
        for index, baseline in baselines.items():
            with self.subTest(battle_index=index):
                ledger = BattleAutomaton(index, [x for x in frames if x["battle_index"] == index]).run()
                self.assertTrue(compare_baseline(ledger, baseline)["exact_core_sequence"])

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_SECOND"), "Requiere el segundo ZIP del usuario")
    def test_second_job_keeps_status_and_exposes_misreadings(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_SECOND"])
        frames, baselines = read_diagnostic(path)
        self.assertEqual(set(baselines), {0, 1, 2, 3})
        ledgers = {i: BattleAutomaton(i, [x for x in frames if x["battle_index"] == i]).run()
                   for i in baselines}
        self.assertTrue(all(compare_baseline(ledgers[i], baselines[i])["exact_core_sequence"]
                            for i in baselines))
        self.assertTrue(any(a["species"] == "Golisopod" and a["status"] == "par"
                            for a in ledgers[0]["actors"].values()))
        self.assertTrue(any(a["species"] == "Indeedee-F" and a["status"] == "par"
                            for a in ledgers[3]["actors"].values()))
        battle = ledgers[1]
        self.assertEqual({e["frame"] for e in battle["events"] if e["kind"] == "hp_ocr_conflict"},
                         {1694, 1766, 2082})
        self.assertEqual(sum(e["kind"] == "mega" and e["slot"] == "p1a" and
                             e["status"] == "consistent" for e in battle["events"]), 0)
        self.assertFalse(any(e["kind"] == "damage" and e["status"] == "consistent" and
                             e["after"] in {"3/100", "1/100"} and e["slot"] == "p2a"
                             for e in battle["events"]))
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist()
                        if n.startswith("output/history/") and n.endswith("/ocr.trace.jsonl")
                        and "b532412c" in n)
            old_frames = [json.loads(line) for line in archive.read(name).splitlines() if line]
        unresolved = BattleAutomaton(2, [x for x in old_frames if x["battle_index"] == 2]).run()
        self.assertIn("unresolved_identity", [x["code"] for x in unresolved["issues"]])
        archaludon = BattleAutomaton(0, [x for x in old_frames if x["battle_index"] == 0]).run()
        self.assertFalse(any(e["kind"] == "heal" and e["slot"] == "p2b" and
                             e["after"] == "9/100" and e["status"] == "consistent"
                             for e in archaludon["events"]))
        self.assertIn("hp_zero_rebound", [x["code"] for x in archaludon["issues"]])


if __name__ == "__main__":
    unittest.main()
