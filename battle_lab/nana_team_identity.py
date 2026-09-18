"""Model-agnostic Team identity for Nana L2 memory.

Roster identity deliberately matches Battle Lab M-C's train/holdout grouping.
Exact identity canonicalizes the competitive set so cosmetic edits, Pokémon
order and move order do not fragment Nana's memory.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from battle_lab.mc_team_split import (
    normalize_species,
    species_from_header,
    team_signature_text,
)
from battle_lab.nana_contracts import canonical_json, fingerprint_payload
from battle_lab.team_corpus import normalize_team_text, team_sha256


TEAM_SIGNATURE_SPEC_VERSION = 1
_EXACT_PREFIX = f"team:v{TEAM_SIGNATURE_SPEC_VERSION}:"
_ROSTER_PREFIX = f"roster:v{TEAM_SIGNATURE_SPEC_VERSION}:"
_STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
_STAT_ALIASES = {
    "hp": "hp",
    "atk": "atk",
    "def": "def",
    "spa": "spa",
    "spd": "spd",
    "spe": "spe",
}


def _to_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _allocations(value: str) -> dict[str, int]:
    rendered = {key: 0 for key in _STAT_KEYS}
    for chunk in str(value or "").split("/"):
        match = re.match(
            r"^\s*(\d+)\s+(HP|Atk|Def|SpA|SpD|Spe)\s*$",
            chunk,
            re.IGNORECASE,
        )
        if not match:
            continue
        stat = _STAT_ALIASES.get(match.group(2).lower())
        if stat:
            rendered[stat] = int(match.group(1))
    return rendered


def _header_fields(header: str) -> dict[str, str]:
    left, separator, item = str(header or "").partition(" @ ")
    gender_match = re.search(r"\s+\((M|F)\)\s*$", left, flags=re.IGNORECASE)
    gender = gender_match.group(1).lower() if gender_match else ""
    species = normalize_species(species_from_header(left))
    return {
        "species": species,
        "item": _to_id(item if separator else ""),
        "gender": gender,
    }


def _canonical_mon(block: str) -> dict[str, Any]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines:
        raise ValueError("bloque Pokémon vacío")
    mon: dict[str, Any] = {
        **_header_fields(lines[0]),
        "ability": "",
        "level": None,
        "teraType": "",
        "nature": "",
        "evs": {key: 0 for key in _STAT_KEYS},
        "ivs": {key: 31 for key in _STAT_KEYS},
        "moves": [],
        "extras": [],
    }
    for line in lines[1:]:
        lower = line.lower()
        if lower.startswith("ability:"):
            mon["ability"] = _to_id(line.split(":", 1)[1])
        elif lower.startswith("level:"):
            raw = line.split(":", 1)[1].strip()
            mon["level"] = int(raw) if raw.isdigit() else raw
        elif lower.startswith("tera type:"):
            mon["teraType"] = _to_id(line.split(":", 1)[1])
        elif lower.startswith("evs:"):
            mon["evs"] = _allocations(line.split(":", 1)[1])
        elif lower.startswith("ivs:"):
            ivs = {key: 31 for key in _STAT_KEYS}
            ivs.update(_allocations(line.split(":", 1)[1]))
            mon["ivs"] = ivs
        elif lower.endswith(" nature"):
            mon["nature"] = _to_id(line[:-7])
        elif line.startswith("- "):
            move = _to_id(line[2:])
            if move:
                mon["moves"].append(move)
        else:
            # Preserve semantically relevant extensions (Happiness, Shiny,
            # Gigantamax, Dynamax Level, etc.) without making line order matter.
            mon["extras"].append(_to_id(line))
    mon["moves"] = sorted(set(mon["moves"]))
    mon["extras"] = sorted(value for value in set(mon["extras"]) if value)
    return mon


def canonical_team_payload(team_text: str) -> dict[str, Any]:
    normalized = normalize_team_text(team_text)
    blocks = [
        block
        for block in re.split(r"\n\s*\n", normalized.strip())
        if block.strip()
    ]
    if len(blocks) != 6:
        raise ValueError(f"se esperaban 6 Pokémon y se detectaron {len(blocks)}")
    pokemon = [_canonical_mon(block) for block in blocks]
    if any(not mon.get("species") for mon in pokemon):
        raise ValueError("no se pudo resolver la especie de todos los Pokémon")
    pokemon.sort(key=canonical_json)
    return {
        "specVersion": TEAM_SIGNATURE_SPEC_VERSION,
        "pokemon": pokemon,
    }


def team_identity(team_text: str) -> dict[str, Any]:
    normalized = normalize_team_text(team_text)
    roster = list(team_signature_text(normalized, source="Nana Team"))
    roster_payload = {
        "specVersion": TEAM_SIGNATURE_SPEC_VERSION,
        "species": roster,
    }
    exact_payload = canonical_team_payload(normalized)
    return {
        "teamSignatureSpecVersion": TEAM_SIGNATURE_SPEC_VERSION,
        "roster": roster,
        "rosterSignature": _ROSTER_PREFIX + fingerprint_payload(roster_payload),
        "exactTeamSignature": _EXACT_PREFIX + fingerprint_payload(exact_payload),
        "pasteSha256": team_sha256(normalized),
        "canonicalTeam": exact_payload,
        "normalizedPaste": normalized,
    }


def persist_team_identity(profile_root: Path, identity: dict[str, Any]) -> Path:
    signature = str(identity.get("exactTeamSignature") or "")
    if not signature.startswith(_EXACT_PREFIX):
        raise ValueError("exactTeamSignature inválida")
    digest = signature.rsplit(":", 1)[-1]
    destination = Path(profile_root) / "teams" / f"{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": TEAM_SIGNATURE_SPEC_VERSION,
        "teamSignatureSpecVersion": identity["teamSignatureSpecVersion"],
        "roster": identity["roster"],
        "rosterSignature": identity["rosterSignature"],
        "exactTeamSignature": identity["exactTeamSignature"],
        "pasteSha256": identity["pasteSha256"],
        "canonicalTeam": identity["canonicalTeam"],
        "paste": identity["normalizedPaste"],
    }
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def session_team_context(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "teamSignatureSpecVersion": identity["teamSignatureSpecVersion"],
        "rosterSignature": identity["rosterSignature"],
        "exactTeamSignature": identity["exactTeamSignature"],
        "pasteSha256": identity["pasteSha256"],
    }


def team_scope_keys(
    *,
    roster_signature: str,
    exact_team_signature: str,
    opponent_archetype: str = "",
) -> list[str]:
    """Backoff order for future L2 statistics, least to most specific."""

    rendered = ["global"]
    archetype = _to_id(opponent_archetype)
    if archetype:
        rendered.append(f"archetype:{archetype}")
    if roster_signature:
        rendered.append(f"roster:{roster_signature}")
    if exact_team_signature:
        rendered.append(f"team:{exact_team_signature}")
    if exact_team_signature and archetype:
        rendered.append(f"team+archetype:{exact_team_signature}|{archetype}")
    return rendered
