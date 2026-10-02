"""Regresiones pequeñas de la máquina de estados; el diagnóstico real es opcional."""

import os
import json
import copy
import unittest
import zipfile
from pathlib import Path

from champions_automaton import (BattleAutomaton, compare_baseline, corroborated_digit_aliases,
                                 entry_hud_pair, health_ratio, hud_nickname, narration_signature, ordered_candidates, read_diagnostic,
                                 read_diagnostic_context, render_markdown, strip_pokemon_title)


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


def opponent_opening_swap_trace():
    trace = [frame(n) for n in range(1, 8)]
    trace[0]["ocr"].append({"text": "Jack sent out Dired One and Arcanine!",
                             "confidence": .999, "left": .15, "top": .73})
    trace[2] = frame(3, [event("switch", "p2a", "__champions_actor_p2_0001__", "5/5"),
                         event("switch", "p2b", "Arcanine-Hisui", "100/100"),
                         event("turn", turn=1)])
    trace[4] = frame(5, [event("switch", "p2a", "Arcanine-Hisui", "100/100"),
                         event("switch", "p2b", "Sneasler", "100/100")])
    trace[5] = frame(6, [event("move", "p2b", "Sneasler", move="Throat Chop")],
                     texts=["The opposing Dired One used Throat Chop!"])
    for row in trace:
        row["resolved_aliases"]["p2"]["dired one"] = "Sneasler"
    for row in trace[2:5]:
        row["ocr"].extend([
            {"text": "Arcanine", "confidence": .999, "left": .62, "top": .05},
            {"text": "Dired One", "confidence": .999, "left": .83, "top": .05},
            {"text": "100%", "confidence": .999, "left": .70, "top": .12},
            {"text": "100%", "confidence": .999, "left": .92, "top": .12},
        ])
    return trace


def delayed_terrain_heal_trace():
    """The detector notices an earlier Rillaboom heal on a later menu redraw."""
    trace = [frame(n) for n in range(1, 31)]
    trace[0] = frame(1, [event("switch", "p2a", "Rillaboom", "100/100"),
                          event("fieldstart", value="move: Grassy Terrain"), event("turn", turn=1)])
    trace[3] = frame(4, [event("damage", "p2a", "Rillaboom", "76/100")])
    trace[9] = frame(10, [event("message", value="The opposing Rillaboom had its HP restored.")],
                      texts=["The opposing Rillaboom had its HP restored."])
    trace[10] = frame(11, texts=["The opposing Rillaboom had its HP restored."])
    trace[11] = frame(12, [event("turn", turn=2)])
    trace[14] = frame(15, [event("move", "p2a", "Rillaboom", move="Protect")],
                      texts=["The opposing Rillaboom used Protect!"])
    trace[29] = frame(30, [event("heal", "p2a", "Rillaboom", "82/100")])
    for n in (6, 7, 8, 27, 28, 30):
        trace[n - 1]["ocr"].append({"text": "Rillaboom", "left": .625, "right": .70,
                                     "top": .05, "bottom": .09, "confidence": .999})
    for n, value in ((6, "76%"), (7, "79"), (8, "82"), (27, "82%"), (28, "82%")):
        trace[n - 1]["ocr"].append({"text": value, "left": .70, "right": .75,
                                     "top": .12, "bottom": .16, "confidence": .999})
    return trace


def voluntary_entry_trace():
    trace = [frame(n) for n in range(1, 20)]
    trace[0] = frame(1, [event("switch", "p1a", "Indeedee-F", "177/177"),
                        event("switch", "p2a", "Pelipper", "100/100"), event("turn", turn=1),
                        event("fieldstart", value="move: Grassy Terrain")])
    for row in trace[:2]:
        row["ocr"].append({"text": "Moon", "left": .08, "top": .86, "confidence": .999})
    for n in (3, 4):
        trace[n - 1]["ocr"] = [{"text": "Moon, come back!", "top": .75, "confidence": .999}]
    for n in (6, 7):
        trace[n - 1]["ocr"] = [{"text": "Go! Ember!", "top": .75, "confidence": .999}]
    trace[5]["detections"]["events"] = [event("message", value="Go! Ember!")]
    for n in (8, 9):
        trace[n - 1]["ocr"] = [{"text": "Ember’s", "left": .08, "top": .36, "confidence": .999},
                               {"text": "Intimidate", "left": .08, "top": .40, "confidence": .999}]
    trace[9] = frame(10, [event("move", "p2a", "Pelipper", move="Surf")], texts=["The opposing Pelipper used Surf!"])
    for n, health in ((12, "150/180"), (13, "120/180"), (14, "120/180"), (16, "131/180"), (17, "131/180")):
        trace[n - 1]["ocr"] = [{"text": "Ember", "left": .08, "top": .86, "confidence": .999},
                               {"text": health, "left": .14, "top": .92, "confidence": .999}]
    trace[17] = frame(18, [event("message", value="Ember had its HP restored.")], texts=["Ember had its HP restored."])
    return trace


def buffered_named_entry_trace():
    trace = [frame(n) for n in range(1, 141)]
    trace[0] = frame(1, [event("switch", "p1b", "Blaziken", "156/156"),
                         event("switch", "p2a", "Pelipper", "100/100"), event("turn", turn=1)])
    trace[18]["ocr"] = [{"text": "Tonatiuh", "confidence": .999, "left": .29, "top": .86},
                         {"text": "156/156", "confidence": .999, "left": .34, "top": .92}]
    trace[19] = frame(20, [event("message", value="Tonatiuh, come back!")], ["Tonatiuh, come back!"])
    trace[20] = frame(21, texts=["Tonatiuh, come back!"])
    for n in (26, 27):
        trace[n - 1]["ocr"] = [{"text": "Go! Dee Dee!", "confidence": .999, "top": .73}]
    for n in (33, 34):
        trace[n - 1]["ocr"] = [{"text": "Dee Dee's", "confidence": .999, "left": .08, "top": .4},
                               {"text": "Psychic Surge", "confidence": .999, "left": .08, "top": .45}]
    trace[34]["ocr"] = [{"text": "The battlefield got weird!", "confidence": .999, "top": .73}]
    trace[69] = frame(70, [event("move", "p2a", "Pelipper", move="Weather Ball")], ["The opposing Pelipper used Weather Ball!"])
    terrain = event("fieldstart", value="move: Psychic Terrain")
    terrain["tags"] = ["[from] ability: Psychic Surge", "[of] p1b: Indeedee-F"]
    trace[126] = frame(127, [event("switch", "p1b", "Indeedee-F", "141/177"),
                            event("ability", "p1b", "Indeedee-F", value="Psychic Surge"), terrain])
    trace[127] = frame(128, [event("damage", "p1b", "Indeedee-F", "99/177")])
    for n, health, confidence in ((127, "141/177", .999), (128, "99/177", .929), (129, "99/177", .999), (130, "99/177", .999)):
        trace[n - 1]["ocr"] = [{"text": "Dee Dee", "confidence": .999, "left": .29, "top": .86},
                               {"text": health, "confidence": confidence, "left": .34, "top": .92}]
    trace[131] = frame(132, [event("damage", "p1b", "Indeedee-F", "0/177")])
    trace[132] = frame(133, [event("faint", "p1b", "Indeedee-F")], ["Dee Dee fainted!"])
    trace[133] = frame(134, texts=["Dee Dee fainted!"])
    trace[139] = frame(140, [event("message", value="The battle has ended due to a forfeit.")])
    for row in trace:
        row["resolved_aliases"]["p1"]["tonatiuh"] = "Blaziken"
    return trace


def mega_duplicate_trace(correct_frame=3):
    text = "The opposing Delphox's Delphoxite is reacting to Rival's Omni Ring!"
    wrong = {**event("mega", "p1a", "Delphox", value="Delphoxite"), "forme": "Delphox-Mega"}
    correct = {**event("mega", "p2a", "Delphox", value="Delphoxite"), "forme": "Delphox-Mega"}
    return [frame(1, [event("switch", "p1a", "Indeedee-F", "100/100"),
                      event("switch", "p2a", "Delphox", "100/100"), event("turn", turn=1)]),
            frame(2, [wrong]), frame(correct_frame, [correct], texts=[text]),
            frame(correct_frame + 1, texts=[text])]


def damaged_side_trace():
    complete = "The opposing Arcanine used Flare Blitz!"
    damaged = "The opposng Arcanine used Flare Blitz!"
    return [frame(1, [event("switch", "p1a", "Blaziken", "156/156"),
                      event("switch", "p2b", "Arcanine", "100/100"), event("turn", turn=1)]),
            frame(2, [event("move", "p2b", "Arcanine", move="Flare Blitz")], texts=[complete]),
            frame(3, [event("move", "p1a", "Arcanine", move="Flare Blitz")], texts=[damaged]),
            frame(4, [event("move", "p2b", "Arcanine", move="Flare Blitz")], texts=[complete]),
            frame(5, [event("damage", "p1a", "Blaziken", "80/156")])]


def faint_hud_trace(same_frame=False):
    faint = event("faint", "p1a", "Rillaboom")
    entry = event("switch", "p1a", "Rillaboom")
    trace = [frame(1, [event("switch", "p1a", "Rillaboom", "10/100"), event("turn", turn=1)]),
             frame(2, [event("damage", "p1a", "Rillaboom", "0/100")]),
             frame(6, [entry, faint] if same_frame else [faint], texts=["Gori fainted!"]),
             frame(7, [] if same_frame else [entry], texts=["Gori fainted!"])]
    trace[0]["resolved_aliases"]["p1"]["gori"] = "Rillaboom"
    trace[2]["ocr"].append({"text": "0/100", "left": .14, "right": .20,
                             "top": .92, "bottom": .96, "confidence": .999})
    return trace


def opponent_menu_hud_trace():
    """The p2a percentage reappears while p2b stays occupied after a menu."""
    raw = "__champions_actor_p2_0001__"
    trace = [frame(n) for n in range(1, 25)]
    trace[0]["detections"]["events"] = [event("switch", "p2a", "Hatterene", "100/100"),
                                         event("switch", "p2b", "Indeedee-F", "100/100"),
                                         event("turn", turn=1)]
    trace[1]["detections"]["events"] = [event("damage", "p2a", "Hatterene", "73/100")]
    trace[2]["detections"]["events"] = [event("damage", "p2b", "Indeedee-F", "0/100")]
    trace[3]["detections"]["events"] = [event("faint", "p2b", "Indeedee-F")]
    trace[5]["detections"]["events"] = [event("switch", "p2b", "Baxcalibur", "100/100")]
    trace[8]["detections"]["events"] = [event("move", "p2b", "Baxcalibur", move="Protect")]
    trace[11]["detections"]["events"] = [event("switch", "p2b", raw, "73/100")]
    trace[17]["detections"]["events"] = [event("switch", "p2b", "Baxcalibur", "100/100")]
    for row in trace:
        n = row["frame"]
        for detected in row["detections"]["events"]:
            detected.update(timestamp_ms=n * 500, source_frame=n - 1)
        row["resolved_aliases"]["p2"].update({"waistis": "Hatterene", "ringo": "Indeedee-F"})
        row["resolved_identities"][raw] = "Indeedee-F"
        row["ocr"] = []
        for slot, name, hp, x in (("p2a", "Waistis", "100%" if n == 1 else "73%", .63),
                                  ("p2b", "Ringo" if n <= 4 else "Baxcalibur",
                                   "0%" if n in (3, 4) else "100%", .83)):
            if slot == "p2b" and n == 5:
                continue
            row["ocr"].extend([{"text": name, "confidence": .999, "left": x,
                                "right": x + .08, "top": .05, "bottom": .09},
                               {"text": hp, "confidence": .999,
                                "left": .70 if slot == "p2a" else .92,
                                "right": .75 if slot == "p2a" else .97,
                                "top": .12, "bottom": .16}])
        if n in (4, 5):
            row["ocr"].append({"text": "The opposing Ringo fainted!", "top": .75,
                               "confidence": .999})
        if n == 18:
            row["ocr"].append({"text": "MOVE TIME", "top": .28, "confidence": .999})
    return trace


def returning_hud_trace(species="Kingambit", name="Kingambit"):
    raw = "__champions_actor_p2_0001__"
    partial = name[:-1].casefold()
    trace = [frame(1, [event("switch", "p2b", species, "6/100"), event("turn", turn=1),
                      event("move", "p2b", species, move="Protect")]),
             frame(2, [event("heal", "p2b", species, "12/100")]),
             frame(3), frame(4, [event("switch", "p2b", raw)]),
             frame(5, [event("turn", turn=2)]), frame(6),
             frame(10, [event("move", "p2b", species, move="Protect")])]
    for i in (1, 4, 5):
        trace[i]["ocr"].append({"text": name, "left": .84, "right": .90,
                                  "top": .05, "bottom": .09, "confidence": .999})
        trace[i]["resolved_aliases"]["p2"][name.casefold()] = species
    trace[3]["ocr"].append({"text": partial, "left": .87, "right": .94,
                              "top": .05, "bottom": .09, "confidence": .933})
    for i in (4, 5):
        trace[i]["ocr"].append({"text": "12%", "left": .92, "right": .97,
                                  "top": .12, "bottom": .16, "confidence": .999})
        trace[i]["resolved_identities"][raw] = species
        trace[i]["resolved_aliases"]["p2"][partial] = species
    return trace


def pending_hp_trace(zero=False):
    trace = [frame(1, [event("switch", "p2a", "Delphox", "64/100"), event("turn", turn=1),
                      event("move", "p2a", "Delphox", move="Protect")]),
             frame(2, [event("damage" if zero else "heal", "p2a", "Delphox", "0/100" if zero else "82/100")]),
             frame(3), frame(4), frame(5), frame(6)]
    trace[1]["ocr"][0]["confidence"] = .82
    for row in trace[1:]:
        row["ocr"].append({"text": "Delphox", "left": .62, "right": .69,
                           "top": .04, "bottom": .08, "confidence": .999})
    if zero:
        trace[3]["ocr"].append({"text": "0%", "left": .70, "right": .75,
                                  "top": .12, "bottom": .16, "confidence": .95})
        trace[3]["detections"]["events"] = [event("faint", "p2a", "Delphox")]
        for row in trace[3:5]:
            row["ocr"].append({"text": "The opposing Delphox fainted!", "top": .75, "confidence": .99})
    else:
        trace[4]["ocr"].extend([
            {"text": "82", "left": .70, "right": .735, "top": .12, "bottom": .16, "confidence": .999},
            {"text": "%", "left": .736, "right": .75, "top": .13, "bottom": .16, "confidence": .999}])
    return trace


def sliding_hp_trace():
    trace = [frame(n) for n in range(1, 9)]
    trace[0] = frame(1, [event("switch", "p2a", "Excadrill", "100/100"), event("turn", turn=1)])
    for row in trace:
        row["resolved_aliases"]["p2"]["excadrill"] = "Excadrill"
    for row in trace[:3]:
        row["ocr"].append({"text": "Excadrill", "left": .625, "right": .688,
                           "top": .05, "bottom": .084, "confidence": .999})
    for row in trace[1:3]:
        row["ocr"].append({"text": "100%", "left": .689, "right": .756,
                           "top": .108, "bottom": .162, "confidence": .999})
    trace[3] = frame(4, [event("damage", "p2a", "Excadrill", "0/100")])
    trace[3]["ocr"] = [{"text": "adrill", "left": .722, "right": .766,
                         "top": .045, "bottom": .086, "confidence": .999},
                        {"text": "00%", "left": .785, "right": .829,
                         "top": .118, "bottom": .154, "confidence": .999}]
    trace[4] = frame(5, [event("move", "p2a", "Excadrill", move="Protect")])
    trace[5] = frame(6, [event("heal", "p2a", "Excadrill", "100/100")])
    trace[6] = frame(7, [event("damage", "p2a", "Excadrill", "68/100")])
    return trace


def transient_mega_text_trace(species="Delphox", stone="Delphoxite", forme="Delphox-Mega"):
    bad = f"The opposing {species}'s {stone} is racting to Rival's Omni Ring!"
    clean = f"The opposing {species}'s {stone} is reacting to Rival's Omni Ring!"
    mega = {**event("mega", "p2a", species, value=stone), "forme": forme}
    return [frame(1, [event("switch", "p2a", species, "100/100"), event("turn", turn=1)]),
            frame(2, [event("message", value=bad)], texts=[bad]), frame(3), frame(4, [mega], texts=[clean])]


def sliding_hud_trace(side="p1", species="Blaziken", partner="Kingambit"):
    raw = f"__champions_actor_{side}_0001__"
    trace = [frame(1, [event("switch", side + "a", raw, "100/100"),
                       event("switch", side + "b", partner, "100/100")]),
             frame(2, [event("turn", turn=1)]), frame(3), frame(4)]
    x, y = (.08, .86) if side == "p1" else (.62, .05)
    for i, row in enumerate(trace):
        row["resolved_aliases"][side].update({"alpha": species, "beta": partner})
        row["ocr"].append({"text": "Beta" if i == 0 else "Alpha", "left": x + .16 if i == 0 else x,
                           "top": y, "confidence": .999})
        if i:
            row["ocr"].append({"text": "Beta", "left": x + .21, "top": y, "confidence": .999})
    return trace


def delayed_entry_trace(side="p1", title="the Paldea Champion"):
    slot = side + "a"
    text = ("Go! " if side == "p1" else "Rival sent out ") + "Alpha " + title + "!"
    trace = [frame(n) for n in range(1, 97)]
    trace[0] = frame(1, [event("switch", slot, "Toxtricity", "10/100"), event("turn", turn=1)])
    trace[1] = frame(2, [event("damage", slot, "Toxtricity", "0/100")])
    trace[2] = frame(3, [event("faint", slot, "Toxtricity")])
    trace[4] = frame(5, texts=[text])
    trace[5] = frame(6, texts=[text])
    trace[49] = frame(50, [event("move", slot, "Pelipper", move="Protect")])
    trace[94] = frame(95, [event("switch", slot, "Pelipper", "100/100")])
    for row in trace:row["resolved_aliases"][side]["alpha"] = "Pelipper"
    for n in (10, 11):
        x, y, hp_x, hp_y = (.08, .86, .14, .92) if side == "p1" else (.62, .05, .70, .12)
        trace[n-1]["ocr"] = [
            {"text": "Alpha", "left": x, "top": y, "confidence": .999},
            {"text": "100/100" if side == "p1" else "100%", "left": hp_x, "top": hp_y, "confidence": .999}]
    return trace


def delayed_health_trace(side="p1", species="Pelipper"):
    trace = delayed_entry_trace(side)
    slot = side + "a"
    for row in trace:
        row["resolved_aliases"][side]["alpha"] = species
        for e in row["detections"]["events"]:
            if e.get("species") == "Pelipper":e["species"] = species
    prefix = "The opposing " if side == "p2" else ""
    for n in (25, 26):trace[n-1]["ocr"].append({"text": prefix + "Alpha used Fake Out!", "top": .75, "confidence": .99})
    buffered = event("move", side + "b", species, move="Fake Out")
    buffered.update(source_frame=25, timestamp_ms=12500)
    trace[94]["detections"]["events"].append(buffered)
    trace[94]["detections"]["events"][0]["health"] = "46/100"
    trace[94]["ocr"] = []
    trace[11]["detections"]["events"] = [event("fieldstart", value="Grassy Terrain")]
    restored = prefix + "Alpha had its HP restored."
    trace[75]["detections"]["events"] = [event("message", value=restored)]
    trace[75]["ocr"].append({"text": restored, "top": .75, "confidence": .99})
    for n, hp in ((54, 70), (55, 40), (56, 40), (74, 42), (75, 46), (76, 46), (95, 46)):
        x, y, hp_x, hp_y = (.08, .86, .14, .92) if side == "p1" else (.62, .05, .70, .12)
        trace[n-1]["ocr"].extend([
            {"text": "Alpha", "left": x, "top": y, "confidence": .999},
            {"text": f"{hp}/100" if side == "p1" else f"{hp}%", "left": hp_x, "top": hp_y, "confidence": .999}])
    return trace


def partial_faint_trace(side="p1", name="Tomoe", species="Kingambit"):
    slot = side + "a"
    prefix = "The opposing " if side == "p2" else ""
    bad = prefix + name[1:] + " fainted!"
    clean = prefix + name + " fainted!"
    trace = [frame(1, [event("switch", slot, species, "10/100"), event("turn", turn=1),
                      event("move", slot, species, move="Double-Edge")]),
             frame(2, [event("damage", slot, species, "0/100")]),
             frame(3, [event("faint", slot, species)], texts=[bad]),
             frame(4), frame(5, [event("faint", slot, species)], texts=[clean]),
             frame(6, texts=[clean])]
    for row in trace:row["resolved_aliases"][side][name.casefold()] = species
    trace[2]["ocr"][0]["confidence"] = .99999
    trace[4]["ocr"][0]["confidence"] = .98
    return trace


class TemporalAutomatonTests(unittest.TestCase):
    def test_archived_action_outside_narration_is_rejected_with_original_ocr(self):
        for kind, text in (("move", "Arcanine used Protect!"), ("faint", "Arcanine fainted!"),
                           ("message", "Arcanine was hurt by its burn!")):
            trace = [frame(1, [event("switch", "p1a", "Arcanine", "180/180")]),
                     frame(2, [event(kind, "p1a", "Arcanine", move="Protect" if kind == "move" else None,
                                     value=text if kind == "message" else None)]), frame(3)]
            trace[1]["ocr"] = [{"text": text, "confidence": .99, "left": .4, "top": .34}]
            original = copy.deepcopy(trace)
            ledger = BattleAutomaton(0, trace).run()
            rejected = ledger["events"][-1]
            with self.subTest(kind=kind):
                self.assertEqual((rejected["kind"], rejected["status"]), ("ui_text", "suppressed"))
                self.assertEqual(rejected["ui_support"]["evidence"][0]["text"], text)
                self.assertEqual(trace, original)
                self.assertFalse(next(iter(ledger["actors"].values()))["fainted"])

    def test_valid_adjacent_narration_protects_an_archived_event_with_off_area_noise(self):
        trace = [frame(1, [event("switch", "p1a", "Arcanine", "180/180")]),
                 frame(2, [event("move", "p1a", "Arcanine", move="Protect")]), frame(3)]
        trace[1]["ocr"] = [{"text": "Arcanine used Protect!", "confidence": .99, "left": .4, "top": .34}]
        trace[2]["ocr"] = [{"text": "Arcanine used Protect!", "confidence": .99, "left": .15, "top": .72}]
        ledger = BattleAutomaton(0, trace).run()
        move = ledger["events"][-1]
        self.assertEqual((move["kind"], move["status"]), ("move", "consistent"))
        self.assertEqual([e["frame"] for e in move["evidence"]], [3])

    def test_literal_alias_override_requires_complete_repeated_local_evidence(self):
        from champions_automaton import corroborated_literal_aliases
        trace = [frame(1, [event("switch", "p2a", "Whimsicott", "100/100")]), frame(2)]
        for row in trace:
            row["resolved_aliases"]["p2"]["whimsicott"] = "Rotom-Wash"
            row["ocr"] = [{"text": "Whimsicott", "left": .62, "top": .04, "confidence": .99},
                           {"text": "100%", "left": .70, "top": .12, "confidence": .99}]
        self.assertEqual(corroborated_literal_aliases(trace)[0]["species"], "Whimsicott")
        for change in ("weak", "other_name", "malformed_hp", "gap", "other_battle"):
            altered = copy.deepcopy(trace)
            if change == "weak": altered[1]["ocr"][0]["confidence"] = .8
            elif change == "other_name": altered[1]["ocr"][0]["text"] = "Rotom"
            elif change == "malformed_hp": altered[1]["ocr"][1]["text"] = "100100"
            elif change == "gap": altered[1]["timestamp_ms"] = 10_000
            else: altered[1]["battle_index"] = 1
            with self.subTest(change=change):
                self.assertEqual(corroborated_literal_aliases(altered), [])

    def test_background_alias_cannot_create_entry_but_real_plate_is_protected(self):
        raw = "__champions_actor_p2_0001__"
        trace = [frame(1, [event("switch", "p2a", "Garchomp", "100/100")]),
                 frame(2, [event("switch", "p2a", raw)])]
        trace[1]["resolved_aliases"]["p2"]["noise"] = "Rotom-Wash"
        trace[1]["resolved_identities"][raw] = "Rotom-Wash"
        trace[1]["ocr"] = [{"text": "noise", "left": .45, "top": .04, "confidence": .6}]
        original = copy.deepcopy(trace)
        ledger = BattleAutomaton(0, trace).run()
        rejected = ledger["events"][-1]
        self.assertEqual((rejected["kind"], rejected["status"]), ("ui_text", "suppressed"))
        self.assertEqual(len(ledger["actors"]), 1)
        self.assertEqual(trace, original)
        for change in ("valid_plate", "announcement", "missing_noise"):
            altered = copy.deepcopy(trace)
            if change == "valid_plate":
                altered[1]["ocr"][0].update(left=.62, confidence=.99)
            elif change == "announcement":
                altered[1]["ocr"].append({"text": "Rival sent out Rotom!", "top": .75, "confidence": .99})
            else:
                altered[1]["ocr"] = []
            machine = BattleAutomaton(0, altered)
            candidate = {"event": altered[1]["detections"]["events"][0], "observed_frame": 2}
            with self.subTest(change=change):
                self.assertIsNone(machine._background_placeholder_entry(candidate))

    def test_later_full_hud_supplies_unknown_maximum_without_repairing_digits(self):
        trace = [frame(1, [event("switch", "p1a", "Arcanine")])] + [frame(n) for n in range(2, 21)]
        trace[1]["ocr"] = [{"text": "Arcanine", "left": .08, "top": .86, "confidence": .99},
                             {"text": "180180", "left": .14, "top": .92, "confidence": .999}]
        trace[2]["detections"]["events"] = [event("move", "p1a", "Arcanine", move="Protect")]
        trace[-1]["ocr"] = [{"text": "Arcanine", "left": .08, "top": .86, "confidence": .99},
                             {"text": "180/180", "left": .14, "top": .92, "confidence": .99}]
        ledger = BattleAutomaton(0, trace).run()
        entry = ledger["events"][0]
        self.assertEqual((entry["health"], entry["hp_state"]), ("180/180", "inferred"))
        self.assertEqual(entry["maximum_support"]["evidence"][0]["frame"], 20)
        for change in ("missing_slash", "other_actor", "weak", "replacement", "damage"):
            altered = copy.deepcopy(trace)
            if change == "missing_slash": altered[-1]["ocr"][1]["text"] = "180180"
            elif change == "other_actor": altered[-1]["ocr"][0]["text"] = "Ninetales"
            elif change == "weak": altered[-1]["ocr"][1]["confidence"] = .8
            elif change == "replacement": altered[1]["detections"]["events"] = [event("switch", "p1a", "Ninetales")]
            else:
                altered[1]["detections"]["events"] = [event("damage", "p1a", "Arcanine", "100/180")]
                altered[1]["ocr"][1]["text"] = "100/180"
            with self.subTest(change=change):
                self.assertNotIn("maximum_support", BattleAutomaton(0, altered).run()["events"][0])

    def test_repeated_move_subject_corrects_slot_and_requires_unique_active_actor(self):
        trace = [frame(1, [event("switch", "p2a", "Whimsicott", "100/100"),
                            event("switch", "p2b", "Rotom-Wash", "100/100")]),
                 frame(2, [event("move", "p2b", "Whimsicott", move="Tailwind")],
                       ["The opposing Whimsicott used Tailwind!"]),
                 frame(3, texts=["The opposing Whimsicott used Tailwind!"])]
        corrected = BattleAutomaton(0, trace).run()["events"][-1]
        self.assertEqual((corrected["slot"], corrected["status"]), ("p2a", "consistent"))
        self.assertEqual(corrected["original_slot"], "p2b")
        for change in ("single_reading", "other_subject", "weak"):
            altered = copy.deepcopy(trace)
            if change == "single_reading": altered[2]["ocr"] = []
            elif change == "other_subject": altered[2]["ocr"][0]["text"] = "The opposing Rotom used Tailwind!"
            else: altered[2]["ocr"][0]["confidence"] = .8
            with self.subTest(change=change):
                self.assertIn("actor_mismatch", {i["code"] for i in BattleAutomaton(0, altered).run()["issues"]})

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_2205"), "Requiere diagnóstico 2205")
    def test_real_2205_preserves_two_opponents_and_exports_through_production(self):
        import tempfile
        from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_2205"])
        frames, _ = read_diagnostic(path)
        original = copy.deepcopy(frames)
        ledger = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(frames, original)
        entries = [e for e in ledger["events"] if e["kind"] == "switch" and e["status"] == "consistent"]
        self.assertEqual([(e["slot"], e["species"]) for e in entries if e["slot"].startswith("p2")][:2],
                         [("p2a", "Whimsicott"), ("p2b", "Rotom-Wash")])
        self.assertEqual(sum(a["species"] == "Rotom-Wash" for a in ledger["actors"].values()), 1)
        self.assertEqual(sum(a["species"] == "Whimsicott" for a in ledger["actors"].values()), 1)
        faint = next(e for e in ledger["events"] if e["frame"] == 481)
        self.assertEqual(faint["species"], "Whimsicott")
        self.assertEqual([e["kind"] for e in ledger["events"] if e["frame"] in {390, 762, 949}], ["ui_text"] * 3)
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(path) as archive:
            root = Path(directory)
            job = json.loads(archive.read("job.json"))
            trace = root / "trace.jsonl"
            trace.write_bytes(archive.read("output/ocr.trace.jsonl"))
            documents = documents_from_trace(trace, job, root / "output")
            self.assertEqual(len(documents), 1)
            report = json.loads((root / "output/ledger-report.json").read_text())
            self.assertEqual((report["status"], report["replay_count"]), ("ready", 1))

    def test_knock_off_after_faint_keeps_victim_identity_without_restoring_occupancy(self):
        for side, item in (("p1", "Life Orb"), ("p2", "Choice Scarf")):
            other = "p2" if side == "p1" else "p1"
            slot, source = side + "b", other + "a"
            text = ("The opposing " if other == "p2" else "") + "Striker knocked off " + (
                "the opposing " if side == "p2" else "") + "Victim's " + item + "!"
            rows = [frame(1, [event("switch", slot, "Kingambit", "100/100"),
                               event("switch", source, "Sneasler", "100/100"), event("turn", turn=1)]),
                    frame(2, [event("move", source, "Sneasler", move="Knock Off")]),
                    frame(3, [event("damage", slot, "Kingambit", "0/100")]),
                    frame(4, [event("faint", slot, "Kingambit")]), frame(5),
                    frame(6, [event("enditem", slot, value=item)], [text]), frame(7, texts=[text]),
                    frame(8, [event("message", value="The battle has ended due to a forfeit.")])]
            rows[5]["detections"]["events"][0]["tags"] = ["[from] move: Knock Off", "[of] " + source + ": Sneasler"]
            for row in rows:
                row["resolved_aliases"][side]["victim"] = "Kingambit"
                row["resolved_aliases"][other]["striker"] = "Sneasler"
            original = copy.deepcopy(rows)
            automaton = BattleAutomaton(0, rows)
            ledger = automaton.run()
            self.assertEqual(rows, original)
            self.assertEqual(ledger["issues"], [])
            lost = next(e for e in ledger["events"] if e["kind"] == "enditem")
            faint = next(e for e in ledger["events"] if e["kind"] == "faint")
            self.assertEqual(lost["actor_id"], faint["actor_id"])
            self.assertNotIn(slot, automaton.active)
            self.assertTrue(ledger["actors"][lost["actor_id"]]["fainted"])
            self.assertTrue(ledger["actors"][lost["actor_id"]]["item_lost"])
            repeated = copy.deepcopy(rows)
            repeated[6]["detections"]["events"] = copy.deepcopy(repeated[5]["detections"]["events"])
            repeated[7]["ocr"].extend(copy.deepcopy(repeated[6]["ocr"]))
            repeated_ledger = BattleAutomaton(0, repeated).run()
            self.assertEqual(repeated_ledger["issues"], [])
            self.assertEqual([e["status"] for e in repeated_ledger["events"] if e["kind"] == "enditem"],
                             ["consistent", "suppressed"])
            for invalid in ("text", "item", "source", "move", "action", "turn", "replacement", "time", "weak", "zero", "future_time"):
                changed = copy.deepcopy(rows)
                if invalid == "text": changed[6]["ocr"] = []
                elif invalid == "item": changed[5]["detections"]["events"][0]["value"] = "Leftovers"
                elif invalid == "source": changed[5]["detections"]["events"][0]["tags"][1] = "[of] " + source + ": Pelipper"
                elif invalid == "move": changed[1]["detections"]["events"][0]["move"] = "Tackle"
                elif invalid == "action": changed[4]["detections"]["events"] = [event("move", source, "Sneasler", move="Protect")]
                elif invalid == "turn": changed[4]["detections"]["events"] = [event("turn", turn=2)]
                elif invalid == "replacement": changed[4]["detections"]["events"] = [event("switch", slot, "Pelipper", "100/100")]
                elif invalid == "time": changed[5]["timestamp_ms"] += 10_000
                elif invalid == "weak": changed[6]["ocr"][0]["confidence"] = .8
                elif invalid == "future_time": changed[6]["timestamp_ms"] += 10_000
                else: changed[2]["ocr"] = []
                uncertain = BattleAutomaton(0, changed).run()
                effect = next(e for e in uncertain["events"] if e["kind"] == "enditem")
                self.assertEqual(effect["status"], "review", (side, invalid))
                self.assertTrue(any(i["code"] == "unbound_item_actor" for i in uncertain["issues"]), (side, invalid))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_32B"), "Requiere diagnóstico 32b")
    def test_32b_lethal_knock_off_preserves_item_victim_after_faint(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_32B"])
        rows, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, rows, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        lost = next(e for e in ledger["events"] if e["kind"] == "enditem")
        self.assertEqual((lost["species"], lost["value"], lost["frame"]), ("Basculegion", "Life Orb", 346))
        self.assertEqual(lost["post_faint_item_support"]["faint_seq"], 15)
        from ledger_replay import build_replay, load_trace_context
        replay = build_replay(ledger, load_trace_context(path, ledger))
        self.assertIn("|-enditem|p1b: Basculegion|Life Orb|[from] move: Knock Off|[of] p2b: Malamar", replay["log"])
        self.assertEqual(replay["log"].count("|faint|p1b: Basculegion"), 1)
        self.assertLess(replay["log"].index("|faint|p1b: Basculegion"), replay["log"].index("|-enditem|p1b: Basculegion"))

    def test_incomplete_first_entry_hud_needs_consecutive_named_complete_readings(self):
        trace = buffered_named_entry_trace()
        trace[126]["ocr"][1]["text"] = "141177"
        lookup = {r["frame"]: r for r in trace}
        self.assertEqual([r["frame"] for r in entry_hud_pair(lookup, 127, "p1b", "dee dee")], [128, 129])
        for missing in ("owner", "peer", "action", "raw_action", "replacement", "time", "second", "outside"):
            rows = copy.deepcopy(trace)
            if missing == "owner": rows[127]["ocr"][0]["text"] = "Another"
            elif missing == "peer": rows[127]["ocr"].append({"text": "Dee Dee", "left": .08, "top": .86, "confidence": .999})
            elif missing == "action": rows[127]["detections"]["events"].append(event("move", "p2a", "Pelipper", move="Protect"))
            elif missing == "raw_action": rows[127]["ocr"].append({"text": "Dee Dee used Protect!", "top": .73, "confidence": .999})
            elif missing == "replacement": rows[127]["detections"]["events"].append(event("switch", "p1b", "Gardevoir"))
            elif missing == "time": rows[127]["timestamp_ms"] += 2_000
            elif missing == "second": rows[128]["ocr"] = rows[129]["ocr"] = []
            else:
                for row in rows[127:130]: row["ocr"][1]["text"] = "99177"
            self.assertEqual(entry_hud_pair({r["frame"]: r for r in rows}, 127, "p1b", "dee dee"), [], missing)

    def test_voluntary_entry_evidence_handles_shorter_delay_both_slots_and_other_names(self):
        for slot, name, species, ability, shorter in (
            ("p1a", "Moon", "Indeedee-F", "Psychic Surge", True),
            ("p1b", "Sky", "Pelipper", "Drizzle", True),
            ("p1a", "Star", "Indeedee-F", "Psychic Surge", False),
        ):
            with self.subTest(slot=slot, name=name, species=species, shorter=shorter):
                rows = buffered_named_entry_trace()
                for row in rows:
                    for e in row["detections"]["events"]:
                        if e.get("slot") == "p1b": e["slot"] = slot
                        if e.get("species") == "Indeedee-F": e["species"] = species
                        if e.get("value") == "Psychic Surge": e["value"] = ability
                        if e.get("health") in {"141/177", "99/177"}: e["health"] = "177/177"
                        e["tags"] = [tag.replace("p1b:", slot + ":").replace("Indeedee-F", species) for tag in e.get("tags", ())]
                    for line in row["ocr"]:
                        line["text"] = line["text"].replace("Dee Dee", name).replace("Psychic Surge", ability)
                        if line["text"] in {"141/177", "99/177"}: line["text"] = "177/177"
                        if slot == "p1a" and line.get("left") in {.29, .34}:
                            line["left"] = .08 if line["left"] == .29 else .14
                rows[126]["ocr"][1]["text"] = "177177"
                rows[127]["detections"]["events"] = []
                rows[39] = frame(40, [event("move", "p2a", "Pelipper", move="Protect")],
                                 ["The opposing Pelipper used Protect!"])
                if shorter:
                    rows = [r for r in rows if not 50 <= r["frame"] < 90]
                    for row in rows:
                        if row["frame"] >= 90: row["frame"] -= 40
                        row["timestamp_ms"] = row["frame"] * 500
                        for e in row["detections"]["events"]:
                            e.update(timestamp_ms=row["timestamp_ms"], source_frame=row["frame"] - 1)
                original = copy.deepcopy(rows)
                ledger = BattleAutomaton(0, rows, {"teams": {"p1": [species, "Gardevoir", "Blaziken"]}}).run()
                self.assertEqual(rows, original)
                self.assertEqual(ledger["issues"], [])
                entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == species and e["slot"] == slot)
                self.assertEqual((entry["logical_frame"], entry["health"], entry["hp_state"]), (26, "177/177", "inferred"))
                self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "ability")["logical_frame"], 33)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_DCB33"), "Requiere diagnóstico dcb33")
    def test_dcb33_incomplete_entry_hud_keeps_entry_ability_terrain_before_actions(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_DCB33"])
        rows, _ = read_diagnostic(path)
        original = copy.deepcopy(rows)
        ledger = BattleAutomaton(0, rows, read_diagnostic_context(path)).run()
        self.assertEqual(rows, original)
        self.assertEqual(ledger["issues"], [])
        entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Indeedee-F")
        self.assertEqual((entry["logical_frame"], entry["health"], entry["hp_state"]), (283, "177/177", "inferred"))
        self.assertEqual(entry["delayed_voluntary_entry"]["incoming_hud_frames"], [345, 346])
        self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "ability" and e["value"] == "Psychic Surge")["logical_frame"], 290)
        self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "fieldstart" and e["value"] == "move: Psychic Terrain")["logical_frame"], 292)
        self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "faint" and e["species"] == "Indeedee-F")["status"], "consistent")

    def test_opening_pair_requires_announcement_two_named_huds_and_no_action(self):
        trace = opponent_opening_swap_trace()
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        entries = [e for e in ledger["events"] if e["kind"] == "switch"]
        self.assertEqual([(e["slot"], e["species"], e["health"]) for e in entries
                          if e["status"] == "consistent"],
                         [("p2a", "Arcanine-Hisui", "100/100"),
                          ("p2b", "Sneasler", "100/100")])
        self.assertEqual([e["status"] for e in entries if e["frame"] == 3],
                         ["suppressed", "suppressed"])
        self.assertTrue(all(e["logical_frame"] == 1 for e in entries
                            if e["status"] == "consistent"))

        for missing in ("announcement", "hud", "action"):
            uncertain = copy.deepcopy(trace)
            if missing == "announcement":
                uncertain[0]["ocr"] = []
            elif missing == "hud":
                uncertain[3]["ocr"] = [line for line in uncertain[3]["ocr"]
                                        if not (line.get("left") == .92 and line["text"] == "100%")]
            else:
                uncertain[3]["detections"]["events"] = [event("move", "p2a", "Arcanine-Hisui",
                                                                move="Protect")]
            candidates = ordered_candidates(uncertain)
            self.assertFalse(any(c.get("opening_pair") for c in candidates), missing)

    def test_buffered_entry_uses_named_ability_hud_and_orders_before_attacks(self):
        trace = buffered_named_entry_trace()
        original = copy.deepcopy(trace)
        context = {"teams": {"p1": ["Indeedee-F", "Gardevoir", "Blaziken"]}}
        ledger = BattleAutomaton(0, trace, context).run()
        self.assertEqual(trace, original)
        self.assertEqual(ledger["issues"], [])
        entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Indeedee-F")
        self.assertEqual((entry["logical_frame"], entry["health"], entry["hp_state"]), (26, "177/177", "inferred"))
        ability = next(e for e in ledger["events"] if e["kind"] == "ability")
        terrain = next(e for e in ledger["events"] if e["kind"] == "fieldstart")
        self.assertEqual((ability["logical_frame"], terrain["logical_frame"]), (33, 35))
        hp = [e for e in ledger["events"] if e["kind"] == "damage" and e["species"] == "Indeedee-F"]
        self.assertEqual([(e["before"], e["after"]) for e in hp], [("177/177", "99/177"), ("99/177", "0/177")])
        self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "faint")["status"], "consistent")
        for missing in ("ability", "announcement", "hud", "outgoing", "endpoint", "continuity", "ambiguous_ability", "illusion"):
            rows, ctx = copy.deepcopy(trace), copy.deepcopy(context)
            if missing == "ability":
                rows[32]["ocr"] = []
            elif missing == "announcement":
                rows[25]["ocr"] = []
            elif missing == "hud":
                rows[127]["ocr"] = []
            elif missing == "outgoing":
                rows[18]["ocr"] = []
            elif missing == "endpoint":
                rows[128]["ocr"] = rows[129]["ocr"] = []
            elif missing == "continuity":
                rows[50]["timestamp_ms"] += 2_000
            elif missing == "ambiguous_ability":
                ctx["teams"]["p1"].append("Tapu Lele")
            else:
                ctx["teams"]["p1"].append("Zoroark-Hisui")
            uncertain = BattleAutomaton(0, rows, ctx).run()
            self.assertFalse(any(e["kind"] == "switch" and e["species"] == "Indeedee-F" and e["logical_frame"] == 26
                                 for e in uncertain["events"]), missing)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_FDD2"), "Requiere diagnóstico fdd2")
    def test_fdd2_three_warnings_and_buffered_entry(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_FDD2"])
        rows, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, rows, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Indeedee-F")
        self.assertEqual((entry["logical_frame"], entry["health"], entry["hp_state"]), (246, "177/177", "inferred"))
        first_damage = next(e for e in ledger["events"] if e["kind"] == "damage" and e["actor_id"] == entry["actor_id"])
        self.assertEqual((first_damage["before"], first_damage["after"]), ("177/177", "99/177"))
        self.assertEqual(next(e for e in ledger["events"] if e["frame"] == 442)["status"], "consistent")
        heal = next(e for e in ledger["events"] if e["kind"] == "heal" and e["slot"] == "p2a" and e["frame"] == 751)
        self.assertEqual((heal["before"], heal["after"]), ("28/100", "33/100"))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_7C47"), "Requiere diagnóstico 7c47")
    def test_dired_one_opening_and_lethal_damage_from_diagnostic(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_7C47"])
        frames, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertFalse(ledger["issues"])
        opening = [(e["slot"], e["species"]) for e in ledger["events"]
                   if e["kind"] == "switch" and e["status"] == "consistent" and e["turn"] == 0]
        self.assertIn(("p2a", "Arcanine-Hisui"), opening)
        self.assertIn(("p2b", "Sneasler"), opening)
        self.assertEqual([(e["slot"], e["before"], e["after"]) for e in ledger["events"]
                          if e["kind"] == "damage" and e["status"] == "consistent" and
                          e["frame"] == 278], [("p2b", "100/100", "0/100")])

    def test_delayed_terrain_heal_requires_animation_narration_and_stable_hud(self):
        trace = delayed_terrain_heal_trace()
        ledger = BattleAutomaton(0, trace).run()
        heal = next(e for e in ledger["events"] if e["kind"] == "heal")
        self.assertEqual((heal["frame"], heal["turn"], heal["before"], heal["after"],
                          heal["hp_state"]), (8, 1, "76/100", "82/100", "confirmed"))
        self.assertEqual(heal["terrain_restoration_support"]["reported_frame"], 30)
        self.assertEqual(heal["cause"], "Grassy Terrain corroborado por HUD y mensaje")
        self.assertEqual([(l["frame"], l["event_seq"], l["status"]) for l in ledger["narration_links"]],
                         [(10, heal["seq"], "linked")])

        for variant in ("no_second_message", "no_stable_hud", "wrong_actor", "changed_terrain"):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(trace)
                if variant == "no_second_message":
                    changed[10]["ocr"] = []
                elif variant == "no_stable_hud":
                    changed[27]["ocr"] = [line for line in changed[27]["ocr"] if line["text"] != "82%"]
                elif variant == "wrong_actor":
                    changed[7]["ocr"] = [{**line, "text": "Salamence"} if line["text"] == "Rillaboom"
                                          else line for line in changed[7]["ocr"]]
                else:
                    changed[6]["detections"]["events"].append(event("fieldend", value="Grassy Terrain"))
                rejected = BattleAutomaton(0, changed).run()
                late = next(e for e in rejected["events"] if e["kind"] == "heal")
                self.assertEqual(late["frame"], 30)
                self.assertNotIn("terrain_restoration_support", late)

    def test_trick_room_repeated_ocr_has_one_start_and_one_end(self):
        trace = [frame(1, [event("fieldstart", value="move: Psychic Terrain")]),
                 frame(2, [event("fieldstart", value="move: Trick Room")],
                       texts=["Dee Dee twisted the dimensions!"]),
                 frame(4, [event("fieldstart", value="Trick Room")],
                       texts=["Dee ee twisted the dimensions!"]),
                 frame(5, [event("fieldstart", value="move: Trick Room")],
                       texts=["Dee Dee twisted the dimensions!"]),
                 frame(30, [event("fieldend", value="move: Trick Room")],
                       texts=["The twisted dimensions returned to normal!"]),
                 frame(31, [event("fieldend", value="Trick Room")],
                       texts=["The twisted dimensions returned to normal!"])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        room = [e for e in ledger["events"] if "Trick Room" in str(e.get("value"))]
        self.assertEqual([(e["kind"], e["frame"]) for e in room if e["status"] == "consistent"],
                         [("fieldstart", 2), ("fieldend", 30)])
        self.assertTrue(all(e["evidence"] for e in room))
        self.assertEqual([e["frame"] for e in room[0]["observations"]], [4, 5])
        self.assertEqual([e["field_state_seq"] for e in room if e["status"] == "suppressed"], [2, 2, 5])
        self.assertEqual(ledger["events"][0]["status"], "consistent")

    def test_trick_room_can_end_early_and_restart_without_erasing_moves(self):
        trace = [frame(1, [event("switch", "p1a", "Indeedee-F", "177/177"),
                          event("turn", turn=1), event("move", "p1a", "Indeedee-F", move="Trick Room")]),
                 frame(2, [event("fieldstart", value="move: Trick Room")]),
                 frame(10, [event("move", "p1a", "Indeedee-F", move="Trick Room")]),
                 frame(11, [event("fieldend", value="Trick Room")]),
                 frame(20, [event("move", "p1a", "Indeedee-F", move="Trick Room")]),
                 frame(21, [event("fieldstart", value="Trick Room")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual([e["kind"] for e in ledger["events"] if e["status"] == "consistent"],
                         ["switch", "turn", "move", "fieldstart", "move", "fieldend", "move", "fieldstart"])
        # No inferred closure: a battle can finish while Trick Room is active.
        self.assertEqual(ledger["counts"]["fieldend"], 1)

    def test_trick_room_end_without_start_remains_an_issue(self):
        ledger = BattleAutomaton(0, [frame(1, [event("fieldend", value="move: Trick Room")])]).run()
        self.assertEqual(ledger["events"][0]["status"], "review")
        self.assertEqual(ledger["issues"][0]["code"], "field_transition")

    def test_trick_room_new_cast_without_closure_is_not_hidden_as_repeated_ocr(self):
        trace = [frame(1, [event("switch", "p1a", "Indeedee-F", "177/177"),
                          event("move", "p1a", "Indeedee-F", move="Trick Room")]),
                 frame(2, [event("fieldstart", value="move: Trick Room")]),
                 frame(10, [event("move", "p1a", "Indeedee-F", move="Trick Room")]),
                 frame(11, [event("fieldstart", value="move: Trick Room")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["events"][-1]["status"], "review")
        self.assertEqual(ledger["issues"][0]["code"], "field_transition")

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_TRICK_ROOM"), "Requiere ZIP de Trick Room")
    def test_trick_room_from_reprocessed_137129(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_TRICK_ROOM"])
        frames, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, [r for r in frames if r["battle_index"] == 0],
                                 read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        room = [e for e in ledger["events"] if e.get("value") == "move: Trick Room"]
        self.assertEqual([(e["kind"], e["turn"], e["frame"]) for e in room if e["status"] == "consistent"],
                         [("fieldstart", 2, 474), ("fieldend", 6, 956)])
        self.assertEqual([e["frame"] for e in room if e["status"] == "suppressed"], [476, 477])

    def test_cjk_hud_alias_requires_stationary_name_and_repeated_faint(self):
        rows = [frame(n) for n in range(1, 5)]
        for row in rows[:2]:
            row["ocr"].append({"text": "わ5びくん", "left": .83, "right": .89,
                               "top": .05, "bottom": .09, "confidence": .98})
        for row in rows[2:]:
            row["ocr"].append({"text": "The opposing わらびくん fainted!",
                               "top": .73, "confidence": .98})
        aliases = {"p1": {}, "p2": {"わ5びくん": "Incineroar"}}
        self.assertEqual(hud_nickname(rows[0], "p2b"), "わ5びくん")
        proof = corroborated_digit_aliases(rows, aliases)
        self.assertEqual([(x["nickname"], x["species"], x["slot"]) for x in proof],
                         [("わらびくん", "Incineroar", "p2b")])
        one_reading = copy.deepcopy(rows)
        one_reading[3]["ocr"] = []
        self.assertEqual(corroborated_digit_aliases(one_reading, aliases), [])
        unstable_hud = copy.deepcopy(rows)
        unstable_hud[1]["ocr"] = []
        self.assertEqual(corroborated_digit_aliases(unstable_hud, aliases), [])
        ambiguous = {"p1": {}, "p2": {**aliases["p2"], "わ8びくん": "Gengar"}}
        two_huds = copy.deepcopy(rows)
        for row in two_huds[:2]:
            row["ocr"].append({"text": "わ8びくん", "left": .63, "right": .69,
                               "top": .05, "bottom": .09, "confidence": .98})
        self.assertEqual(corroborated_digit_aliases(two_huds, ambiguous), [])

    def test_result_screen_closes_battle_only_with_matching_ocr(self):
        for result in ("You defeated Chinos!", "You lost to Chinos!",
                       "You were defeated by Chinos!"):
            with self.subTest(result=result):
                trace = [frame(1, [event("switch", "p1a", "Basculegion", "54/219"),
                                   event("turn", turn=1),
                                   event("move", "p1a", "Basculegion", move="Protect")]),
                         frame(2, [event("message", value=result)], texts=[result])]
                trace[1]["detections"]["battle_complete"] = True
                ledger = BattleAutomaton(0, trace).run()
                self.assertEqual(ledger["issues"], [])
                ends = [item for item in ledger["events"] if item["kind"] == "battle_end"]
                self.assertEqual(len(ends), 1)
                self.assertEqual((ends[0]["frame"], ends[0]["value"]), (2, result))
                self.assertEqual(ends[0]["evidence"],
                                 [{"frame": 2, "text": result, "confidence": 1}])

                # A terminal flag alone or an OCR result without that flag
                # cannot turn an unrelated message into a battle ending.
                for defect in ("flag", "missing", "weak", "mismatch", "menu"):
                    altered = json.loads(json.dumps(trace))
                    if defect == "flag":
                        altered[1]["detections"]["battle_complete"] = False
                    elif defect == "missing":
                        altered[1]["ocr"] = []
                    elif defect == "weak":
                        altered[1]["ocr"][0]["confidence"] = .89
                    elif defect == "mismatch":
                        altered[1]["ocr"][0]["text"] = "You defeated someone else!"
                    else:
                        altered[1]["ocr"][0]["top"] = .2
                    with self.subTest(defect=defect):
                        self.assertFalse(any(item["kind"] == "battle_end" for item in
                                             BattleAutomaton(0, altered).run()["events"]))

    def test_result_after_forfeit_does_not_duplicate_battle_end(self):
        trace = [frame(1, [event("switch", "p1a", "Basculegion", "54/219"),
                           event("turn", turn=1),
                           event("move", "p1a", "Basculegion", move="Protect")]),
                 frame(2, [event("message", value="The battle has ended due to a forfeit.")]),
                 frame(3, [event("message", value="You lost to Chinos!")],
                       texts=["You lost to Chinos!"])]
        trace[2]["detections"]["battle_complete"] = True
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(len([item for item in ledger["events"] if item["kind"] == "battle_end"]), 1)

    def test_terminal_card_without_detector_message_closes_only_with_ocr_and_flag(self):
        trace = [frame(1, [event("switch", "p1a", "Basculegion", "54/219"),
                           event("turn", turn=1),
                           event("move", "p1a", "Basculegion", move="Protect")]),
                 frame(2, texts=["You lost to Chinos!"])]
        trace[1]["detections"]["battle_complete"] = True
        ledger = BattleAutomaton(0, trace).run()
        ends = [item for item in ledger["events"] if item["kind"] == "battle_end"]
        self.assertEqual([(item["frame"], item["value"]) for item in ends],
                         [(2, "You lost to Chinos!")])
        self.assertEqual(ends[0]["evidence"],
                         [{"frame": 2, "text": "You lost to Chinos!", "confidence": 1}])
        for defect in ("flag", "weak", "menu"):
            altered = copy.deepcopy(trace)
            if defect == "flag":
                altered[1]["detections"]["battle_complete"] = False
            elif defect == "weak":
                altered[1]["ocr"][0]["confidence"] = .89
            else:
                altered[1]["ocr"][0]["top"] = .2
            with self.subTest(defect=defect):
                self.assertFalse(any(item["kind"] == "battle_end" for item in
                                     BattleAutomaton(0, altered).run()["events"]))

    def test_partial_high_confidence_faint_cannot_change_actor_state(self):
        for side, name, species in (("p1", "Tomoe", "Kingambit"), ("p2", "Alpha", "Pelipper")):
            with self.subTest(side=side):
                trace = partial_faint_trace(side, name, species)[:3]
                automaton = BattleAutomaton(0, trace)
                ledger = automaton.run()
                actor_id = automaton.active[side + "a"]
                self.assertFalse(ledger["actors"][actor_id]["fainted"])
                self.assertEqual(ledger["actors"][actor_id]["health"], "0/100")
                self.assertEqual([i["code"] for i in ledger["issues"]], ["faint_text_unconfirmed"])
                pending = ledger["events"][-1]
                self.assertEqual((pending["status"], pending["text_support"]["state"]), ("review", "unconfirmed"))
                self.assertEqual(pending["text_support"]["evidence"][0]["confidence"], .99999)

    def test_partial_faint_reuses_transient_text_state_after_complete_accepted_event(self):
        for side, name, species in (("p1", "Tomoe", "Kingambit"), ("p2", "Alpha", "Pelipper")):
            with self.subTest(side=side):
                trace = partial_faint_trace(side, name, species)
                raw = json.dumps(trace, sort_keys=True)
                ledger = BattleAutomaton(0, trace).run()
                self.assertEqual(json.dumps(trace, sort_keys=True), raw)
                self.assertEqual(ledger["issues"], [])
                pending, accepted = [e for e in ledger["events"] if e["kind"] == "faint"]
                self.assertEqual((pending["frame"], pending["status"], pending["text_support"]["state"]),
                                 (3, "suppressed", "rejected"))
                self.assertEqual((accepted["frame"], accepted["status"]), (5, "consistent"))
                self.assertEqual(ledger["counts"]["faint"], 1)
                self.assertTrue(ledger["actors"][accepted["actor_id"]]["fainted"])
                proof = pending["resolution"]
                self.assertEqual((proof["event_seq"], proof["actor_id"], proof["slot"]),
                                 (accepted["seq"], accepted["actor_id"], side + "a"))
                self.assertEqual([e["frame"] for e in proof["evidence"]], [5, 6])
                self.assertEqual(pending["text_support"]["raw_event"]["source_frame"], 3)

    def test_partial_faint_stays_pending_without_same_actor_and_temporal_proof(self):
        for missing in ("accepted_event", "repeated", "confidence", "side", "name", "known_other_actor",
                        "zero_hp", "unconfirmed_hp", "move", "turn", "entry", "raw_action", "raw_entry", "gap"):
            with self.subTest(missing=missing):
                trace = partial_faint_trace()
                if missing == "accepted_event":trace[4]["detections"]["events"] = []
                elif missing == "repeated":trace[5]["ocr"] = []
                elif missing in {"confidence", "side", "name"}:
                    for i in (4, 5):
                        if missing == "confidence":trace[i]["ocr"][0]["confidence"] = .8
                        elif missing == "side":trace[i]["ocr"][0]["text"] = "The opposing Tomoe fainted!"
                        else:trace[i]["ocr"][0]["text"] = "Alpha fainted!"
                elif missing == "known_other_actor":
                    for row in trace:row["resolved_aliases"]["p1"]["omoe"] = "Pelipper"
                elif missing == "zero_hp":trace[1] = frame(2, [event("damage", "p1a", "Kingambit", "1/100")])
                elif missing == "unconfirmed_hp":trace[1]["ocr"][0]["confidence"] = .8
                elif missing in {"move", "turn", "entry"}:
                    kind = "switch" if missing == "entry" else missing
                    trace[3]["detections"]["events"] = [event(kind, "p1a", "Pelipper", move="Protect", turn=2)]
                elif missing == "raw_action":trace[3]["ocr"] = [{"text": "Tomoe used Protect!", "top": .75, "confidence": .99}]
                elif missing == "raw_entry":trace[3]["ocr"] = [{"text": "Go! Tomoe!", "top": .75, "confidence": .99}]
                elif missing == "gap":
                    for row in trace[3:]:row["timestamp_ms"] += 3000
                ledger = BattleAutomaton(0, trace).run()
                pending = next(e for e in ledger["events"] if e["kind"] == "faint" and e["frame"] == 3)
                self.assertEqual(pending["status"], "review")
                self.assertNotIn("resolution", pending)
                self.assertTrue(any(i["code"] == "faint_text_unconfirmed" for i in ledger["issues"]))

    def test_retrospective_appearance_recovers_hp_and_buffered_move_on_both_sides(self):
        for side, species in (("p1", "Pelipper"), ("p2", "Kingambit")):
            with self.subTest(side=side):
                trace = delayed_health_trace(side, species)
                original = json.dumps(trace, sort_keys=True)
                ledger = BattleAutomaton(0, trace).run()
                self.assertEqual(json.dumps(trace, sort_keys=True), original)
                self.assertEqual(ledger["issues"], [])
                entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == species)
                self.assertEqual((entry["logical_frame"], entry["health"]), (5, "100/100"))
                hp = [e for e in ledger["events"] if e["kind"] in {"damage", "heal"} and e["actor_id"] == entry["actor_id"]]
                self.assertEqual([(e["before"], e["after"], e["hp_state"]) for e in hp],
                                 [("100/100", "40/100", "confirmed"), ("40/100", "46/100", "confirmed")])
                self.assertEqual([[o["frame"] for o in e["observations"]] for e in hp], [[54, 55], [74, 75]])
                link = ledger["narration_links"][0]
                self.assertEqual((link["status"], link["event_seq"], link["actor_id"]),
                                 ("linked", hp[1]["seq"], entry["actor_id"]))
                self.assertIn("Grassy Terrain", hp[1]["cause"])
                move = next(e for e in ledger["events"] if e["move"] == "Fake Out")
                self.assertEqual((move["frame"], move["slot"], move["status"]), (25, side + "a", "consistent"))
                self.assertEqual(move["action_reconstruction"]["detected_frame"], 95)
                self.assertEqual(move["action_reconstruction"]["raw_event"]["slot"], side + "b")

    def test_retrospective_hp_rejects_unconfirmed_changes_and_broken_continuity(self):
        for missing in ("name", "partner_name", "confidence", "endpoint", "action", "raw_action", "denominator",
                        "contradiction", "gap", "replacement", "zero_rebound"):
            with self.subTest(missing=missing):
                trace = delayed_health_trace()
                if missing == "name":trace[54]["ocr"][0]["text"] = "Beta"
                elif missing == "partner_name":
                    trace[54]["ocr"].append({"text": "Alpha", "left": .29, "top": .86, "confidence": .999})
                elif missing == "confidence":trace[54]["ocr"][1]["confidence"] = .92
                elif missing == "endpoint":trace[55]["ocr"] = []
                elif missing == "action":trace[55]["detections"]["events"] = [event("move", "p1a", "Pelipper", move="Protect")]
                elif missing == "raw_action":trace[55]["ocr"].append({"text": "Alpha used Protect!", "top": .75, "confidence": .99})
                elif missing == "denominator":trace[54]["ocr"][1]["text"] = "40/200"
                elif missing == "contradiction":
                    trace[54]["ocr"].append({"text": "60/100", "left": .14, "top": .92, "confidence": .999})
                elif missing == "gap":trace.pop(60)
                elif missing == "replacement":trace[60]["detections"]["events"] = [event("switch", "p1a", "Pikachu")]
                elif missing == "zero_rebound":
                    for i in (54, 55):trace[i]["ocr"][1]["text"] = "0/100"
                ledger = BattleAutomaton(0, trace).run()
                self.assertFalse(any(e.get("entry_reconstruction") for e in ledger["events"]))
                self.assertTrue(ledger["issues"])

    def test_buffered_move_needs_its_own_clock_actor_and_repeated_text(self):
        for missing in ("clock", "source", "one_text", "side", "actor", "move", "confidence"):
            with self.subTest(missing=missing):
                trace = delayed_health_trace()
                buffered = trace[94]["detections"]["events"][1]
                if missing == "clock":buffered["timestamp_ms"] = 13500
                elif missing == "source":buffered["source_frame"] = 80
                elif missing == "one_text":trace[25]["ocr"] = []
                else:
                    for i in (24, 25):
                        line = trace[i]["ocr"][0]
                        if missing == "side":line["text"] = "The opposing " + line["text"]
                        elif missing == "actor":line["text"] = line["text"].replace("Alpha", "Beta")
                        elif missing == "move":line["text"] = line["text"].replace("Fake Out", "Protect")
                        elif missing == "confidence":line["confidence"] = .90
                ledger = BattleAutomaton(0, trace).run()
                move = next(e for e in ledger["events"] if e["move"] == "Fake Out")
                self.assertNotIn("action_reconstruction", move)
                self.assertEqual((move["frame"], move["slot"], move["status"]), (95, "p1b", "review"))

    def test_delayed_entry_during_impact_requires_real_continuation_and_endpoint(self):
        for missing in (None, "candidate", "endpoint", "name", "new_action", "reversal"):
            with self.subTest(missing=missing):
                trace = delayed_health_trace()
                trace[94]["detections"]["events"][0]["health"] = "20/100"
                trace[94]["ocr"][1]["text"] = "20/100"
                trace[95] = frame(96, [event("damage", "p1a", "Pelipper", "10/100")])
                trace.extend([frame(97, [event("damage", "p1a", "Pelipper", "0/100")]), frame(98)])
                for row in trace[95:]:
                    row["ocr"].append({"text": "Alpha", "left": .08, "top": .86, "confidence": .999})
                trace[-1]["ocr"].append({"text": "0/100", "left": .14, "top": .92, "confidence": .999})
                if missing == "candidate":trace[95]["detections"]["events"] = []
                elif missing == "endpoint":trace[-1]["ocr"] = []
                elif missing == "name":trace[95]["ocr"][-1]["text"] = "Beta"
                elif missing == "new_action":trace[95]["ocr"].append({"text": "Alpha used Protect!", "top": .75, "confidence": .99})
                elif missing == "reversal":
                    trace[95]["detections"]["events"][0].update(kind="heal", health="30/100")
                    trace[95]["ocr"][0]["text"] = "30/100"
                ledger = BattleAutomaton(0, trace).run()
                entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Pelipper")
                if missing:
                    self.assertNotIn("entry_reconstruction", entry)
                else:
                    self.assertEqual(ledger["issues"], [])
                    damage = [e for e in ledger["events"] if e["kind"] == "damage"][-1]
                    self.assertEqual((damage["before"], damage["after"]), ("46/100", "0/100"))
                    self.assertEqual([o["frame"] for o in damage["observations"]], [95, 96, 97])
    def test_shared_title_rules_preserve_real_nicknames_and_double_announcements(self):
        self.assertEqual(strip_pokemon_title("Revenant the Paldea Champion"), "Revenant")
        self.assertEqual(strip_pokemon_title("Rex the Tried and True"), "Rex")
        self.assertEqual(strip_pokemon_title("Tonatiuh and Revenant the Paldea Champion"), "Tonatiuh and Revenant")
        self.assertEqual(strip_pokemon_title("Judge the Royal Master"), "Judge")
        self.assertEqual(strip_pokemon_title("Revenant the Unknown Title"), "Revenant the Unknown Title")
        self.assertEqual(strip_pokemon_title("Alpha and Beta"), "Alpha and Beta")

    def test_delayed_entry_uses_announced_time_and_real_initial_hp_on_both_sides(self):
        for side, title in (("p1", "the Paldea Champion"), ("p2", "the Peckish")):
            with self.subTest(side=side):
                ledger = BattleAutomaton(0, delayed_entry_trace(side, title)).run()
                self.assertEqual(ledger["issues"], [])
                entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["species"] == "Pelipper")
                self.assertEqual((entry["frame"], entry["logical_frame"], entry["health"], entry["hp_state"]),
                                 (95, 5, "100/100", "confirmed"))
                proof = entry["entry_reconstruction"]
                self.assertEqual([e["frame"] for e in proof["evidence"]], [10, 11])
                self.assertEqual([e["frame"] for e in proof["announcements"]], [5, 6])
                self.assertEqual(proof["raw_event"]["source_frame"], 95)
                move = next(e for e in ledger["events"] if e["kind"] == "move")
                self.assertEqual((move["actor_id"], move["status"]), (entry["actor_id"], "consistent"))
                self.assertLess(entry["seq"], move["seq"])

    def test_delayed_entry_never_bridges_missing_or_conflicting_evidence(self):
        for missing in ("announcement", "title", "alias_collision", "name", "hp", "confidence", "slot",
                        "one_hud", "motion", "gap", "action_before_hud", "occupied", "replacement",
                        "faint", "raw_faint", "withdraw", "end", "different_name", "hp_change", "zero_hp", "candidate_hp"):
            with self.subTest(missing=missing):
                trace = delayed_entry_trace()
                if missing == "announcement":trace[5]["ocr"] = []
                elif missing in {"title", "alias_collision"}:
                    for i in (4, 5):
                        trace[i]["ocr"][0]["text"] = trace[i]["ocr"][0]["text"].replace(
                            "the Paldea Champion" if missing == "title" else "Alpha",
                            "the Unknown Title" if missing == "title" else "AlphaBeta")
                elif missing in {"name", "hp", "confidence", "slot", "motion"}:
                    for i in (9, 10):
                        if missing == "name":trace[i]["ocr"][0]["text"] = "Beta"
                        elif missing == "hp":trace[i]["ocr"][1]["text"] = "100"
                        elif missing == "confidence":trace[i]["ocr"][0]["confidence"] = .92
                        elif missing == "slot":trace[i]["ocr"][1]["left"] = .34
                        elif missing == "motion" and i == 10:trace[i]["ocr"][0]["left"] += .02
                elif missing == "one_hud":trace[10]["ocr"] = []
                elif missing == "gap":trace.pop(20)
                elif missing == "action_before_hud":trace[7] = frame(8, texts=["Alpha used Protect!"])
                elif missing == "occupied":trace[2]["detections"]["events"] = []
                elif missing in {"replacement", "faint"}:
                    trace[29] = frame(30, [event("switch" if missing == "replacement" else "faint", "p1a", "Pelipper")])
                elif missing in {"raw_faint", "withdraw", "end"}:
                    text = {"raw_faint": "Alpha fainted!", "withdraw": "Alpha, come back!", "end": "The battle has ended!"}[missing]
                    trace[29] = frame(30, texts=[text])
                elif missing == "different_name":
                    trace[29]["ocr"] = [{"text": "Beta", "left": .08, "top": .86, "confidence": .999}]
                elif missing in {"hp_change", "zero_hp"}:
                    trace[29]["ocr"] = [{"text": "90/100" if missing == "hp_change" else "0/100",
                                         "left": .14, "top": .92, "confidence": .999}]
                elif missing == "candidate_hp":trace[94]["detections"]["events"][0]["health"] = "50/100"
                ledger = BattleAutomaton(0, trace).run()
                self.assertFalse(any(e.get("entry_reconstruction") for e in ledger["events"]))
                self.assertTrue(ledger["issues"])

    def test_sliding_hud_assigns_identity_after_stable_names_on_either_side(self):
        for side, species, partner in (("p1", "Blaziken", "Kingambit"), ("p2", "Delphox", "Indeedee-F")):
            with self.subTest(side=side):
                trace = sliding_hud_trace(side, species, partner)
                mega = {**event("mega", side + "a", species, value="test stone"), "forme": species + "-Mega"}
                trace.append(frame(8, [mega]))
                trace.append(frame(9, [event("move", side + "a", species, move="Protect")]))
                ledger = BattleAutomaton(0, trace).run()
                self.assertEqual(ledger["issues"], [])
                entries = [e for e in ledger["events"] if e["kind"] == "switch"]
                self.assertEqual([(e["slot"], e["species"]) for e in entries],
                                 [(side + "a", species), (side + "b", partner)])
                proof = entries[0]["identity_support"]
                self.assertEqual((proof["from"], proof["state"], proof["confirmed_frame"]),
                                 ("provisional", "confirmed", 3))
                self.assertEqual([e["frame"] for e in proof["evidence"]], [2, 3])
                self.assertEqual((entries[0]["frame"], entries[0]["health"]), (1, "100/100"))
                self.assertEqual(next(e for e in ledger["events"] if e["kind"] == "mega")["status"], "consistent")

    def test_conflicting_entry_stays_pending_without_safe_identity_evidence(self):
        for barrier in ("move", "faint", "mega", "switch", "raw_action", "raw_entry", "withdraw",
                        "time", "gap", "confidence", "moving", "one_frame", "partner_name", "late_conflict",
                        "battle_complete", "battle_message", "raw_mega"):
            with self.subTest(barrier=barrier):
                trace = sliding_hud_trace()
                # An unambiguous global detector resolution must not override
                # an unresolved local conflict between two HUD names.
                raw = trace[0]["detections"]["events"][0]["species"]
                for row in trace:
                    row["resolved_identities"][raw] = "Kingambit"
                if barrier in {"move", "faint", "mega", "switch"}:
                    trace[1]["detections"]["events"].append(event(barrier, "p1a", "Blaziken", move="Detect"))
                elif barrier in {"raw_action", "raw_entry", "withdraw"}:
                    text = {"raw_action": "Alpha used Detect!", "raw_entry": "Go! Alpha!",
                            "withdraw": "Rival withdrew Beta!"}[barrier]
                    trace[1]["ocr"].append({"text": text, "top": .75, "confidence": .99})
                elif barrier == "time":
                    for i, row in enumerate(trace[1:], 1):row["timestamp_ms"] += i * 800
                elif barrier == "gap":trace.pop(1)
                elif barrier == "confidence":
                    for row in trace[1:]:row["ocr"][0]["confidence"] = .92
                elif barrier == "moving":
                    for i, row in enumerate(trace[1:], 1):row["ocr"][0]["left"] += i * .02
                elif barrier == "one_frame":trace = trace[:2]
                elif barrier == "partner_name":
                    for row in trace[1:]:row["ocr"][1]["text"] = "Alpha"
                elif barrier == "late_conflict":trace[-1]["ocr"][0]["text"] = "Beta"
                elif barrier == "battle_complete":trace[1]["detections"]["battle_complete"] = True
                elif barrier == "battle_message":
                    trace[1]["detections"]["events"].append(event("message", value="The battle has ended."))
                elif barrier == "raw_mega":
                    trace[1]["ocr"].append({"text": "Alpha's Blazikenite is reacting to Roku's Omni Ring!",
                                            "top": .75, "confidence": .99})
                ledger = BattleAutomaton(0, trace).run()
                entry = next(e for e in ledger["events"] if e["kind"] == "switch" and e["frame"] == 1 and e["slot"] == "p1a")
                self.assertEqual((entry["status"], entry["identity_support"]["state"]), ("suppressed", "unconfirmed"))
                self.assertIsNone(entry["actor_id"])
                self.assertIn("entry_identity_unconfirmed", [i["code"] for i in ledger["issues"]])

    def test_confirmed_hud_identity_survives_a_later_action_boundary(self):
        trace = sliding_hud_trace()
        trace[3]["detections"]["events"] = [event("move", "p1a", "Blaziken", move="Detect")]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        move = next(e for e in ledger["events"] if e["kind"] == "move")
        self.assertEqual((move["species"], move["status"]), ("Blaziken", "consistent"))

    def test_pending_hp_confirms_later_split_percent_and_preserves_next_baseline(self):
        trace = pending_hp_trace()
        trace.append(frame(9, [event("damage", "p2a", "Delphox", "0/100")]))
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        hp = [e for e in ledger["events"] if e["kind"] in {"damage", "heal"}]
        self.assertEqual([(e["before"], e["after"]) for e in hp], [("64/100", "82/100"), ("82/100", "0/100")])
        self.assertEqual(hp[0]["hp_support"]["confirmation"]["confirmed_frame"], 5)

    def test_pending_hp_never_borrows_across_boundaries_or_conflicts(self):
        for boundary in ("move", "turn", "switch", "raw_action", "raw_entry", "time", "gap", "other_name",
                         "contradiction", "new_hp", "partner_hp", "no_percent"):
            with self.subTest(boundary=boundary):
                trace = pending_hp_trace()
                if boundary in {"move", "turn", "switch"}:
                    trace[2]["detections"]["events"] = [event(boundary, "p2a", "Delphox", move="Protect", turn=2)]
                elif boundary in {"raw_action", "raw_entry"}:
                    text = "The opposing Delphox used Protect!" if boundary == "raw_action" else "Rival sent out Delphox!"
                    trace[2]["ocr"].append({"text": text, "top": .75, "confidence": .99})
                elif boundary == "time":
                    trace = trace[:4] + [frame(n) for n in range(5, 9)] + [trace[4]]
                    trace[-1].update(frame=9, timestamp_ms=4500)
                elif boundary == "gap":
                    trace = trace[:2] + trace[4:]
                elif boundary == "other_name":
                    trace[2]["ocr"][0]["text"] = "Garchomp"
                elif boundary == "contradiction":
                    trace[2]["ocr"].append({"text": "75%", "left": .70, "top": .12, "confidence": .99})
                elif boundary == "new_hp":
                    trace[2]["detections"]["events"] = [event("damage", "p2a", "Delphox", "75/100")]
                elif boundary == "partner_hp":
                    for line in trace[4]["ocr"][-2:]:
                        line["left"] += .21
                        line["right"] += .21
                elif boundary == "no_percent":
                    trace[4]["ocr"].pop()
                ledger = BattleAutomaton(0, trace).run()
                self.assertFalse(any(e["kind"] == "heal" and e["status"] == "consistent" and e["after"] == "82/100"
                                     for e in ledger["events"]))
                self.assertTrue(ledger["issues"])

    def test_pending_zero_uses_visible_zero_and_repeated_matching_faint(self):
        ledger = BattleAutomaton(0, pending_hp_trace(zero=True)).run()
        self.assertEqual(ledger["issues"], [])
        hp = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((hp["before"], hp["after"], hp["hp_state"]), ("64/100", "0/100", "confirmed"))
        self.assertEqual({e["kind"] for e in hp["hp_support"]["evidence"]}, {"zero_hp", "faint_narration"})
        self.assertTrue(next(iter(ledger["actors"].values()))["fainted"])

    def test_pending_zero_requires_hud_identity_zero_and_matching_narration(self):
        for missing in ("zero", "name", "partner", "one_text", "confidence", "side", "species", "action", "positive"):
            with self.subTest(missing=missing):
                trace = pending_hp_trace(zero=True)
                if missing == "zero":trace[3]["ocr"].pop(1)
                elif missing == "name":trace[3]["ocr"].pop(0)
                elif missing == "partner":trace[3]["ocr"][1].update(left=.92, right=.97)
                elif missing == "one_text":trace[4]["ocr"].pop()
                elif missing in {"confidence", "side", "species"}:
                    for row in trace[3:5]:
                        line = row["ocr"][-1]
                        if missing == "confidence":line["confidence"] = .7
                        elif missing == "side":line["text"] = "Delphox fainted!"
                        else:line["text"] = "The opposing Garchomp fainted!"
                elif missing == "action":trace[2]["detections"]["events"] = [event("move", "p2a", "Delphox", move="Protect")]
                elif missing == "positive":trace[3]["ocr"][1]["text"] = "1%"
                ledger = BattleAutomaton(0, trace).run()
                self.assertTrue(any(e["kind"] == "hp_unconfirmed" for e in ledger["events"]))

    def test_transient_text_is_discarded_only_after_legible_accepted_event(self):
        ledger = BattleAutomaton(0, transient_mega_text_trace()).run()
        self.assertFalse(any(i["code"] == "unclassified_text" for i in ledger["issues"]))
        text = next(e for e in ledger["events"] if e["kind"] == "unclassified_text")
        self.assertEqual(text["status"], "suppressed")
        self.assertEqual(text["resolution"]["to"], "discarded")
        self.assertEqual(text["resolution"]["evidence"][0]["frame"], 4)
        self.assertIn("racting", text["value"])
        self.assertEqual(sum(e["kind"] == "mega" and e["status"] == "consistent" for e in ledger["events"]), 1)
        self.assertIn("Avisos resueltos", render_markdown(ledger, None))

    def test_multiword_mega_stone_resolves_transient_ocr_against_same_event(self):
        trace = transient_mega_text_trace("Raichu", "Raichunite Y", "Raichu-Mega-Y")
        self.assertEqual(narration_signature(trace[-1]["ocr"][0]["text"]),
                         ("mega", "p2", "Raichu", "Raichunite Y"))
        ledger = BattleAutomaton(0, trace).run()
        self.assertNotIn("unclassified_text", {issue["code"] for issue in ledger["issues"]})
        message = next(e for e in ledger["events"] if e["kind"] == "unclassified_text")
        mega = next(e for e in ledger["events"] if e["kind"] == "mega")
        self.assertEqual(message["resolution"]["event_seq"], mega["seq"])
        self.assertEqual(message["resolution"]["slot"], "p2a")

        trace[-1]["detections"]["events"][0]["value"] = "Raichunite X"
        self.assertIn("unclassified_text", {issue["code"] for issue in BattleAutomaton(0, trace).run()["issues"]})

    def test_transient_text_preserves_unexplained_or_different_events(self):
        for missing in ("event", "legible", "confidence", "time", "action", "turn", "species", "stone", "side", "meaningful", "unrelated"):
            with self.subTest(missing=missing):
                trace = transient_mega_text_trace()
                if missing == "event":trace[-1]["detections"]["events"] = []
                elif missing == "legible":trace[-1]["ocr"] = []
                elif missing == "confidence":trace[-1]["ocr"][0]["confidence"] = .8
                elif missing == "time":trace[-1].update(frame=12, timestamp_ms=6000)
                elif missing in {"action", "turn"}:
                    trace[2]["detections"]["events"] = [event("move" if missing == "action" else "turn", "p2a", "Delphox", move="Protect", turn=2)]
                else:
                    value = trace[1]["detections"]["events"][0]["value"]
                    if missing == "species":
                        trace[0]["detections"]["events"].insert(0, event("switch", "p2b", "Garchomp", "100/100"))
                        value = value.replace("Delphox's", "Garchomp's")
                    elif missing == "stone":value = value.replace("Delphoxite", "Garchompite")
                    elif missing == "side":
                        trace[0]["detections"]["events"][0]["slot"] = "p1a"
                        trace[-1]["detections"]["events"][0]["slot"] = "p1a"
                        trace[-1]["ocr"][0]["text"] = trace[-1]["ocr"][0]["text"].replace("The opposing ", "")
                    elif missing == "meaningful":value = value.replace("racting", "reacting")
                    else:value = "An unrelated and incomplete observation"
                    trace[1]["detections"]["events"][0]["value"] = value
                    trace[1]["ocr"][0]["text"] = value
                ledger = BattleAutomaton(0, trace).run()
                self.assertTrue(any(i["code"] == "unclassified_text" for i in ledger["issues"]))

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

    def test_damaged_side_narration_is_one_opponent_action_with_original_evidence(self):
        trace = damaged_side_trace()
        # Reproduce the detector's poisoned alias; it is not HUD evidence.
        for row in trace:
            row["resolved_aliases"]["p1"]["the opposng arcanine"] = "Arcanine"
        raw = json.dumps(trace)
        ledger = BattleAutomaton(0, trace).run()
        moves = [e for e in ledger["events"] if e["kind"] == "move"]
        self.assertEqual([(e["frame"], e["slot"], e["status"]) for e in moves],
                         [(2, "p2b", "consistent"), (3, "p2b", "suppressed"), (4, "p2b", "suppressed")])
        repaired = moves[1]["move_narration_support"]
        self.assertEqual(repaired["raw_event"]["slot"], "p1a")
        self.assertEqual([e["frame"] for e in repaired["evidence"]], [2, 4])
        self.assertIn("opposng", repaired["suspect_readings"][0]["text"])
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual(damage["cause"], moves[0]["seq"])
        self.assertFalse(ledger["issues"])
        self.assertEqual(json.dumps(trace), raw)

    def test_damaged_side_keeps_same_species_on_both_teams_separate(self):
        trace = damaged_side_trace()
        trace[0]["detections"]["events"][0]["species"] = "Arcanine"
        trace[-1]["detections"]["events"][0]["species"] = "Arcanine"
        ledger = BattleAutomaton(0, trace).run()
        move = next(e for e in ledger["events"] if e["frame"] == 3)
        self.assertEqual((move["slot"], move["status"]), ("p2b", "suppressed"))
        self.assertNotEqual(move["actor_id"], next(e["actor_id"] for e in ledger["events"] if e["kind"] == "damage"))

    def test_damaged_side_can_use_exact_opponent_nickname(self):
        trace = damaged_side_trace()
        for row in trace:
            row["resolved_aliases"]["p2"]["kuma"] = "Arcanine"
            for line in row["ocr"]:
                line["text"] = line["text"].replace("Arcanine", "Kuma")
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(next(e for e in ledger["events"] if e["frame"] == 3)["slot"], "p2b")
        self.assertFalse(ledger["issues"])

    def test_damaged_side_needs_complete_repetition_without_actions_or_gaps(self):
        for case in ("one_reading", "weak", "turn", "hp", "gap", "other_move", "other_actor", "withdraw"):
            with self.subTest(case=case):
                trace = damaged_side_trace()
                if case == "one_reading": trace[3]["ocr"] = []
                if case == "weak": trace[3]["ocr"][0]["confidence"] = .94
                if case == "turn": trace[3]["detections"]["events"].append(event("turn", turn=2))
                if case == "hp": trace[3]["detections"]["events"].append(event("damage", "p1a", "Blaziken", "140/156"))
                if case == "gap": trace[3]["timestamp_ms"] += 1500
                if case == "other_move": trace[3]["ocr"][0]["text"] = "The opposing Arcanine used Protect!"
                if case == "other_actor": trace[3]["ocr"][0]["text"] = "The opposing Incineroar used Flare Blitz!"
                if case == "withdraw": trace[3]["ocr"].append({"text": "Arcanine, come back!", "top": .75, "confidence": .99})
                ledger = BattleAutomaton(0, trace).run()
                suspect = next(e for e in ledger["events"] if e["frame"] == 3)
                self.assertEqual((suspect["kind"], suspect["status"]), ("move_text_unconfirmed", "review"))
                self.assertEqual(suspect["move_narration_support"]["state"], "unconfirmed")

    def test_damaged_side_does_not_choose_between_two_possible_opponents(self):
        trace = damaged_side_trace()
        trace[0]["detections"]["events"].insert(1, event("switch", "p2a", "Arcanine", "100/100"))
        ledger = BattleAutomaton(0, trace).run()
        suspect = next(e for e in ledger["events"] if e["frame"] == 3)
        self.assertEqual(suspect["kind"], "move_text_unconfirmed")
        self.assertIsNone(suspect["actor_id"])

    def test_damaged_side_never_fuzzes_the_actor_name_or_move(self):
        for text in ("The opposng Incineroar used Flare Blitz!", "The opposng Arcanine used Protect!"):
            with self.subTest(text=text):
                trace = damaged_side_trace()
                trace[2]["ocr"][0]["text"] = text
                ledger = BattleAutomaton(0, trace).run()
                suspect = next(e for e in ledger["events"] if e["frame"] == 3)
                self.assertNotIn("move_narration_support", suspect)
                self.assertEqual(suspect["slot"], "p1a")

    def test_real_nickname_resembling_opponent_prefix_is_not_reassigned(self):
        trace = damaged_side_trace()
        trace[0]["ocr"].append({"text": "The opposng Arcanine", "confidence": .99, "left": .08, "top": .86})
        ledger = BattleAutomaton(0, trace).run()
        suspect = next(e for e in ledger["events"] if e["frame"] == 3)
        self.assertEqual(suspect["slot"], "p1a")
        self.assertNotIn("move_narration_support", suspect)

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

    def test_move_info_label_needs_panel_position_and_repeated_ocr(self):
        trace = [frame(1, [event("switch", "p1a", "Indeedee-F", "177/177"),
                           event("turn", turn=1)]), frame(2),
                 frame(3, [event("message", value="Torrain Pulse")]),
                 frame(4), frame(5),
                 frame(6, [event("move", "p1a", "Indeedee-F", move="Terrain Pulse")],
                       texts=["Indeedee-F used Terrain Pulse!"])]
        trace[1]["ocr"].append({"text": "Move Info", "confidence": .999})
        for row in trace[2:5]:
            row["ocr"].extend({"text": text, "confidence": .999}
                              for text in ("Battle Info", "MOVE TIME", "Close"))
            row["ocr"].append({"text": "Torrain Pulse" if row["frame"] == 3 else "Terrain Pulse",
                               "confidence": .99, "top": .484, "left": .576})
        ledger = BattleAutomaton(0, trace).run()
        fragment = next(item for item in ledger["events"] if item["value"] == "Torrain Pulse")
        self.assertEqual((fragment["kind"], fragment["status"]), ("ui_text", "suppressed"))
        self.assertEqual([e["frame"] for e in fragment["ui_support"]["ocr"]], [3, 4, 5])
        self.assertEqual(sum(e["kind"] == "move" for e in ledger["events"]), 1)
        self.assertFalse(ledger["issues"])

        for change in ("no_prior_panel", "missing_menu_cue", "single_reading", "narration_position"):
            with self.subTest(change=change):
                broken = copy.deepcopy(trace)
                if change == "no_prior_panel":
                    broken[1]["ocr"] = []
                elif change == "missing_menu_cue":
                    broken[2]["ocr"] = [o for o in broken[2]["ocr"] if o["text"] != "MOVE TIME"]
                elif change == "single_reading":
                    broken[4]["ocr"] = [o for o in broken[4]["ocr"] if o["text"] != "Terrain Pulse"]
                else:
                    next(o for o in broken[2]["ocr"] if o["text"] == "Torrain Pulse")["top"] = .75
                unresolved = BattleAutomaton(0, broken).run()
                if change == "narration_position":
                    self.assertIn("unclassified_text", {i["code"] for i in unresolved["issues"]})
                else:
                    # Geometry can exclude off-area text without identifying
                    # its menu. It must not claim the repeated-label proof.
                    fragment = next(item for item in unresolved["events"] if item["value"] == "Torrain Pulse")
                    self.assertEqual((fragment["kind"], fragment["status"]), ("ui_text", "suppressed"))
                    self.assertNotIn("ocr", fragment["ui_support"])
                    self.assertEqual(fragment["ui_support"]["evidence"][0]["frame"], 3)
                    self.assertFalse(unresolved["issues"])

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_44FF"),
                         "requiere diagnóstico real 44ff")
    def test_menu_terrain_pulse_del_diagnostico_44ff(self):
        frames, context = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_44FF"]))
        battle = BattleAutomaton(0, frames, context).run()
        self.assertEqual(battle["issues"], [])
        self.assertEqual(sum(e["kind"] in ("damage", "heal") and e["status"] == "consistent"
                             for e in battle["events"]), 10)
        fragment = next(e for e in battle["events"] if e["value"] == "Torrain Pulse")
        self.assertEqual((fragment["kind"], fragment["status"], fragment["frame"]),
                         ("ui_text", "suppressed", 539))
        self.assertEqual([e["frame"] for e in fragment["ui_support"]["ocr"]], [539, 540, 541, 542])
        self.assertEqual(sum(e["kind"] == "move" and e["move"] == "Terrain Pulse"
                             for e in battle["events"]), 1)

    def test_status_panel_excludes_all_candidates_and_resumes_real_battle(self):
        for heading in ("Active Statuses & Effects", " ACTIVE   STATUSES & EFFECTS ",
                        "Active Statuses and Effects"):
            with self.subTest(heading=heading):
                panel = frame(2, [event("damage", "p1a", "Pelipper", "0/100"),
                                  event("heal", "p1a", "Pelipper", "99/100"),
                                  event("faint", "p1a", "Pelipper"),
                                  event("move", "p1a", "Pelipper", move="Tackle"),
                                  event("switch", "p1a", "Pikachu", "100/100"),
                                  event("status", "p1a", "Pelipper", value="brn"),
                                  event("mega", "p1a", "Pelipper", value="Fake Stone"),
                                  event("fieldstart", value="Grassy Terrain"),
                                  event("turn", turn=2), event("battle_end"),
                                  event("message", value="are immune to priority moves.")],
                              texts=[heading, "Pelipper used Tackle!", "Pelipper fainted!", "Go! Pikachu!"])
                panel["resolved_aliases"]["p1"]["falsealias"] = "Pikachu"
                panel["resolved_identities"]["__champions_actor_p1_0001__"] = "Pikachu"
                panel["detections"]["battle_complete"] = True
                trace = [frame(1, [event("switch", "p1a", "Pelipper", "100/100"), event("turn", turn=1),
                                   event("move", "p1a", "Pelipper", move="Protect")]),
                         panel, frame(3, [event("damage", "p1a", "Pelipper", "75/100")])]
                original = json.dumps(trace, sort_keys=True)
                automaton = BattleAutomaton(0, trace)
                ledger = automaton.run()
                self.assertEqual(json.dumps(trace, sort_keys=True), original)
                self.assertEqual(ledger["issues"], [])
                self.assertEqual(ledger["counts"], {"switch": 1, "turn": 1, "move": 1, "damage": 1})
                self.assertEqual((ledger["events"][-1]["before"], ledger["events"][-1]["after"]), ("100/100", "75/100"))
                self.assertIsNone(automaton.terrain)
                self.assertNotIn("falsealias", automaton.nickname_species["p1"])
                self.assertNotIn("__champions_actor_p1_0001__", automaton.id_resolution)
                actor = next(iter(ledger["actors"].values()))
                self.assertFalse(actor["fainted"])
                self.assertIsNone(actor["status"])
                self.assertIsNone(actor["forme"])
                self.assertEqual(ledger["candidate_events"], 15)
                ignored = ledger["ignored_ui_frames"][0]
                self.assertEqual((ignored["frame"], ignored["ocr"], ignored["detections"]),
                                 (2, panel["ocr"], panel["detections"]))
                self.assertIn("Panel de estados excluido del combate", render_markdown(ledger, None))

    def test_status_panel_cannot_confirm_hp_or_supply_raw_action_evidence(self):
        trace = [frame(1, [event("switch", "p1a", "Pelipper", "100/100"), event("turn", turn=1),
                           event("move", "p1a", "Pelipper", move="Protect")]),
                 frame(2, [event("damage", "p1a", "Pelipper", "40/100")]),
                 frame(3, texts=["Active Statuses & Effects", "Pelipper used Surf!", "Pelipper fainted!"]),
                 frame(4)]
        trace[1]["ocr"][0]["confidence"] = .8
        trace[2]["ocr"].append({"text": "40/100", "left": .14, "top": .92, "confidence": .999})
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([i["code"] for i in ledger["issues"]], ["hp_unconfirmed"])
        self.assertFalse(any(e["kind"] in {"damage", "faint"} for e in ledger["events"]))
        self.assertEqual(len(ledger["ignored_ui_frames"]), 1)

    def test_status_panel_requires_complete_heading_not_effect_description_or_menu_label(self):
        for heading, confidence in (("Active Statuses", 1), ("Battle Info", 1), ("Psychic Terrain", 1),
                                    ("Active Statuses & Effects", .89), ("", 1)):
            with self.subTest(heading=heading, confidence=confidence):
                text = "are immune to priority moves."
                trace = [frame(1, [event("switch", "p1a", "Pelipper", "100/100"), event("turn", turn=1)]),
                         frame(2, [event("message", value=text)], texts=[heading]),
                         frame(3, [event("move", "p1a", "Pelipper", move="Protect")])]
                trace[1]["ocr"][0]["confidence"] = confidence
                ledger = BattleAutomaton(0, trace).run()
                self.assertNotIn("ignored_ui_frames", ledger)
                self.assertTrue(any(i["code"] == "unclassified_text" for i in ledger["issues"]))

    def test_retrospective_burn_does_not_attach_to_previous_heal(self):
        trace = [frame(1, [event("switch", "p1a", "Gardevoir", "20/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Gardevoir", move="Protect")]),
                 frame(3, [event("heal", "p1a", "Gardevoir", "26/100")]),
                 frame(5, [event("message", value="Gardevoir had its HP restored.")]),
                 frame(7, [event("message", value="Gardevoir was hurt by its burn!")]),
                 frame(8, [event("damage", "p1a", "Gardevoir", "16/100")])]
        ledger = BattleAutomaton(0, trace).run()
        heal = next(e for e in ledger["events"] if e["kind"] == "heal")
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual(heal["narration"], ["Gardevoir had its HP restored."])
        self.assertEqual(damage["narration"], ["Gardevoir was hurt by its burn!"])
        self.assertEqual((damage["before"], damage["after"], damage["cause"]),
                         ("26/100", "16/100", "quemadura observada"))
        self.assertEqual(damage["causal_evidence"][0]["frame"], 7)
        self.assertEqual([x["delta_ms"] for x in ledger["narration_links"]], [1000, -500])

    def test_retrospective_message_can_link_to_a_closed_episode(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Blaziken", move="Flare Blitz")]),
                 frame(3, [event("damage", "p1a", "Blaziken", "82/100")]),
                 frame(4, [event("ability", "p1a", "Blaziken", value="Speed Boost")]),
                 frame(5, [event("message", value="Blaziken was damaged by the recoil!")])]
        ledger = BattleAutomaton(0, trace).run()
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual(damage["cause"], "retroceso observado")
        self.assertEqual(damage["causal_evidence"][0]["frame"], 5)
        self.assertEqual(ledger["narration_links"][0]["event_seq"], damage["seq"])

    def test_retrospective_keeps_opposing_actor_and_nickname_separate(self):
        trace = [frame(1, [event("switch", "p1a", "Gardevoir", "50/100"),
                           event("switch", "p2b", "Gardevoir", "50/100"), event("turn", turn=1)]),
                 frame(2, [event("heal", "p1a", "Gardevoir", "56/100"),
                           event("heal", "p2b", "Gardevoir", "56/100")]),
                 frame(4, [event("message", value="The opposing Moon had its HP restored.")])]
        trace[0]["resolved_aliases"]["p2"]["moon"] = "Gardevoir"
        ledger = BattleAutomaton(0, trace).run()
        heals = {e["slot"]: e for e in ledger["events"] if e["kind"] == "heal"}
        self.assertEqual(heals["p1a"]["narration"], [])
        self.assertEqual(heals["p2b"]["narration"], ["The opposing Moon had its HP restored."])
        self.assertEqual(ledger["narration_links"][0]["actor_id"], heals["p2b"]["actor_id"])

    def test_retrospective_keeps_equally_plausible_episodes_ambiguous(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                 frame(3, [event("damage", "p1a", "Blaziken", "80/100")]),
                 frame(4, [event("ability", "p1a", "Blaziken", value="Speed Boost"),
                           event("message", value="Blaziken was hurt by its burn!")]),
                 frame(5, [event("damage", "p1a", "Blaziken", "60/100")])]
        ledger = BattleAutomaton(0, trace).run()
        link = ledger["narration_links"][0]
        self.assertEqual((link["status"], link["event_seq"]), ("ambiguous", None))
        self.assertEqual(len(link["candidate_event_seqs"]), 2)
        self.assertIn("hp_narration_ambiguous", [x["code"] for x in ledger["issues"]])
        self.assertFalse(any(e.get("causal_evidence") for e in ledger["events"]))

    def test_retrospective_respects_time_turn_action_and_entry_boundaries(self):
        for boundary in ("time", "turn", "move", "reentry"):
            with self.subTest(boundary=boundary):
                trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                         frame(3, [event("damage", "p1a", "Blaziken", "80/100")])]
                if boundary == "turn":
                    trace.append(frame(4, [event("turn", turn=2)]))
                elif boundary == "move":
                    trace.append(frame(4, [event("move", "p1a", "Blaziken", move="Protect")]))
                elif boundary == "reentry":
                    trace.extend([frame(4, [event("switch", "p1a", "Rillaboom", "100/100")]),
                                  frame(5, [event("switch", "p1a", "Blaziken", "80/100")])])
                trace.append(frame(20 if boundary == "time" else 6,
                                   [event("message", value="Blaziken was hurt by its burn!")]))
                ledger = BattleAutomaton(0, trace).run()
                self.assertEqual(ledger["narration_links"][0]["status"], "unmatched")
                self.assertFalse(any(e.get("causal_evidence") for e in ledger["events"]))

    def test_retrospective_preserves_unknown_actor_and_unconfirmed_hp_messages(self):
        for unknown_actor in (False, True):
            with self.subTest(unknown_actor=unknown_actor):
                trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                         frame(3, [event("damage", "p1a", "Blaziken", "80/100")]),
                         frame(4, [event("message", value=("Rillaboom" if unknown_actor else "Blaziken") +
                                         " was hurt by its burn!")])]
                if not unknown_actor:
                    trace[1]["ocr"] = []
                ledger = BattleAutomaton(0, trace).run()
                link = ledger["narration_links"][0]
                self.assertEqual((link["status"], link["frame"]), ("unmatched", 4))
                self.assertIn("was hurt by its burn!", link["text"])
                self.assertFalse(any(e.get("causal_evidence") for e in ledger["events"]))

    def test_retrospective_keeps_conflicting_effects_for_review(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                 frame(3, [event("damage", "p1a", "Blaziken", "80/100")]),
                 frame(4, [event("message", value="Blaziken was hurt by its burn!"),
                           event("message", value="Blaziken was damaged by the recoil!")])]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([x["status"] for x in ledger["narration_links"]], ["ambiguous", "ambiguous"])
        self.assertFalse(any(e.get("causal_evidence") for e in ledger["events"]))

    def test_retrospective_repeated_ocr_does_not_duplicate_hp_event_or_narration(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                 frame(3, [event("damage", "p1a", "Blaziken", "80/100")]),
                 frame(4, [event("message", value="Blaziken was damaged by the recoil!")]),
                 frame(5, [event("message", value="Blaziken was damaged by the recoil!")])]
        ledger = BattleAutomaton(0, trace).run()
        damage = [e for e in ledger["events"] if e["kind"] == "damage"]
        self.assertEqual(len(damage), 1)
        self.assertEqual(len(damage[0]["narration"]), 1)
        self.assertEqual(len(damage[0]["causal_evidence"]), 2)

    def test_retrospective_links_delayed_recoil_after_faint(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "10/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p1a", "Blaziken", move="Flare Blitz")]),
                 frame(3, [event("damage", "p1a", "Blaziken", "0/100")]),
                 frame(4, [event("faint", "p1a", "Blaziken")]),
                 frame(5, [event("message", value="Blaziken was damaged by the recoil!")])]
        ledger = BattleAutomaton(0, trace).run()
        damage = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual(damage["cause"], "retroceso observado")
        self.assertEqual(ledger["narration_links"][0]["event_seq"], damage["seq"])
        self.assertTrue(ledger["actors"][damage["actor_id"]]["fainted"])

    def test_retrospective_prefers_the_clearly_closest_compatible_episode(self):
        trace = [frame(1, [event("switch", "p1a", "Blaziken", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p1a", "Blaziken", "80/100")]),
                 frame(3, [event("ability", "p1a", "Blaziken", value="Speed Boost")]),
                 frame(6, [event("message", value="Blaziken was hurt by its burn!")]),
                 frame(7, [event("damage", "p1a", "Blaziken", "74/100")])]
        ledger = BattleAutomaton(0, trace).run()
        damage = [e for e in ledger["events"] if e["kind"] == "damage"]
        self.assertEqual(damage[0]["narration"], [])
        self.assertEqual(damage[1]["cause"], "quemadura observada")
        self.assertEqual(ledger["narration_links"][0]["event_seq"], damage[1]["seq"])

    def test_opposite_hp_readings_before_action_are_flagged_without_heal(self):
        trace = [frame(1, [event("switch", "p2a", "Rillaboom", "88/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Rillaboom", move="Protect")]),
                 frame(3, [event("turn", turn=2)]),
                 frame(4, [event("damage", "p2a", "Rillaboom", "0/100")]),
                 frame(5, [event("heal", "p2a", "Rillaboom", "88/100")])]
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

    def test_opponent_menu_hud_uses_both_named_slots_and_keeps_real_entries(self):
        trace = opponent_menu_hud_trace()
        ledger = BattleAutomaton(0, trace).run()
        self.assertFalse([i for i in ledger["issues"] if i["frame"] in (12, 18)])
        resolved = {i["frame"]: i for i in ledger["resolved_issues"]}
        self.assertEqual(set(resolved), {12, 18})
        self.assertEqual({e["kind"] for e in resolved[12]["resolution"]["evidence"]},
                         {"hud_name", "hud_hp"})
        self.assertEqual([e["species"] for e in ledger["events"]
                          if e["kind"] == "switch" and e["status"] == "consistent" and e["slot"] == "p2b"],
                         ["Indeedee-F", "Baxcalibur"])
        for change in ("other_hp", "missing_name", "entry_announcement", "duplicate_announcement"):
            with self.subTest(change=change):
                altered = copy.deepcopy(trace)
                if change == "other_hp":
                    for row in altered:
                        if 12 <= row["frame"] <= 16:
                            row["ocr"] = [line for line in row["ocr"] if line.get("text") != "73%"]
                elif change == "missing_name":
                    for row in altered:
                        if 12 <= row["frame"] <= 16:
                            row["ocr"] = [line for line in row["ocr"] if line.get("text") != "Baxcalibur"]
                elif change == "entry_announcement":
                    altered[11]["ocr"].append({"text": "Rival sent out Ringo!", "top": .75, "confidence": 1})
                else:
                    altered[17]["ocr"].append({"text": "Rival sent out Baxcalibur!", "top": .75,
                                                "confidence": 1})
                issues = BattleAutomaton(0, altered).run()["issues"]
                expected = 18 if change == "duplicate_announcement" else 12
                self.assertTrue(any(i["frame"] == expected for i in issues), change)

    def test_local_effect_source_wins_when_placeholder_is_reused_later(self):
        raw = "__champions_actor_p2_0001__"
        trace = [frame(1, [event("switch", "p2a", "Hatterene", "100/100"),
                           event("switch", "p2b", "Indeedee-F", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("move", "p2a", "Hatterene", move="Trick Room")]),
                 frame(3, [event("fieldstart", value="move: Trick Room")]), frame(4)]
        trace[2]["detections"]["events"][0]["tags"] = [f"[of] p2a: {raw}"]
        for row in trace[:3]:
            row["resolved_identities"][raw] = "Hatterene"
        trace[3]["resolved_identities"][raw] = "Indeedee-F"
        ledger = BattleAutomaton(0, trace).run()
        source = next(e for e in ledger["events"] if e["kind"] == "fieldstart")
        self.assertEqual(source["tags"], ["[of] p2a: Hatterene"])
        self.assertEqual(source["raw_tags"], [f"[of] p2a: {raw}"])
        trace[2]["resolved_identities"][raw] = "Indeedee-F"
        untrusted = BattleAutomaton(0, trace).run()
        source = next(e for e in untrusted["events"] if e["kind"] == "fieldstart")
        self.assertEqual(source["tags"], [f"[of] p2a: {raw}"])

    def test_lingering_faint_hud_resolves_ghost_entry_with_evidence(self):
        ledger = BattleAutomaton(0, faint_hud_trace()).run()
        self.assertNotIn("ghost_reentry_after_faint", [x["code"] for x in ledger["issues"]])
        resolved = ledger["resolved_issues"][0]
        rejected = ledger["events"][resolved["event_seq"] - 1]
        faint = ledger["events"][resolved["resolution"]["event_seq"] - 1]
        self.assertEqual((rejected["kind"], rejected["status"], faint["kind"]), ("switch", "suppressed", "faint"))
        self.assertEqual({e["kind"] for e in resolved["resolution"]["evidence"]}, {"zero_hp", "faint_narration"})
        actor = ledger["actors"][faint["actor_id"]]
        self.assertEqual((actor["health"], actor["fainted"]), ("0/100", True))
        self.assertEqual(sum(e["kind"] == "switch" and e["status"] == "consistent" for e in ledger["events"]), 1)

    def test_faint_hud_handles_same_frame_duplicate_and_last_stable_zero(self):
        trace = faint_hud_trace(same_frame=True)
        zero = trace[2]["ocr"].pop()
        trace.insert(2, frame(5))
        trace[2]["ocr"].append(zero)
        ledger = BattleAutomaton(0, trace).run()
        self.assertNotIn("reentry_without_exit", [x["code"] for x in ledger["issues"]])
        self.assertEqual(ledger["resolved_issues"][0]["code"], "reentry_without_exit")
        proof = ledger["resolved_issues"][0]["resolution"]["evidence"]
        self.assertTrue(any(e["kind"] == "zero_hp" and e["frame"] == 5 for e in proof))

    def test_faint_hud_does_not_resolve_without_matching_and_uncontradicted_evidence(self):
        for missing in ("damage", "zero_hud", "partner_hud", "one_message", "wrong_side", "wrong_name",
                        "confidence", "entry_announcement", "time", "action", "positive_hp"):
            with self.subTest(missing=missing):
                trace = faint_hud_trace()
                if missing == "damage":
                    trace[1]["detections"]["events"] = []
                elif missing == "zero_hud":
                    trace[2]["ocr"].pop()
                elif missing == "partner_hud":
                    trace[2]["ocr"][-1].update(left=.34, right=.40)
                elif missing == "one_message":
                    trace[-1]["ocr"] = []
                elif missing in {"wrong_side", "wrong_name", "confidence"}:
                    for row in trace[-2:]:
                        if missing == "wrong_side":row["ocr"][0]["text"] = "The opposing Gori fainted!"
                        elif missing == "wrong_name":row["ocr"][0]["text"] = "Blaziken fainted!"
                        else:row["ocr"][0]["confidence"] = .8
                elif missing == "entry_announcement":
                    trace[-1]["ocr"].append({"text": "Go! Gori!", "top": .75, "confidence": 1})
                elif missing == "time":
                    trace[-1]["frame"] = 20
                    trace[-1]["timestamp_ms"] = 10000
                elif missing == "action":
                    trace[-1] = frame(8, trace[-1]["detections"]["events"], texts=["Gori fainted!"])
                    trace.insert(-1, frame(7, [event("move", "p2a", "Altaria", move="Protect")]))
                elif missing == "positive_hp":
                    trace[-1]["detections"]["events"][0]["health"] = "10/100"
                    trace[-1]["ocr"].append({"text": "10/100", "confidence": .999, "left": .14, "top": .92})
                ledger = BattleAutomaton(0, trace).run()
                if missing in {"wrong_side", "wrong_name", "confidence"}:
                    self.assertIn("faint_text_unconfirmed", [x["code"] for x in ledger["issues"]])
                    self.assertFalse(next(iter(ledger["actors"].values()))["fainted"])
                else:
                    self.assertIn("ghost_reentry_after_faint", [x["code"] for x in ledger["issues"]])
                self.assertEqual(ledger["resolved_issues"], [])

    def test_real_replacement_after_faint_stays_an_entry(self):
        trace = faint_hud_trace()
        trace[-1] = frame(7, [event("switch", "p1a", "Blaziken", "100/100")], texts=["Gori fainted!"])
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([(e["species"], e["health"]) for e in ledger["events"]
                          if e["kind"] == "switch" and e["status"] == "consistent"],
                         [("Rillaboom", "10/100"), ("Blaziken", "100/100")])
        self.assertEqual(ledger["resolved_issues"], [])

    def test_returning_hud_resolves_provisional_identity_without_new_entry(self):
        for species, name in (("Kingambit", "Kingambit"), ("Rillaboom", "Bonkers")):
            with self.subTest(species=species):
                ledger = BattleAutomaton(0, returning_hud_trace(species, name)).run()
                self.assertEqual(ledger["issues"], [])
                self.assertEqual(len(ledger["actors"]), 1)
                self.assertEqual(next(iter(ledger["actors"].values()))["health"], "12/100")
                self.assertEqual(sum(e["kind"] == "switch" and e["status"] == "consistent"
                                     for e in ledger["events"]), 1)
                resolved = ledger["resolved_issues"][0]
                duplicate = ledger["events"][resolved["event_seq"] - 1]
                self.assertEqual((duplicate["frame"], duplicate["status"]), (4, "suppressed"))
                proof = resolved["resolution"]["evidence"]
                self.assertEqual({e["frame"] for e in proof}, {2, 5, 6})
                self.assertEqual({e["kind"] for e in proof}, {"hud_name", "hud_hp"})

    def test_returning_hud_keeps_warning_without_continuity(self):
        for missing in ("prior_name", "prior_hp", "prior_event", "later_name", "later_hp", "partner_name",
                        "partner_hp", "one_frame", "confidence", "different_hp", "different_name",
                        "resolution", "alias", "placeholder", "candidate_hp", "time",
                        "entry_text", "exit_text", "action_text", "move", "turn", "faint"):
            with self.subTest(missing=missing):
                trace = returning_hud_trace()
                if missing == "prior_name":
                    trace[1]["ocr"].pop()
                elif missing == "prior_hp":
                    trace[1]["ocr"].pop(0)
                elif missing == "prior_event":
                    trace[1]["detections"]["events"] = []
                elif missing in {"later_name", "later_hp", "partner_name", "partner_hp", "confidence",
                                 "different_hp", "different_name", "resolution", "alias"}:
                    for row in trace[4:6]:
                        if missing == "later_name":row["ocr"].pop(0)
                        elif missing == "later_hp":row["ocr"].pop()
                        elif missing == "partner_name":row["ocr"][0].update(left=.62, right=.69)
                        elif missing == "partner_hp":row["ocr"][-1].update(left=.70, right=.75)
                        elif missing == "confidence":row["ocr"][0]["confidence"] = .8
                        elif missing == "different_hp":row["ocr"][-1]["text"] = "13%"
                        elif missing == "different_name":row["ocr"][0]["text"] = "Blaziken"
                        elif missing == "resolution":row["resolved_identities"] = {}
                        elif missing == "alias":row["resolved_aliases"]["p2"].pop("kingambi")
                elif missing == "one_frame":
                    trace[5]["ocr"] = []
                elif missing == "placeholder":
                    trace[3]["detections"]["events"][0]["species"] = "Kingambit"
                elif missing == "candidate_hp":
                    trace[3]["detections"]["events"][0]["health"] = "15/100"
                elif missing == "time":
                    for row in trace[3:]:
                        row["timestamp_ms"] += 10_000
                elif missing in {"entry_text", "exit_text", "action_text"}:
                    text = {"entry_text": "Rival sent out Kingambit!", "exit_text": "Kingambit, come back!",
                            "action_text": "The opposing Kingambit used Protect!"}[missing]
                    trace[2]["ocr"].append({"text": text, "top": .75, "confidence": 1})
                elif missing == "move":
                    trace[2] = frame(3, [event("move", "p2b", "Kingambit", move="Protect")])
                elif missing == "turn":
                    trace[2] = frame(3, [event("turn", turn=2)])
                elif missing == "faint":
                    trace[5]["detections"]["events"].append(event("faint", "p2b", "Kingambit"))
                ledger = BattleAutomaton(0, trace).run()
                self.assertTrue(any(i["code"] == "reentry_without_exit" and i["frame"] == 4
                                    for i in ledger["issues"]))
                self.assertEqual(ledger["resolved_issues"], [])

    def test_returning_hud_does_not_absorb_real_replacement(self):
        trace = returning_hud_trace()
        trace[3] = frame(4, [event("switch", "p2b", "Blaziken", "100/100")],
                         texts=["Rival sent out Blaziken!"])
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual([e["species"] for e in ledger["events"]
                          if e["kind"] == "switch" and e["status"] == "consistent"],
                         ["Kingambit", "Blaziken"])
        self.assertEqual(ledger["resolved_issues"], [])

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

    def test_voluntary_entry_recovers_any_nickname_using_unique_team_ability(self):
        trace = voluntary_entry_trace()
        raw = json.dumps(trace)
        ledger = BattleAutomaton(0, trace, {"teams": {"p1": ["Indeedee-F", "Arcanine"]}}).run()
        entry = next(e for e in ledger["events"] if e["frame"] == 6)
        self.assertEqual((entry["kind"], entry["species"], entry["slot"]), ("switch", "Arcanine", "p1a"))
        self.assertEqual(entry["hp_state"], "inferred")
        hp = [e for e in ledger["events"] if e["actor_id"] == entry["actor_id"] and e["kind"] in {"damage", "heal"}]
        self.assertEqual([(e["before"], e["after"]) for e in hp], [("180/180", "120/180"), ("120/180", "131/180")])
        self.assertEqual(ledger["narration_links"][0]["event_seq"], hp[1]["seq"])
        self.assertEqual(json.dumps(trace), raw)
        self.assertFalse(ledger["issues"])

    def test_voluntary_entry_requires_independent_unambiguous_continuous_proof(self):
        for case in ("one_withdrawal", "one_announcement", "one_ability", "weak_ability", "no_team", "ambiguous_team",
                     "wrong_panel_owner", "other_side", "other_slot", "action_before_entry", "gap", "no_endpoint", "illusion"):
            with self.subTest(case=case):
                trace = voluntary_entry_trace()
                team = ["Indeedee-F", "Arcanine"]
                if case == "one_withdrawal": trace[3]["ocr"] = []
                if case == "one_announcement": trace[6]["ocr"] = []
                if case == "one_ability": trace[8]["ocr"] = []
                if case == "weak_ability": trace[8]["ocr"][1]["confidence"] = .8
                if case == "no_team": team = []
                if case == "ambiguous_team": team.append("Incineroar")
                if case == "illusion": team.append("Zoroark")
                if case == "wrong_panel_owner":
                    for row in trace[7:9]: row["ocr"][0]["text"] = "Other’s"
                if case == "other_side":
                    for row in trace[7:9]:
                        for line in row["ocr"]: line["left"] = .75
                if case == "other_slot":
                    trace[11]["ocr"].append({"text": "Ember", "left": .29, "top": .86, "confidence": .999})
                    trace[12]["ocr"].append({"text": "Ember", "left": .29, "top": .86, "confidence": .999})
                if case == "action_before_entry": trace[4]["detections"]["events"] = [event("move", "p2a", "Pelipper", move="Surf")]
                if case == "gap": trace[4]["timestamp_ms"] += 2000
                if case == "no_endpoint": trace[13]["ocr"] = []
                ledger = BattleAutomaton(0, trace, {"teams": {"p1": team}}).run()
                self.assertFalse(any(e.get("withdrawal_reconstruction") for e in ledger["events"]))
                self.assertIn("hp_narration_unmatched", [e["code"] for e in ledger["issues"]])

    def test_malformed_own_entry_never_confirms_a_repaired_separator(self):
        for raw in ("177177", "1771177", "1777177", "177:177", "177", "177/0", "178/177"):
            with self.subTest(raw=raw):
                trace = [frame(1, [event("switch", "p1b", "Indeedee-F", "177/177")]), frame(2)]
                for row in trace:
                    row["ocr"] = [{"text": raw, "confidence": .999, "left": .34, "top": .92}]
                original = json.dumps(trace)
                ledger = BattleAutomaton(0, trace).run()
                entry = next(e for e in ledger["events"] if e["kind"] == "switch")
                self.assertEqual((entry["health"], entry["hp_state"]), (None, "inferred"))
                self.assertEqual(entry["ignored_hp_reading"]["evidence"][0]["text"], raw)
                self.assertEqual(json.dumps(trace), original)

    def test_invalid_own_hp_preserves_last_confirmed_state_and_animation(self):
        for raw in ("40100", "40:100", "40", "40%", "40/0", "140/100", "0./100"):
            with self.subTest(raw=raw):
                trace = [frame(1, [event("switch", "p1a", "Arcanine", "100/100"), event("turn", turn=1)]),
                         frame(2, [event("move", "p2a", "Pelipper", move="Surf")]),
                         frame(3, [event("damage", "p1a", "Arcanine", "70/100")]),
                         frame(4, [event("heal", "p1a", "Arcanine", "40/100")])]
                trace[-1]["ocr"][0]["text"] = raw
                ledger = BattleAutomaton(0, trace).run()
                hp = [e for e in ledger["events"] if e["kind"] in {"damage", "heal"} and e["status"] != "suppressed"]
                self.assertEqual([(e["before"], e["after"]) for e in hp], [("100/100", "70/100")])
                self.assertEqual(next(iter(ledger["actors"].values()))["health"], "70/100")
                self.assertTrue(any(e["kind"] == "hp_rejected_reading" for e in ledger["events"]))

    def test_valid_own_hp_after_invalid_reading_uses_its_actual_frame(self):
        trace = [frame(1, [event("switch", "p1a", "Arcanine", "100/100"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p1a", "Arcanine", "40/100")]), frame(3)]
        trace[1]["ocr"][0]["text"] = "401100"
        trace[2]["ocr"] = [{"text": "40 / 100", "confidence": .999, "left": .14, "top": .92}]
        ledger = BattleAutomaton(0, trace).run()
        hp = next(e for e in ledger["events"] if e["kind"] == "damage")
        self.assertEqual((hp["frame"], hp["before"], hp["after"]), (3, "100/100", "40/100"))
        self.assertEqual(hp["observations"][0]["hp_reconstruction"]["rejected_frame"], 2)
        for boundary in (event("switch", "p1a", "Indeedee-F"), event("move", "p1a", "Arcanine", move="Protect")):
            trace[2]["detections"]["events"] = [boundary]
            checked = BattleAutomaton(0, trace).run()
            self.assertFalse(any(e["kind"] == "damage" for e in checked["events"]))

    def test_entry_waits_for_literal_hp_of_same_unknown_nickname(self):
        trace = [frame(n) for n in range(1, 10)]
        trace[0] = frame(1, [event("switch", "p1b", "Indeedee-F", "177/177")])
        for row in trace:
            row["ocr"] = [{"text": "Example", "confidence": .999, "left": .29, "top": .86},
                          {"text": "1777177", "confidence": .999, "left": .34, "top": .92}]
        trace[7]["ocr"][1]["text"] = "177/177"
        ledger = BattleAutomaton(0, trace).run()
        entry = ledger["events"][0]
        self.assertEqual((entry["health"], entry["hp_state"]), ("177/177", "confirmed"))
        self.assertEqual(entry["hp_support"]["evidence"][0]["frame"], 8)
        for barrier in ("action", "other_occupant"):
            trace[5]["ocr"] = ([{"text": "Example used Protect!", "top": .75, "confidence": .999}] if barrier == "action"
                               else [{"text": "Other", "left": .29, "top": .86, "confidence": .999}])
            ledger = BattleAutomaton(0, trace).run()
            self.assertNotEqual(ledger["events"][0]["hp_state"], "confirmed")

    def test_missing_slash_stays_provisional_without_continuity_or_unique_name(self):
        for case in ("single", "weak", "moving", "other_name", "duplicate_name", "action", "gap", "partial"):
            with self.subTest(case=case):
                trace = [frame(1), frame(2)]
                for row in trace:
                    row["ocr"] = [
                        {"text": "143143", "confidence": .93, "left": .34, "top": .92},
                        {"text": "Example", "confidence": .999, "left": .29, "top": .86},
                    ]
                health = "143/143"
                if case == "single": trace[1]["ocr"] = []
                if case == "weak": trace[1]["ocr"][0]["confidence"] = .89
                if case == "moving": trace[1]["ocr"][0]["left"] = .37
                if case == "other_name": trace[1]["ocr"][1]["text"] = "Other"
                if case == "duplicate_name":
                    trace[1]["ocr"].append({"text": "Example", "confidence": .999, "left": .09, "top": .86})
                if case == "action": trace[1]["detections"]["events"] = [event("move", "p1a", "Blaziken", move="Protect")]
                if case == "gap": trace[1]["timestamp_ms"] = 2500
                if case == "partial":
                    health = "14/3143"
                machine = BattleAutomaton(0, trace)
                self.assertEqual(machine._hp_support("p1b", health, 1)["state"], "unconfirmed")

    def test_clock_numbers_are_excluded_before_all_hp_passes_without_mutating_trace(self):
        trace = [frame(1)]
        trace[0]["ocr"] = [
            {"text": "3%", "confidence": .999, "left": .79, "top": .16, "bottom": .19},
            {"text": "5/180", "confidence": .999, "left": .17, "top": .81, "bottom": .84},
            {"text": "Protect", "confidence": .999, "left": .85, "top": .55},
        ]
        raw = json.dumps(trace)
        machine = BattleAutomaton(0, trace)
        self.assertEqual(machine._hp_support("p2a", "3/100", 1)["state"], "unconfirmed")
        self.assertEqual([line["text"] for line in machine.frames[0]["ocr"]], ["Protect"])
        self.assertEqual({line["region"] for line in machine.clock_readings[1]}, {"p1_clock", "p2_clock"})
        self.assertEqual(json.dumps(trace), raw)

    def test_clock_candidate_is_rejected_before_state_even_after_an_action(self):
        for slot, species, health, left, top in (("p2b", "Pikachu", "100/100", .79, .16),
                                                 ("p1a", "Arcanine", "180/180", .17, .81)):
            with self.subTest(slot=slot):
                trace = [frame(1, [event("switch", slot, species, health), event("turn", turn=1),
                                  event("move", slot, species, move="Protect")]),
                         frame(2, [event("damage", slot, species, "5/180")]),
                         frame(3, [event("heal", slot, species, health)])]
                trace[1]["ocr"] = [{"text": "05:18 0", "confidence": .84, "left": left,
                                     "right": left + .14, "top": top, "bottom": top + .03}]
                ledger = BattleAutomaton(0, trace).run()
                rejected = next(e for e in ledger["events"] if e["kind"] == "hp_rejected_reading")
                self.assertEqual((rejected["before"], rejected["after"], rejected["hp_state"]),
                                 (health, health, "rejected"))
                self.assertEqual(rejected["hp_support"]["raw_event"]["health"], "5/180")
                self.assertEqual(rejected["hp_support"]["evidence"][0]["text"], "05:18 0")
                self.assertFalse(ledger["issues"])
                self.assertFalse(any(e["kind"] in {"damage", "heal"} and e["status"] != "suppressed"
                                     for e in ledger["events"]))

    def test_real_hp_in_its_hud_wins_over_same_number_in_clock(self):
        for slot, species, initial, after, clock in (("p1a", "Arcanine", "180/180", "5/180", "05:180"),
                                                    ("p2b", "Pikachu", "100/100", "4/100", "04:100")):
            with self.subTest(slot=slot):
                trace = [frame(1, [event("switch", slot, species, initial), event("turn", turn=1),
                                  event("move", slot, species, move="Protect")]),
                         frame(2, [event("damage", slot, species, after)])]
                trace[1]["ocr"].append({"text": clock, "confidence": .999, "left": .79,
                                          "right": .92, "top": .16, "bottom": .19})
                ledger = BattleAutomaton(0, trace).run()
                damage = next(e for e in ledger["events"] if e["kind"] == "damage")
                self.assertEqual((damage["after"], damage["hp_state"]), (after, "confirmed"))
                self.assertFalse(any(e["kind"] == "hp_rejected_reading" for e in ledger["events"]))

    def test_clock_source_requires_matching_digits_and_clock_coordinates(self):
        trace = [frame(1)]
        for text, left, top in (("05:180", .50, .40), ("04:330", .79, .16)):
            with self.subTest(text=text):
                trace[0]["ocr"] = [{"text": text, "confidence": .99, "left": left, "top": top}]
                machine = BattleAutomaton(0, trace)
                candidate = {"event": event("damage", "p2b", "Pikachu", "5/180"),
                             "observed_frame": 1, "observed_ms": 500}
                self.assertIsNone(machine._clock_hp_source(candidate))

    def test_weak_real_hp_matching_clock_remains_pending_instead_of_being_discarded(self):
        trace = [frame(1, [event("switch", "p2b", "Pikachu", "100/100"), event("turn", turn=1),
                          event("move", "p2b", "Pikachu", move="Protect")]),
                 frame(2, [event("damage", "p2b", "Pikachu", "4/100")])]
        trace[1]["ocr"][0]["confidence"] = .8
        trace[1]["ocr"].append({"text": "04:100", "confidence": .999, "left": .79,
                                  "right": .92, "top": .16, "bottom": .19})
        ledger = BattleAutomaton(0, trace).run()
        self.assertTrue(any(e["kind"] == "hp_unconfirmed" for e in ledger["events"]))
        self.assertFalse(any(e["kind"] == "hp_rejected_reading" for e in ledger["events"]))

    def test_clock_noise_does_not_split_an_ongoing_hp_animation(self):
        trace = [frame(1, [event("switch", "p1a", "Arcanine", "180/180"),
                          event("switch", "p2b", "Pikachu", "100/100"), event("turn", turn=1),
                          event("move", "p2b", "Pikachu", move="Thunderbolt")]),
                 frame(2, [event("damage", "p1a", "Arcanine", "140/180")]),
                 frame(3, [event("damage", "p2b", "Pikachu", "5/180")]),
                 frame(4, [event("damage", "p1a", "Arcanine", "80/180")])]
        trace[2]["ocr"] = [{"text": "05:180", "confidence": .9, "left": .79, "top": .16}]
        ledger = BattleAutomaton(0, trace).run()
        hp = [e for e in ledger["events"] if e["kind"] == "damage"]
        self.assertEqual(len(hp), 1)
        self.assertEqual((hp[0]["before"], hp[0]["after"]), ("180/180", "80/180"))
        self.assertEqual([o["frame"] for o in hp[0]["observations"]], [2, 4])

    def test_percentage_hud_cannot_confirm_fraction_with_another_denominator(self):
        machine = BattleAutomaton(0, [frame(1, [event("damage", "p2b", "Pikachu", "5/180")])])
        self.assertEqual(machine._hp_support("p2b", "5/180", 1)["state"], "unconfirmed")

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

    def test_terrain_restoration_discards_truncated_damage_and_heals_from_prior_hp(self):
        trace = [frame(1, [event("switch", "p2a", "Sneasler", "41/100"),
                           event("fieldstart", value="move: Grassy Terrain"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Sneasler", "1/100")]),
                 frame(3, [event("heal", "p2a", "Sneasler", "47/100")]), frame(4),
                 frame(5, [event("message", value="The opposing Sneasler had its HP restored.")])]
        trace[1]["ocr"] = [
            {"text": "44", "confidence": .999, "left": .70, "right": .74,
             "top": .11, "bottom": .16},
            {"text": "1%", "confidence": .86, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        trace[2]["ocr"] = [{"text": "47", "confidence": .97, "left": .70,
                            "right": .74, "top": .11, "bottom": .16},
                           {"text": "%", "confidence": .99, "left": .73,
                            "right": .75, "top": .12, "bottom": .16}]
        trace[3]["ocr"] = [{"text": "47%", "confidence": .999, "left": .70,
                            "right": .75, "top": .11, "bottom": .16}]
        ledger = BattleAutomaton(0, trace).run()
        rejected = next(x for x in ledger["events"] if x["kind"] == "hp_rejected_reading")
        heal = next(x for x in ledger["events"] if x["kind"] == "heal")
        self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                         ("suppressed", "41/100", "41/100"))
        self.assertEqual(rejected["observations"][0]["competing_ocr"]["stronger"]["text"], "44")
        self.assertEqual((heal["status"], heal["before"], heal["after"]),
                         ("consistent", "41/100", "47/100"))
        self.assertEqual(heal["cause"], "Grassy Terrain corroborado por HUD y mensaje")
        self.assertIn("The opposing Sneasler had its HP restored.", heal["narration"])
        self.assertFalse(any(x["code"] == "hp_ocr_conflict" for x in ledger["issues"]))

    def test_terrain_animation_conflicting_zero_requires_monotone_named_heal(self):
        trace = [frame(1, [event("switch", "p2a", "Rillaboom", "28/100"),
                           event("fieldstart", value="move: Grassy Terrain"), event("turn", turn=1),
                           event("move", "p2a", "Rillaboom", move="Protect")]),
                 frame(2, [event("damage", "p2a", "Rillaboom", "0/100")]),
                 frame(3, [event("heal", "p2a", "Rillaboom", "32/100")]),
                 frame(4, [event("heal", "p2a", "Rillaboom", "33/100")]),
                 frame(5, [event("message", value="The opposing Rillaboom had its HP restored.")])]
        trace[1]["ocr"] = [
            {"text": "29", "confidence": .99999, "left": .70, "right": .74, "top": .11, "bottom": .15},
            {"text": "0%", "confidence": .75, "left": .73, "right": .75, "top": .12, "bottom": .16},
            {"text": "Rillaboom", "confidence": .999, "left": .62, "top": .05}]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(ledger["issues"], [])
        heal = next(e for e in ledger["events"] if e["kind"] == "heal")
        self.assertEqual((heal["before"], heal["after"]), ("28/100", "33/100"))
        for missing in ("terrain", "narration", "nickname", "number"):
            rows = copy.deepcopy(trace)
            if missing == "terrain":
                rows[0]["detections"]["events"] = [e for e in rows[0]["detections"]["events"] if e["kind"] != "fieldstart"]
            elif missing == "narration":
                rows[4]["detections"]["events"] = []
            elif missing == "nickname":
                rows[1]["ocr"][-1]["text"] = "Pelipper"
            else:
                rows[1]["ocr"][0]["text"] = "50"
            uncertain = BattleAutomaton(0, rows).run()
            self.assertTrue(any(e["code"] == "hp_ocr_conflict" for e in uncertain["issues"]), missing)

    def test_terrain_without_restoration_message_keeps_conflict_for_review(self):
        trace = [frame(1, [event("switch", "p2a", "Sneasler", "41/100"),
                           event("fieldstart", value="move: Grassy Terrain"), event("turn", turn=1)]),
                 frame(2, [event("damage", "p2a", "Sneasler", "1/100")]),
                 frame(3, [event("heal", "p2a", "Sneasler", "47/100")]), frame(4)]
        trace[1]["ocr"] = [
            {"text": "44", "confidence": .999, "left": .70, "right": .74,
             "top": .11, "bottom": .16},
            {"text": "1%", "confidence": .86, "left": .73, "right": .75,
             "top": .12, "bottom": .16},
        ]
        trace[3]["ocr"] = [{"text": "47%", "confidence": .999, "left": .70,
                            "right": .75, "top": .11, "bottom": .16}]
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(x["code"] == "hp_ocr_conflict" for x in ledger["issues"]), 1)
        self.assertEqual(next(x for x in ledger["events"] if x["kind"] == "heal")["before"],
                         "41/100")

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

    def test_wrong_slot_mega_resolves_only_to_corroborated_accepted_event(self):
        ledger = BattleAutomaton(0, mega_duplicate_trace()).run()
        rejected = next(e for e in ledger["events"] if e["kind"] == "mega" and e["status"] == "suppressed")
        accepted = next(e for e in ledger["events"] if e["kind"] == "mega" and e["status"] == "consistent")
        self.assertEqual((accepted["slot"], accepted["species"]), ("p2a", "Delphox-Mega"))
        self.assertEqual(rejected["resolution"]["event_seq"], accepted["seq"])
        self.assertEqual([e["frame"] for e in rejected["resolution"]["evidence"]], [3, 4])
        self.assertEqual(rejected["mega_candidate"]["species"], "Delphox")
        self.assertEqual(len(ledger["resolved_issues"]), 1)
        self.assertNotIn("mega_wrong_occupant", [x["code"] for x in ledger["issues"]])
        self.assertIsNone(next(a["forme"] for a in ledger["actors"].values() if a["species"] == "Indeedee-F"))

    def test_wrong_slot_mega_keeps_warning_without_complete_corroboration(self):
        for missing in ("one_frame", "side", "stone", "forme", "confidence", "time", "action", "turn",
                        "accepted_event", "distinct_frames"):
            with self.subTest(missing=missing):
                trace = mega_duplicate_trace(20 if missing == "time" else 5)
                if missing == "one_frame":
                    trace[-1]["ocr"] = []
                elif missing == "side":
                    for row in trace[-2:]:
                        row["ocr"][0]["text"] = row["ocr"][0]["text"].removeprefix("The opposing ")
                elif missing in {"stone", "forme"}:
                    trace[1]["detections"]["events"][0]["value" if missing == "stone" else "forme"] = "other"
                elif missing == "confidence":
                    for row in trace[-2:]:
                        row["ocr"][0]["confidence"] = .8
                elif missing == "action":
                    trace.insert(2, frame(3, [event("move", "p1a", "Indeedee-F", move="Protect")]))
                elif missing == "turn":
                    trace.insert(2, frame(3, [event("turn", turn=2)]))
                elif missing == "accepted_event":
                    trace[-2]["detections"]["events"] = []
                elif missing == "distinct_frames":
                    trace[-1]["frame"] = trace[-2]["frame"]
                    trace[-1]["timestamp_ms"] = trace[-2]["timestamp_ms"]
                ledger = BattleAutomaton(0, trace).run()
                self.assertIn("mega_wrong_occupant", [x["code"] for x in ledger["issues"]])
                self.assertEqual(ledger["resolved_issues"], [])

    def test_two_supported_megas_do_not_resolve_an_ambiguous_wrong_slot(self):
        trace = mega_duplicate_trace()
        trace[0]["detections"]["events"].insert(1, event("switch", "p1b", "Delphox", "100/100"))
        own = {**event("mega", "p1b", "Delphox", value="Delphoxite"), "forme": "Delphox-Mega"}
        trace[2]["detections"]["events"].append(own)
        for row in trace[-2:]:
            row["ocr"].append({"text": "Delphox's Delphoxite is reacting to Player's Omni Ring!",
                               "top": .75, "confidence": 1})
        ledger = BattleAutomaton(0, trace).run()
        self.assertEqual(sum(e["kind"] == "mega" and e["status"] == "consistent" for e in ledger["events"]), 2)
        self.assertIn("mega_wrong_occupant", [x["code"] for x in ledger["issues"]])
        self.assertEqual(ledger["resolved_issues"], [])

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

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_EIGHTH"), "Requiere el octavo ZIP del usuario")
    def test_buffered_opponent_replacement_keeps_entry_and_ability_at_their_evidence(self):
        frames, _ = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_EIGHTH"]))
        ledger = BattleAutomaton(0, frames).run()
        entry = next(e for e in ledger["events"] if e["kind"] == "switch" and
                     e["slot"] == "p2b" and e["species"] == "Weavile")
        ability = next(e for e in ledger["events"] if e["kind"] == "ability" and
                       e["slot"] == "p2b" and e["value"] == "Pressure")
        checkpoint = next(e for e in ledger["events"] if e["kind"] == "hp_checkpoint" and
                          e["slot"] == "p2b")
        self.assertEqual((entry["logical_frame"], entry["frame"], entry["hp_state"]), (300, 386, "inferred"))
        self.assertEqual((ability["frame"], ability["logical_frame"], ability["turn"]), (307, 307, 2))
        self.assertIn("pressure", " ".join(ability["narration"]).casefold())
        self.assertEqual((checkpoint["frame"], checkpoint["health"], checkpoint["hp_state"]),
                         (386, "100/100", "confirmed"))
        self.assertFalse(ledger["issues"])
        # The buffered timestamp is not enough: remove the independent ability
        # panel and the old interpretation must remain open to review.
        for row in frames:
            if 307 <= row["frame"] <= 310:
                row["ocr"] = [line for line in row["ocr"] if
                              line.get("text") not in {"Kushina's", "Pressure"}]
        uncertain = BattleAutomaton(0, frames).run()
        self.assertFalse(any(e.get("delayed_voluntary_entry") for e in uncertain["events"]))
        self.assertIn("unclassified_text", {issue["code"] for issue in uncertain["issues"]})

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_NINTH"), "Requiere el noveno ZIP del usuario")
    def test_partner_hud_and_repeated_faint_text_need_independent_evidence(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_NINTH"])
        frames, baselines = read_diagnostic(path)
        self.assertEqual(set(baselines), {0, 1})
        context = read_diagnostic_context(path)
        first = [r for r in frames if r["battle_index"] == 0]
        ledgers = {i: BattleAutomaton(i, [r for r in frames if r["battle_index"] == i], context).run()
                   for i in baselines}
        self.assertEqual([ledger["issues"] for ledger in ledgers.values()], [[], []])
        self.assertEqual([sum(e["kind"] in {"damage", "heal"} and e["status"] == "consistent"
                              for e in ledger["events"]) for ledger in ledgers.values()], [21, 11])
        self.assertTrue(all(compare_baseline(ledgers[i], baselines[i])["exact_hp_sequence"] for i in baselines))
        first_ledger = ledgers[0]
        self.assertTrue(compare_baseline(first_ledger, baselines[0])["exact_core_sequence"])
        opening = [e for e in ledgers[1]["events"] if e["kind"] == "ability" and e["frame"] < 1390]
        self.assertEqual([(e["frame"], e["slot"], e["value"]) for e in opening],
                         [(1370, "p2a", "Intimidate"), (1375, "p1a", "Defiant"),
                          (1380, "p2b", "Intimidate"), (1385, "p1a", "Defiant")])
        self.assertEqual(compare_baseline(ledgers[1], baselines[1])["first_differences"],
                         [{"operation": "insert", "baseline": [],
                           "automaton": [("ability", "p1a", "Defiant")]}])
        self.assertTrue(all(len(e["ability_reconstruction"]["evidence"]) >= 2 for e in opening))
        readable = render_markdown(ledgers[1], compare_baseline(ledgers[1], baselines[1]))
        self.assertIn("0 avisos abiertos", readable)
        self.assertIn("Inicial · Entra Salamence (rival, p2a)", readable)
        self.assertIn("Habilidad de Kingambit (propio, p1a): Defiant", readable)
        self.assertIn("pierde PS: 177/177 → 102/177 · fotograma 1479 · acción asociada: Dragon Pulse", readable)
        self.assertIn("recupera PS: 30/177 → 74/177 · fotograma 1580 · tras Sitrus Berry", readable)
        self.assertNotIn("Replay archivado", readable)
        self.assertNotIn("detector tardío en frame None", readable)
        self.assertNotIn("causa: 14", readable)
        self.assertLess(readable.index("Habilidad de Salamence"), readable.index("Habilidad de Kingambit"))
        repeated = next(e for e in first_ledger["events"] if e["kind"] == "faint" and e["frame"] == 672)
        resurfaced = next(e for e in first_ledger["events"] if e["kind"] == "switch" and
                          e["slot"] == "p2a" and e["frame"] == 714)
        self.assertEqual((repeated["status"], resurfaced["status"]), ("suppressed", "suppressed"))
        hp_proof = next(e for e in first_ledger["events"] if e["seq"] == repeated["resolution"]["hp_event_seq"])
        self.assertEqual((repeated["resolution"]["slot"], hp_proof["kind"], hp_proof["slot"], hp_proof["after"]),
                         ("p2b", "damage", "p2b", "0/100"))
        self.assertEqual({e["frame"] for e in resurfaced["resolution"]["evidence"]},
                         {640, 641, 714, 715})
        self.assertEqual([i["code"] for i in first_ledger["resolved_issues"]],
                         ["faint_text_unconfirmed", "reentry_without_exit"])
        first_log = render_markdown(first_ledger, compare_baseline(first_ledger, baselines[0]))
        self.assertIn("Trick Room · fotograma 473 · 3 lecturas del mismo estado (fotogramas 473, 475, 476)", first_log)
        self.assertIn("Avisos resueltos con evidencia", first_log)

        # A single faint reading cannot discard the detector's other-slot
        # candidate; an explicit withdrawal forbids HUD continuity.
        missing_narration = json.loads(json.dumps(first))
        for row in missing_narration:
            if row["frame"] == 670:
                row["ocr"] = [line for line in row["ocr"] if
                              line.get("text") != "The opposing Salamence fainted!"]
        unresolved = BattleAutomaton(0, missing_narration, context).run()
        self.assertIn("faint_text_unconfirmed", {i["code"] for i in unresolved["issues"]})
        withdrawn = json.loads(json.dumps(first))
        for row in withdrawn:
            if row["frame"] == 705:
                row["ocr"].append({"text": "Rival withdrew Incineroar!", "confidence": .999,
                                   "left": .15, "top": .75})
        unresolved = BattleAutomaton(0, withdrawn, context).run()
        self.assertIn("reentry_without_exit", {i["code"] for i in unresolved["issues"]})
        missing_panel = json.loads(json.dumps([r for r in frames if r["battle_index"] == 1]))
        for row in missing_panel:
            if 1380 <= row["frame"] <= 1383:
                row["ocr"] = [line for line in row["ocr"] if line.get("text") != "Captain's"]
        uncertain = BattleAutomaton(1, missing_panel, context).run()
        self.assertFalse(any(e["kind"] == "ability" and e["slot"] == "p2b" and
                             e["value"] == "Intimidate" for e in uncertain["events"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_TENTH"), "Requiere el décimo ZIP del usuario")
    def test_tenth_job_has_readable_logs_without_local_issues(self):
        frames, _ = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_TENTH"]))
        ledgers = [BattleAutomaton(i, [r for r in frames if r["battle_index"] == i]).run()
                   for i in range(2)]
        self.assertEqual([ledger["issues"] for ledger in ledgers], [[], []])
        self.assertEqual([sum(e["kind"] in {"damage", "heal"} and e["status"] == "consistent"
                              for e in ledger["events"]) for ledger in ledgers], [10, 8])
        text = render_markdown(ledgers[1], None)
        self.assertEqual(text.count("Termina la batalla por abandono"), 1)
        self.assertNotIn("Are you sure you wish to forfeit?", text)
        self.assertIn("HUD: «83» y «%» separados", text)
        prompt = next(e for e in ledgers[1]["events"] if e["frame"] == 1301)
        self.assertEqual((prompt["kind"], prompt["status"]), ("ui_text", "suppressed"))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_ELEVENTH"), "Requiere el undécimo ZIP del usuario")
    def test_golden_opponent_hp_needs_hud_and_repeated_narration(self):
        frames, _ = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_ELEVENTH"]))
        second = [r for r in frames if r["battle_index"] == 1]
        ledger = BattleAutomaton(1, second).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(sum(e["kind"] in {"damage", "heal"} and e["status"] == "consistent"
                             for e in ledger["events"]), 19)
        self.assertEqual(len(ledger["narration_links"]), 9)
        self.assertTrue(all(link["status"] == "linked" for link in ledger["narration_links"]))
        fragment = next(e for e in ledger["events"] if e["frame"] == 1705)
        restored = next(e for e in ledger["events"] if e["frame"] == 1707)
        loss = next(e for e in ledger["events"] if e["frame"] == 1782)
        self.assertEqual((fragment["kind"], fragment["status"]), ("hp_rejected_reading", "suppressed"))
        self.assertEqual((restored["before"], restored["after"], restored["cause"]),
                         ("78/100", "84/100", "Grassy Terrain corroborado por HUD y mensaje"))
        self.assertEqual((loss["kind"], loss["before"], loss["after"], loss["hp_state"]),
                         ("damage", "84/100", "75/100", "confirmed"))
        self.assertEqual([e["frame"] for e in loss["hp_support"]["evidence"]],
                         [1780, 1782, 1782, 1784, 1785])
        log = render_markdown(ledger, None)
        self.assertIn("Gholdengo (rival, p2a) pierde PS: 84/100 → 75/100", log)
        blaziken = log.split("Blaziken-Mega (propio, p1a) pierde PS: 156/156 → 0/156", 1)[1]
        self.assertIn("Pantalla: «0/156»", blaziken.split("\n- ", 1)[0])

        def without_ocr(number, words):
            altered = json.loads(json.dumps(second))
            for row in altered:
                if row["frame"] == number:
                    row["ocr"] = [line for line in row["ocr"] if line.get("text") not in words]
            return BattleAutomaton(1, altered).run()

        no_stronger = without_ocr(1705, {"78"})
        self.assertFalse(any(e["frame"] == 1705 and e["status"] == "suppressed"
                             for e in no_stronger["events"]))
        altered = json.loads(json.dumps(second))
        for row in altered:
            if row["frame"] in {1709, 1710, 1711}:
                row["ocr"] = [line for line in row["ocr"] if
                              line.get("text") != "The opposing Gholdengo had its HP restored."]
                row["detections"]["events"] = [event for event in row["detections"]["events"] if
                                                 event.get("value") != "The opposing Gholdengo had its HP restored."]
        no_restoration = BattleAutomaton(1, altered).run()
        self.assertIn("hp_ocr_conflict", {i["code"] for i in no_restoration["issues"]})
        no_number = without_ocr(1782, {"75"})
        self.assertIn("hp_unconfirmed", {i["code"] for i in no_number["issues"]})
        altered = json.loads(json.dumps(second))
        for row in altered:
            if row["frame"] in {1785, 1786}:
                row["ocr"] = [line for line in row["ocr"] if
                              line.get("text") != "The opposing Gholdengo lost some of its HP!"]
        no_repetition = BattleAutomaton(1, altered).run()
        self.assertIn("hp_unconfirmed", {i["code"] for i in no_repetition["issues"]})

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC"), "Requiere el ZIP original del usuario")
    def test_five_approved_battles_retain_their_core_event_order(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC"]))
        self.assertEqual(len(baselines), 5)
        message_count = 0
        for index, baseline in baselines.items():
            with self.subTest(battle_index=index):
                ledger = BattleAutomaton(index, [x for x in frames if x["battle_index"] == index]).run()
                comparison = compare_baseline(ledger, baseline)
                if index == 0:
                    # Four repeated frames show a lead's Psychic Surge panel;
                    # the archived replay omitted the ability but kept its terrain.
                    self.assertEqual(comparison["aligned_events"], comparison["baseline_core_events"])
                    self.assertEqual(comparison["first_differences"],
                                     [{"operation": "insert", "baseline": [],
                                       "automaton": [("ability", "p1b", "Psychic Surge")]}])
                    ability = next(e for e in ledger["events"] if e["kind"] == "ability" and e["frame"] == 209)
                    self.assertEqual([x["frame"] for x in ability["ability_reconstruction"]["evidence"]],
                                     [209, 210, 211, 212])
                else:
                    self.assertTrue(comparison["exact_core_sequence"])
                self.assertTrue(all(link["status"] == "linked" for link in ledger["narration_links"]))
                message_count += len(ledger["narration_links"])
                if index == 0:
                    rejected = next(e for e in ledger["events"] if e["frame"] == 358 and
                                    e["kind"] == "hp_rejected_reading")
                    self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                                     ("suppressed", "18/100", "18/100"))
                    self.assertFalse(any(x["code"] == "hp_ocr_conflict" and x["frame"] == 358
                                         for x in ledger["issues"]))
                    burn = next(e for e in ledger["events"] if e["frame"] == 371 and e["kind"] == "damage")
                    self.assertEqual(burn["cause"], "quemadura observada")
                    self.assertEqual(burn["causal_evidence"][0]["frame"], 369)
                    self.assertEqual(rejected["narration"], [])
                    ghost = next(e for e in ledger["events"] if e["frame"] == 1350 and e["kind"] == "switch")
                    self.assertEqual(ghost["resolution"]["state"], "resolved")
                    faint = ledger["events"][ghost["resolution"]["event_seq"] - 1]
                    self.assertEqual((faint["frame"], faint["kind"]), (1349, "faint"))
                if index == 1:
                    self.assertEqual(ledger["issues"], [])
                    duplicate = next(e for e in ledger["events"] if e["frame"] == 2668 and e["kind"] == "switch")
                    self.assertEqual(duplicate["resolution"]["state"], "resolved")
                    self.assertEqual(duplicate["resolution"]["observed_name"], "kingamh")
                    self.assertEqual({e["frame"] for e in duplicate["resolution"]["evidence"]}, {2659, 2669, 2670})
                    hp = ledger["events"][duplicate["resolution"]["event_seq"] - 1]
                    self.assertEqual((hp["frame"], hp["after"]), (2659, "12/100"))
                    rejected = next(e for e in ledger["events"] if e["frame"] == 2797 and
                                    e["kind"] == "hp_rejected_reading")
                    self.assertEqual((rejected["status"], rejected["before"], rejected["after"]),
                                     ("suppressed", "88/100", "88/100"))
                    self.assertFalse(any(x["code"] == "hp_oscillation" and x["frame"] == 2797
                                         for x in ledger["issues"]))
                if index == 4:
                    ghost = next(e for e in ledger["events"] if e["frame"] == 6106 and e["kind"] == "switch")
                    self.assertEqual(ghost["resolution"]["state"], "resolved")
                    self.assertTrue(any(e["kind"] == "zero_hp" and e["frame"] < 6105
                                        for e in ghost["resolution"]["evidence"]))
        self.assertEqual(message_count, 57)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_SECOND"), "Requiere el segundo ZIP del usuario")
    def test_second_job_keeps_status_and_exposes_misreadings(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_SECOND"])
        frames, baselines = read_diagnostic(path)
        self.assertEqual(set(baselines), {0, 1, 2, 3})
        ledgers = {i: BattleAutomaton(i, [x for x in frames if x["battle_index"] == i]).run()
                   for i in baselines}
        links = [link for ledger in ledgers.values() for link in ledger["narration_links"]]
        self.assertEqual(len(links), 39)
        self.assertTrue(all(link["status"] == "linked" for link in links))
        for damage_frame, message_frame in ((3067, 3064), (3183, 3181)):
            burn = next(e for e in ledgers[2]["events"] if e["frame"] == damage_frame)
            self.assertEqual(burn["cause"], "quemadura observada")
            self.assertEqual(burn["causal_evidence"][0]["frame"], message_frame)
        self.assertFalse(any("hurt by its burn" in text for e in ledgers[2]["events"]
                             if e["kind"] == "heal" for text in e["narration"]))
        self.assertTrue(all(compare_baseline(ledgers[i], baselines[i])["exact_core_sequence"]
                            for i in (0, 1, 3)))
        self.assertTrue(any(a["species"] == "Golisopod" and a["status"] == "par"
                            for a in ledgers[0]["actors"].values()))
        self.assertTrue(any(a["species"] == "Indeedee-F" and a["status"] == "par"
                            for a in ledgers[3]["actors"].values()))
        battle = ledgers[1]
        self.assertEqual({e["frame"] for e in battle["events"] if e["kind"] == "hp_ocr_conflict"},
                         set())
        self.assertTrue({1694, 1766, 2082}.issubset(
            {e["frame"] for e in battle["events"] if e["kind"] == "hp_rejected_reading" and
             e["status"] == "suppressed"}))
        sneasler_heal = next(e for e in battle["events"] if e["kind"] == "heal" and
                             e["frame"] == 2083 and e["slot"] == "p2a")
        self.assertEqual((sneasler_heal["before"], sneasler_heal["after"],
                          sneasler_heal["cause"]),
                         ("41/100", "47/100", "Grassy Terrain corroborado por HUD y mensaje"))
        self.assertEqual(sum(e["kind"] == "mega" and e["slot"] == "p1a" and
                             e["status"] == "consistent" for e in battle["events"]), 0)
        rejected_mega = next(e for e in battle["events"] if e["kind"] == "mega" and e["frame"] == 1465)
        accepted_mega = battle["events"][rejected_mega["resolution"]["event_seq"] - 1]
        self.assertEqual((accepted_mega["frame"], accepted_mega["slot"], accepted_mega["species"]),
                         (1466, "p2a", "Delphox-Mega"))
        self.assertEqual([e["frame"] for e in rejected_mega["resolution"]["evidence"]], [1466, 1467])
        self.assertNotIn("mega_wrong_occupant", [x["code"] for x in battle["issues"]])
        ghost = next(e for e in battle["events"] if e["kind"] == "switch" and e["frame"] == 1550)
        self.assertEqual(ghost["resolution"]["state"], "resolved")
        duplicate = next(e for e in ledgers[2]["events"] if e["kind"] == "switch" and e["frame"] == 2688)
        faint = ledgers[2]["events"][duplicate["resolution"]["event_seq"] - 1]
        self.assertEqual((faint["frame"], faint["kind"], faint["actor_id"]),
                         (2688, "faint", duplicate["actor_id"]))
        self.assertFalse(any(x["code"] in {"ghost_reentry_after_faint", "reentry_without_exit"}
                             for ledger in ledgers.values() for x in ledger["issues"]))
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

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_SEVENTH"), "Requiere el séptimo ZIP del usuario")
    def test_seventh_job_consolidates_opponent_move_and_recovers_withdrawal_and_literal_hp(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_SEVENTH"]))
        self.assertEqual(len(baselines), 2)
        ledgers = [BattleAutomaton(i, [r for r in frames if r["battle_index"] == i],
                                   read_diagnostic_context(Path(os.environ["CHAMPIONS_DIAGNOSTIC_SEVENTH"]))).run() for i in range(2)]
        first = ledgers[0]
        moves = [e for e in first["events"] if 685 <= e["frame"] <= 687]
        self.assertEqual([(e["slot"], e["move"], e["status"]) for e in moves],
                         [("p2b", "Wood Hammer", "consistent"), ("p2b", "Wood Hammer", "suppressed"),
                          ("p2b", "Wood Hammer", "suppressed")])
        proof = moves[1]["move_narration_support"]
        self.assertEqual(proof["raw_event"]["slot"], "p1a")
        self.assertEqual([e["frame"] for e in proof["evidence"]], [685, 687, 688])
        hp = next(e for e in first["events"] if e["frame"] == 692 and e["kind"] == "damage")
        self.assertEqual(hp["cause"], moves[0]["seq"])
        comparison = compare_baseline(first, baselines[0])
        self.assertEqual(comparison["automaton_core_events"], 40)
        self.assertFalse(comparison["exact_hp_sequence"])
        self.assertFalse(first["issues"])
        self.assertFalse(ledgers[1]["issues"])
        entry = next(e for e in first["events"] if e["frame"] == 810 and e["kind"] == "switch")
        self.assertEqual((entry["species"], entry["slot"], entry["hp_state"]), ("Rillaboom", "p1a", "inferred"))
        self.assertEqual(entry["withdrawal_reconstruction"]["hud_frames"], [852, 853])
        hp = [e for e in first["events"] if e["actor_id"] == entry["actor_id"] and e["kind"] in {"damage", "heal"}]
        self.assertEqual([(e["before"], e["after"]) for e in hp], [("207/207", "147/207"), ("147/207", "159/207")])
        self.assertEqual(hp[0]["hp_baseline"]["state"], "inferred")
        self.assertTrue(all(e["hp_state"] == "confirmed" for e in hp))
        self.assertEqual(next(e for e in first["narration_links"] if e["frame"] == 876)["event_seq"], hp[1]["seq"])
        own = next(e for e in ledgers[1]["events"] if e["kind"] == "switch" and e["slot"] == "p1b")
        self.assertEqual((own["health"], own["hp_state"]), ("177/177", "confirmed"))
        self.assertEqual(own["hp_support"]["evidence"][0]["frame"], 1206)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_SIXTH"), "Requiere el sexto ZIP del usuario")
    def test_sixth_job_confirms_entry_and_excludes_clocks_without_losing_real_hp(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_SIXTH"]))
        self.assertEqual(len(baselines), 2)
        ledgers = [BattleAutomaton(i, [r for r in frames if r["battle_index"] == i]).run() for i in range(2)]
        for ledger, baseline in zip(ledgers, (baselines[0], baselines[1])):
            self.assertFalse(ledger["issues"])
            self.assertTrue(compare_baseline(ledger, baseline)["exact_core_sequence"])
        entry = next(e for e in ledgers[0]["events"] if e["frame"] == 184 and e["kind"] == "switch" and e["slot"] == "p1b")
        self.assertEqual((entry["species"], entry["health"], entry["hp_state"]),
                         ("Indeedee-F", "177/177", "confirmed"))
        self.assertEqual([e["frame"] for e in entry["hp_support"]["evidence"]], [198])
        for number, raw_health in ((1603, "5/180"), (1815, "4/330")):
            reading = next(e for e in ledgers[1]["events"] if e["frame"] == number)
            self.assertEqual((reading["kind"], reading["status"], reading["before"], reading["after"]),
                             ("hp_rejected_reading", "suppressed", "100/100", "100/100"))
            self.assertEqual(reading["hp_support"]["raw_event"]["health"], raw_health)
            self.assertEqual(reading["hp_support"]["evidence"][0]["region"], "p2_clock")
            rebound = next(e for e in ledgers[1]["events"] if e["frame"] == number + 1)
            self.assertEqual((rebound["status"], rebound["before"], rebound["after"]),
                             ("suppressed", "100/100", "100/100"))
        hp = [e for ledger in ledgers for e in ledger["events"]
              if e["kind"] in {"damage", "heal"} and e["status"] != "suppressed"]
        self.assertEqual(len(hp), 34)
        self.assertTrue(all(e["hp_state"] == "confirmed" for e in hp))
        self.assertEqual(len(ledgers[1]["narration_links"]), 9)
        self.assertTrue(all(e["status"] == "linked" for e in ledgers[1]["narration_links"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_FIFTH"), "Requiere el quinto ZIP del usuario")
    def test_fifth_job_stabilizes_lead_identity_and_keeps_unrelated_issues(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_FIFTH"]))
        self.assertEqual(len(baselines), 2)
        ledgers = [BattleAutomaton(i, [r for r in frames if r["battle_index"] == i]).run() for i in range(2)]
        first = ledgers[0]
        leads = [e for e in first["events"] if e["kind"] == "switch" and e["frame"] == 164]
        self.assertEqual([(e["slot"], e["species"], e["health"]) for e in leads],
                         [("p1a", "Blaziken", "156/156"), ("p1b", "Kingambit", "177/177"),
                          ("p2a", "Toxtricity", "100/100"), ("p2b", "Chandelure", "100/100")])
        self.assertEqual([e["frame"] for e in leads[0]["identity_support"]["evidence"]], [165, 166])
        mega = next(e for e in first["events"] if e["kind"] == "mega")
        self.assertEqual((mega["frame"], mega["species"], mega["status"]), (239, "Blaziken-Mega", "consistent"))
        damage = [e for e in first["events"] if e["kind"] == "damage" and e["actor_id"] == leads[0]["actor_id"]]
        self.assertEqual([(e["before"], e["after"], e["status"]) for e in damage],
                         [("156/156", "35/156", "consistent"), ("35/156", "0/156", "consistent")])
        recoil = next(n for n in first["narration_links"] if n["effect"] == "recoil")
        self.assertEqual((recoil["status"], recoil["actor_id"], recoil["event_seq"]),
                         ("linked", leads[0]["actor_id"], damage[1]["seq"]))
        self.assertEqual(first["issues"], [])
        entry = next(e for e in first["events"] if e["kind"] == "switch" and e["species"] == "Basculegion")
        self.assertEqual((entry["frame"], entry["logical_frame"], entry["health"], entry["hp_state"]),
                         (672, 479, "219/219", "confirmed"))
        self.assertEqual([e["frame"] for e in entry["entry_reconstruction"]["evidence"]], [495, 496])
        aqua_jet = next(e for e in first["events"] if e["kind"] == "move" and e["frame"] == 609)
        self.assertEqual((aqua_jet["actor_id"], aqua_jet["status"]), (entry["actor_id"], "consistent"))
        gori = next(e for e in first["events"] if e["kind"] == "switch" and e["species"] == "Rillaboom")
        self.assertEqual((gori["frame"], gori["logical_frame"], gori["health"]), (899, 650, "207/207"))
        hp = [e for e in first["events"] if e["kind"] in {"damage", "heal"} and e["actor_id"] == gori["actor_id"]]
        self.assertEqual([(e["before"], e["after"], e["status"], e["hp_state"]) for e in hp],
                         [("207/207", "31/207", "consistent", "confirmed"),
                          ("31/207", "43/207", "consistent", "confirmed"),
                          ("43/207", "0/207", "consistent", "confirmed")])
        restored = next(n for n in first["narration_links"] if n["frame"] == 776)
        self.assertEqual((restored["status"], restored["actor_id"], restored["event_seq"]),
                         ("linked", gori["actor_id"], hp[1]["seq"]))
        moves = [e for e in first["events"] if e["kind"] == "move" and e["actor_id"] == gori["actor_id"] and e["status"] != "suppressed"]
        self.assertEqual([(e["move"], e["frame"], e["slot"], e["turn"]) for e in moves],
                         [("Fake Out", 703, "p1b", 4), ("Grassy Glide", 882, "p1b", 5)])
        self.assertEqual(moves[0]["action_reconstruction"]["detected_frame"], 882)
        self.assertEqual(compare_baseline(first, baselines[0])["aligned_hp_episodes"], 17)
        self.assertEqual(ledgers[1]["issues"], [])
        ignored = ledgers[1]["ignored_ui_frames"]
        self.assertEqual([row["frame"] for row in ignored], [1587, 1588])
        self.assertEqual(ignored[0]["detections"]["events"][0]["value"], "are immune to priority moves.")
        self.assertFalse(any(e["frame"] == 1587 for e in ledgers[1]["events"]))
        pending = next(e for e in ledgers[1]["events"] if e["frame"] == 1437)
        faint = next(e for e in ledgers[1]["events"] if e["frame"] == 1438)
        self.assertEqual((pending["status"], pending["text_support"]["state"]), ("suppressed", "rejected"))
        self.assertEqual((faint["kind"], faint["status"], faint["species"]), ("faint", "consistent", "Kingambit"))
        self.assertEqual(pending["resolution"]["event_seq"], faint["seq"])
        self.assertEqual([e["frame"] for e in pending["resolution"]["evidence"]], [1438, 1439, 1440])
        self.assertIn("Identidad corroborada tras estabilizarse el HUD", render_markdown(first, None))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_FOURTH"), "Requiere el cuarto ZIP del usuario")
    def test_fourth_job_confirms_pending_observations_without_duplicate_events(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_FOURTH"]))
        self.assertEqual(len(baselines), 2)
        for index, baseline in baselines.items():
            ledger = BattleAutomaton(index, [r for r in frames if r["battle_index"] == index]).run()
            self.assertEqual(ledger["issues"], [])
            comparison = compare_baseline(ledger, baseline)
            self.assertTrue(comparison["exact_core_sequence"])
            self.assertTrue(comparison["exact_hp_sequence"])
            self.assertTrue(all(link["status"] == "linked" for link in ledger["narration_links"]))
            transient = next(e for e in ledger["events"] if e["kind"] == "unclassified_text")
            self.assertEqual(transient["status"], "suppressed")
            accepted = ledger["events"][transient["resolution"]["event_seq"] - 1]
            self.assertEqual((transient["frame"], accepted["frame"]), (179, 181) if index == 0 else (1298, 1299))
            self.assertIn("Avisos resueltos", render_markdown(ledger, comparison))
            if index == 0:
                heal = next(e for e in ledger["events"] if e["frame"] == 261)
                self.assertEqual((heal["kind"], heal["before"], heal["after"]), ("heal", "38/100", "87/100"))
                self.assertEqual(heal["hp_support"]["confirmation"]["confirmed_frame"], 264)
                damage = next(e for e in ledger["events"] if e["frame"] == 493)
                self.assertEqual((damage["before"], damage["after"], damage["status"]), ("87/100", "0/100", "consistent"))
                zero = next(e for e in ledger["events"] if e["frame"] == 709)
                self.assertEqual((zero["kind"], zero["after"], zero["status"]), ("damage", "0/100", "consistent"))
                self.assertTrue(any(e.get("kind") == "faint_narration" for e in zero["hp_support"]["evidence"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_THIRD"), "Requiere el tercer ZIP del usuario")
    def test_third_job_keeps_distinct_salamence_and_indeedee_states(self):
        frames, baselines = read_diagnostic(Path(os.environ["CHAMPIONS_DIAGNOSTIC_THIRD"]))
        self.assertEqual(set(baselines), {0, 1, 2})
        ledgers = [BattleAutomaton(i, [row for row in frames if row["battle_index"] == i]).run()
                   for i in range(3)]
        links = [link for ledger in ledgers for link in ledger["narration_links"]]
        self.assertEqual(len(links), 17)
        self.assertTrue(all(link["status"] == "linked" for link in links))
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


    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_AD28"), "Requiere diagnóstico Floette")
    def test_floette_mega_y_reentradas_necesitan_evidencia(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_AD28"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        battle = BattleAutomaton(0, frames, context).run()
        self.assertFalse(battle["issues"])
        self.assertEqual(sum(x["code"] == "unclassified_text" for x in battle["resolved_issues"]), 4)
        mega = next(e for e in battle["events"] if e["kind"] == "mega" and e["slot"] == "p2b")
        self.assertEqual((mega["species"], mega["value"]), ("Floette-Mega", "Floettite"))
        reentries = [e for e in battle["events"] if e.get("reentry_health_support")]
        self.assertEqual([(e["seq"], e["health"]) for e in reentries],
                         [(35, "100/100"), (40, "100/100"), (49, "100/100"), (75, "74/100")])
        self.assertEqual((battle["events"][36]["before"], battle["events"][36]["after"]),
                         ("100/100", "68/100"))

        missing_reaction = copy.deepcopy(frames)
        for row in missing_reaction:
            if row["frame"] == 443:
                row["ocr"] = [line for line in row["ocr"] if
                              "reacting to Trainer's Omni Ring!" not in line.get("text", "")]
        uncertain = BattleAutomaton(0, missing_reaction, context).run()
        self.assertIn(442, [x["frame"] for x in uncertain["issues"]])

        missing_move = copy.deepcopy(frames)
        for row in missing_move:
            if row["frame"] in (599, 600):
                row["detections"]["events"] = [e for e in row["detections"]["events"]
                                                    if e["kind"] != "move"]
        uncertain = BattleAutomaton(0, missing_move, context).run()
        self.assertFalse(any(e.get("reentry_health_support") for e in uncertain["events"]
                             if e["frame"] == 604))
        self.assertIn("unparsed_action_text", {x["code"] for x in uncertain["issues"]})

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_CC5298"), "Requiere diagnóstico de mención japonesa")
    def test_motes_japoneses_y_ocr_repetido_no_duplican_actores(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_CC5298"])
        frames, baseline = read_diagnostic(path)
        self.assertFalse(baseline)  # Producción rechazó esta batalla.
        ledger = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual(ledger["alias_reconstructions"][0]["nickname"], "わらびくん")
        opening = [(e["kind"], e["slot"], e["species"]) for e in ledger["events"]
                   if e["kind"] == "switch" and e["status"] == "consistent"]
        self.assertEqual(opening, [("switch", "p1a", "Kingambit"),
                                   ("switch", "p1b", "Indeedee-F"),
                                   ("switch", "p2a", "Gengar"),
                                   ("switch", "p2b", "Incineroar")])
        self.assertIn(("p2b", "Intimidate", 207),
                      [(e["slot"], e["value"], e["frame"]) for e in ledger["events"]
                       if e["kind"] == "ability"])
        self.assertEqual([(e["slot"], e["status"]) for e in ledger["events"]
                          if e["kind"] == "faint"],
                         [("p2b", "consistent"), ("p2a", "suppressed"), ("p2a", "suppressed")])
        self.assertEqual([issue["code"] for issue in ledger["resolved_issues"]],
                         ["faint_text_unconfirmed", "faint_text_unconfirmed"])

    def test_sliding_hud_keeps_confirmed_hp_without_inventing_a_heal(self):
        trace = sliding_hp_trace()
        ledger = BattleAutomaton(0, trace).run()
        self.assertFalse(ledger["issues"])
        reading = next(e for e in ledger["events"] if e["frame"] == 4)
        self.assertEqual((reading["kind"], reading["status"], reading["before"], reading["after"]),
                         ("hp_rejected_reading", "suppressed", "100/100", "100/100"))
        self.assertEqual([e["frame"] for e in reading["hp_support"]["evidence"]], [2, 3, 4])
        self.assertEqual((ledger["events"][4]["kind"], ledger["events"][4]["status"]),
                         ("heal", "suppressed"))
        self.assertEqual((ledger["events"][5]["before"], ledger["events"][5]["after"]),
                         ("100/100", "68/100"))

        without_prior = copy.deepcopy(trace)
        without_prior[1]["ocr"] = [line for line in without_prior[1]["ocr"]
                                     if line["text"] != "100%"]
        self.assertIn("hp_unconfirmed", {issue["code"] for issue in
                                         BattleAutomaton(0, without_prior).run()["issues"]})
        without_name = copy.deepcopy(trace)
        without_name[3]["ocr"] = [line for line in without_name[3]["ocr"]
                                    if line["text"] != "adrill"]
        self.assertIn("hp_unconfirmed", {issue["code"] for issue in
                                         BattleAutomaton(0, without_name).run()["issues"]})
        real_zero = copy.deepcopy(trace)
        real_zero[3]["ocr"][1]["text"] = "0%"
        real_zero[3]["ocr"][1]["left"] = .689
        self.assertFalse(any(e["frame"] == 4 and e["status"] == "suppressed"
                             for e in BattleAutomaton(0, real_zero).run()["events"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_472247"), "Requiere diagnóstico 472247")
    def test_sliding_excadrill_real_trace_keeps_damage_after_hyper_voice(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_472247"])
        frames, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        shifted = next(e for e in ledger["events"] if e["frame"] == 454)
        self.assertEqual((shifted["kind"], shifted["status"], shifted["before"], shifted["after"]),
                         ("hp_rejected_reading", "suppressed", "100/100", "100/100"))
        self.assertEqual([line["frame"] for line in shifted["hp_support"]["evidence"]],
                         [452, 453, 454])
        repeat = next(e for e in ledger["events"] if e["frame"] == 497)
        damage = next(e for e in ledger["events"] if e["frame"] == 499 and e["slot"] == "p2a")
        self.assertEqual((repeat["kind"], repeat["status"]), ("heal", "suppressed"))
        self.assertEqual((damage["before"], damage["after"], damage["cause"]),
                         ("100/100", "68/100", 36))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_B3F7"), "Requiere diagnóstico b3f7")
    def test_pelipper_y_archaludon_conservan_actor_y_ps(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_B3F7"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        ledger = BattleAutomaton(0, frames, context).run()
        self.assertFalse(ledger["issues"])
        self.assertEqual([(item["code"], item["frame"]) for item in ledger["resolved_issues"]],
                         [("ghost_reentry_after_faint", 1070),
                          ("faint_text_unconfirmed", 477), ("reentry_without_exit", 488)])
        faint = next(e for e in ledger["events"] if e["frame"] == 477)
        entry = next(e for e in ledger["events"] if e["frame"] == 488)
        heal = next(e for e in ledger["events"] if e["frame"] == 489)
        self.assertEqual((faint["status"], faint["resolution"]["slot"]), ("suppressed", "p2b"))
        self.assertEqual((entry["status"], entry["resolution"]["event_seq"]),
                         ("suppressed", heal["seq"]))
        self.assertEqual((heal["before"], heal["after"]), ("6/100", "12/100"))
        self.assertIn((488, "9%"), [(proof["frame"], proof["text"])
                                       for proof in entry["resolution"]["evidence"]])

        # The full repeated faint is required to reject the damaged reading.
        one_announcement = copy.deepcopy(frames)
        for row in one_announcement:
            if row["frame"] == 476:
                row["ocr"] = [line for line in row["ocr"]
                              if line.get("text") != "The opposing Pelipper fainted!"]
        uncertain = BattleAutomaton(0, one_announcement, context).run()
        self.assertIn("faint_text_unconfirmed", {item["code"] for item in uncertain["issues"]})

        # The intermediate percentage must be literal, with no return announced.
        for mutate in ("missing_middle", "announced_entry"):
            altered = copy.deepcopy(frames)
            for row in altered:
                if mutate == "missing_middle" and row["frame"] == 488:
                    row["ocr"] = [line for line in row["ocr"] if line.get("text") != "9%"]
                if mutate == "announced_entry" and row["frame"] == 487:
                    row["ocr"].append({"text": "Dragonsbane sent out Archaludon!",
                                       "confidence": .999, "left": .15, "top": .75})
            uncertain = BattleAutomaton(0, altered, context).run()
            self.assertIn("reentry_without_exit", {item["code"] for item in uncertain["issues"]}, mutate)

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_DA3F"), "Requiere diagnóstico da3f")
    def test_da3f_separates_partner_hud_and_confirms_late_zero(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_DA3F"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        battle = [row for row in frames if row["battle_index"] == 0]
        ledger = BattleAutomaton(0, battle, context).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual([(x["code"], x["frame"]) for x in ledger["resolved_issues"]],
                         [("hp_unconfirmed", 296), ("hp_transition", 297)])
        indeedee = next(e for e in ledger["events"] if e["frame"] == 296 and e["slot"] == "p2a")
        self.assertEqual((indeedee["kind"], indeedee["status"], indeedee["before"],
                          indeedee["after"], indeedee["cause"]),
                         ("damage", "consistent", "100/100", "77/100", 12))
        self.assertEqual(next(e for e in ledger["events"] if e["frame"] == 297 and
                              e["slot"] == "p2a")["status"], "suppressed")
        last = next(e for e in ledger["events"] if e["frame"] == 826 and e["kind"] == "damage")
        self.assertEqual((last["before"], last["after"],
                          last["endpoint_reconstruction"]["faint_seq"]),
                         ("6/100", "0/100", 75))

        no_partner = copy.deepcopy(battle)
        for row in no_partner:
            if 294 <= row["frame"] <= 299:
                row["ocr"] = [l for l in row["ocr"] if
                              not (l.get("text") == "1%" and l.get("left", 0) > .89)]
        self.assertIn("hp_unconfirmed", {x["code"] for x in
                                         BattleAutomaton(0, no_partner, context).run()["issues"]})
        no_zero = copy.deepcopy(battle)
        for row in no_zero:
            if row["frame"] == 830:
                row["ocr"] = [l for l in row["ocr"] if l.get("text") != "0%"]
        uncertain = BattleAutomaton(0, no_zero, context).run()
        self.assertFalse(any(e.get("endpoint_reconstruction") for e in uncertain["events"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_A026"), "Requiere diagnóstico a026")
    def test_a026_rejects_ghost_hud_and_links_malformed_mega_announcement(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_A026"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        battle = [row for row in frames if row["battle_index"] == 0]
        ledger = BattleAutomaton(0, battle, context).run()
        self.assertEqual(ledger["issues"], [])
        ghost = next(e for e in ledger["events"] if e["frame"] == 344)
        self.assertEqual((ghost["kind"], ghost["status"],
                          ghost["resolution"]["event_seq"]), ("switch", "suppressed", 17))
        self.assertEqual([i["frame"] for i in ledger["resolved_issues"]], [258])
        mega = next(e for e in ledger["events"] if e["kind"] == "mega" and e["frame"] == 278)
        self.assertEqual(ledger["resolved_issues"][0]["resolution"]["event_seq"], mega["seq"])

        no_clean_stone = copy.deepcopy(battle)
        for row in no_clean_stone:
            if row["frame"] in (259, 260):
                row["ocr"] = [l for l in row["ocr"] if
                              "Gardevoirite is reacting" not in l.get("text", "")]
        self.assertIn("unclassified_text", {i["code"] for i in
                                         BattleAutomaton(0, no_clean_stone, context).run()["issues"]})
        no_faint_repeat = copy.deepcopy(battle)
        for row in no_faint_repeat:
            if row["frame"] in (344, 345, 346):
                row["ocr"] = [l for l in row["ocr"] if
                              l.get("text") != "The opposing Garchomp fainted!"]
        self.assertIn("unresolved_identity", {i["code"] for i in
                                          BattleAutomaton(0, no_faint_repeat, context).run()["issues"]})

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_3432"), "Requiere diagnóstico 3432")
    def test_3432_dates_opponent_switch_and_merges_partner_hud_damage(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_3432"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        battle = [row for row in frames if row["battle_index"] == 0]
        ledger = BattleAutomaton(0, battle, context).run()
        self.assertEqual(ledger["issues"], [])
        switch = next(e for e in ledger["events"] if e["kind"] == "switch" and
                      e["slot"] == "p2a" and e["species"] == "Camerupt")
        self.assertEqual((switch["logical_frame"], switch["frame"], switch["hp_state"]),
                         (337, 419, "inferred"))
        self.assertEqual([(i["code"], i["frame"]) for i in ledger["resolved_issues"]],
                         [("hp_unconfirmed", 800), ("hp_transition", 804),
                          ("unclassified_text", 329)])
        impact = next(e for e in ledger["events"] if e["kind"] == "damage" and e["frame"] == 796)
        cause = next(e for e in ledger["events"] if e["seq"] == impact["cause"])
        self.assertEqual((impact["before"], impact["after"], cause["kind"], cause["move"]),
                         ("28/100", "4/100", "move", "Hyper Voice"))
        self.assertEqual([(e["frame"], e["status"]) for e in ledger["events"]
                          if e["frame"] in (800, 804) and e["slot"] == "p2a"],
                         [(800, "suppressed"), (804, "suppressed")])

        missing_departure = copy.deepcopy(battle)
        for row in missing_departure:
            if row["frame"] in (330, 331):
                row["ocr"] = [line for line in row["ocr"] if
                              line.get("text") != "The Trainer withdrew Maushold!"]
        self.assertIn("unclassified_text", {i["code"] for i in
                                       BattleAutomaton(0, missing_departure, context).run()["issues"]})
        missing_final = copy.deepcopy(battle)
        for row in missing_final:
            if row["frame"] == 804:
                row["ocr"] = [line for line in row["ocr"] if line.get("text") != "4%"]
        self.assertTrue(any(i["code"] in {"hp_unconfirmed", "hp_transition"} for i in
                            BattleAutomaton(0, missing_final, context).run()["issues"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_433C"), "Requiere diagnóstico 433c")
    def test_433c_resolves_voicing_faint_and_recovers_terminal_card(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_433C"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        battle = [row for row in frames if row["battle_index"] == 0]
        ledger = BattleAutomaton(0, battle, context).run()
        self.assertEqual(ledger["issues"], [])
        faint = next(item for item in ledger["events"] if item["frame"] == 864)
        self.assertEqual((faint["kind"], faint["status"], faint["slot"]),
                         ("faint", "consistent", "p2a"))
        self.assertEqual({proof["frame"] for proof in faint["resolution"]["evidence"]},
                         {863, 864, 865, 866})
        self.assertEqual([(e["frame"], e["value"]) for e in ledger["events"]
                          if e["kind"] == "battle_end"], [(885, "You lost to トグロチチ!")])

        missing_hud = copy.deepcopy(battle)
        for row in missing_hud:
            if row["frame"] == 863:
                row["ocr"] = [line for line in row["ocr"] if line.get("text") != "ぼるつくす"]
        unresolved = BattleAutomaton(0, missing_hud, context).run()
        self.assertIn("faint_text_unconfirmed", {issue["code"] for issue in unresolved["issues"]})

        no_end = copy.deepcopy(battle)
        next(row for row in no_end if row["frame"] == 885)["detections"]["battle_complete"] = False
        self.assertFalse(any(e["kind"] == "battle_end" for e in
                             BattleAutomaton(0, no_end, context).run()["events"]))

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_7F41"), "Requiere diagnóstico 7f41")
    def test_7f41_recovers_named_lethal_hp_and_nickname_withdrawals(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_7F41"])
        frames, _ = read_diagnostic(path)
        context = read_diagnostic_context(path)
        ledger = BattleAutomaton(0, frames, context).run()
        self.assertEqual(ledger["issues"], [])
        self.assertEqual([e["frame"] for e in ledger["events"] if e["kind"] == "damage" and
                          e["slot"] == "p2b" and e["after"] == "0/100" and e["frame"] < 609], [602])
        damage = next(e for e in ledger["events"] if e["kind"] == "damage" and e["frame"] == 602)
        self.assertEqual((damage["before"], damage["after"], damage["cause"]),
                         ("100/100", "0/100", 42))
        self.assertEqual([i["frame"] for i in ledger["resolved_issues"] if
                          i["code"] == "unclassified_text"], [543, 928])

        no_zero_repeat = copy.deepcopy(frames)
        for row in no_zero_repeat:
            if row["frame"] == 603:
                row["ocr"] = [line for line in row["ocr"] if line.get("text") != "0%"]
        uncertain = BattleAutomaton(0, no_zero_repeat, context).run()
        self.assertFalse(any(e["kind"] == "damage" and e["frame"] == 602
                             for e in uncertain["events"]))

        no_withdrawal_repeat = copy.deepcopy(frames)
        for row in no_withdrawal_repeat:
            if row["frame"] in (544, 545):
                row["ocr"] = [line for line in row["ocr"] if "withdrew Kaiju!" not in line.get("text", "")]
        uncertain = BattleAutomaton(0, no_withdrawal_repeat, context).run()
        self.assertIn(543, [i["frame"] for i in uncertain["issues"] if i["code"] == "unclassified_text"])

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_BC6"), "Requiere diagnóstico bc6")
    def test_bc6_replays_timed_damage_faint_terrain_and_helmet(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_BC6"])
        frames, _ = read_diagnostic(path)
        ledger = BattleAutomaton(0, frames, read_diagnostic_context(path)).run()
        self.assertEqual(ledger["issues"], [])
        by_frame = {item["frame"]: item for item in ledger["events"]}
        self.assertEqual((by_frame[645]["turn"], by_frame[645]["before"], by_frame[645]["after"]),
                         (3, "76/100", "82/100"))
        self.assertEqual(by_frame[645]["terrain_restoration_support"]["reported_frame"], 823)
        self.assertEqual((by_frame[738]["slot"], by_frame[738]["status"],
                          by_frame[739]["status"]), ("p1b", "consistent", "suppressed"))
        self.assertEqual((by_frame[1096]["before"], by_frame[1096]["after"],
                          by_frame[1096]["detection_lag"]["reported_frame"]),
                         ("55/100", "0/100", 1119))
        self.assertEqual((by_frame[1496]["before"], by_frame[1496]["after"],
                          by_frame[1496]["hp_state"]), ("100/100", "83/100", "confirmed"))
        self.assertIn("rocky_helmet_support", by_frame[1496])
        self.assertEqual((by_frame[508]["kind"], by_frame[508]["status"]), ("ui_text", "suppressed"))

        no_helmet = copy.deepcopy(frames)
        for row in no_helmet:
            if 1494 <= row["frame"] <= 1497:
                row["ocr"] = [line for line in row["ocr"] if line["text"] != "Rocky Helmet"]
        uncertain = BattleAutomaton(0, no_helmet, read_diagnostic_context(path)).run()
        self.assertNotIn("rocky_helmet_support", next(e for e in uncertain["events"] if e["frame"] == 1496))
        self.assertTrue(any(i["code"] in {"hp_unconfirmed", "hp_transition"} for i in uncertain["issues"]))


if __name__ == "__main__":
    unittest.main()
