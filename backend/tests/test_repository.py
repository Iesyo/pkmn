import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkmn_vgc.repository import Repository

from .fixtures import TEAM_PASTE


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "pkmn.db"
        self.repository = Repository(self.database_path)
        self.repository.initialize()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_creates_immutable_versions_and_keeps_match_reference(self) -> None:
        first = self.repository.create_team("Aurora Protocol", TEAM_PASTE)
        second = self.repository.create_version(
            first.team_id,
            TEAM_PASTE.replace("Choice Scarf", "Focus Sash", 1),
        )
        match = self.repository.add_match(
            first.id,
            "win",
            opponent_name="Rain Balance",
            replay_url="https://replay.pokemonshowdown.com/",
            selected=("Kleavor", "Miraidon", "Incineroar", "Farigiraf"),
            lead=("Kleavor", "Miraidon"),
            moves_used={
                "Kleavor": ("Stone Axe", "Stone Axe", "X-Scissor"),
                "Miraidon": (),
            },
        )

        self.assertEqual(first.version, 1)
        self.assertEqual(first.format, "champions")
        self.assertEqual(first.mechanics, ("mega",))
        self.assertEqual(second.version, 1)
        self.assertEqual(second.minor_version, 1)
        self.assertEqual(match["team_version_id"], first.id)
        self.assertEqual([(item["version_number"], item["minor_version"]) for item in self.repository.list_teams()[0]["versions"]], [(1, 1), (1, 0)])

        with self.repository.connect() as connection:
            stored_match = connection.execute(
                "SELECT moves_used_json FROM matches WHERE id = ?", (match["id"],)
            ).fetchone()
            self.assertEqual(
                json.loads(str(stored_match["moves_used_json"])),
                {"Kleavor": ["Stone Axe", "X-Scissor"], "Miraidon": []},
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE team_versions SET paste = 'mutated' WHERE id = ?",
                    (first.id,),
                )

    def test_rejects_duplicate_paste_as_new_version(self) -> None:
        first = self.repository.create_team("Aurora Protocol", TEAM_PASTE)
        with self.assertRaisesRegex(ValueError, "ya existe como v1"):
            self.repository.create_version(first.team_id, TEAM_PASTE)

    def test_species_or_format_change_creates_major_version(self) -> None:
        first = self.repository.create_team("Aurora Protocol", TEAM_PASTE)
        species_change = self.repository.create_version(
            first.team_id,
            TEAM_PASTE.replace("Kleavor @ Choice Scarf", "Groudon @ Choice Scarf", 1),
        )
        self.assertEqual((species_change.version, species_change.minor_version), (2, 0))

        format_change = self.repository.create_version(
            first.team_id,
            TEAM_PASTE.replace("Choice Scarf", "Focus Sash", 1).replace("Kleavor", "Groudon", 1),
            format="gen8",
            mechanics=("dynamax",),
        )
        self.assertEqual((format_change.version, format_change.minor_version), (3, 0))

    def test_persists_and_normalizes_showdown_names(self) -> None:
        saved = self.repository.save_showdown_names([" Roku4523 ", "Iesyo", "Roku4523"])
        self.assertEqual(saved, ["Roku4523", "Iesyo"])
        self.assertEqual(self.repository.get_showdown_names(), saved)


if __name__ == "__main__":
    unittest.main()
