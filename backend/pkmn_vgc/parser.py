from __future__ import annotations

import re

from .models import MoveSet, PokemonSet


class PasteValidationError(ValueError):
    """El texto no representa un equipo completo de Showdown."""


def _to_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


SPECIES_TYPES: dict[str, tuple[str, ...]] = {
    "kleavor": ("Bug", "Rock"),
    "miraidon": ("Electric", "Dragon"),
    "incineroar": ("Fire", "Dark"),
    "ogerponwellspring": ("Grass", "Water"),
    "farigiraf": ("Normal", "Psychic"),
    "urshifurapidstrike": ("Fighting", "Water"),
    "koraidon": ("Fighting", "Dragon"),
    "fluttermane": ("Ghost", "Fairy"),
    "rillaboom": ("Grass",),
    "ragingbolt": ("Electric", "Dragon"),
    "amoonguss": ("Grass", "Poison"),
    "chiyu": ("Dark", "Fire"),
}

MOVE_TYPES: dict[str, str] = {
    "stoneaxe": "Rock",
    "xscissor": "Bug",
    "closecombat": "Fighting",
    "uturn": "Bug",
    "electrodrift": "Electric",
    "dracometeor": "Dragon",
    "voltswitch": "Electric",
    "protect": "Normal",
    "fakeout": "Normal",
    "flareblitz": "Fire",
    "knockoff": "Dark",
    "partingshot": "Dark",
    "ivycudgel": "Water",
    "hornleech": "Grass",
    "followme": "Normal",
    "spikyshield": "Grass",
    "trickroom": "Psychic",
    "psychic": "Psychic",
    "helpinghand": "Normal",
    "surgingstrikes": "Water",
    "aquajet": "Water",
    "detect": "Fighting",
    "collisioncourse": "Fighting",
    "flamecharge": "Fire",
    "dragonclaw": "Dragon",
    "moonblast": "Fairy",
    "shadowball": "Ghost",
    "icywind": "Ice",
    "grassyglide": "Grass",
    "woodhammer": "Grass",
    "thunderclap": "Electric",
    "snarl": "Dark",
    "spore": "Grass",
    "ragepowder": "Bug",
    "pollenpuff": "Bug",
    "heatwave": "Fire",
    "darkpulse": "Dark",
    "overheat": "Fire",
}

STATUS_MOVES = {
    "protect",
    "detect",
    "partingshot",
    "followme",
    "spikyshield",
    "trickroom",
    "helpinghand",
    "spore",
    "ragepowder",
}


def _parse_header(header: str) -> tuple[str, str, str]:
    identity, separator, item = header.rpartition(" @ ")
    if not separator:
        identity, item = header, ""
    identity = re.sub(r"\s+\((?:M|F)\)$", "", identity).strip()
    nickname_match = re.match(r"^(.*?)\s+\(([^()]+)\)$", identity)
    if nickname_match:
        return nickname_match.group(1).strip(), nickname_match.group(2).strip(), item.strip()
    return identity, identity, item.strip()


def parse_showdown_paste(paste: str) -> tuple[PokemonSet, ...]:
    normalized = paste.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise PasteValidationError("Pega un equipo de Pokémon Showdown.")

    blocks = [block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    if len(blocks) != 6:
        raise PasteValidationError(
            f"El equipo debe contener exactamente 6 Pokémon; encontramos {len(blocks)}."
        )

    parsed: list[PokemonSet] = []
    for slot, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        nickname, species, item = _parse_header(lines[0])
        ability = ""
        level = 50
        tera_type: str | None = None
        mechanics: dict[str, object] = {"dynamaxLevel": 10, "gigantamax": False, "megaEvolution": False, "zMove": False}
        evs = ""
        nature = ""
        moves: list[MoveSet] = []

        for line in lines[1:]:
            if line.startswith("Ability:"):
                ability = line.removeprefix("Ability:").strip()
            elif line.startswith("Level:"):
                try:
                    level = int(line.removeprefix("Level:").strip())
                except ValueError:
                    level = 50
            elif line.startswith("Tera Type:"):
                tera_type = line.removeprefix("Tera Type:").strip()
            elif line.startswith("Dynamax Level:"):
                try:
                    mechanics["dynamaxLevel"] = int(line.removeprefix("Dynamax Level:").strip())
                except ValueError:
                    mechanics["dynamaxLevel"] = 10
            elif line == "Gigantamax: Yes":
                mechanics["gigantamax"] = True
            elif line.startswith("EVs:"):
                evs = line.removeprefix("EVs:").strip()
            elif line.endswith(" Nature"):
                nature = line.removesuffix(" Nature").strip()
            elif line.startswith("- "):
                move_name = line.removeprefix("- ").strip()
                move_id = _to_id(move_name)
                moves.append(
                    MoveSet(
                        name=move_name,
                        type=MOVE_TYPES.get(move_id),
                        damaging=move_id not in STATUS_MOVES,
                    )
                )

        if not species:
            raise PasteValidationError(f"No pudimos leer el Pokémon {slot}.")
        if not moves:
            raise PasteValidationError(f"{species} no contiene movimientos reconocibles.")

        parsed.append(
            PokemonSet(
                slot=slot,
                nickname=nickname,
                species=species,
                item=item,
                ability=ability,
                level=level,
                tera_type=tera_type,
                mechanics=mechanics,
                evs=evs,
                nature=nature,
                moves=tuple(moves),
                types=SPECIES_TYPES.get(_to_id(species), ("Normal",)),
            )
        )

    return tuple(parsed)
