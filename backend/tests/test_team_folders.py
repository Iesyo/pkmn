from pathlib import Path

import pytest

from pkmn_vgc.repository import Repository


def insert_team(repository: Repository, team_id: str = "team-1") -> None:
    with repository.connect() as connection:
        with connection:
            connection.execute(
                "INSERT INTO teams (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (team_id, "Mega Team", "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z"),
            )


def test_team_can_move_between_folder_and_unfiled(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository)

    folder = repository.create_team_folder("Megas")
    repository.move_team_to_folder("team-1", folder.id)
    assert repository.list_teams()[0]["folder_id"] == folder.id

    repository.move_team_to_folder("team-1", None)
    assert repository.list_teams()[0]["folder_id"] is None


def test_deleting_folder_keeps_team_and_returns_it_to_unfiled(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository)

    folder = repository.create_team_folder("Torneo")
    repository.move_team_to_folder("team-1", folder.id)
    repository.delete_team_folder(folder.id)

    teams = repository.list_teams()
    assert len(teams) == 1
    assert teams[0]["id"] == "team-1"
    assert teams[0]["folder_id"] is None
    assert repository.list_team_folders() == []


def test_folder_names_are_case_insensitively_unique(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    repository.create_team_folder("Megas")

    with pytest.raises(ValueError, match="Ya existe una carpeta"):
        repository.create_team_folder("  megas  ")
