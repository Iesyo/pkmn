from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MatchResult = Literal["win", "loss"]


@dataclass(frozen=True, slots=True)
class MoveSet:
    name: str
    type: str | None
    damaging: bool
    usage: float = 0


@dataclass(frozen=True, slots=True)
class PokemonSet:
    slot: int
    nickname: str
    species: str
    item: str
    ability: str
    level: int
    tera_type: str | None
    evs: str
    nature: str
    moves: tuple[MoveSet, ...]
    types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TeamVersion:
    id: str
    team_id: str
    team_name: str
    version: int
    paste: str
    created_at: str
    pokemon: tuple[PokemonSet, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    team_version_id: str
    result: MatchResult
    opponent_name: str
    opponent_paste: str
    replay_url: str
    selected: tuple[str, ...]
    opponent_selected: tuple[str, ...]
    lead: tuple[str, ...]
    rating: int | None
    notes: str
    played_at: str
