"""Live Speed Tier / move-order projection for Battle Lab Sparring.

The panel is intentionally advisory: Showdown remains the authority that resolves
the turn.  We project the same-priority speed order from the live poke-env
battle, enrich opponent raw Speed from the exact Open Team Sheet paste, and
surface priority/fractional-order threats instead of pretending uncertain order
is deterministic.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction
from typing import Any, Iterable


_SPEED_PLUS_NATURES = {"hasty", "jolly", "naive", "timid"}
_SPEED_MINUS_NATURES = {"brave", "quiet", "relaxed", "sassy"}

_SPEED_HALVING_ITEMS = {
    "ironball",
    "machobrace",
    "poweranklet",
    "powerband",
    "powerbelt",
    "powerbracer",
    "powerlens",
    "powerweight",
}
_RANDOM_ORDER_ITEMS = {"quickclaw", "custapberry"}
_LATE_ORDER_ITEMS = {"laggingtail", "fullincense"}
_RANDOM_ORDER_ABILITIES = {"quickdraw"}
_LATE_ORDER_ABILITIES = {"stall", "myceliummight"}
_ORDER_MOVES = {"afteryou", "quash"}

_SUN_WEATHER = {"SUNNYDAY", "DESOLATELAND"}
_RAIN_WEATHER = {"RAINDANCE", "PRIMORDIALSEA"}
_SNOW_WEATHER = {"HAIL", "SNOW", "SNOWSCAPE"}

_STAT_LABELS = {
    "hp": "HP",
    "atk": "Atk",
    "def": "Def",
    "spa": "SpA",
    "spd": "SpD",
    "spe": "Spe",
}


def _to_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _enum_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name is not None:
        return str(name).upper()
    text = str(value or "")
    match = re.match(r"^([A-Za-z0-9_]+)", text)
    return (match.group(1) if match else text).upper()


def _dict_names(values: Any) -> set[str]:
    if not values:
        return set()
    keys = values.keys() if hasattr(values, "keys") else values
    return {_enum_name(value) for value in keys}


def _split_team_blocks(raw_paste: str) -> list[list[str]]:
    normalized = str(raw_paste or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [
        [line.strip() for line in block.splitlines() if line.strip()]
        for block in re.split(r"\n\s*\n+", normalized)
        if block.strip()
    ]


def _parse_header_species(line: str) -> tuple[str, str]:
    left, _, item = str(line or "").partition(" @ ")
    left = re.sub(r"\s+\((?:M|F)\)\s*$", "", left.strip(), flags=re.IGNORECASE)
    parenthetical = re.findall(r"\(([^()]+)\)", left)
    species = parenthetical[-1].strip() if parenthetical else left
    return _to_id(species), item.strip()


def _parse_allocations(payload: str) -> dict[str, int]:
    result = {key: 0 for key in _STAT_LABELS}
    reverse = {label.lower(): key for key, label in _STAT_LABELS.items()}
    for chunk in str(payload or "").split("/"):
        match = re.match(r"^\s*(\d+)\s+(HP|Atk|Def|SpA|SpD|Spe)\s*$", chunk, re.IGNORECASE)
        if match:
            key = reverse.get(match.group(2).lower())
            if key:
                result[key] = int(match.group(1))
    return result


def _parse_paste(raw_paste: str) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for lines in _split_team_blocks(raw_paste):
        species_id, item = _parse_header_species(lines[0])
        if not species_id:
            continue
        entry: dict[str, Any] = {
            "species": species_id,
            "item": _to_id(item),
            "ability": "",
            "level": 50,
            "nature": "serious",
            "allocations": {key: 0 for key in _STAT_LABELS},
            "moves": [],
        }
        for line in lines[1:]:
            lower = line.lower()
            if lower.startswith("ability:"):
                entry["ability"] = _to_id(line.split(":", 1)[1])
            elif lower.startswith("level:"):
                try:
                    entry["level"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lower.startswith("evs:"):
                # Champions serializes Stat Points through Showdown's EV field.
                entry["allocations"] = _parse_allocations(line.split(":", 1)[1])
            elif lower.endswith(" nature"):
                entry["nature"] = _to_id(line[:-7])
            elif line.startswith("- "):
                entry["moves"].append(line[2:].strip())
        parsed[species_id] = entry
    return parsed


def _team_species_ids(team: Any) -> set[str]:
    if not team:
        return set()
    values = team.values() if hasattr(team, "values") else team
    result: set[str] = set()
    for mon in values:
        for attr in ("species", "base_species"):
            try:
                ident = _to_id(getattr(mon, attr, ""))
            except Exception:
                ident = ""
            if ident:
                result.add(ident)
    return result


def _orient_parsed_teams(
    current: Any,
    own_sets: dict[str, dict[str, Any]],
    opponent_sets: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool]:
    """Align session pastes to the perspective of the battle object.

    Nana also snapshots the model-side battle for recording.  The same room is
    then seen with teams reversed, so blindly applying the human-side paste would
    attach the wrong raw Speed.  Full-team species are enough to orient the two
    exact OTS pastes because VGC enforces Species Clause.
    """

    current_ids = _team_species_ids(getattr(current, "team", {}))
    if not current_ids:
        return own_sets, opponent_sets, True
    own_score = len(current_ids & set(own_sets))
    opponent_score = len(current_ids & set(opponent_sets))
    if opponent_score > own_score:
        return opponent_sets, own_sets, False
    return own_sets, opponent_sets, True


def _find_paste_entry(mon: Any, parsed: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[str] = []
    for attr in ("species", "base_species", "name"):
        try:
            value = getattr(mon, attr, "")
        except Exception:
            value = ""
        ident = _to_id(value)
        if ident and ident not in candidates:
            candidates.append(ident)
    for candidate in candidates:
        if candidate in parsed:
            return parsed[candidate]
    # Current Mega/forme may not equal the species written in the paste.
    for candidate in candidates:
        stripped = re.sub(r"(?:mega(?:x|y|z)?|primal)$", "", candidate)
        if stripped in parsed:
            return parsed[stripped]
    return None


def _champions_raw_speed(mon: Any, entry: dict[str, Any] | None) -> int | None:
    """Return the pre-battle-modifier Speed stat for Champions.

    Prefer poke-env's exact request stat.  Opponent request stats are hidden, so
    fall back to the exact OTS allocation and the *current* species base stat.
    Champions' Level Clause formula uses Stat Points: max(2*SP - 1, 0).
    """

    try:
        raw = getattr(mon, "stats", {}).get("spe")
    except Exception:
        raw = None
    transformed = bool(entry) and _to_id(getattr(mon, "species", "")) != _to_id(entry.get("species", ""))
    if isinstance(raw, (int, float)) and raw > 0 and not transformed:
        return int(raw)
    if not entry:
        return int(raw) if isinstance(raw, (int, float)) and raw > 0 else None
    try:
        base = int(getattr(mon, "base_stats", {}).get("spe"))
    except Exception:
        return None
    try:
        allocation = int(entry.get("allocations", {}).get("spe", 0) or 0)
        level = int(getattr(mon, "level", 0) or entry.get("level", 50) or 50)
    except (TypeError, ValueError):
        return None
    contribution = max(2 * allocation - 1, 0)
    neutral = math.floor((2 * base + 31 + contribution) * level / 100) + 5
    nature = _to_id(entry.get("nature", "serious"))
    if nature in _SPEED_PLUS_NATURES:
        return math.floor(neutral * 1.1)
    if nature in _SPEED_MINUS_NATURES:
        return math.floor(neutral * 0.9)
    return neutral


def _boosted_speed(raw: int, stage: int) -> int:
    stage = max(-6, min(6, int(stage)))
    if stage >= 0:
        return math.floor(raw * (2 + stage) / 2)
    return math.floor(raw * 2 / (2 - stage))


def _remaining_turns(mapping: Any, name: str, turn: int, duration: int) -> int | None:
    if not mapping:
        return None
    for key, started in mapping.items():
        if _enum_name(key) != name:
            continue
        try:
            return max(0, duration - (int(turn) - int(started)))
        except (TypeError, ValueError):
            return None
    return None


def _status_name(mon: Any) -> str:
    return _enum_name(getattr(mon, "status", None)) if getattr(mon, "status", None) is not None else ""


def _effect_names(mon: Any) -> set[str]:
    return _dict_names(getattr(mon, "effects", {}))


def _ability_active(ability: str, item: str, fields: set[str]) -> bool:
    if "NEUTRALIZING_GAS" not in fields:
        return True
    return ability == "neutralizinggas" or item == "abilityshield"


def _effective_speed(
    mon: Any,
    *,
    entry: dict[str, Any] | None,
    raw_speed: int | None,
    tailwind: bool,
    swamp: bool,
    weather: set[str],
    fields: set[str],
) -> tuple[int | None, list[str], list[str]]:
    if raw_speed is None:
        return None, [], ["Speed base desconocida"]

    try:
        stage = int(getattr(mon, "boosts", {}).get("spe", 0) or 0)
    except Exception:
        stage = 0
    speed = _boosted_speed(raw_speed, stage)
    modifiers: list[str] = []
    uncertainty: list[str] = []
    multiplier = Fraction(1, 1)

    if stage:
        modifiers.append(f"{stage:+d} Spe")

    item = _to_id(getattr(mon, "item", ""))
    if item in {"", "unknownitem"} and entry:
        item = _to_id(entry.get("item", ""))
    ability = _to_id(getattr(mon, "ability", ""))
    if not ability and entry:
        ability = _to_id(entry.get("ability", ""))
    effects = _effect_names(mon)
    status = _status_name(mon)
    ability_on = _ability_active(ability, item, fields)

    if tailwind:
        multiplier *= 2
        modifiers.append("Tailwind ×2")
    if swamp:
        multiplier *= Fraction(1, 4)
        modifiers.append("Swamp ×0.25")

    if item == "choicescarf":
        multiplier *= Fraction(3, 2)
        modifiers.append("Choice Scarf ×1.5")
    elif item in _SPEED_HALVING_ITEMS:
        multiplier *= Fraction(1, 2)
        modifiers.append(f"{item} ×0.5")

    if status == "PAR" and not (ability_on and ability == "quickfeet"):
        multiplier *= Fraction(1, 2)
        modifiers.append("Parálisis ×0.5")

    if ability_on:
        if ability == "quickfeet" and status and status != "FNT":
            multiplier *= Fraction(3, 2)
            modifiers.append("Quick Feet ×1.5")
        elif ability == "chlorophyll" and weather & _SUN_WEATHER:
            multiplier *= 2
            modifiers.append("Chlorophyll ×2")
        elif ability == "swiftswim" and weather & _RAIN_WEATHER:
            multiplier *= 2
            modifiers.append("Swift Swim ×2")
        elif ability == "sandrush" and "SANDSTORM" in weather:
            multiplier *= 2
            modifiers.append("Sand Rush ×2")
        elif ability == "slushrush" and weather & _SNOW_WEATHER:
            multiplier *= 2
            modifiers.append("Slush Rush ×2")
        elif ability == "surgesurfer" and "ELECTRIC_TERRAIN" in fields:
            multiplier *= 2
            modifiers.append("Surge Surfer ×2")
        elif ability == "unburden" and not item:
            multiplier *= 2
            modifiers.append("Unburden ×2")

        if ability == "slowstart" and "SLOW_START" in effects:
            multiplier *= Fraction(1, 2)
            modifiers.append("Slow Start ×0.5")

    if "PROTOSYNTHESISSPE" in effects or "QUARKDRIVESPE" in effects:
        multiplier *= Fraction(3, 2)
        modifiers.append("Booster de Speed ×1.5")

    if item in _RANDOM_ORDER_ITEMS:
        uncertainty.append(f"{item}: puede adelantar dentro del bracket")
    if item in _LATE_ORDER_ITEMS:
        uncertainty.append(f"{item}: actúa tarde dentro del bracket")
    if ability in _RANDOM_ORDER_ABILITIES and ability_on:
        uncertainty.append(f"{ability}: puede adelantar dentro del bracket")
    if ability in _LATE_ORDER_ABILITIES and ability_on:
        uncertainty.append(f"{ability}: puede alterar el orden dentro del bracket")

    # Keep integer output concise; Showdown remains authoritative for exact ties.
    effective = math.floor(speed * multiplier.numerator / multiplier.denominator)
    return max(1, effective), modifiers, uncertainty


def _move_label(move: Any) -> str:
    entry = getattr(move, "entry", {}) or {}
    label = entry.get("name") if isinstance(entry, dict) else None
    if label:
        return str(label)
    value = str(getattr(move, "id", "") or "")
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("-", " ").title()


def _move_type(move: Any) -> str:
    value = getattr(move, "type", None)
    return _enum_name(value) if value is not None else str((getattr(move, "entry", {}) or {}).get("type", "")).upper()


def _move_category(move: Any) -> str:
    value = getattr(move, "category", None)
    if value is not None:
        return _enum_name(value)
    return str((getattr(move, "entry", {}) or {}).get("category", "")).upper()


def _move_flags(move: Any) -> set[str]:
    try:
        return {_to_id(flag) for flag in getattr(move, "flags", set())}
    except Exception:
        entry = getattr(move, "entry", {}) or {}
        flags = entry.get("flags", {}) if isinstance(entry, dict) else {}
        return {_to_id(flag) for flag in (flags.keys() if hasattr(flags, "keys") else flags)}


def _fallback_moves(entry: dict[str, Any] | None, gen: int) -> list[Any]:
    if not entry:
        return []
    try:
        from poke_env.battle.move import Move
    except Exception:
        return []
    moves = []
    for raw_name in entry.get("moves", []):
        try:
            move_id = Move.retrieve_id(raw_name)
            moves.append(Move(move_id, gen=gen))
        except Exception:
            continue
    return moves


def _priority_moves(
    mon: Any,
    *,
    entry: dict[str, Any] | None,
    gen: int,
    fields: set[str],
) -> list[dict[str, Any]]:
    try:
        known_moves = list(getattr(mon, "moves", {}).values())
    except Exception:
        known_moves = []
    seen = {_to_id(getattr(move, "id", "")) for move in known_moves}
    for move in _fallback_moves(entry, gen):
        if _to_id(getattr(move, "id", "")) not in seen:
            known_moves.append(move)
            seen.add(_to_id(getattr(move, "id", "")))

    ability = _to_id(getattr(mon, "ability", ""))
    if ability in {"", "unknownability"} and entry:
        ability = _to_id(entry.get("ability", ""))
    item = _to_id(getattr(mon, "item", ""))
    if item in {"", "unknownitem"} and entry:
        item = _to_id(entry.get("item", ""))
    ability_on = _ability_active(ability, item, fields)
    hp_fraction = float(getattr(mon, "current_hp_fraction", 0.0) or 0.0)
    result: list[dict[str, Any]] = []

    for move in known_moves:
        try:
            priority = int(getattr(move, "priority"))
        except Exception:
            priority = int((getattr(move, "entry", {}) or {}).get("priority", 0) or 0)
        move_id = _to_id(getattr(move, "id", ""))
        category = _move_category(move)
        move_type = _move_type(move)
        modifiers: list[str] = []

        if ability_on and ability == "prankster" and category == "STATUS":
            priority += 1
            modifiers.append("Prankster +1")
        if ability_on and ability == "galewings" and move_type == "FLYING" and hp_fraction >= 0.999:
            priority += 1
            modifiers.append("Gale Wings +1")
        if ability_on and ability == "triage":
            try:
                heals = float(getattr(move, "heal", 0.0) or 0.0) > 0 or float(getattr(move, "drain", 0.0) or 0.0) > 0
            except Exception:
                heals = False
            if heals or "heal" in _move_flags(move):
                priority += 3
                modifiers.append("Triage +3")
        if move_id == "grassyglide" and "GRASSY_TERRAIN" in fields:
            priority += 1
            modifiers.append("Grassy Terrain +1")

        if priority or move_id in _ORDER_MOVES:
            if ability_on and ability == "myceliummight" and category == "STATUS":
                modifiers.append("Mycelium Might: tarde en el bracket")
            if move_id in _ORDER_MOVES:
                modifiers.append("reordena la cola")
            result.append(
                {
                    "id": move_id,
                    "label": _move_label(move),
                    "priority": priority,
                    "modifiers": modifiers,
                }
            )
    result.sort(key=lambda value: (-int(value["priority"]), str(value["label"])))
    return result


def build_speed_tier_snapshot(
    current: Any,
    *,
    own_paste: str = "",
    opponent_paste: str = "",
    native_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    own_sets = _parse_paste(own_paste)
    opponent_sets = _parse_paste(opponent_paste)
    own_sets, opponent_sets, human_orientation = _orient_parsed_teams(
        current, own_sets, opponent_sets
    )
    turn = int(getattr(current, "turn", 0) or 0)
    fields_mapping = getattr(current, "fields", {}) or {}
    fields = _dict_names(fields_mapping)
    weather = _dict_names(getattr(current, "weather", {}) or {})
    own_conditions = getattr(current, "side_conditions", {}) or {}
    opponent_conditions = getattr(current, "opponent_side_conditions", {}) or {}

    trick_room = "TRICK_ROOM" in fields
    trick_room_turns = _remaining_turns(fields_mapping, "TRICK_ROOM", turn, 5)
    own_tailwind = "TAILWIND" in _dict_names(own_conditions)
    opponent_tailwind = "TAILWIND" in _dict_names(opponent_conditions)
    own_tailwind_turns = _remaining_turns(own_conditions, "TAILWIND", turn, 4)
    opponent_tailwind_turns = _remaining_turns(opponent_conditions, "TAILWIND", turn, 4)
    own_swamp = "GRASS_PLEDGE" in _dict_names(own_conditions)
    opponent_swamp = "GRASS_PLEDGE" in _dict_names(opponent_conditions)

    rows: list[dict[str, Any]] = []
    try:
        own_active = list(getattr(current, "active_pokemon", []) or [])
    except Exception:
        own_active = []
    try:
        opponent_active = list(getattr(current, "opponent_active_pokemon", []) or [])
    except Exception:
        opponent_active = []

    request_active = native_request.get("active") if isinstance(native_request, dict) else None

    for side, active, roster, tailwind, swamp in (
        ("own", own_active, own_sets, own_tailwind, own_swamp),
        ("opponent", opponent_active, opponent_sets, opponent_tailwind, opponent_swamp),
    ):
        for slot, mon in enumerate(active):
            if mon is None or bool(getattr(mon, "fainted", False)):
                continue
            entry = _find_paste_entry(mon, roster)
            raw_speed = _champions_raw_speed(mon, entry)
            effective_speed, modifiers, uncertainty = _effective_speed(
                mon,
                entry=entry,
                raw_speed=raw_speed,
                tailwind=tailwind,
                swamp=swamp,
                weather=weather,
                fields=fields,
            )
            if (
                human_orientation
                and side == "own"
                and isinstance(request_active, list)
                and slot < len(request_active)
                and isinstance(request_active[slot], dict)
                and request_active[slot].get("canMegaEvo")
            ):
                uncertainty.append("Mega disponible: la Speed puede cambiar antes de atacar")
            row = {
                "side": side,
                "slot": slot,
                "species": str(getattr(mon, "species", "") or getattr(mon, "name", "")),
                "name": str(getattr(mon, "name", "") or getattr(mon, "species", "")),
                "rawSpeed": raw_speed,
                "effectiveSpeed": effective_speed,
                "speedStage": int(getattr(mon, "boosts", {}).get("spe", 0) or 0),
                "modifiers": modifiers,
                "uncertainty": uncertainty,
                "priorityMoves": _priority_moves(
                    mon,
                    entry=entry,
                    gen=int(getattr(current, "gen", 9) or 9),
                    fields=fields,
                ),
            }
            rows.append(row)

    known = [row for row in rows if row["effectiveSpeed"] is not None]
    unknown = [row for row in rows if row["effectiveSpeed"] is None]
    known.sort(
        key=lambda row: (
            int(row["effectiveSpeed"]) if trick_room else -int(row["effectiveSpeed"]),
            0 if row["side"] == "own" else 1,
            int(row["slot"]),
        )
    )
    ordered = known + unknown

    ties: list[list[str]] = []
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in known:
        groups.setdefault(int(row["effectiveSpeed"]), []).append(row)
    for speed, group in groups.items():
        if len(group) > 1:
            names = [str(row["species"]) for row in group]
            ties.append(names)
            for row in group:
                row["uncertainty"].append(f"Empate de Speed efectiva ({speed}): orden aleatorio")

    priority: list[dict[str, Any]] = []
    for row in rows:
        for move in row["priorityMoves"]:
            priority.append(
                {
                    "side": row["side"],
                    "slot": row["slot"],
                    "species": row["species"],
                    **move,
                }
            )
    priority.sort(
        key=lambda value: (
            -int(value["priority"]),
            0 if value["side"] == "own" else 1,
            str(value["species"]),
            str(value["label"]),
        )
    )

    notes = [
        "La lista de Speed aplica dentro del mismo bracket de prioridad.",
        "Showdown sigue siendo la autoridad final del orden del turno.",
    ]
    if trick_room:
        notes.append("Trick Room invierte Speed, no los brackets de prioridad.")
    if any(row["uncertainty"] for row in rows):
        notes.append("Hay efectos que pueden reordenar el bracket; se marcan como incertidumbre.")

    return {
        "turn": turn,
        "trickRoom": trick_room,
        "trickRoomTurns": trick_room_turns,
        "ownTailwind": own_tailwind,
        "ownTailwindTurns": own_tailwind_turns,
        "opponentTailwind": opponent_tailwind,
        "opponentTailwindTurns": opponent_tailwind_turns,
        "order": ordered,
        "priority": priority,
        "ties": ties,
        "notes": notes,
    }


def install_speed_tier_snapshot() -> type:
    """Patch the active Sparring service without touching Showdown vendor code."""

    from battle_lab import local_sparring_service as sparring

    current = sparring.BattleLabLocalService
    if getattr(current, "_battle_lab_speed_tier", False):
        return current

    original_init = current.__init__
    original_snapshot = sparring._battle_snapshot
    holder: dict[str, Any] = {"service": None}

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        holder["service"] = self

    def battle_snapshot(current_battle: Any) -> dict[str, Any]:
        data = original_snapshot(current_battle)
        service = holder.get("service")
        session = getattr(service, "active_session", None) if service is not None else None
        try:
            data["speedTier"] = build_speed_tier_snapshot(
                current_battle,
                own_paste=str(getattr(session, "own_team", "") or ""),
                opponent_paste=str(getattr(session, "opponent_team", "") or ""),
                native_request=getattr(session, "native_request", None),
            )
        except Exception as error:
            data["speedTier"] = {
                "order": [],
                "priority": [],
                "notes": [f"Speed Tier no disponible: {error}"],
            }
        return data

    current.__init__ = __init__
    current._battle_lab_speed_tier = True
    sparring._battle_snapshot = battle_snapshot
    return current
