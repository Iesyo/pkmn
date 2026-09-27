"""Regresiones pequeñas de la máquina de estados; el diagnóstico real es opcional."""

import os
import unittest
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
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["kind"] == "heal" for x in ledger["events"]), 0)
        self.assertIn("hp_oscillation", [x["code"] for x in ledger["issues"]])
        self.assertEqual(ledger["actors"][next(iter(ledger["actors"]))]["health"], "88/100")

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

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC"), "Requiere el ZIP original del usuario")
    def test_five_approved_battles_retain_their_core_event_order(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC"]))
        self.assertEqual(len(baselines), 5)
        for index, baseline in baselines.items():
            with self.subTest(battle_index=index):
                ledger = BattleAutomaton(index, [x for x in frames if x["battle_index"] == index]).run()
                self.assertTrue(compare_baseline(ledger, baseline)["exact_core_sequence"])


if __name__ == "__main__":
    unittest.main()
