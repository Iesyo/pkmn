from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Match


def _rate(value: int, total: int) -> float:
    return 0 if total == 0 else round((value / total) * 100, 1)


@dataclass(frozen=True, slots=True)
class OpponentPokemonStat:
    species: str
    games: int
    wins: int
    win_rate: float
    attendance_rate: float


def opponent_pokemon_stats(matches: Iterable[Match]) -> tuple[OpponentPokemonStat, ...]:
    match_list = tuple(matches)
    grouped: dict[str, dict[str, int | str]] = {}

    for match in match_list:
        seen = {species.strip() for species in match.opponent_selected if species.strip()}
        for species in seen:
            key = species.casefold()
            current = grouped.setdefault(key, {"species": species, "games": 0, "wins": 0})
            current["games"] = int(current["games"]) + 1
            if match.result == "win":
                current["wins"] = int(current["wins"]) + 1

    return tuple(
        OpponentPokemonStat(
            species=str(entry["species"]),
            games=int(entry["games"]),
            wins=int(entry["wins"]),
            win_rate=_rate(int(entry["wins"]), int(entry["games"])),
            attendance_rate=_rate(int(entry["games"]), len(match_list)),
        )
        for entry in grouped.values()
    )
