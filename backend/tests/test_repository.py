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
        )

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertEqual(match["team_version_id"], first.id)
        self.assertEqual([item["version_number"] for item in self.repository.list_teams()[0]["versions"]], [2, 1])

        with self.repository.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE team_versions SET paste = 'mutated' WHERE id = ?",
                    (first.id,),
                )

    def test_rejects_duplicate_paste_as_new_version(self) -> None:
        first = self.repository.create_team("Aurora Protocol", TEAM_PASTE)
        with self.assertRaisesRegex(ValueError, "ya existe como v1"):
            self.repository.create_version(first.team_id, TEAM_PASTE)

    def test_persists_and_normalizes_showdown_names(self) -> None:
        saved = self.repository.save_showdown_names([" Roku4523 ", "Iesyo", "Roku4523"])
        self.assertEqual(saved, ["Roku4523", "Iesyo"])
        self.assertEqual(self.repository.get_showdown_names(), saved)


if __name__ == "__main__":
    unittest.main()
