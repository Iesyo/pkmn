"""Pruebas de regresión para las decisiones que ya fallaron en COL-102.

Portadas del prototipo que Roku compartió en la Mesa de colaboración el
26 sep (`COL-102-reconciliador.zip`, `col102_prototype/test_reconcile.py`)
sin cambios de comportamiento -sólo la ruta de import.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pkmn_vgc.champions_replay.reconcile import analyze, identity_candidates


def frame(number: int, battle: int, rival: str, *, texts=(), events=(), preview=False, team=()):
    return {
        "frame": number, "battle_index": battle,
        "ocr": [{"text": t, "top": 0.72 if "used " in t or "fainted" in t else 0.22, "confidence": .99} for t in texts],
        "detections": {
            "players": {"p2": rival}, "teams": {"p2": list(team)}, "events": list(events),
            "team_preview": preview, "battle_started": not preview,
        },
    }


def inspect(records, lines):
    with tempfile.TemporaryDirectory() as tmp:
        trace, replay = Path(tmp) / "trace.jsonl", Path(tmp) / "replay.log"
        trace.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        replay.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return analyze(trace, replay)


class EvidenceDecoderTests(unittest.TestCase):
    def test_lost_preview_label_does_not_create_a_lead(self):
        records = [
            frame(10, 0, "sky", preview=True),
            frame(11, 0, "sky", texts=("Seleet 4 Pokémon", "to send into battle."),
                  events=({"kind": "switch", "slot": "p1a", "species": "Kingambit", "health": "3/4"},)),
            frame(12, 0, "sky", preview=True),
        ]
        result = inspect(records, ["|player|p2|sky|2|", "|switch|p1a: Kingambit|Kingambit, L50|3/4",
                                   "|switch|p1a: Blaziken|Blaziken, L50|156/156", "|turn|1"])
        self.assertEqual(len(result["edits"]), 1)
        self.assertEqual(result["edits"][0]["frame"], 11)
        self.assertNotIn("Kingambit|Kingambit", result["patched_log"])

    def test_zero_between_matching_stable_hp_is_replaced_not_revived(self):
        team = ("Salamence",)
        records = [frame(10, 0, "Andrew", events=({"kind": "damage", "slot": "p2a", "species": "Salamence", "health": "65/100"},), team=team),
                   frame(11, 0, "Andrew", events=({"kind": "damage", "slot": "p2a", "species": "Salamence", "health": "0/100"},)),
                   frame(12, 0, "Andrew", events=({"kind": "heal", "slot": "p2a", "species": "Salamence", "health": "5/20"},)),
                   frame(13, 0, "Andrew", events=({"kind": "heal", "slot": "p2a", "species": "Salamence", "health": "65/100"},))]
        result = inspect(records, ["|player|p2|Andrew|2|", "|switch|p2a: Salamence|Salamence, L50|100/100",
                                   "|move|p1a: Kingambit|Sucker Punch|p2a: Salamence", "|-damage|p2a: Salamence|0/100",
                                   "|move|p2a: Salamence|Hyper Voice|", "|turn|2", "|-heal|p2a: Salamence|65/100"])
        self.assertEqual(len(result["edits"]), 2)
        self.assertIn("|-damage|p2a: Salamence|65/100", result["patched_log"])
        self.assertNotIn("|-heal|p2a: Salamence|65/100", result["patched_log"])

    def test_faint_confirmation_keeps_zero_and_drops_transient_heal(self):
        result = inspect([frame(1, 0, "Frate")], ["|player|p2|Frate|2|", "|-damage|p2b: Archaludon|0/100",
                                                    "|-heal|p2b: Archaludon|9/100", "|faint|p2b: Archaludon"])
        self.assertEqual(len(result["edits"]), 1)
        self.assertIn("|-damage|p2b: Archaludon|0/100", result["patched_log"])
        self.assertNotIn("|-heal|p2b: Archaludon", result["patched_log"])

    def test_zero_without_confirmation_is_flagged_not_synthetic_faint(self):
        result = inspect([frame(1, 0, "BullsEye")], ["|player|p2|BullsEye|2|", "|-damage|p2b: Milotic|0/100"])
        self.assertEqual(result["edits"], [])
        self.assertTrue(any(i["category"] == "cero_sin_faint" for i in result["findings"]))
        self.assertNotIn("|faint|", result["patched_log"])

    def test_explicit_faint_with_slot_and_zero_restores_the_missing_event(self):
        records = [frame(1, 0, "BullsEye", team=("Milotic",)),
                   frame(20, 0, "BullsEye", texts=("The opposing Milotic fainted!",),
                         events=({"kind": "faint", "slot": "p2b", "species": "Milotic"},)),
                   frame(21, 0, "BullsEye", texts=("Rillaboom used Wood Hammer!",))]
        result = inspect(records, ["|player|p2|BullsEye|2|", "|switch|p2b: Milotic|Milotic, L50|100/100",
                                   "|-damage|p2b: Milotic|0/100",
                                   "|-message|It's super effective on the opposing Milotic!", "|move|p1a: Rillaboom|Wood Hammer|"])
        self.assertEqual(len(result["edits"]), 1)
        self.assertEqual(result["findings"], [])
        self.assertIn("opposing Milotic!\n|faint|p2b: Milotic\n|move|", result["patched_log"])

    def test_faint_without_actor_slot_remains_pending(self):
        result = inspect([frame(1, 0, "Ender", texts=("The opposing MineMine fainted!",))],
                         ["|player|p2|Ender|2|", "|-message|The opposing MineMine fainted!"])
        self.assertEqual(result["edits"], [])
        self.assertEqual([issue["category"] for issue in result["findings"]], ["faltante"])

    def test_source_index_survives_a_discarded_battle_and_order_matters(self):
        records = [frame(1, 0, "First"), frame(2, 1, "Discarded"),
                   frame(3, 2, "Last", texts=("The opposing Ace used Protect!",)),
                   frame(7, 2, "Last", texts=("The opposing Ace used Ice Beam!",))]
        result = inspect(records, ["|player|p2|Last|2|", "|move|p2a: Metagross|Ice Beam|",
                                   "|move|p2a: Metagross|Protect|"])
        self.assertEqual(result["source_battle_index"], 2)
        self.assertEqual(len([i for i in result["findings"] if i["category"] in {"faltante", "sin_respaldo"}]), 2)

    def test_ocr_prefix_variants_vote_for_the_opponent(self):
        records = [frame(1, 0, "Andrew", texts=("The opposing Ace used Protect!",)),
                   frame(2, 0, "Andrew", texts=("The-opposing-Ace used Protect!",))]
        result = inspect(records, ["|player|p2|Andrew|2|", "|move|p2a: Salamence|Protect|"])
        self.assertEqual(result["on_screen_episodes"], 1)
        self.assertEqual(result["findings"], [])

    def test_orphan_actor_can_be_resolved_from_direct_faint_and_unique_roster(self):
        actor = "__champions_actor_p2_0002__"
        records = [frame(1, 2, "Warrior96", texts=("The opposing Zoroark fainted!",),
                         events=({"kind": "faint", "slot": "p2a", "species": actor},),
                         team=("Zoroark-Hisui", "Mimikyu"))]
        self.assertEqual(identity_candidates(records, 2)[0]["species"], "Zoroark-Hisui")
        records[0]["detections"]["teams"]["p2"].append("Zoroark")
        self.assertEqual(identity_candidates(records, 2), [])

    def test_a_persisted_source_battle_index_skips_the_rival_name_vote(self) -> None:
        # COL-102, bloqueante de Roku del 26 sep: mismo caso que
        # `test_source_index_survives_a_discarded_battle_and_order_matters`,
        # pero pasando el índice ya persistido -no hace falta el respaldo
        # por nombre, y funciona incluso si el rival no está en la traza.
        records = [frame(1, 0, "First"), frame(2, 1, "Discarded"),
                   frame(3, 2, "Last", texts=("The opposing Ace used Protect!",))]
        with tempfile.TemporaryDirectory() as tmp:
            trace, replay = Path(tmp) / "trace.jsonl", Path(tmp) / "replay.log"
            trace.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
            replay.write_text("|move|p2a: Metagross|Protect|\n", encoding="utf-8")

            result = analyze(trace, replay, source_battle_index=2)

        self.assertEqual(result["source_battle_index"], 2)
        self.assertEqual([issue["category"] for issue in result["findings"]], [])


if __name__ == "__main__":
    unittest.main()
