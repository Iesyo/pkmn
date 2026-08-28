import unittest

from pkmn_vgc.parser import PasteValidationError, parse_showdown_paste

from .fixtures import TEAM_PASTE


class PasteParserTests(unittest.TestCase):
    def test_parses_six_competitive_sets(self) -> None:
        team = parse_showdown_paste(TEAM_PASTE)

        self.assertEqual(len(team), 6)
        self.assertEqual(team[0].species, "Kleavor")
        self.assertEqual(team[0].item, "Choice Scarf")
        self.assertEqual(team[0].ability, "Sharpness")
        self.assertEqual(team[0].tera_type, "Water")
        self.assertEqual(team[0].moves[0].type, "Rock")
        self.assertEqual(team[3].types, ("Grass", "Water"))

    def test_rejects_incomplete_team(self) -> None:
        with self.assertRaisesRegex(PasteValidationError, "exactamente 6"):
            parse_showdown_paste(TEAM_PASTE.split("\n\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
