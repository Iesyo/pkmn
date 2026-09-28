"""Contratos antiguos comprobados a través de los dos autómatas de producción."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
from pkmn_vgc.champions_replay.models import BattleEvent, VALID_EVENT_KINDS
from pkmn_vgc.champions_replay.pipeline import CaptureIncompleteError
from pkmn_vgc.champions_replay.prototype.ledger_replay import _SUPPORTED, _critical_targets
from pkmn_vgc.champions_replay.showdown import _event_lines
from test_champions_ledger_pipeline import CONTEXT, trace_battle, write_trace


def event(kind, **values):
    return {"kind": kind, **values}


class LedgerEquivalenceTests(unittest.TestCase):
    def rows(self, steps):
        rows = trace_battle()[:3]
        for number, events in enumerate(steps, 4):
            row = copy.deepcopy(rows[-1])
            row.update(frame=number, timestamp_ms=number * 500, ocr=[])
            row["detections"] = {"events": [], "battle_started": True}
            for raw in events:
                item = {"timestamp_ms": number * 500, "source_frame": number - 1,
                        "confidence": 1, **raw}
                row["detections"]["events"].append(item)
                if raw.get("health"):
                    slot = raw["slot"]
                    x, y = {"p1a": (.14, .92), "p1b": (.34, .92),
                            "p2a": (.70, .12), "p2b": (.92, .12)}[slot]
                    value = raw["health"] if slot.startswith("p1") else raw["health"].split("/")[0] + "%"
                    row["ocr"].append({"text": value, "confidence": 1, "left": x,
                                       "right": x + .06, "top": y, "bottom": y + .04})
                if raw.get("value") and raw["kind"] == "message":
                    row["ocr"].append({"text": raw["value"], "confidence": 1, "top": .7})
            rows.append(row)
        terminal = copy.deepcopy(rows[-1])
        terminal.update(frame=rows[-1]["frame"] + 1, timestamp_ms=rows[-1]["timestamp_ms"] + 500)
        terminal["detections"] = {"events": [event("message", value="The battle has ended due to a forfeit.",
                                                  timestamp_ms=terminal["timestamp_ms"],
                                                  source_frame=terminal["frame"] - 1, confidence=1)],
                                  "battle_complete": True, "winner": "p1"}
        terminal["ocr"] = [{"text": "You defeated Benji!", "confidence": 1, "top": .7}]
        return [*rows, terminal]

    def replay(self, steps, identities=None, aliases=None):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            rows = self.rows(steps)
            for row in rows:
                if identities:
                    row["resolved_identities"] = identities
                if aliases:
                    row["resolved_aliases"] = aliases
            write_trace(out / "ocr.trace.jsonl", rows)
            documents = documents_from_trace(out / "ocr.trace.jsonl", {
                "id": "equivalence", "created_at": "2026-09-28T18:00:00+00:00", "context": CONTEXT,
            }, out)
            return documents[0].log.splitlines(), json.loads((out / "ledger-battle-001.json").read_text())

    def opening(self, move="Protect"):
        return [event("turn", turn=1), event("move", slot="p1a", species="Blaziken", move=move)]

    def test_forced_switch_preserves_identity_hp_and_legacy_protocol(self):
        lines, _ = self.replay([
            self.opening("Dragon Tail"),
            [event("damage", slot="p2a", species="Garchomp", health="80/100")],
            [event("drag", slot="p2a", species="Charizard", health="100/100")],
            [event("drag", slot="p2a", species="Garchomp")],
        ])
        self.assertIn("|drag|p2a: Garchomp|Garchomp, L50|80/100", lines)
        legacy = _event_lines(BattleEvent(kind="drag", timestamp_ms=0, slot="p2a",
                                          species="Charizard", health="100/100"), {}, {}, {})
        self.assertIn(legacy[0], lines)

    def test_explicit_critical_is_exported_like_legacy(self):
        lines, _ = self.replay([self.opening("Rock Tomb"),
                               [event("damage", slot="p2a", species="Garchomp", health="80/100")],
                               [event("crit", slot="p2a", species="Garchomp")]])
        legacy = _event_lines(BattleEvent(kind="crit", timestamp_ms=0, slot="p2a", species="Garchomp"),
                              {"p2a": "Garchomp"}, {}, {})
        self.assertIn(legacy[0], lines)

    def test_critical_and_item_must_belong_to_the_named_actor(self):
        for kind in ("crit", "item"):
            with self.subTest(kind=kind), self.assertRaises(CaptureIncompleteError):
                self.replay([self.opening(), [event(kind, slot="p1a", species="Garchomp", value="Leftovers")]])

    def test_critical_does_not_drop_a_delayed_second_target_to_make_a_unique_hit(self):
        events = [{"seq": 1, "kind": "move", "frame": 1},
                  {"seq": 2, "kind": "damage", "frame": 2, "cause": 1, "slot": "p2a", "actor_id": "one"},
                  {"seq": 3, "kind": "message", "frame": 3, "value": "A critical hit!"},
                  {"seq": 4, "kind": "damage", "frame": 30, "cause": 1, "slot": "p2b", "actor_id": "two"}]
        self.assertEqual(_critical_targets(events), {})

    def test_fainted_actor_cannot_return_through_a_forced_switch(self):
        with self.assertRaises(CaptureIncompleteError):
            self.replay([self.opening(),
                         [event("damage", slot="p2a", species="Garchomp", health="0/100")],
                         [event("faint", slot="p2a", species="Garchomp")],
                         [event("drag", slot="p2a", species="Charizard", health="100/100")],
                         [event("drag", slot="p2a", species="Garchomp", health="100/100")]])

    def test_narrated_critical_is_attributed_only_with_one_target(self):
        for spread in (False, True):
            with self.subTest(spread=spread):
                damage = [event("damage", slot="p2a", species="Garchomp", health="80/100")]
                if spread:
                    damage.append(event("damage", slot="p2b", species="Sneasler", health="70/100"))
                lines, _ = self.replay([self.opening("Rock Slide"), damage,
                                       [event("message", value="A critical hit!")]])
                expected = "|-message|A critical hit!" if spread else "|-crit|p2a: Garchomp"
                self.assertIn(expected, lines)
                if spread:
                    self.assertFalse(any(line.startswith("|-crit|") for line in lines))

    def test_critical_does_not_cross_turn_or_switch_boundaries(self):
        for boundary in ([event("turn", turn=2), event("move", slot="p1a", species="Blaziken", move="Protect")],
                         [event("switch", slot="p2a", species="Charizard", health="100/100")]):
            lines, _ = self.replay([self.opening("Rock Tomb"),
                                   [event("damage", slot="p2a", species="Garchomp", health="80/100")],
                                   boundary, [event("message", value="A critical hit!")]])
            self.assertIn("|-message|A critical hit!", lines)
            self.assertFalse(any(line.startswith("|-crit|") for line in lines))

    def test_item_revelation_consumption_and_reacquisition(self):
        lines, _ = self.replay([self.opening(),
            [event("item", slot="p1b", species="Indeedee-F", value="Sitrus Berry")],
            [event("item", slot="p1b", species="Indeedee-F", value="Sitrus Berry")],
            [event("enditem", slot="p1b", value="Sitrus Berry", tags=["[eat]"])],
            [event("item", slot="p1b", species="Indeedee-F", value="Sitrus Berry")],
            [event("enditem", slot="p1b", value="Sitrus Berry", tags=["[eat]"])],
        ])
        self.assertEqual(lines.count("|-item|p1b: Indeedee-F|Sitrus Berry"), 2)
        self.assertEqual(lines.count("|-enditem|p1b: Indeedee-F|Sitrus Berry|[eat]"), 2)

    def test_field_origin_and_heal_origin_survive_both_automata(self):
        source = ["[of] p1b: Indeedee-F"]
        lines, ledger = self.replay([
            self.opening(), [event("fieldstart", value="move: Trick Room", tags=source)],
            [event("damage", slot="p1b", species="Indeedee-F", health="80/177")],
            [event("enditem", slot="p1b", value="Sitrus Berry", tags=["[eat]"])],
            [event("heal", slot="p1b", species="Indeedee-F", health="90/177", tags=["[from] item: Sitrus Berry"])],
            [event("heal", slot="p1b", species="Indeedee-F", health="124/177")],
            [event("fieldend", value="move: Trick Room")],
        ])
        self.assertIn("|-fieldstart|move: Trick Room|[of] p1b: Indeedee-F", lines)
        self.assertIn("|-heal|p1b: Indeedee-F|124/177|[from] item: Sitrus Berry", lines)
        self.assertEqual(next(e["tags"] for e in ledger["events"] if e["kind"] == "fieldstart"), source)

    def test_source_tag_cannot_name_another_occupant_or_inject_protocol(self):
        for tag in ("[of] p1b: Garchomp", "[from] item: Leftovers|win|Benji"):
            with self.subTest(tag=tag), self.assertRaises(CaptureIncompleteError):
                self.replay([self.opening(), [event("fieldstart", value="move: Trick Room", tags=[tag])]])

    def test_field_source_resolves_provisional_identity_and_japanese_nickname(self):
        for named, identities, aliases in (
            ("__champions_actor_p1_0012__", {"__champions_actor_p1_0012__": "Indeedee-F"}, None),
            ("イエッサン", None, {"p1": {"イエッサン": "Indeedee-F"}, "p2": {}}),
        ):
            with self.subTest(named=named):
                raw_tag = f"[of] p1b: {named}"
                lines, ledger = self.replay([self.opening(),
                    [event("fieldstart", value="move: Trick Room", tags=[raw_tag])]], identities, aliases)
                self.assertIn("|-fieldstart|move: Trick Room|[of] p1b: Indeedee-F", lines)
                source = next(e for e in ledger["events"] if e["kind"] == "fieldstart")
                self.assertEqual(source["raw_tags"], [raw_tag])

    def test_status_survives_damage_heal_exit_and_return_until_cured(self):
        for status in ("brn", "par", "slp", "frz", "psn", "tox"):
            with self.subTest(status=status):
                lines, _ = self.replay([self.opening(),
                    [event("status", slot="p1b", value=status)],
                    [event("status", slot="p1b", value=status)],
                    [event("damage", slot="p1b", species="Indeedee-F", health="150/177")],
                    [event("heal", slot="p1b", species="Indeedee-F", health="160/177")],
                    [event("switch", slot="p1b", species="Gardevoir", health="100/100")],
                    [event("drag", slot="p1b", species="Indeedee-F")],
                    [event("cant", slot="p1b", value="par")] if status == "par" else [],
                    [event("curestatus", slot="p1b", value="status")],
                    [event("damage", slot="p1b", species="Indeedee-F", health="140/177")],
                ])
                self.assertEqual(lines.count(f"|-status|p1b: Indeedee-F|{status}"), 1)
                self.assertIn(f"|-damage|p1b: Indeedee-F|150/177 {status}", lines)
                self.assertIn(f"|-heal|p1b: Indeedee-F|160/177 {status}", lines)
                self.assertIn(f"|drag|p1b: Indeedee-F|Indeedee-F, L50|160/177 {status}", lines)
                self.assertIn("|switch|p1b: Gardevoir|Gardevoir, L50|100/100", lines)
                self.assertIn(f"|-curestatus|p1b: Indeedee-F|{status}", lines)
                self.assertIn("|-damage|p1b: Indeedee-F|140/177", lines)
                if status == "par":
                    self.assertIn("|cant|p1b: Indeedee-F|par", lines)

    def test_conflicting_status_is_still_blocked(self):
        with self.assertRaises(CaptureIncompleteError):
            self.replay([self.opening(), [event("status", slot="p1b", value="brn")],
                         [event("status", slot="p1b", value="par")]])

    def test_spread_move_misses_share_the_source_without_guessing_one_target(self):
        for hit_first in (False, True):
            with self.subTest(hit_first=hit_first):
                first = (event("damage", slot="p2a", species="Garchomp", health="80/100") if hit_first else
                         event("miss", target_slot="p2a"))
                lines, _ = self.replay([self.opening("Rock Slide"), [first], [event("miss", target_slot="p2b")]])
                self.assertIn("|move|p1a: Blaziken|Rock Slide|", lines)
                self.assertIn("|-miss|p1a: Blaziken|p2b: Sneasler", lines)
                if not hit_first:
                    self.assertIn("|-miss|p1a: Blaziken|p2a: Garchomp", lines)

    def test_miss_without_current_action_is_still_blocked(self):
        with self.assertRaises(CaptureIncompleteError):
            self.replay([self.opening("Rock Slide"),
                         [event("switch", slot="p1a", species="Gardevoir", health="100/100")],
                         [event("miss", target_slot="p2b")]])

    def test_legacy_event_inventory_has_only_declared_tera_pending(self):
        self.assertEqual(VALID_EVENT_KINDS - _SUPPORTED, {"terastallize"})


if __name__ == "__main__":
    unittest.main()
