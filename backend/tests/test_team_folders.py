from pathlib import Path

import pytest

from pkmn_vgc.repository import Repository


def insert_team(repository: Repository, team_id: str = "team-1", name: str = "Mega Team") -> None:
    with repository.connect() as connection:
        with connection:
            connection.execute(
                "INSERT INTO teams (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (team_id, name, "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z"),
            )


def ordered_ids(repository: Repository, folder_id: str | None) -> list[str]:
    with repository.connect() as connection:
        if folder_id is None:
            rows = connection.execute(
                "SELECT id FROM teams WHERE folder_id IS NULL ORDER BY sort_order ASC, id ASC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id FROM teams WHERE folder_id = ? ORDER BY sort_order ASC, id ASC",
                (folder_id,),
            ).fetchall()
    return [str(row["id"]) for row in rows]


def test_team_can_move_between_folder_and_unfiled(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository)

    folder = repository.create_team_folder("Megas")
    organization = repository.move_team_to_folder("team-1", folder.id)
    assert organization["team-1"] == {"folderId": folder.id, "sortOrder": 0}

    organization = repository.move_team_to_folder("team-1", None)
    assert organization["team-1"] == {"folderId": None, "sortOrder": 0}


def test_deleting_folder_keeps_team_and_returns_it_to_unfiled(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository, "team-unfiled", "Unfiled")
    insert_team(repository, "team-folder", "Folder Team")

    folder = repository.create_team_folder("Torneo")
    repository.move_team_to_folder("team-folder", folder.id)
    repository.delete_team_folder(folder.id)

    teams = repository.list_teams()
    assert len(teams) == 2
    assert {team["id"] for team in teams} == {"team-unfiled", "team-folder"}
    assert all(team["folder_id"] is None for team in teams)
    assert ordered_ids(repository, None) == ["team-unfiled", "team-folder"]
    assert repository.list_team_folders() == []


def test_folder_names_are_case_insensitively_unique(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    repository.create_team_folder("Megas")

    with pytest.raises(ValueError, match="Ya existe una carpeta"):
        repository.create_team_folder("  megas  ")


def test_folder_order_can_be_reordered_and_persists(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    first = repository.create_team_folder("Primera")
    second = repository.create_team_folder("Segunda")
    third = repository.create_team_folder("Tercera")

    reordered = repository.reorder_team_folders([third.id, first.id, second.id])

    assert [folder.id for folder in reordered] == [third.id, first.id, second.id]
    assert [folder.sort_order for folder in reordered] == [0, 1, 2]
    assert [folder.id for folder in repository.list_team_folders()] == [third.id, first.id, second.id]

    with pytest.raises(ValueError, match="no está actualizada"):
        repository.reorder_team_folders([third.id, first.id])


def test_team_order_can_be_reordered_inside_folder_and_persists(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository, "team-1", "First")
    insert_team(repository, "team-2", "Second")
    insert_team(repository, "team-3", "Third")
    folder = repository.create_team_folder("Reg G")

    repository.move_team_to_folder("team-1", folder.id)
    repository.move_team_to_folder("team-2", folder.id)
    repository.move_team_to_folder("team-3", folder.id)
    assert ordered_ids(repository, folder.id) == ["team-1", "team-2", "team-3"]

    organization = repository.reorder_team_by_target("team-3", "team-1", "before")

    assert ordered_ids(repository, folder.id) == ["team-3", "team-1", "team-2"]
    assert organization["team-3"] == {"folderId": folder.id, "sortOrder": 0}
    assert organization["team-1"] == {"folderId": folder.id, "sortOrder": 1}
    assert organization["team-2"] == {"folderId": folder.id, "sortOrder": 2}


def test_team_drag_can_move_between_folders_at_a_precise_position(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "pkmn.sqlite")
    repository.initialize()
    insert_team(repository, "team-a", "A")
    insert_team(repository, "team-b", "B")
    insert_team(repository, "team-c", "C")
    source = repository.create_team_folder("Source")
    target = repository.create_team_folder("Target")

    repository.move_team_to_folder("team-a", source.id)
    repository.move_team_to_folder("team-b", target.id)
    repository.move_team_to_folder("team-c", target.id)

    repository.reorder_team_by_target("team-a", "team-c", "before")

    assert ordered_ids(repository, source.id) == []
    assert ordered_ids(repository, target.id) == ["team-b", "team-a", "team-c"]


def test_existing_database_gets_team_sort_order_without_losing_current_order(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite"
    with Repository(database_path).connect() as connection:
        connection.executescript(
            """
            CREATE TABLE team_folders (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE teams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                folder_id TEXT REFERENCES team_folders(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE team_versions (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                version_number INTEGER NOT NULL,
                minor_version INTEGER NOT NULL DEFAULT 0,
                format TEXT NOT NULL DEFAULT 'gen9',
                mechanics_json TEXT NOT NULL DEFAULT '["tera"]',
                paste TEXT NOT NULL,
                paste_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (team_id, version_number, minor_version),
                UNIQUE (team_id, paste_hash)
            );
            CREATE TABLE pokemon_sets (
                id TEXT PRIMARY KEY,
                team_version_id TEXT NOT NULL REFERENCES team_versions(id) ON DELETE CASCADE,
                slot INTEGER NOT NULL,
                nickname TEXT NOT NULL,
                species TEXT NOT NULL,
                item TEXT NOT NULL DEFAULT '',
                ability TEXT NOT NULL DEFAULT '',
                level INTEGER NOT NULL DEFAULT 50,
                tera_type TEXT,
                mechanics_json TEXT NOT NULL DEFAULT '{}',
                evs TEXT NOT NULL DEFAULT '',
                nature TEXT NOT NULL DEFAULT '',
                moves_json TEXT NOT NULL,
                types_json TEXT NOT NULL,
                UNIQUE (team_version_id, slot)
            );
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                team_version_id TEXT NOT NULL REFERENCES team_versions(id) ON DELETE RESTRICT,
                result TEXT NOT NULL,
                opponent_name TEXT NOT NULL DEFAULT 'Rival',
                opponent_paste TEXT NOT NULL DEFAULT '',
                replay_url TEXT NOT NULL DEFAULT '',
                selected_json TEXT NOT NULL DEFAULT '[]',
                opponent_selected_json TEXT NOT NULL DEFAULT '[]',
                lead_json TEXT NOT NULL DEFAULT '[]',
                moves_used_json TEXT,
                rating INTEGER,
                notes TEXT NOT NULL DEFAULT '',
                played_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO teams (id, name, folder_id, created_at, updated_at) VALUES
                ('older', 'Older', NULL, '2026-01-01', '2026-01-01'),
                ('newer', 'Newer', NULL, '2026-01-02', '2026-01-02');
            """
        )

    repository = Repository(database_path)
    repository.initialize()

    assert ordered_ids(repository, None) == ["newer", "older"]
