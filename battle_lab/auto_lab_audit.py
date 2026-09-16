"""Replay-backed empirical audit for War Room Auto Lab.

The audit only promotes observable facts. It describes how the frozen LIGHT
policy used one candidate team against the Gauntlet pool; it does not claim to
measure human ladder strength or prove that a move/set is intrinsically bad.
"""

from __future__ import annotations

import html
import math
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


def _confidence95(row: Mapping[str, Any], *, positive_key: str = "wins") -> dict[str, float]:
    """Wilson interval in percentage points for a binary or half-point record."""

    games = int(row.get("games", 0) or 0)
    if games < 1:
        return {"low": 0.0, "high": 100.0, "width": 100.0}
    points = float(row.get(positive_key, 0) or 0)
    if positive_key == "wins":
        points += 0.5 * float(row.get("ties", 0) or 0)
    score = points / games
    z = 1.95996398454
    denominator = 1 + z * z / games
    center = (score + z * z / (2 * games)) / denominator
    margin = (
        z
        * math.sqrt(score * (1 - score) / games + z * z / (4 * games * games))
        / denominator
    )
    low = max(0.0, center - margin) * 100
    high = min(1.0, center + margin) * 100
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "width": round(high - low, 2),
    }


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
    sampling: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    total = len(summaries)
    pool_estimate = candidate_report.get("poolEstimate", candidate_report)
    overall_score = float(pool_estimate.get("scorePercent", 0.0) or 0.0)
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
    opponent_pokemon_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    opponent_core_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    opponent_pokemon_ids: dict[str, set[str]] = defaultdict(set)
    opponent_core_ids: dict[str, set[str]] = defaultdict(set)
    opponent_pokemon_screening_games: Counter[str] = Counter()
    opponent_core_screening_games: Counter[str] = Counter()
    archetype_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    archetype_screening_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    archetype_opponents: dict[str, set[str]] = defaultdict(set)
    pattern_counts: Counter[str] = Counter()
    preview_signatures: set[tuple[str, ...]] = set()
    candidate_side_counts: Counter[str] = Counter()

    for summary in summaries:
        outcome = _outcome(summary, candidate_id)
        pairing = summary.get("pairing", {})
        candidate_side = (
            "alpha" if pairing.get("alphaTeamId") == candidate_id else "beta"
        )
        candidate_side_counts[candidate_side] += 1
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
        if canonical_preview:
            preview_signatures.add(tuple(sorted(canonical_preview)))

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
            opponent_id = _opponent_id(summary, candidate_id)
            for species in set(replay.opponent_observed_pokemon):
                pressure = opponent_pokemon_rows[species]
                pressure["games"] += 1
                pressure[outcome] += 1
                opponent_pokemon_ids[species].add(opponent_id)
                if summary.get("samplingStage") != "deepening":
                    opponent_pokemon_screening_games[species] += 1
            core = replay.opponent_leads or replay.opponent_observed_pokemon[:2]
            if len(core) >= 2:
                core_key = " + ".join(sorted(core[:2]))
                pressure = opponent_core_rows[core_key]
                pressure["games"] += 1
                pressure[outcome] += 1
                opponent_core_ids[core_key].add(opponent_id)
                if summary.get("samplingStage") != "deepening":
                    opponent_core_screening_games[core_key] += 1
            if outcome == "losses":
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
            if summary.get("samplingStage") != "deepening":
                screening_row = archetype_screening_rows[str(tag)]
                screening_row["games"] += 1
                screening_row[outcome] += 1
            archetype_opponents[str(tag)].add(opponent_id)

    leads = [
        {
            "lead": label,
            **row,
            "scorePercent": _score(row),
            "confidence95": _confidence95(row),
        }
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
        interval = _confidence95(row)
        signal = (
            "rarely-selected"
            if total >= 12 and selected_rate <= 20
            else "review"
            if games >= 6 and interval["high"] + 5 < overall_score
            else "ok"
        )
        selection_usage.append(
            {
                "pokemon": species,
                "selectedGames": games,
                "selectedRate": selected_rate,
                "leadGames": row["leadGames"],
                "scoreWhenSelected": score,
                "confidence95": interval,
                "signal": signal,
            }
        )
    selection_usage.sort(
        key=lambda row: roster_order.get(row["pokemon"], len(roster_order))
    )

    move_signals = []
    for (species, move), row in move_rows.items():
        score = _score(row)
        interval = _confidence95(row)
        signal = (
            "review"
            if row["games"] >= 6 and interval["high"] + 5 < overall_score
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
                "confidence95": interval,
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

    archetypes = []
    for label, combined_row in archetype_rows.items():
        screening_row = archetype_screening_rows.get(label, combined_row)
        archetypes.append(
            {
                "archetype": label,
                **screening_row,
                "scorePercent": _score(screening_row),
                "confidence95": _confidence95(screening_row),
                "uniqueOpponents": len(archetype_opponents[label]),
                "adaptiveCombined": {
                    **combined_row,
                    "scorePercent": _score(combined_row),
                    "confidence95": _confidence95(combined_row),
                },
            }
        )
    archetypes.sort(
        key=lambda row: (row["scorePercent"], -row["games"], row["archetype"])
    )
    losses = sum(
        1 for summary in summaries if _outcome(summary, candidate_id) == "losses"
    )
    parsed_summaries = [
        summary
        for summary in summaries
        if str(summary.get("battleTag") or "") in facts
    ]
    parsed_losses = sum(
        1 for summary in parsed_summaries if _outcome(summary, candidate_id) == "losses"
    )
    parsed_screening_summaries = [
        summary
        for summary in parsed_summaries
        if summary.get("samplingStage") != "deepening"
    ]
    pool_games = int(pool_estimate.get("games", 0) or 0)
    reference_loss_rate = (
        100 * int(pool_estimate.get("losses", 0) or 0) / pool_games
        if pool_games
        else 100 * parsed_losses / len(parsed_summaries)
        if parsed_summaries
        else 0.0
    )

    pokemon_pressure: list[dict[str, Any]] = []
    for species, row in opponent_pokemon_rows.items():
        games = row["games"]
        loss_rate = 100 * row["losses"] / games if games else 0.0
        screening_games = opponent_pokemon_screening_games[species]
        exposure_rate = (
            100 * screening_games / len(parsed_screening_summaries)
            if parsed_screening_summaries
            else 0.0
        )
        interval = _confidence95(row, positive_key="losses")
        confidence = max(0.0, 1 - interval["width"] / 100)
        lift = loss_rate - reference_loss_rate
        priority = (
            exposure_rate
            / 100
            * (0.55 * max(0.0, lift) / 100 + 0.45 * loss_rate / 100)
            * confidence
        )
        pokemon_pressure.append(
            {
                "pokemon": species,
                "observedGames": games,
                "screeningObservedGames": screening_games,
                "lossGames": row["losses"],
                "lossRate": round(loss_rate, 2),
                "lossRateLift": round(lift, 2),
                "exposureRate": round(exposure_rate, 2),
                "uniqueOpponents": len(opponent_pokemon_ids[species]),
                "confidence95": interval,
                "priorityScore": round(priority * 100, 2),
                "lossShare": round(100 * row["losses"] / losses, 2) if losses else 0.0,
            }
        )
    pokemon_pressure.sort(
        key=lambda row: (
            -float(row["priorityScore"]),
            -int(row["observedGames"]),
            str(row["pokemon"]),
        )
    )

    core_pressure: list[dict[str, Any]] = []
    for core, row in opponent_core_rows.items():
        games = row["games"]
        loss_rate = 100 * row["losses"] / games if games else 0.0
        screening_games = opponent_core_screening_games[core]
        exposure_rate = (
            100 * screening_games / len(parsed_screening_summaries)
            if parsed_screening_summaries
            else 0.0
        )
        interval = _confidence95(row, positive_key="losses")
        confidence = max(0.0, 1 - interval["width"] / 100)
        lift = loss_rate - reference_loss_rate
        priority = (
            exposure_rate
            / 100
            * (0.55 * max(0.0, lift) / 100 + 0.45 * loss_rate / 100)
            * confidence
        )
        core_pressure.append(
            {
                "core": core,
                "observedGames": games,
                "screeningObservedGames": screening_games,
                "lossGames": row["losses"],
                "lossRate": round(loss_rate, 2),
                "lossRateLift": round(lift, 2),
                "exposureRate": round(exposure_rate, 2),
                "uniqueOpponents": len(opponent_core_ids[core]),
                "confidence95": interval,
                "priorityScore": round(priority * 100, 2),
                "lossShare": round(100 * row["losses"] / losses, 2) if losses else 0.0,
            }
        )
    core_pressure.sort(
        key=lambda row: (
            -float(row["priorityScore"]),
            -int(row["observedGames"]),
            str(row["core"]),
        )
    )

    unique_opponents = len({_opponent_id(summary, candidate_id) for summary in summaries})
    unique_rosters = len(
        {
            tuple(sorted(str(species).lower() for species in meta.get("roster", [])))
            for meta in opponents.values()
            if meta.get("roster")
        }
    )
    replay_rate = 100 * len(facts) / total if total else 0.0
    deep_dive = sampling.get("deepDive", {}) if isinstance(sampling, Mapping) else {}
    deep_dive_count = int(deep_dive.get("opponents", 0) or 0)
    if total < 48 or unique_opponents < 8 or replay_rate < 50:
        signal_level = "exploratory"
    elif total < 240 or unique_opponents < 30 or replay_rate < 80:
        signal_level = "directional"
    else:
        signal_level = "stronger"

    side_rows: dict[str, dict[str, int]] = {
        "alpha": {"games": 0, "wins": 0, "losses": 0, "ties": 0},
        "beta": {"games": 0, "wins": 0, "losses": 0, "ties": 0},
    }
    for summary in summaries:
        pairing = summary.get("pairing", {})
        side = "alpha" if pairing.get("alphaTeamId") == candidate_id else "beta"
        side_rows[side]["games"] += 1
        side_rows[side][_outcome(summary, candidate_id)] += 1
    side_scores = {side: _score(row) for side, row in side_rows.items()}
    side_gap = abs(side_scores["alpha"] - side_scores["beta"])
    if min(row["games"] for row in side_rows.values()) < 8:
        policy_status = "insufficient"
    elif side_gap >= 15:
        policy_status = "review"
    else:
        policy_status = "stable"

    return {
        "signal": {
            "games": total,
            "uniqueOpponents": unique_opponents,
            "level": signal_level,
            "note": (
                "Muestra exploratoria: úsala para detectar qué merece más batallas."
                if signal_level == "exploratory"
                else "Muestra direccional: la cobertura es útil, pero compara patrones e intervalos, no diferencias pequeñas."
                if signal_level == "directional"
                else "Muestra más fuerte por volumen, variedad de rivales y cobertura de replay; sigue sin ser win rate humano."
            ),
        },
        "dataQuality": {
            "games": total,
            "screeningGames": pool_games,
            "deepeningGames": max(0, total - pool_games),
            "uniqueOpponents": unique_opponents,
            "uniqueRosters": unique_rosters,
            "deepDiveOpponents": deep_dive_count,
            "parsedReplays": len(facts),
            "replayCoveragePercent": round(replay_rate, 2),
            "uniquePreviewCombinations": len(preview_signatures),
            "candidateAlphaGames": candidate_side_counts["alpha"],
            "candidateBetaGames": candidate_side_counts["beta"],
            "sideImbalanceGames": abs(
                candidate_side_counts["alpha"] - candidate_side_counts["beta"]
            ),
        },
        "policySensitivity": {
            "status": policy_status,
            "alphaScorePercent": side_scores["alpha"],
            "betaScorePercent": side_scores["beta"],
            "sideGapPercentagePoints": round(side_gap, 2),
            "note": (
                "Brecha de lados: revisa sesgo de ejecución o emparejamiento antes de culpar al Team."
                if policy_status == "review"
                else "No apareció una brecha grande entre lados dentro de esta muestra."
                if policy_status == "stable"
                else "Aún no hay suficientes partidas por lado para leer sensibilidad de ejecución."
            ),
            "limitation": "Es un control de sensibilidad por lado, no una confirmación con un segundo piloto o política.",
        },
        "heuristic": {
            "deepDive": (
                "prevalence × severity × repeatability × confidence; la selección también reserva peso "
                "para incertidumbre y diversidad de arquetipos"
            ),
            "matchups": "Score con intervalo Wilson 95%; screening y confirmación se etiquetan por separado.",
            "pressure": (
                "Prioridad por exposición × tasa de derrota sobre la referencia × confianza; "
                "no por conteo bruto de derrotas."
            ),
        },
        "goodMatchups": list(reversed(matchup_rows[-5:])),
        "badMatchups": matchup_rows[:5],
        "problematicOpponents": matchup_rows[:8],
        "leadPerformance": leads[:10],
        "selectionUsage": selection_usage,
        "setSignals": [row for row in selection_usage if row["signal"] != "ok"],
        "moveSignals": move_signals[:12],
        "opponentPokemonPressure": pokemon_pressure[:10],
        "opponentCorePressure": core_pressure[:8],
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
            "La sensibilidad de política se aproxima por lado; para separarla del Team hace falta un segundo piloto o política.",
            "Los arquetipos son etiquetas observables y pueden solaparse (por ejemplo Rain + Tailwind).",
            "El RNG de daño/efectos de Showdown no se empareja entre baseline y variantes.",
        ],
    }
