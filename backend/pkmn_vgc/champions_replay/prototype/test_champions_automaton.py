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
    hud = {"p1a": (.14, .92), "p1b": (.34, .92),
           "p2a": (.70, .12), "p2b": (.92, .12)}
    ocr = [{"text": text, "top": .75, "confidence": 1} for text in texts]
    for item in events:
        item["timestamp_ms"] = n * 500
        item["source_frame"] = n
        if item.get("health") and item.get("slot") in hud:
            left, top = hud[item["slot"]]
            value = (item["health"].split("/", 1)[0] + "%"
                     if item["slot"].startswith("p2") else item["health"])
            ocr.append({"text": value, "confidence": .999, "left": left,
                        "right": left + .06, "top": top, "bottom": top + .04})
    return {"frame": n, "timestamp_ms": n * 500, "battle_index": 0,
            "resolved_aliases": {"p1": {}, "p2": {"minemine": "Pelipper"}},
            "resolved_identities": {},
            "ocr": ocr,
            "detections": {"events": list(events)}}


class TemporalAutomatonTests(unittest.TestCase):
    def test_hp_ratio_parses_fractions_and_rejects_invalid_values(self):
        self.assertEqual(health_ratio("50/200"), .25)
        self.assertEqual(health_ratio("0/100"), 0)
        self.assertIsNone(health_ratio("50%"))
        self.assertIsNone(health_ratio("50/0"))

    def test_first_entry_infers_full_hp_and_keeps_late_health_as_observation(self):
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
        self.assertEqual(entries[0]["health"], "100/100")
        self.assertEqual(entries[0]["hp_state"], "inferred")
        self.assertEqual(entries[0]["observations"][0]["health"], "74/100")
        self.assertEqual(entries[0]["logical_frame"], 2)
        damage = next(x for x in ledger["events"] if x["kind"] == "damage")
        self.assertEqual((damage["before"], damage["after"]), ("100/100", "37/100"))
        self.assertEqual(damage["hp_baseline"]["state"], "inferred")
        self.assertNotIn("late_switch_health", [x["code"] for x in ledger["issues"]])

    def test_first_player_entry_deduces_max_from_first_confirmed_damage(self):
        trace = [frame(1, [event("switch", "p1a", "Kingambit"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Salamence", move="Hyper Voice")]),
                 frame(3, [event("damage", "p1a", "Kingambit", "86/177")])]
        ledger = BattleAutomaton(0, trace).run()
        entry = next(e for e in ledger["events"] if e["kind"] == "switch")
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((entry["health"], entry["hp_state"]), ("177/177", "inferred"))
        self.assertEqual((damage["before"], damage["after"]), ("177/177", "86/177"))
        self.assertEqual(damage["hp_baseline"]["state"], "inferred")

    def test_returning_actor_keeps_confirmed_hp_instead_of_resetting_to_full(self):
        trace = [frame(1, [event("switch", "p2a", "Pelipper", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Pelipper", "63/100")]),
                 frame(3, [event("switch", "p2a", "Kingambit", "100/100")]),
                 frame(4, [event("switch", "p2a", "Pelipper")]),
                 frame(5, [event("damage", "p2a", "Pelipper", "40/100")])]
        ledger = BattleAutomaton(0, trace).run()
        pelipper = [e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Pelipper"]
        damage = next(e for e in ledger["events"] if e["kind"] == "damage" and e["frame"] == 5)
        self.assertEqual(pelipper[1]["last_confirmed_health"], "63/100")
        self.assertEqual((damage["before"], damage["after"]), ("63/100", "40/100"))

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

    def test_burn_tick_keeps_one_damage_when_detector_calls_midpoint_heal(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"),
                           event("switch", "p2b", "Indeedee-F", "18/100"),
                           event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Blaziken", move="Flare Blitz")]),
                 frame(3, [event("damage", "p1a", "Blaziken", "82/100")]),
                 frame(4, [event("message", value="The opposing Indeedee was hurt by its burn!")]),
                 frame(5, [event("heal", "p2b", "Indeedee-F", "15/100")]),
                 frame(6, [event("damage", "p2b", "Indeedee-F", "12/100")]),
                 frame(7, [event("turn", turn=2)])]
        ledger = BattleAutomaton(0, trace).run()
        tick = [x for x in ledger["events"] if x["slot"] == "p2b" and
                x["kind"] in {"damage", "heal"}]
        self.assertEqual(len(tick), 1)
        self.assertEqual((tick[0]["kind"], tick[0]["before"], tick[0]["after"]),
                         ("damage", "18/100", "12/100"))
        self.assertEqual([x["health"] for x in tick[0]["observations"]], ["15/100", "12/100"])
        self.assertEqual(tick[0]["cause"], "quemadura observada")
        self.assertFalse(any("hurt by its burn" in text for x in ledger["events"]
                             if x["slot"] == "p1a" for text in x["narration"]))
        self.assertFalse(any(x["code"] == "hp_transition" for x in ledger["issues"]))

    def test_menu_messages_are_audited_without_becoming_battle_warnings(self):
        trace = [frame(1, [event("switch", "p1a", "Indeedee-F", "100/100"),
                           event("turn", turn=1)]),
                 frame(2, [event("message", value="Indeedee-F has no energy left to battle!")],
                       texts=["Battle Info", "Indeedee-F has no energy left to battle!"]),
                 frame(3, [event("message", value="Indeedee-F can't use its sealed Follow Me!")],
                       texts=["MOVE TIME", "Indeedee-F can't use its sealed Follow Me!"])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([x["kind"] for x in ledger["events"] if x["status"] == "suppressed"],
                         ["ui_text", "ui_text"])
        self.assertFalse(any(x["code"] == "unclassified_text" for x in ledger["issues"]))

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

    def test_repeated_hp_around_zero_fragment_suppresses_false_rebound(self):
        trace = [frame(1, [event("switch", "p2a", "Rillaboom", "88/100"), event("turn", turn=1)]),
                 frame(2), frame(3),
                 frame(4, [event("damage", "p2a", "Rillaboom", "0/100")]),
                 frame(5, [event("heal", "p2a", "Rillaboom", "88/100")]), frame(6)]
        for row in trace[1:3]:
            row["ocr"] = [{"text": "88%", "confidence": .999, "left": .70,
                           "right": .755, "top": .11, "bottom": .16}]
        trace[3]["ocr"] = [
            {"text": "88", "confidence": .999, "left": .70, "right": .74,
             "top": .11, "bottom": .16},
            {"text": "0%", "confidence": .78, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        trace[4]["ocr"][0]["confidence"] = .87
        trace[5]["ocr"] = [{"text": "88%", "confidence": .999, "left": .70,
                            "right": .755, "top": .11, "bottom": .16}]
        ledger = BattleAutomaton(0, trace).run()
        rejected = next(e for e in ledger["events"] if e["kind"] == "hp_rejected_reading")
        self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                         ("suppressed", "88/100", "88/100"))
        self.assertEqual([x["health"] for x in rejected["observations"]], ["0/100", "88/100"])
        self.assertEqual([x["frame"] for x in rejected["hp_support"]["evidence"]], [2, 3, 6])
        self.assertEqual(rejected["observations"][0]["competing_ocr"]["suspect"]["text"], "0%")
        self.assertFalse(any(x["code"] == "hp_oscillation" for x in ledger["issues"]))
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "88/100")

    def test_zero_fragment_with_different_stronger_number_keeps_warning(self):
        trace = [frame(1, [event("switch", "p2a", "Sneasler", "41/100"), event("turn", turn=1)]),
                 frame(2), frame(3),
                 frame(4, [event("damage", "p2a", "Sneasler", "0/100")]),
                 frame(5, [event("heal", "p2a", "Sneasler", "41/100")])]
        for row in trace[1:3]:
            row["ocr"] = [{"text": "41%", "confidence": .999, "left": .70,
                           "right": .755, "top": .11, "bottom": .16}]
        trace[3]["ocr"] = [
            {"text": "44", "confidence": .999, "left": .70, "right": .74,
             "top": .11, "bottom": .16},
            {"text": "0%", "confidence": .78, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["code"] == "hp_oscillation" for x in ledger["issues"]), 1)
        self.assertFalse(any(x["kind"] == "hp_rejected_reading" for x in ledger["events"]))

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

    def test_unconfirmed_hp_marks_actor_unknown_until_a_new_reading_is_confirmed(self):
        trace = [frame(1, [event("switch", "p2a", "Delphox", "28/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Delphox", move="Protect")]),
                 frame(3, [event("damage", "p2a", "Delphox", "3/100")]),
                 frame(4, [event("move", "p2a", "Delphox", move="Protect")])]
        # A high-confidence 3% belongs to the other HUD. The local 3 has no
        # percent sign and is not repeated; neither is proof of 3/100 here.
        trace[2]["ocr"] = [
            {"text": "3", "confidence": .99, "left": .70, "right": .72, "top": .12},
            {"text": "3%", "confidence": .999, "left": .92, "right": .96, "top": .12},
        ]
        ledger = BattleAutomaton(0, trace).run()
        suspect = next(x for x in ledger["events"] if x["kind"] == "hp_unconfirmed")
        self.assertEqual((suspect["before"], suspect["after"], suspect["health"]),
                         ("28/100", None, "3/100"))
        self.assertEqual(suspect["hp_state"], "unconfirmed")
        self.assertEqual(next(iter(ledger["actors"].values()))["health_state"], "unconfirmed")
        self.assertFalse(any(x["kind"] == "damage" for x in ledger["events"]))

    def test_split_percent_confirms_only_its_own_hud(self):
        trace = [frame(1, [event("switch", "p2a", "Golisopod", "65/100"), event("turn", turn=1)]),
                 frame(2, [event("heal", "p2a", "Golisopod", "71/100")])]
        trace[1]["ocr"] = [
            {"text": "71", "confidence": .99, "left": .70, "right": .739, "top": .12},
            {"text": "%", "confidence": .98, "left": .734, "right": .75, "top": .12},
            {"text": "0%", "confidence": 1, "left": .92, "right": .96, "top": .12},
        ]
        ledger = BattleAutomaton(0, trace).run()
        hp = next(x for x in ledger["events"] if x["kind"] == "heal")
        self.assertEqual(hp["hp_state"], "confirmed")
        self.assertEqual(hp["hp_support"]["reason"], "número y porcentaje separados")
        self.assertEqual(hp["hp_support"]["evidence"][0]["percent"]["text"], "%")
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "71/100")

    def test_repeated_wrong_separator_can_confirm_own_entry(self):
        trace = [frame(1, [event("switch", "p1b", "Indeedee-F", "177/177")]), frame(2)]
        trace[0]["ocr"] = [{"text": "1777177", "confidence": .94, "left": .34, "top": .92}]
        trace[1]["ocr"] = [{"text": "1777177", "confidence": .95, "left": .34, "top": .92}]
        ledger = BattleAutomaton(0, trace).run()
        entry = next(x for x in ledger["events"] if x["kind"] == "switch")
        self.assertEqual(entry["hp_state"], "confirmed")
        self.assertEqual(entry["hp_support"]["reason"], "separador OCR reparado en dos frames")

    def test_unconfirmed_entry_cannot_seed_a_false_transition(self):
        trace = [frame(1, [event("switch", "p2a", "Delphox", "3/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Delphox", "1/100")])]
        trace[0]["ocr"] = [{"text": "3", "confidence": .8, "left": .70, "top": .12}]
        ledger = BattleAutomaton(0, trace).run()
        entry = next(x for x in ledger["events"] if x["kind"] == "switch")
        damage = next(x for x in ledger["events"] if x["kind"] == "damage")
        self.assertIsNone(entry["health"])
        self.assertEqual(entry["hp_state"], "unconfirmed")
        self.assertEqual(damage["status"], "review")
        self.assertIsNone(damage["before"])
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "1/100")

    def test_stronger_number_in_same_hud_rejects_even_high_confidence_partial(self):
        trace = [frame(1, [event("switch", "p2a", "Delphox", "28/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Delphox", "3/100")])]
        trace[1]["ocr"] = [
            {"text": "28", "confidence": .999, "left": .70, "right": .74,
             "top": .12, "bottom": .16},
            {"text": "3%", "confidence": .995, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(e["kind"] == "hp_ocr_conflict" for e in ledger["events"]), 1)
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "28/100")

    def test_repeated_complete_hp_before_overlapping_fragment_suppresses_warning(self):
        trace = [frame(1, [event("switch", "p2b", "Indeedee-F", "18/100")]),
                 frame(2), frame(3),
                 frame(4, [event("damage", "p2b", "Indeedee-F", "3/100")])]
        for row in trace[1:3]:
            row["ocr"] = [{"text": "18%", "confidence": .999, "left": .91,
                           "right": .96, "top": .11, "bottom": .15}]
        trace[3]["ocr"] = [
            {"text": "18", "confidence": .999, "left": .91, "right": .94,
             "top": .11, "bottom": .15},
            {"text": "3%", "confidence": .8, "left": .93, "right": .96,
             "top": .12, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        rejected = next(x for x in ledger["events"] if x["kind"] == "hp_rejected_reading")
        self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                         ("suppressed", "18/100", "18/100"))
        self.assertEqual([x["frame"] for x in rejected["hp_support"]["evidence"]], [2, 3])
        self.assertEqual(rejected["observations"][0]["competing_ocr"]["suspect"]["text"], "3%")
        self.assertFalse(any(x["code"] == "hp_ocr_conflict" for x in ledger["issues"]))
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "18/100")

    def test_repeated_old_hp_does_not_resolve_a_different_stronger_number(self):
        trace = [frame(1, [event("switch", "p2a", "Sneasler", "41/100")]),
                 frame(2), frame(3),
                 frame(4, [event("damage", "p2a", "Sneasler", "1/100")])]
        for row in trace[1:3]:
            row["ocr"] = [{"text": "41%", "confidence": .999, "left": .70,
                           "right": .75, "top": .11, "bottom": .15}]
        trace[3]["ocr"] = [
            {"text": "44", "confidence": .999, "left": .70, "right": .74,
             "top": .11, "bottom": .15},
            {"text": "1%", "confidence": .86, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["code"] == "hp_ocr_conflict" for x in ledger["issues"]), 1)
        self.assertEqual(next(iter(ledger["actors"].values()))["health"], "41/100")

    def test_illusion_reveals_one_actor_then_real_disguised_species_gets_its_own_state(self):
        trace = [frame(1, [event("switch", "p2a", "Kingambit", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Kingambit", move="Bitter Malice")]),
                 frame(3, [event("damage", "p2a", "Kingambit", "1/100")]),
                 frame(4, [event("enditem", "p2a", "Kingambit", value="Focus Sash")]),
                 frame(5, [event("switch", "p2a", "Zoroark-Hisui", "1/100")],
                       texts=["The opposing Zoroark's illusion wore off!"]),
                 frame(6, [event("switch", "p2a", "Kingambit")],
                       texts=["Trainer sent out Kingambit!"]),
                 frame(7, [event("damage", "p2a", "Kingambit", "85/100")]),
                 frame(8, [event("faint", "p2a", "Kingambit")]),
                 frame(9, [event("switch", "p2a", "Zoroark-Hisui")])]
        ledger = BattleAutomaton(0, trace).run()
        entries = [x for x in ledger["events"] if x["kind"] == "switch" and x["slot"] == "p2a"]
        self.assertEqual([x["species"] for x in entries],
                         ["Zoroark-Hisui", "Kingambit", "Zoroark-Hisui"])
        self.assertEqual(entries[0]["display_species"], "Kingambit")
        self.assertEqual(entries[0]["actor_id"], entries[2]["actor_id"])
        self.assertNotEqual(entries[0]["actor_id"], entries[1]["actor_id"])
        self.assertEqual(entries[2]["last_confirmed_health"], "1/100")
        reveal = next(x for x in ledger["events"] if x["kind"] == "illusion_reveal")
        self.assertEqual((reveal["actor_id"], reveal["before"], reveal["after"]),
                         (entries[0]["actor_id"], "1/100", "1/100"))
        self.assertEqual(next(x for x in ledger["events"] if x["kind"] == "move")["species"],
                         "Zoroark-Hisui")
        self.assertTrue(ledger["actors"][entries[0]["actor_id"]]["item_lost"])
        self.assertFalse(ledger["actors"][entries[1]["actor_id"]]["item_lost"])
        damage = next(x for x in ledger["events"] if x["kind"] == "damage" and x["frame"] == 7)
        self.assertEqual(damage["before"], "100/100")
        self.assertEqual(damage["after"], "85/100")
        self.assertNotIn("illusion_reveal_mismatch", [x["code"] for x in ledger["issues"]])

    def test_species_change_without_illusion_message_remains_a_switch(self):
        trace = [frame(1, [event("switch", "p2a", "Kingambit", "100/100")]),
                 frame(2, [event("switch", "p2a", "Zoroark-Hisui", "1/100")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([x["species"] for x in ledger["events"] if x["kind"] == "switch"],
                         ["Kingambit", "Zoroark-Hisui"])
        self.assertFalse(any(x["kind"] == "illusion_reveal" for x in ledger["events"]))

    def test_pre_action_hud_seeds_hp_omitted_from_switch(self):
        trace = [frame(1, [event("switch", "p1a", "Kingambit"), event("turn", turn=1)]),
                 frame(2), frame(3, [event("move", "p2a", "Salamence", move="Hyper Voice")]),
                 frame(4, [event("damage", "p1a", "Kingambit", "86/177")])]
        trace[1]["ocr"] = [
            {"text": "Tomoe", "left": .08, "top": .86, "confidence": 1},
            {"text": "177/177", "left": .13, "top": .92, "confidence": .99},
        ]
        for row in trace:
            row["resolved_aliases"]["p1"]["tomoe"] = "Kingambit"
        ledger = BattleAutomaton(0, trace).run()
        entry = next(e for e in ledger["events"] if e["kind"] == "switch")
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((entry["health"], entry["hp_support"]["reason"]),
                         ("177/177", "OCR del HUD anterior a la primera acción"))
        self.assertEqual((damage["before"], damage["after"], damage["status"]),
                         ("177/177", "86/177", "consistent"))

    def test_pre_impact_hud_seeds_hp_after_move_announcement(self):
        trace = [frame(1, [event("switch", "p1b", "Rillaboom"), event("turn", turn=1)]),
                 frame(2), frame(3, [event("move", "p2a", "Golisopod", move="Iron Head")]),
                 frame(4), frame(5, [event("damage", "p1b", "Rillaboom", "128/207")]),
                 frame(6, [event("damage", "p1b", "Rillaboom", "117/207")])]
        trace[3]["ocr"] = [
            {"text": "Gori", "left": .287, "top": .866, "confidence": 1},
            {"text": "207/207", "left": .332, "top": .923, "confidence": .972},
        ]
        for row in trace:
            row["resolved_aliases"]["p1"]["gori"] = "Rillaboom"
        ledger = BattleAutomaton(0, trace).run()
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((damage["before"], damage["after"], damage["status"]),
                         ("207/207", "117/207", "consistent"))
        self.assertEqual(damage["hp_baseline"]["evidence"][0]["frame"], 4)

    def test_two_different_pre_impact_hp_values_leave_only_inferred_baseline(self):
        trace = [frame(1, [event("switch", "p1b", "Rillaboom"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Golisopod", move="Iron Head")]),
                 frame(3), frame(4), frame(5),
                 frame(6, [event("damage", "p1b", "Rillaboom", "117/207")])]
        for index, value in ((2, "207/207"), (3, "128/207")):
            trace[index]["ocr"] = [
                {"text": "Gori", "left": .287, "top": .866, "confidence": 1},
                {"text": value, "left": .332, "top": .923, "confidence": .99},
            ]
        for row in trace:
            row["resolved_aliases"]["p1"]["gori"] = "Rillaboom"
        ledger = BattleAutomaton(0, trace).run()
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual(damage["before"], "207/207")
        self.assertEqual(damage["hp_baseline"]["state"], "inferred")
        self.assertFalse(damage["hp_baseline"]["evidence"])

    def test_provisional_id_reused_in_two_slots_uses_nickname_and_appearance(self):
        anon = "__champions_actor_p2_0003__"
        trace = [frame(1, [event("switch", "p2b", "Salamence", "100/100"),
                           event("switch", "p2a", "Kingambit", "100/100"),
                           event("turn", turn=1)]),
                 frame(2, [event("switch", "p2b", anon, "100/100")]),
                 frame(3, [event("switch", "p2a", "Salamence")]),
                 frame(4, [event("switch", "p2a", anon, "100/100")]),
                 frame(5, [event("move", "p2b", anon, move="Hyper Voice")],
                       texts=["The opposing Farmingdale used Hyper Voice!"]),
                 frame(6, [event("damage", "p2a", anon, "65/100")])]
        for row in trace:
            row["resolved_aliases"]["p2"].update(
                {"farmingdale": "Salamence", "inwood": "Indeedee-F"})
            row["resolved_identities"][anon] = (
                "Salamence" if row["frame"] == 4 else "Indeedee-F")
        for index in (1, 3):
            trace[index]["ocr"].extend([
                {"text": "Inwood", "left": .83, "top": .05, "confidence": 1},
                {"text": "Farmingdale", "left": .62, "top": .05, "confidence": 1},
            ])
        trace[5]["ocr"].append({"text": "Farmingdale", "left": .62, "top": .05,
                                 "confidence": 1})
        for row in trace[4:]:
            row["resolved_aliases"]["p2"]["farmingdale"] = "Indeedee-F"
        ledger = BattleAutomaton(0, trace).run()
        entries = [e for e in ledger["events"] if e["kind"] == "switch" and
                   e["status"] != "suppressed"]
        self.assertEqual([(e["slot"], e["species"]) for e in entries],
                         [("p2a", "Kingambit"), ("p2b", "Salamence"),
                          ("p2b", "Indeedee-F"), ("p2a", "Salamence")])
        self.assertEqual(next(e for e in entries if e["slot"] == "p2a" and
                              e["species"] == "Salamence")["health"], "100/100")
        move = next(e for e in ledger["events"] if e["kind"] == "move")
        self.assertEqual((move["slot"], move["species"], move["original_slot"]),
                         ("p2a", "Salamence", "p2b"))
        self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "damage")["before"],
                         "100/100")
        self.assertNotIn("actor_mismatch", [e["code"] for e in ledger["issues"]])

    def test_persistent_hp_on_correct_hud_rejects_partner_noise_without_losing_baseline(self):
        trace = [frame(1, [event("switch", "p2b", "Indeedee-F", "6/100"),
                           event("switch", "p1a", "Basculegion", "219/219"),
                           event("turn", turn=1)]),
                 frame(2),
                 frame(3, [event("heal", "p2b", "Indeedee-F", "65/100")]),
                 frame(4, [event("damage", "p2b", "Indeedee-F", "0/100")]),
                 frame(5, [event("heal", "p2b", "Indeedee-F", "5/20")]),
                 frame(6, [event("move", "p1a", "Basculegion", move="Aqua Jet")]),
                 frame(7, [event("damage", "p2b", "Indeedee-F", "0/100")])]
        for row in trace[1:5]:
            row["ocr"] = [
                {"text": "6%", "left": .92, "top": .12, "confidence": .96},
                {"text": "65", "left": .70, "top": .12, "confidence": 1},
            ]
        ledger = BattleAutomaton(0, trace).run()
        rejected = [e for e in ledger["events"] if e["kind"] == "hp_rejected_reading"]
        self.assertEqual(len(rejected), 3)
        self.assertTrue(all(e["status"] == "suppressed" and e["before"] == "6/100"
                            for e in rejected))
        real = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((real["before"], real["after"], real["status"]),
                         ("6/100", "0/100", "consistent"))
        self.assertFalse(any(e["code"] in {"hp_transition", "hp_unconfirmed", "hp_ocr_conflict"}
                             for e in ledger["issues"]))

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
                if index == 0:
                    rejected = next(e for e in ledger["events"] if e["frame"] == 358 and
                                    e["kind"] == "hp_rejected_reading")
                    self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                                     ("suppressed", "18/100", "18/100"))
                    self.assertFalse(any(x["code"] == "hp_ocr_conflict" and x["frame"] == 358
                                         for x in ledger["issues"]))
                if index == 1:
                    rejected = next(e for e in ledger["events"] if e["frame"] == 2797 and
                                    e["kind"] == "hp_rejected_reading")
                    self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                                     ("suppressed", "88/100", "88/100"))
                    self.assertFalse(any(x["code"] == "hp_oscillation" and x["frame"] == 2797
                                         for x in ledger["issues"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_SECOND"), "Requiere el segundo ZIP del usuario")
    def test_second_job_keeps_status_and_exposes_misreadings(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_SECOND"])
        frames, baselines = read_diagnostic(path)
        self.assertEqual(set(baselines), {0, 1, 2, 3})
        ledgers = {i: BattleAutomaton(i, [x for x in frames if x["battle_index"] == i]).run()
                   for i in baselines}
        self.assertTrue(all(compare_baseline(ledgers[i], baselines[i])["exact_core_sequence"]
                            for i in (0, 1, 3)))
        self.assertTrue(any(a["species"] == "Golisopod" and a["status"] == "par"
                            for a in ledgers[0]["actors"].values()))
        self.assertTrue(any(a["species"] == "Indeedee-F" and a["status"] == "par"
                            for a in ledgers[3]["actors"].values()))
        battle = ledgers[1]
        self.assertEqual({e["frame"] for e in battle["events"] if e["kind"] == "hp_ocr_conflict"},
                         {2082})
        self.assertTrue({1694, 1766}.issubset(
            {e["frame"] for e in battle["events"] if e["kind"] == "hp_rejected_reading" and
             e["status"] == "suppressed"}))
        self.assertEqual(sum(e["kind"] == "mega" and e["slot"] == "p1a" and
                             e["status"] == "consistent" for e in battle["events"]), 0)
        self.assertFalse(any(e["kind"] == "damage" and e["status"] == "consistent" and
                             e["after"] in {"3/100", "1/100"} and e["slot"] == "p2a"
                             for e in battle["events"]))
        kingambit = ledgers[2]
        comparison = compare_baseline(kingambit, baselines[2])
        self.assertFalse(comparison["exact_core_sequence"])
        self.assertEqual(comparison["first_differences"], [
            {"operation": "replace", "baseline": [("switch", "p2a", "Kingambit")],
             "automaton": [("switch", "p2a", "Zoroark-Hisui")]},
            {"operation": "delete", "baseline": [("switch", "p2a", "Zoroark-Hisui")],
             "automaton": []}])
        disguised = next(e for e in kingambit["events"] if e["frame"] == 2568 and e["slot"] == "p2a")
        revealed = next(e for e in kingambit["events"] if e["kind"] == "illusion_reveal")
        real = next(e for e in kingambit["events"] if e["frame"] == 2988 and e["kind"] == "switch")
        self.assertEqual((disguised["species"], disguised["display_species"]),
                         ("Zoroark-Hisui", "Kingambit"))
        self.assertEqual(revealed["actor_id"], disguised["actor_id"])
        self.assertNotEqual(real["actor_id"], disguised["actor_id"])
        self.assertEqual(next(e for e in kingambit["events"] if e["frame"] == 3193 and
                              e["kind"] == "switch")["last_confirmed_health"], "1/100")
        self.assertTrue(kingambit["actors"][disguised["actor_id"]]["item_lost"])
        self.assertFalse(kingambit["actors"][real["actor_id"]]["item_lost"])
        suspicious = next(e for e in kingambit["events"] if e["frame"] == 3001 and e["kind"] == "damage")
        self.assertEqual((suspicious["status"], suspicious["before"], suspicious["after"]),
                         ("consistent", "93/100", "85/100"))
        self.assertEqual(suspicious["hp_baseline"]["evidence"][0]["frame"], 3000)
        following = next(e for e in kingambit["events"] if e["frame"] == 3025 and e["kind"] == "damage")
        self.assertEqual(following["before"], "85/100")
        # Retirar la prueba OCR de una observación de una batalla real simula
        # un nuevo vídeo con lectura incompleta, sin retocar eventos candidatos.
        altered = []
        for row in frames:
            if row["battle_index"] != 0:
                continue
            if 599 <= row["frame"] <= 601:
                ocr = [line for line in row["ocr"] if not
                       (.66 <= line.get("left", -1) <= .82 and .08 <= line.get("top", -1) <= .20)]
                altered.append({**row, "ocr": ocr})
            else:
                altered.append(row)
        partial = BattleAutomaton(0, altered).run()
        self.assertTrue(any(e["kind"] == "hp_unconfirmed" and e["frame"] == 599
                            for e in partial["events"]))
        self.assertFalse(any(e["kind"] == "damage" and e["frame"] == 599
                             for e in partial["events"]))
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

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_THIRD"), "Requiere el tercer ZIP del usuario")
    def test_third_job_keeps_distinct_salamence_and_indeedee_states(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_THIRD"]))
        self.assertEqual(set(baselines), {0, 1, 2})
        ledgers = [BattleAutomaton(i, [row for row in frames if row["battle_index"] == i]).run()
                   for i in range(3)]
        self.assertEqual([len(ledger["issues"]) for ledger in ledgers], [0, 0, 0])
        self.assertTrue(all(compare_baseline(ledgers[i], baselines[i])["exact_core_sequence"]
                            for i in (0, 1)))
        battle = ledgers[2]
        self.assertFalse(any(e["frame"] == 2999 and e["kind"] == "switch" and
                             e["status"] != "suppressed" for e in battle["events"]))
        move = next(e for e in battle["events"] if e["kind"] == "move" and e["frame"] == 3057)
        self.assertEqual((move["slot"], move["species"]), ("p2a", "Salamence-Mega"))
        faint_damage = next(e for e in battle["events"] if e["kind"] == "damage" and
                            e["frame"] == 3161)
        self.assertEqual((faint_damage["before"], faint_damage["after"], faint_damage["species"]),
                         ("6/100", "0/100", "Indeedee-F"))
        inwood_entry = next(e for e in battle["events"] if e["kind"] == "switch" and
                            e["slot"] == "p2b" and e["frame"] == 2735)
        inwood_damage = next(e for e in battle["events"] if e["kind"] == "damage" and
                             e["slot"] == "p2b" and e["frame"] == 2736)
        self.assertEqual((inwood_entry["health"], inwood_entry["hp_state"]), ("100/100", "inferred"))
        self.assertEqual(inwood_entry["observations"][0]["health"], "76/100")
        self.assertEqual((inwood_damage["before"], inwood_damage["after"]), ("100/100", "54/100"))
        self.assertEqual(inwood_damage["hp_baseline"]["state"], "inferred")
        gardevoir = next(e for e in battle["events"] if e["kind"] == "damage" and
                         e["frame"] == 2527 and e["slot"] == "p1a")
        self.assertEqual((gardevoir["before"], gardevoir["after"],
                          gardevoir["hp_baseline"]["evidence"][0]["frame"]),
                         ("168/171", "113/171", 2525))


if __name__ == "__main__":
    unittest.main()
