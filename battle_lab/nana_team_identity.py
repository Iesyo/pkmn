"""Model-agnostic Team identity for Nana L2 memory.

Roster identity matches Battle Lab M-C's six-species grouping. Exact identity
contains competitive set semantics only: cosmetic/unknown export lines are
retained as diagnostics but never fragment Team memory.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from battle_lab.mc_team_split import (
    normalize_species,
    species_from_header,
    team_signature_text,
)
from battle_lab.nana_contracts import canonical_json, fingerprint_payload
from battle_lab.team_corpus import normalize_team_text, team_sha256


ROSTER_SIGNATURE_SPEC_VERSION = 1
EXACT_TEAM_SIGNATURE_SPEC_VERSION = 2
TEAM_SIGNATURE_SPEC_VERSION = 2
_EXACT_PREFIX = f"team:v{EXACT_TEAM_SIGNATURE_SPEC_VERSION}:"
_ROSTER_PREFIX = f"roster:v{ROSTER_SIGNATURE_SPEC_VERSION}:"
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


def _allocations(value: str, *, default: int) -> dict[str, int]:
    rendered = {key: int(default) for key in _STAT_KEYS}
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
        # Gender is deliberately competitive identity: Rivalry/Cute Charm/Attract
        # can depend on it even though most teams omit it.
        "gender": gender,
    }


def _canonical_mon(block: str) -> tuple[dict[str, Any], list[str]]:
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
    }
    unknown_lines: list[str] = []
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
            mon["evs"] = _allocations(line.split(":", 1)[1], default=0)
        elif lower.startswith("ivs:"):
            mon["ivs"] = _allocations(line.split(":", 1)[1], default=31)
        elif lower.endswith(" nature"):
            mon["nature"] = _to_id(line[:-7])
        elif line.startswith("- "):
            move = _to_id(line[2:])
            if move:
                mon["moves"].append(move)
        else:
            # Gen9 VGC ignores cosmetic/legacy fields such as Shiny, Happiness,
            # Gigantamax and Dynamax Level. Unknown exporter lines are diagnosed
            # but do not silently become competitive identity.
            unknown_lines.append(line)
    mon["moves"] = sorted(set(mon["moves"]))
    return mon, sorted(set(unknown_lines))


def _parse_team(team_text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = normalize_team_text(team_text)
    blocks = [
        block
        for block in re.split(r"\n\s*\n", normalized.strip())
        if block.strip()
    ]
    if len(blocks) != 6:
        raise ValueError(f"se esperaban 6 Pokémon y se detectaron {len(blocks)}")

    pokemon: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for block in blocks:
        mon, unknown_lines = _canonical_mon(block)
        if not mon.get("species"):
            raise ValueError("no se pudo resolver la especie de todos los Pokémon")
        pokemon.append(mon)
        if unknown_lines:
            diagnostics.append(
                {
                    "species": mon["species"],
                    "unknownLines": unknown_lines,
                }
            )
    pokemon.sort(key=canonical_json)
    diagnostics.sort(key=canonical_json)
    return {
        "specVersion": EXACT_TEAM_SIGNATURE_SPEC_VERSION,
        "pokemon": pokemon,
    }, diagnostics


def canonical_team_payload(team_text: str) -> dict[str, Any]:
    payload, _diagnostics = _parse_team(team_text)
    return payload


def team_identity(team_text: str) -> dict[str, Any]:
    normalized = normalize_team_text(team_text)
    roster = list(team_signature_text(normalized, source="Nana Team"))
    roster_payload = {
        "specVersion": ROSTER_SIGNATURE_SPEC_VERSION,
        "species": roster,
    }
    exact_payload, diagnostics = _parse_team(normalized)
    return {
        "teamSignatureSpecVersion": TEAM_SIGNATURE_SPEC_VERSION,
        "rosterSignatureSpecVersion": ROSTER_SIGNATURE_SPEC_VERSION,
        "exactTeamSignatureSpecVersion": EXACT_TEAM_SIGNATURE_SPEC_VERSION,
        "roster": roster,
        "rosterSignature": _ROSTER_PREFIX + fingerprint_payload(roster_payload),
        "exactTeamSignature": _EXACT_PREFIX + fingerprint_payload(exact_payload),
        "pasteSha256": team_sha256(normalized),
        "canonicalTeam": exact_payload,
        "diagnostics": diagnostics,
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
        "rosterSignatureSpecVersion": identity["rosterSignatureSpecVersion"],
        "exactTeamSignatureSpecVersion": identity["exactTeamSignatureSpecVersion"],
        "roster": identity["roster"],
        "rosterSignature": identity["rosterSignature"],
        "exactTeamSignature": identity["exactTeamSignature"],
        "pasteSha256": identity["pasteSha256"],
        "canonicalTeam": identity["canonicalTeam"],
        "diagnostics": identity.get("diagnostics") or [],
        "paste": identity["normalizedPaste"],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if destination.is_file():
        try:
            if destination.read_text(encoding="utf-8") == rendered:
                return destination
        except OSError:
            pass

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def session_team_context(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "teamSignatureSpecVersion": identity["teamSignatureSpecVersion"],
        "rosterSignatureSpecVersion": identity["rosterSignatureSpecVersion"],
        "exactTeamSignatureSpecVersion": identity["exactTeamSignatureSpecVersion"],
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
        rendered.append(str(roster_signature))
    if exact_team_signature:
        rendered.append(str(exact_team_signature))
    if exact_team_signature and archetype:
        digest = str(exact_team_signature).rsplit(":", 1)[-1]
        rendered.append(
            f"team+archetype:v{EXACT_TEAM_SIGNATURE_SPEC_VERSION}:{digest}|{archetype}"
        )
    return rendered
