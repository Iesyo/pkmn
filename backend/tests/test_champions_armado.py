from __future__ import annotations

import unittest

from pkmn_vgc.champions_replay.armado import BattleView, HudFrame


def same(name: str):
    return lambda pokemon: pokemon == name


class BattleViewTests(unittest.TestCase):
    # COL-102, job 82923f56ce264a92: "sent out Pelipper!" en el frame 669 y
    # el HUD lo confirma en p2a en el 712, junto con el menú del turno 5.
    view = BattleView(
        (
            HudFrame(frame=660, slots={"p2a": "Incineroar", "p2b": "Sneasler"}, opens_turn=False),
            HudFrame(frame=700, slots={}, opens_turn=False),
            HudFrame(frame=712, slots={"p2a": "Pelipper", "p2b": "Incineroar"}, opens_turn=True),
            HudFrame(frame=800, slots={"p2a": "Basculegion", "p2b": "Pelipper"}, opens_turn=True),
        )
    )

    def test_finds_where_the_hud_confirms_an_entry(self) -> None:
        self.assertEqual(self.view.slot_confirmed("p2", same("Pelipper"), from_frame=669), "p2a")

    def test_does_not_look_past_the_next_turn(self) -> None:
        # En el frame 800 Pelipper está en p2b, pero eso ya es otro turno:
        # podría ser otra entrada, así que no cuenta.
        self.assertIsNone(self.view.slot_confirmed("p2", same("Basculegion"), from_frame=669))

    def test_only_counts_the_side_asked_and_a_single_slot(self) -> None:
        self.assertIsNone(self.view.slot_confirmed("p1", same("Pelipper"), from_frame=669))
        doubled = BattleView((HudFrame(frame=10, slots={"p2a": "Ditto", "p2b": "Ditto"}, opens_turn=True),))
        self.assertIsNone(doubled.slot_confirmed("p2", same("Ditto"), from_frame=0))


if __name__ == "__main__":
    unittest.main()
