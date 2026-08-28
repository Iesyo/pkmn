from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence
from uuid import uuid4

from .models import MatchResult, PokemonSet, TeamVersion
from .parser import parse_showdown_paste


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_paste(paste: str) -> str:
    return paste.replace("\r\n", "\n").replace("\r", "\n").strip()


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

    def create_team(self, name: str, paste: str) -> TeamVersion:
        clean_name = name.strip()
        if not 2 <= len(clean_name) <= 80:
            raise ValueError("El nombre debe tener entre 2 y 80 caracteres.")
        normalized = _normalize_paste(paste)
        pokemon = parse_showdown_paste(normalized)
        now = _now()
        team_id = str(uuid4())
        version_id = str(uuid4())
        paste_hash = hashlib.sha256(normalized.encode()).hexdigest()

        with self.connect() as connection:
            with connection:
                connection.execute(
                    "INSERT INTO teams (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (team_id, clean_name, now, now),
                )
                connection.execute(
                    "INSERT INTO team_versions (id, team_id, version_number, paste, paste_hash, created_at) VALUES (?, ?, 1, ?, ?, ?)",
                    (version_id, team_id, normalized, paste_hash, now),
                )
                self._insert_pokemon(connection, version_id, pokemon)

        return TeamVersion(version_id, team_id, clean_name, 1, normalized, now, pokemon)

    def create_version(self, team_id: str, paste: str) -> TeamVersion:
        normalized = _normalize_paste(paste)
        pokemon = parse_showdown_paste(normalized)
        paste_hash = hashlib.sha256(normalized.encode()).hexdigest()
        now = _now()
        version_id = str(uuid4())

        with self.connect() as connection:
            team = connection.execute(
                "SELECT id, name FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            if team is None:
                raise LookupError("No encontramos ese equipo.")
            duplicate = connection.execute(
                "SELECT version_number FROM team_versions WHERE team_id = ? AND paste_hash = ?",
                (team_id, paste_hash),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"Este Pokepaste ya existe como v{duplicate['version_number']}."
                )
            latest = connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM team_versions WHERE team_id = ?",
                (team_id,),
            ).fetchone()[0]
            version_number = int(latest) + 1
            with connection:
                connection.execute(
                    "INSERT INTO team_versions (id, team_id, version_number, paste, paste_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (version_id, team_id, version_number, normalized, paste_hash, now),
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
                        rating, notes, played_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                "SELECT id, name, created_at, updated_at FROM teams ORDER BY updated_at DESC"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for team in teams:
                versions = connection.execute(
                    "SELECT id, version_number, paste, created_at FROM team_versions WHERE team_id = ? ORDER BY version_number DESC",
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                )
                for set_ in pokemon
            ],
        )
