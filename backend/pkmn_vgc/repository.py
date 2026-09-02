from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from .models import MatchResult, PokemonSet, TeamFolder, TeamVersion
from .parser import parse_showdown_paste


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_paste(paste: str) -> str:
    return paste.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_folder_name(name: str) -> str:
    normalized = " ".join(name.strip().split())
    if not 1 <= len(normalized) <= 40:
        raise ValueError("El nombre de la carpeta debe tener entre 1 y 40 caracteres.")
    return normalized


class Repository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema)
            match_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(matches)").fetchall()
            }
            team_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(teams)").fetchall()
            }
            with connection:
                if "moves_used_json" not in match_columns:
                    connection.execute("ALTER TABLE matches ADD COLUMN moves_used_json TEXT")
                if "folder_id" not in team_columns:
                    connection.execute(
                        "ALTER TABLE teams ADD COLUMN folder_id TEXT REFERENCES team_folders(id) ON DELETE SET NULL"
                    )

    def get_showdown_names(self) -> list[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = 'showdown_names'"
            ).fetchone()
        if row is None:
            return []
        value = json.loads(str(row["value"]))
        return [str(name) for name in value] if isinstance(value, list) else []

    def save_showdown_names(self, names: Sequence[str]) -> list[str]:
        normalized = list(dict.fromkeys(name.strip() for name in names if name.strip()))
        if not normalized:
            raise ValueError("Agrega al menos un nombre de Showdown.")
        if len(normalized) > 10 or any(len(name) > 30 for name in normalized):
            raise ValueError("Puedes guardar hasta 10 nombres de 30 caracteres cada uno.")
        with self.connect() as connection:
            with connection:
                connection.execute(
                    """INSERT INTO app_settings (key, value, updated_at)
                    VALUES ('showdown_names', ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (json.dumps(normalized), _now()),
                )
        return normalized

    def list_team_folders(self) -> list[TeamFolder]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, sort_order, created_at FROM team_folders ORDER BY sort_order ASC, name COLLATE NOCASE ASC"
            ).fetchall()
        return [
            TeamFolder(
                id=str(row["id"]),
                name=str(row["name"]),
                sort_order=int(row["sort_order"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def reorder_team_folders(self, folder_ids: Sequence[str]) -> list[TeamFolder]:
        if isinstance(folder_ids, (str, bytes)):
            raise ValueError("El orden de carpetas no es válido.")
        ordered_ids = [str(folder_id).strip() for folder_id in folder_ids]
        if any(not folder_id for folder_id in ordered_ids) or len(set(ordered_ids)) != len(ordered_ids):
            raise ValueError("El orden de carpetas no es válido.")
        with self.connect() as connection:
            rows = connection.execute("SELECT id FROM team_folders").fetchall()
            current_ids = {str(row["id"]) for row in rows}
            if len(ordered_ids) != len(current_ids) or any(folder_id not in current_ids for folder_id in ordered_ids):
                raise ValueError("La lista de carpetas ya no está actualizada.")
            with connection:
                connection.executemany(
                    "UPDATE team_folders SET sort_order = ? WHERE id = ?",
                    [(sort_order, folder_id) for sort_order, folder_id in enumerate(ordered_ids)],
                )
        return self.list_team_folders()

    def create_team_folder(self, name: str) -> TeamFolder:
        clean_name = _normalize_folder_name(name)
        folder_id = str(uuid4())
        created_at = _now()
        with self.connect() as connection:
            duplicate = connection.execute(
                "SELECT id FROM team_folders WHERE lower(name) = lower(?) LIMIT 1",
                (clean_name,),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Ya existe una carpeta con ese nombre.")
            row = connection.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM team_folders"
            ).fetchone()
            sort_order = int(row["next_order"]) if row is not None else 0
            with connection:
                connection.execute(
                    "INSERT INTO team_folders (id, name, sort_order, created_at) VALUES (?, ?, ?, ?)",
                    (folder_id, clean_name, sort_order, created_at),
                )
        return TeamFolder(folder_id, clean_name, sort_order, created_at)

    def rename_team_folder(self, folder_id: str, name: str) -> TeamFolder:
        clean_name = _normalize_folder_name(name)
        with self.connect() as connection:
            current = connection.execute(
                "SELECT id, sort_order, created_at FROM team_folders WHERE id = ?",
                (folder_id,),
            ).fetchone()
            if current is None:
                raise LookupError("No encontramos esa carpeta.")
            duplicate = connection.execute(
                "SELECT id FROM team_folders WHERE lower(name) = lower(?) AND id <> ? LIMIT 1",
                (clean_name, folder_id),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Ya existe una carpeta con ese nombre.")
            with connection:
                connection.execute(
                    "UPDATE team_folders SET name = ? WHERE id = ?",
                    (clean_name, folder_id),
                )
        return TeamFolder(
            folder_id,
            clean_name,
            int(current["sort_order"]),
            str(current["created_at"]),
        )

    def delete_team_folder(self, folder_id: str) -> None:
        with self.connect() as connection:
            current = connection.execute(
                "SELECT id FROM team_folders WHERE id = ?", (folder_id,)
            ).fetchone()
            if current is None:
                raise LookupError("No encontramos esa carpeta.")
            with connection:
                connection.execute(
                    "UPDATE teams SET folder_id = NULL WHERE folder_id = ?", (folder_id,)
                )
                connection.execute("DELETE FROM team_folders WHERE id = ?", (folder_id,))

    def move_team_to_folder(self, team_id: str, folder_id: str | None) -> None:
        with self.connect() as connection:
            team = connection.execute(
                "SELECT id FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if team is None:
                raise LookupError("No encontramos ese equipo.")
            if folder_id is not None:
                folder = connection.execute(
                    "SELECT id FROM team_folders WHERE id = ?", (folder_id,)
                ).fetchone()
                if folder is None:
                    raise LookupError("No encontramos esa carpeta.")
            with connection:
                connection.execute(
                    "UPDATE teams SET folder_id = ? WHERE id = ?", (folder_id, team_id)
                )

    def create_team(self, name: str, paste: str, *, format: str = "champions", mechanics: Sequence[str] = ("mega",)) -> TeamVersion:
        clean_name = name.strip()
        if not 2 <= len(clean_name) <= 80:
            raise ValueError("El nombre debe tener entre 2 y 80 caracteres.")
        normalized = _normalize_paste(paste)
        pokemon = parse_showdown_paste(normalized)
        now = _now()
        team_id = str(uuid4())
        version_id = str(uuid4())
        normalized_mechanics = tuple(sorted(mechanics))
        signature = f"{normalized}\n#{format}\n#{json.dumps(normalized_mechanics, separators=(',', ':'))}"
        paste_hash = hashlib.sha256(signature.encode()).hexdigest()

        with self.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO teams (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (team_id, clean_name, now, now),
                )
                connection.execute(
                    "INSERT INTO team_versions (id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash, created_at) VALUES (?, ?, 1, 0, ?, ?, ?, ?, ?)",
                    (version_id, team_id, format, json.dumps(list(normalized_mechanics)), normalized, paste_hash, now),
                )
                self._insert_pokemon(connection, version_id, pokemon)

        return TeamVersion(version_id, team_id, clean_name, 1, 0, format, normalized_mechanics, normalized, now, pokemon)

    def create_version(self, team_id: str, paste: str, *, format: str | None = None, mechanics: Sequence[str] | None = None) -> TeamVersion:
        normalized = _normalize_paste(paste)
        pokemon = parse_showdown_paste(normalized)
        now = _now()
        version_id = str(uuid4())
        with self.connect() as connection:
            team = connection.execute(
                "SELECT id, name FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if team is None:
                raise LookupError("No encontramos ese equipo.")
            latest = connection.execute(
                "SELECT id, version_number, minor_version, format, mechanics_json FROM team_versions WHERE team_id = ? ORDER BY version_number DESC, minor_version DESC LIMIT 1",
                (team_id,),
            ).fetchone()
            if latest is None:
                raise LookupError("No encontramos una versión base.")
            current_species = sorted(row[0].lower() for row in connection.execute("SELECT species FROM pokemon_sets WHERE team_version_id = ?", (latest["id"],)).fetchall())
            next_species = sorted(set_.species.lower() for set_ in pokemon)
            next_format = format or str(latest["format"])
            current_mechanics = tuple(json.loads(str(latest["mechanics_json"])))
            next_mechanics = tuple(sorted(mechanics)) if mechanics is not None else tuple(sorted(current_mechanics))
            signature = f"{normalized}\n#{next_format}\n#{json.dumps(sorted(next_mechanics), separators=(',', ':'))}"
            paste_hash = hashlib.sha256(signature.encode()).hexdigest()
            duplicate = connection.execute(
                "SELECT version_number, minor_version FROM team_versions WHERE team_id = ? AND (paste_hash = ? OR (paste = ? AND format = ? AND mechanics_json = ?))",
                (team_id, paste_hash, normalized, next_format, json.dumps(list(next_mechanics))),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"Esta configuración ya existe como v{duplicate['version_number']}{'.' + str(duplicate['minor_version']).zfill(2) if duplicate['minor_version'] else ''}."
                )
            major_change = current_species != next_species or next_format != latest["format"] or sorted(next_mechanics) != sorted(current_mechanics)
            version_number = int(latest["version_number"]) + 1 if major_change else int(latest["version_number"])
            minor_version = 0 if major_change else int(latest["minor_version"]) + 1
            with connection:
                connection.execute(
                    "INSERT INTO team_versions (id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (version_id, team_id, version_number, minor_version, next_format, json.dumps(list(next_mechanics)), normalized, paste_hash, now),
                )
                self._insert_pokemon(connection, version_id, pokemon)
                connection.execute(
                    "UPDATE teams SET updated_at = ? WHERE id = ?", (now, team_id)
                )

        return TeamVersion(
            version_id,
            team_id,
            str(team["name"]),
            version_number,
            minor_version,
            next_format,
            next_mechanics,
            normalized,
            now,
            pokemon,
        )

    def add_match(
        self,
        team_version_id: str,
        result: MatchResult,
        *,
        opponent_name: str = "Rival",
        opponent_paste: str = "",
        replay_url: str = "",
        selected: Sequence[str] = (),
        opponent_selected: Sequence[str] = (),
        lead: Sequence[str] = (),
        moves_used: Mapping[str, Sequence[str]] | None = None,
        rating: int | None = None,
        notes: str = "",
        played_at: str | None = None,
    ) -> dict[str, Any]:
        if result not in ("win", "loss"):
            raise ValueError("El resultado debe ser victoria o derrota.")
        if replay_url and not replay_url.startswith("https://replay.pokemonshowdown.com/"):
            raise ValueError("El replay debe pertenecer a replay.pokemonshowdown.com.")
        if len(selected) not in (0, 4):
            raise ValueError("La selección debe contener exactamente 4 Pokémon.")
        if len(lead) not in (0, 2):
            raise ValueError("Los leads deben contener exactamente 2 Pokémon.")
        if len(opponent_selected) > 6:
            raise ValueError("El equipo rival puede contener como máximo 6 Pokémon.")

        match_id = str(uuid4())
        timestamp = played_at or _now()
        normalized_moves_used = (
            {
                species.strip()[:80]: list(
                    dict.fromkeys(move.strip() for move in moves if move.strip())
                )[:24]
                for species, moves in list(moves_used.items())[:6]
                if species.strip()
            }
            if moves_used is not None
            else None
        )
        with self.connect() as connection:
            version = connection.execute(
                "SELECT id FROM team_versions WHERE id = ?", (team_version_id,)
            ).fetchone()
            if version is None:
                raise LookupError("No encontramos esa versión del equipo.")
            with connection:
                connection.execute(
                    """INSERT INTO matches (
                        id, team_version_id, result, opponent_name, opponent_paste,
                        replay_url, selected_json, opponent_selected_json, lead_json,
                        moves_used_json, rating, notes, played_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        match_id,
                        team_version_id,
                        result,
                        opponent_name.strip() or "Rival",
                        opponent_paste.strip(),
                        replay_url.strip(),
                        json.dumps(list(selected)),
                        json.dumps(list(opponent_selected)),
                        json.dumps(list(lead)),
                        json.dumps(normalized_moves_used) if normalized_moves_used is not None else None,
                        rating,
                        notes.strip(),
                        timestamp,
                        _now(),
                    ),
                )
        return {
            "id": match_id,
            "team_version_id": team_version_id,
            "result": result,
            "played_at": timestamp,
        }

    def list_teams(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            teams = connection.execute(
                "SELECT id, name, folder_id, created_at, updated_at FROM teams ORDER BY updated_at DESC"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for team in teams:
                versions = connection.execute(
                    "SELECT id, version_number, minor_version, format, mechanics_json, paste, created_at FROM team_versions WHERE team_id = ? ORDER BY version_number DESC, minor_version DESC",
                    (team["id"],),
                ).fetchall()
                result.append(
                    {
                        **dict(team),
                        "versions": [dict(version) for version in versions],
                    }
                )
            return result

    @staticmethod
    def _insert_pokemon(
        connection: sqlite3.Connection,
        version_id: str,
        pokemon: tuple[PokemonSet, ...],
    ) -> None:
        connection.executemany(
            """INSERT INTO pokemon_sets (
                id, team_version_id, slot, nickname, species, item, ability,
                level, tera_type, evs, nature, moves_json, types_json
                , mechanics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    str(uuid4()),
                    version_id,
                    set_.slot,
                    set_.nickname,
                    set_.species,
                    set_.item,
                    set_.ability,
                    set_.level,
                    set_.tera_type,
                    set_.evs,
                    set_.nature,
                    json.dumps([asdict(move) for move in set_.moves]),
                    json.dumps(list(set_.types)),
                    json.dumps(set_.mechanics),
                )
                for set_ in pokemon
            ],
        )
