"""Replay-backed empirical audit for War Room Auto Lab.

The audit only promotes observable facts. It describes how the frozen LIGHT
policy used one candidate team against the Gauntlet pool; it does not claim to
measure human ladder strength or prove that a move/set is intrinsically bad.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


LOG_PATTERN = re.compile(
    r'<script[^>]*class=["\']battle-log-data["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
PROTECTIVE_MOVES = {
    "Baneful Bunker",
    "Burning Bulwark",
    "Detect",
    "King's Shield",
    "Obstruct",
    "Protect",
    "Silk Trap",
    "Spiky Shield",
}
DYNAMIC_FORM_SUFFIXES = {
    "mega",
    "megax",
    "megay",
    "primal",
    "ultra",
    "gmax",
}


@dataclass
class AutoLabReplayFacts:
    leads: list[str] = field(default_factory=list)
    opponent_leads: list[str] = field(default_factory=list)
    observed_pokemon: list[str] = field(default_factory=list)
    opponent_observed_pokemon: list[str] = field(default_factory=list)
    move_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    turns: int = 0
    first_faint_conceded: bool | None = None
    observable_events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize_user(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _species_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _canonical_candidate_species(value: str, candidate_roster: Sequence[str]) -> str:
    """Map Showdown ids/battle forms back to the six Team Builder identities."""

    key = _species_key(value)
    if not key:
        return value
    exact = {_species_key(name): name for name in candidate_roster}
    if key in exact:
        return exact[key]

    # Team Preview can expose a transient battle form (for example mawilemega)
    # while the Team Builder identity is the base species (Mawile). Collapse only
    # known battle-only suffixes so distinct legal formes are not merged casually.
    for name in sorted(candidate_roster, key=lambda item: len(_species_key(item)), reverse=True):
        base = _species_key(name)
        if not base or not key.startswith(base):
            continue
        suffix = key[len(base):]
        if suffix in DYNAMIC_FORM_SUFFIXES:
            return name
    return value


def _protocol_side(value: str) -> str | None:
    match = re.match(r"(p[12])(?:[a-z])?:", value.strip())
    return match.group(1) if match else None


def _slot(value: str) -> str | None:
    match = re.match(r"p[12]([a-z]):", value.strip())
    return match.group(1) if match else None


def _species(details: str) -> str:
    return details.split(",", 1)[0].strip()


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _extract_protocol(replay_html: str) -> list[str]:
    match = LOG_PATTERN.search(replay_html)
    if not match:
        raise ValueError("Replay sin battle-log-data")
    protocol = html.unescape(match.group(1)).strip()
    return [line for line in protocol.splitlines() if line.startswith("|")]


def _candidate_protocol_side(
    lines: Sequence[str], summary: Mapping[str, Any], candidate_id: str
) -> str:
    pairing = summary.get("pairing", {})
    candidate_side = "alpha" if pairing.get("alphaTeamId") == candidate_id else "beta"
    expected_user = str(summary.get("players", {}).get(candidate_side) or "")
    if not expected_user:
        raise ValueError("Resumen sin username Alpha/Beta")
    wanted = _normalize_user(expected_user)
    for line in lines:
        parts = line.split("|")
        if (
            len(parts) >= 4
            and parts[1] == "player"
            and _normalize_user(parts[3]) == wanted
        ):
            return parts[2]
    raise ValueError(f"No se encontró {candidate_side}={expected_user} en el replay")


def parse_auto_lab_replay(
    replay_html: str,
    summary: Mapping[str, Any],
    candidate_id: str,
) -> AutoLabReplayFacts:
    lines = _extract_protocol(replay_html)
    candidate_side = _candidate_protocol_side(lines, summary, candidate_id)
    opponent_side = "p2" if candidate_side == "p1" else "p1"
    slots: dict[tuple[str, str], str] = {}
    leads: dict[str, str] = {}
    opponent_leads: dict[str, str] = {}
    observed: list[str] = []
    opponent_observed: list[str] = []
    moves: dict[str, Counter[str]] = defaultdict(Counter)
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    turn = 0
    first_faint_conceded: bool | None = None
    current_move: dict[str, Any] | None = None
    last_protection_turn: dict[str, int] = {}

    for line in lines:
        parts = line.split("|")
        if len(parts) < 2:
            continue
        command = parts[1]
        if command == "turn" and len(parts) >= 3:
            turn = int(parts[2])
            current_move = None
            continue
        if command in {"switch", "drag", "replace"} and len(parts) >= 4:
            side = _protocol_side(parts[2])
            slot = _slot(parts[2])
            species = _species(parts[3])
            if side and slot:
                slots[(side, slot)] = species
            if side == candidate_side:
                _append_unique(observed, species)
                if turn == 0 and slot in {"a", "b"}:
                    leads.setdefault(slot, species)
            elif side == opponent_side:
                _append_unique(opponent_observed, species)
                if turn == 0 and slot in {"a", "b"}:
                    opponent_leads.setdefault(slot, species)
            current_move = None
            continue
        if command == "move" and len(parts) >= 4:
            actor = parts[2]
            side = _protocol_side(actor)
            slot = _slot(actor)
            if side == candidate_side:
                species = slots.get(
                    (side, slot or ""), actor.split(":", 1)[-1].strip()
                )
                move = parts[3]
                target = parts[4] if len(parts) >= 5 else ""
                moves[species][move] += 1
                current_move = {
                    "actor": actor,
                    "species": species,
                    "move": move,
                    "target": target,
                    "turn": turn,
                }
                if move in PROTECTIVE_MOVES:
                    previous = last_protection_turn.get(actor)
                    if previous == turn - 1:
                        events.append(
                            {
                                "type": "consecutive-protection",
                                "move": move,
                                "species": species,
                                "turn": turn,
                            }
                        )
                    last_protection_turn[actor] = turn
            else:
                current_move = None
            continue
        if command in {"-immune", "-fail"} and len(parts) >= 3 and current_move:
            event_type = "immune" if command == "-immune" else "failed"
            events.append(
                {
                    "type": event_type,
                    "move": current_move["move"],
                    "species": current_move["species"],
                    "target": parts[2],
                    "turn": turn,
                }
            )
            continue
        if command in {"-activate", "-block"} and len(parts) >= 4 and current_move:
            if "protect" in "|".join(parts[3:]).lower():
                events.append(
                    {
                        "type": "target-protected",
                        "move": current_move["move"],
                        "species": current_move["species"],
                        "target": parts[2],
                        "turn": turn,
                    }
                )
            continue
        if command == "faint" and len(parts) >= 3 and first_faint_conceded is None:
            first_faint_conceded = _protocol_side(parts[2]) == candidate_side

    if len(leads) < 2:
        warnings.append("No se recuperaron ambos leads del candidato.")
    if len(opponent_leads) < 2:
        warnings.append("No se recuperaron ambos leads del rival.")
    return AutoLabReplayFacts(
        leads=[leads[key] for key in ("a", "b") if key in leads],
        opponent_leads=[
            opponent_leads[key] for key in ("a", "b") if key in opponent_leads
        ],
        observed_pokemon=observed,
        opponent_observed_pokemon=opponent_observed,
        move_counts={species: dict(counter) for species, counter in moves.items()},
        turns=int(summary.get("turns", turn) or turn),
        first_faint_conceded=first_faint_conceded,
        observable_events=events,
        warnings=warnings,
    )


def classify_archetypes(team_text: str) -> list[str]:
    text = team_text.lower()
    tags: list[str] = []
    checks = [
        ("Rain", ("ability: drizzle", "- rain dance")),
        ("Sun", ("ability: drought", "- sunny day")),
        ("Sand", ("ability: sand stream", "- sandstorm")),
        ("Snow", ("ability: snow warning", "- snowscape")),
        ("Trick Room", ("- trick room",)),
        ("Tailwind", ("- tailwind",)),
        ("Redirection", ("- follow me", "- rage powder")),
        ("Screens", ("- reflect", "- light screen", "- aurora veil")),
        ("Perish", ("- perish song",)),
        (
            "Setup",
            (
                "- swords dance",
                "- nasty plot",
                "- calm mind",
                "- dragon dance",
                "- quiver dance",
                "- bulk up",
                "- belly drum",
                "- coil",
            ),
        ),
    ]
    for label, needles in checks:
        if any(needle in text for needle in needles):
            tags.append(label)
    return tags or ["Balance / Other"]


def _score(row: Mapping[str, int]) -> float:
    games = int(row.get("games", 0))
    return (
        round(
            100
            * (int(row.get("wins", 0)) + 0.5 * int(row.get("ties", 0)))
            / games,
            2,
        )
        if games
        else 0.0
    )


def _outcome(summary: Mapping[str, Any], candidate_id: str) -> str:
    pairing = summary.get("pairing", {})
    side = "alpha" if pairing.get("alphaTeamId") == candidate_id else "beta"
    winner = summary.get("winnerSide")
    if winner == side:
        return "wins"
    if winner == "tie":
        return "ties"
    return "losses"


def _opponent_id(summary: Mapping[str, Any], candidate_id: str) -> str:
    pairing = summary.get("pairing", {})
    return str(
        pairing.get("betaTeamId")
        if pairing.get("alphaTeamId") == candidate_id
        else pairing.get("alphaTeamId")
    )


def _index_replay_paths(
    replay_root: Path,
    battle_tags: Sequence[str],
) -> dict[str, Path]:
    """Scan the replay tree once and map battle tags to files.

    Poke-env normally saves replays as `<battle-tag>.html`; the fallback handles
    wrappers/prefixes without repeating a filesystem traversal for every battle.
    """

    wanted = [tag for tag in dict.fromkeys(str(tag) for tag in battle_tags) if tag]
    if not wanted:
        return {}

    paths = list(replay_root.rglob("*.html"))
    by_stem = {path.stem: path for path in paths}
    index: dict[str, Path] = {}
    unresolved: list[str] = []
    for tag in wanted:
        direct = by_stem.get(tag)
        if direct is not None:
            index[tag] = direct
        else:
            unresolved.append(tag)

    if unresolved and paths:
        pattern = re.compile(
            "|".join(re.escape(tag) for tag in sorted(unresolved, key=len, reverse=True))
        )
        for path in paths:
            match = pattern.search(path.name)
            if match:
                index.setdefault(match.group(0), path)
    return index


def _record(rows: Sequence[Mapping[str, Any]], candidate_id: str) -> dict[str, int]:
    result = {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    for summary in rows:
        result["games"] += 1
        result[_outcome(summary, candidate_id)] += 1
    return result


def build_auto_lab_audit(
    *,
    candidate_id: str,
    candidate_roster: Sequence[str],
    summaries: Sequence[dict[str, Any]],
    candidate_report: Mapping[str, Any],
    opponents: Mapping[str, Mapping[str, Any]],
    replay_root: Path,
) -> dict[str, Any]:
    total = len(summaries)
    signal_level = (
        "exploratory" if total < 24 else "directional" if total < 60 else "stronger"
    )
    overall_score = float(candidate_report.get("scorePercent", 0.0) or 0.0)
    roster = [str(name) for name in candidate_roster if str(name)]
    roster_set = set(roster)
    roster_order = {name: index for index, name in enumerate(roster)}
    canonical = lambda value: _canonical_candidate_species(str(value), roster)

    battle_tags = [str(summary.get("battleTag") or "") for summary in summaries]
    replay_index = _index_replay_paths(replay_root, battle_tags)
    facts: dict[str, AutoLabReplayFacts] = {}
    replay_errors: list[dict[str, str]] = []
    for summary in summaries:
        tag = str(summary.get("battleTag") or "")
        path = replay_index.get(tag) if tag else None
        if not path:
            replay_errors.append({"battleTag": tag, "error": "Replay ausente"})
            continue
        try:
            facts[tag] = parse_auto_lab_replay(
                path.read_text(encoding="utf-8"), summary, candidate_id
            )
        except Exception as error:
            replay_errors.append({"battleTag": tag, "error": str(error)[:240]})

    matchup_rows: list[dict[str, Any]] = []
    for opponent_id, row in candidate_report.get("byOpponent", {}).items():
        meta = opponents.get(opponent_id, {})
        matchup_rows.append(
            {
                "id": opponent_id,
                "label": meta.get("label", opponent_id),
                "roster": list(meta.get("roster", [])),
                "archetypes": list(meta.get("archetypes", [])),
                **row,
            }
        )
    matchup_rows.sort(
        key=lambda row: (
            float(row.get("scorePercent", 0)),
            str(row.get("label", "")),
        )
    )

    lead_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    selection_rows: dict[str, dict[str, int]] = {
        name: {"games": 0, "wins": 0, "losses": 0, "ties": 0, "leadGames": 0}
        for name in roster
    }
    move_rows: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0, "uses": 0}
    )
    opponent_pokemon_losses: Counter[str] = Counter()
    opponent_core_losses: Counter[str] = Counter()
    archetype_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    pattern_counts: Counter[str] = Counter()

    for summary in summaries:
        outcome = _outcome(summary, candidate_id)
        pairing = summary.get("pairing", {})
        candidate_side = (
            "alpha" if pairing.get("alphaTeamId") == candidate_id else "beta"
        )
        preview = list(summary.get("teamPreview", {}).get(candidate_side, []) or [])
        canonical_preview: list[str] = []
        for species in preview:
            identity = canonical(species)
            if identity in roster_set and identity not in canonical_preview:
                canonical_preview.append(identity)
        for identity in canonical_preview:
            row = selection_rows[identity]
            row["games"] += 1
            row[outcome] += 1

        replay = facts.get(str(summary.get("battleTag") or ""))
        if replay:
            canonical_leads = [canonical(species) for species in replay.leads]
            lead_key = " + ".join(canonical_leads) if canonical_leads else "No recuperado"
            lead = lead_rows[lead_key]
            lead["games"] += 1
            lead[outcome] += 1
            for identity in canonical_leads:
                if identity in roster_set:
                    selection_rows[identity]["leadGames"] += 1
            for species, moves in replay.move_counts.items():
                identity = canonical(species)
                for move, uses in moves.items():
                    row = move_rows[(identity, move)]
                    row["games"] += 1
                    row[outcome] += 1
                    row["uses"] += uses
            if outcome == "losses":
                for species in set(replay.opponent_observed_pokemon):
                    opponent_pokemon_losses[species] += 1
                core = replay.opponent_leads or replay.opponent_observed_pokemon[:2]
                if len(core) >= 2:
                    opponent_core_losses[" + ".join(sorted(core[:2]))] += 1
                if replay.turns <= 5:
                    pattern_counts["Derrota en 5 turnos o menos"] += 1
                if replay.first_faint_conceded:
                    pattern_counts["LIGHT recibió la primera baja"] += 1
                for event in replay.observable_events:
                    pattern_counts[
                        f"{event['type']}: {event.get('move', '—')}"
                    ] += 1

        opponent_id = _opponent_id(summary, candidate_id)
        for tag in opponents.get(opponent_id, {}).get(
            "archetypes", ["Balance / Other"]
        ):
            row = archetype_rows[str(tag)]
            row["games"] += 1
            row[outcome] += 1

    leads = [
        {"lead": label, **row, "scorePercent": _score(row)}
        for label, row in lead_rows.items()
    ]
    leads.sort(
        key=lambda row: (-row["games"], -row["scorePercent"], row["lead"])
    )

    selection_usage = []
    for species, row in selection_rows.items():
        games = row["games"]
        selected_rate = round(100 * games / total, 2) if total else 0.0
        score = _score(row)
        signal = (
            "rarely-selected"
            if total >= 12 and selected_rate <= 20
            else "review"
            if games >= 3 and score + 15 < overall_score
            else "ok"
        )
        selection_usage.append(
            {
                "pokemon": species,
                "selectedGames": games,
                "selectedRate": selected_rate,
                "leadGames": row["leadGames"],
                "scoreWhenSelected": score,
                "signal": signal,
            }
        )
    selection_usage.sort(
        key=lambda row: roster_order.get(row["pokemon"], len(roster_order))
    )

    move_signals = []
    for (species, move), row in move_rows.items():
        score = _score(row)
        signal = (
            "review"
            if row["games"] >= 3 and score + 15 < overall_score
            else "observed"
        )
        move_signals.append(
            {
                "pokemon": species,
                "move": move,
                "gamesUsed": row["games"],
                "totalUses": row["uses"],
                "wins": row["wins"],
                "losses": row["losses"],
                "ties": row["ties"],
                "scoreWhenUsed": score,
                "signal": signal,
            }
        )
    move_signals.sort(
        key=lambda row: (
            0 if row["signal"] == "review" else 1,
            -row["gamesUsed"],
            row["pokemon"],
            row["move"],
        )
    )

    archetypes = [
        {"archetype": label, **row, "scorePercent": _score(row)}
        for label, row in archetype_rows.items()
    ]
    archetypes.sort(
        key=lambda row: (row["scorePercent"], -row["games"], row["archetype"])
    )
    losses = sum(
        1 for summary in summaries if _outcome(summary, candidate_id) == "losses"
    )

    return {
        "signal": {
            "games": total,
            "level": signal_level,
            "note": (
                "Muestra exploratoria: úsala para detectar qué merece más batallas."
                if signal_level == "exploratory"
                else "Muestra direccional: compara patrones, no diferencias pequeñas."
                if signal_level == "directional"
                else "Muestra más fuerte para este benchmark LIGHT-equipo; sigue sin ser win rate humano."
            ),
        },
        "goodMatchups": list(reversed(matchup_rows[-5:])),
        "badMatchups": matchup_rows[:5],
        "problematicOpponents": matchup_rows[:8],
        "leadPerformance": leads[:10],
        "selectionUsage": selection_usage,
        "setSignals": [row for row in selection_usage if row["signal"] != "ok"],
        "moveSignals": move_signals[:12],
        "opponentPokemonPressure": [
            {
                "pokemon": species,
                "lossGames": count,
                "lossShare": round(100 * count / losses, 2) if losses else 0.0,
            }
            for species, count in opponent_pokemon_losses.most_common(10)
        ],
        "opponentCorePressure": [
            {
                "core": core,
                "lossGames": count,
                "lossShare": round(100 * count / losses, 2) if losses else 0.0,
            }
            for core, count in opponent_core_losses.most_common(8)
        ],
        "archetypePerformance": archetypes,
        "recurringLossPatterns": [
            {
                "pattern": pattern,
                "count": count,
                "lossShare": round(100 * count / losses, 2) if losses else 0.0,
            }
            for pattern, count in pattern_counts.most_common(10)
        ],
        "replayCoverage": {
            "expected": total,
            "parsed": len(facts),
            "errors": replay_errors[:12],
        },
        "limitations": [
            "Mide compatibilidad entre LIGHT M-C y el Team dentro de este pool; no la calidad objetiva del Team ni tu win rate.",
            "Auto Lab muestrea solo Team Preview del candidato; las decisiones por turno permanecen deterministas.",
            "Los scores por move/set son correlaciones de uso, no evidencia causal de que ese recurso sea malo.",
            "Los arquetipos son etiquetas observables y pueden solaparse (por ejemplo Rain + Tailwind).",
            "El RNG de daño/efectos de Showdown no se empareja entre baseline y variantes.",
        ],
    }