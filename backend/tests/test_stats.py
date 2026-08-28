import unittest

from pkmn_vgc.models import Match
from pkmn_vgc.stats import opponent_pokemon_stats


def match(id_: str, result: str, opponent_selected: tuple[str, ...]) -> Match:
    return Match(
        id=id_,
        team_version_id="version-1",
        result=result,
        opponent_name="Rival",
        opponent_paste="",
        replay_url="",
        selected=(),
        opponent_selected=opponent_selected,
        lead=(),
        rating=None,
        notes="",
        played_at="2026-08-28T00:00:00Z",
    )


class StatsTests(unittest.TestCase):
    def test_calculates_matchup_and_attendance_by_opposing_pokemon(self) -> None:
        stats = opponent_pokemon_stats(
            (
                match("1", "win", ("Rillaboom", "Incineroar")),
                match("2", "loss", ("Rillaboom", "Rillaboom", "Calyrex-Ice")),
            )
        )
        by_species = {entry.species: entry for entry in stats}

        self.assertEqual(by_species["Rillaboom"].games, 2)
        self.assertEqual(by_species["Rillaboom"].wins, 1)
        self.assertEqual(by_species["Rillaboom"].win_rate, 50.0)
        self.assertEqual(by_species["Rillaboom"].attendance_rate, 100.0)
        self.assertEqual(by_species["Incineroar"].attendance_rate, 50.0)


if __name__ == "__main__":
    unittest.main()
