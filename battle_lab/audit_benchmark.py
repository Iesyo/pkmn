#!/usr/bin/env python3
"""Audit mirrored VGC-Bench benchmark results and their Showdown replays.

The auditor intentionally separates observable protocol facts from tactical
judgment. It can prove that a pattern repeats (for example, VGC-Bench loses
with one team but wins the mirrored game against it), while attacks into
Protect or unused mechanics remain review signals rather than automatic
claims that a move was wrong.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_TEAM_ID = "MC182"
DEFAULT_BASELINE_ID = "simple-heuristics"
DEFAULT_SAMPLE_SIZE = 20
PROTECTIVE_MOVES = {
    "Baneful Bunker",
    "Burning Bulwark",
    "Detect",
    "King's Shield",
    "Mat Block",
    "Obstruct",
    "Protect",
    "Silk Trap",
    "Spiky Shield",
    "Wide Guard",
}
STALL_PROTECTION_MOVES = PROTECTIVE_MOVES - {"Mat Block", "Wide Guard"}
LOG_PATTERN = re.compile(
    r'<script[^>]*class=["\']battle-log-data["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
BATTLE_TAG_PATTERN = re.compile(r"battle-[a-z0-9-]+", re.IGNORECASE)


class AuditError(RuntimeError):
    """Raised when benchmark evidence is missing or inconsistent."""


@dataclass
class ReplayFacts:
    battle_tag: str
    players: dict[str, str]
    winner: str
    protocol_side: str
    roster: list[str]
    leads: list[str]
    observed_pokemon: list[str]
    move_counts: dict[str, int]
    turns: int
    first_faint_turn: int | None
    first_faint_conceded: bool | None
    switches: int
    unable_to_move: int
    protective_moves: int
    attacks_into_protection: int
    immunity_events: int
    failed_move_events: int
    consecutive_protection: int
    mechanics_used: list[str]
    fields_started: list[str]
    observable_events: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    @property
    def lead_key(self) -> str:
        return " + ".join(self.leads) if self.leads else "No recuperado"

    @property
    def evidence_flags(self) -> list[str]:
        flags: list[str] = []
        if self.turns <= 5:
            flags.append("derrota-corta")
        if self.first_faint_conceded:
            flags.append("primera-baja-recibida")
        if self.attacks_into_protection:
            flags.append("ataque-en-protección")
        if self.immunity_events:
            flags.append("inmunidad")
        if self.failed_move_events:
            flags.append("movimiento-fallido")
        if self.consecutive_protection:
            flags.append("protección-consecutiva")
        return flags


class AuditProgress:
    def __init__(self, total: int = 5):
        self.total = total
        self.started = time.monotonic()

    def advance(self, completed: int, label: str) -> None:
        elapsed = time.monotonic() - self.started
        eta = elapsed / completed * (self.total - completed) if completed else None
        filled = round(24 * completed / self.total)
        bar = "█" * filled + "░" * (24 - filled)
        eta_text = f"{eta:.1f}s" if eta is not None else "calculando"
        print(
            f"Auditoría [{bar}] {completed}/{self.total} · "
            f"{elapsed:.1f}s · ETA {eta_text} · {label}",
            flush=True,
        )


def _protocol_side(value: str) -> str | None:
    match = re.match(r"(p[12])(?:[a-z])?:", value.strip())
    return match.group(1) if match else None


def _slot(value: str) -> str | None:
    match = re.match(r"p[12]([a-z]):", value.strip())
    return match.group(1) if match else None


def _species(details: str) -> str:
    return details.split(",", 1)[0].strip()


def _roster_species(species: str, roster: Sequence[str]) -> str:
    """Collapse transformed forms back to the species shown at Team Preview."""

    if species in roster:
        return species
    matches = [
        candidate
        for candidate in roster
        if species.startswith(f"{candidate}-") or candidate.startswith(f"{species}-")
    ]
    return max(matches, key=len) if matches else species


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def extract_protocol(replay_html: str) -> str:
    match = LOG_PATTERN.search(replay_html)
    if not match:
        raise AuditError("El replay no contiene battle-log-data.")
    return html.unescape(match.group(1)).strip()


def infer_alpha_protocol_side(
    battle: dict[str, Any], players: dict[str, str]
) -> str:
    winner_side = battle.get("winnerSide")
    winner = battle.get("winner")
    if winner_side not in {"alpha", "beta"} or winner == "tie":
        return "p1"
    winner_protocol = next(
        (side for side, username in players.items() if username == winner), None
    )
    if winner_protocol is None:
        normalized_winner = re.sub(r"[^a-z0-9]", "", str(winner).lower())
        winner_protocol = next(
            (
                side
                for side, username in players.items()
                if re.sub(r"[^a-z0-9]", "", username.lower()) == normalized_winner
            ),
            None,
        )
    if winner_protocol is None:
        raise AuditError(
            f"No se pudo relacionar el ganador {winner!r} con p1/p2."
        )
    if winner_side == "alpha":
        return winner_protocol
    return "p2" if winner_protocol == "p1" else "p1"


def parse_replay_html(replay_html: str, battle: dict[str, Any]) -> ReplayFacts:
    """Extract observable facts for VGC-Bench from one poke-env HTML replay."""

    protocol = extract_protocol(replay_html)
    lines = [line for line in protocol.splitlines() if line.startswith("|")]
    players: dict[str, str] = {}
    winner = ""
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 4 and parts[1] == "player":
            players[parts[2]] = parts[3]
        elif len(parts) >= 3 and parts[1] == "win":
            winner = parts[2]
    if set(players) != {"p1", "p2"}:
        raise AuditError("El replay no declara exactamente los jugadores p1 y p2.")

    alpha_protocol = infer_alpha_protocol_side(battle, players)
    beta_protocol = "p2" if alpha_protocol == "p1" else "p1"
    vgc_side = battle["benchmark"]["vgcBenchSide"]
    protocol_side = alpha_protocol if vgc_side == "alpha" else beta_protocol

    roster: list[str] = []
    leads_by_slot: dict[str, str] = {}
    observed: list[str] = []
    move_counts: Counter[str] = Counter()
    mechanics: list[str] = []
    fields: list[str] = []
    observable_events: list[dict[str, Any]] = []
    warnings: list[str] = []
    turn = 0
    first_faint_turn: int | None = None
    first_faint_conceded: bool | None = None
    switches = 0
    unable_to_move = 0
    protective_moves = 0
    attacks_into_protection = 0
    immunity_events = 0
    failed_move_events = 0
    consecutive_protection = 0
    last_protection_turn: dict[str, int] = {}
    current_vgc_move: dict[str, Any] | None = None

    for line in lines:
        parts = line.split("|")
        if len(parts) < 2:
            continue
        command = parts[1]
        if command == "turn" and len(parts) >= 3:
            turn = int(parts[2])
            current_vgc_move = None
            continue
        if command == "poke" and len(parts) >= 4 and parts[2] == protocol_side:
            _append_unique(roster, _species(parts[3]))
            continue
        if command in {"switch", "drag", "replace"} and len(parts) >= 4:
            actor_side = _protocol_side(parts[2])
            if actor_side == protocol_side:
                species = _roster_species(_species(parts[3]), roster)
                _append_unique(observed, species)
                slot = _slot(parts[2])
                if turn == 0 and slot in {"a", "b"}:
                    leads_by_slot.setdefault(slot, species)
                elif turn > 0:
                    switches += 1
            current_vgc_move = None
            continue
        if command == "move" and len(parts) >= 4:
            actor = parts[2]
            actor_side = _protocol_side(actor)
            if actor_side == protocol_side:
                move = parts[3]
                target = parts[4] if len(parts) >= 5 else ""
                if not target and move in PROTECTIVE_MOVES:
                    target = actor
                move_counts[move] += 1
                current_vgc_move = {
                    "actor": actor,
                    "move": move,
                    "target": target,
                    "turn": turn,
                    "spread": any(
                        token.startswith("[spread]") for token in parts[5:]
                    ),
                }
                if move in PROTECTIVE_MOVES:
                    protective_moves += 1
                    if move in STALL_PROTECTION_MOVES:
                        previous_turn = last_protection_turn.get(actor)
                        if previous_turn == turn - 1:
                            consecutive_protection += 1
                            observable_events.append(
                                {
                                    "turn": turn,
                                    "type": "consecutive-protection",
                                    "actor": actor,
                                    "move": move,
                                    "target": target,
                                }
                            )
                        last_protection_turn[actor] = turn
            else:
                current_vgc_move = None
            continue
        if command == "cant" and len(parts) >= 3:
            if _protocol_side(parts[2]) == protocol_side:
                unable_to_move += 1
            current_vgc_move = None
            continue
        if command in {"-immune", "-fail"} and len(parts) >= 3:
            if current_vgc_move is not None:
                affected = parts[2]
                target = str(current_vgc_move.get("target", ""))
                if current_vgc_move.get("spread") or not target or affected == target:
                    if command == "-immune":
                        immunity_events += 1
                        event_type = "immune"
                    else:
                        failed_move_events += 1
                        event_type = "failed"
                    observable_events.append(
                        {
                            "turn": turn,
                            "type": event_type,
                            "actor": current_vgc_move["actor"],
                            "move": current_vgc_move["move"],
                            "target": affected,
                        }
                    )
            continue
        if command in {"-activate", "-block"} and len(parts) >= 4:
            if (
                current_vgc_move is not None
                and (
                    current_vgc_move.get("spread")
                    or parts[2] == current_vgc_move.get("target")
                )
                and "protect" in "|".join(parts[3:]).lower()
            ):
                attacks_into_protection += 1
                observable_events.append(
                    {
                        "turn": turn,
                        "type": "target-protected",
                        "actor": current_vgc_move["actor"],
                        "move": current_vgc_move["move"],
                        "target": parts[2],
                    }
                )
            continue
        if command == "faint" and len(parts) >= 3 and first_faint_turn is None:
            fainted_side = _protocol_side(parts[2])
            first_faint_turn = turn
            first_faint_conceded = fainted_side == protocol_side
            continue
        if command in {"-mega", "-terastallize", "-zpower", "-dynamax"}:
            if len(parts) >= 3 and _protocol_side(parts[2]) == protocol_side:
                _append_unique(mechanics, command.removeprefix("-"))
            continue
        if command == "-start" and len(parts) >= 4:
            if (
                _protocol_side(parts[2]) == protocol_side
                and "dynamax" in parts[3].lower()
            ):
                _append_unique(mechanics, "dynamax")
            continue
        if command == "-fieldstart" and len(parts) >= 3:
            _append_unique(fields, parts[2].removeprefix("move: "))

    if len(leads_by_slot) < 2:
        warnings.append("No fue posible recuperar ambos leads.")
    if len(observed) < 4:
        warnings.append(
            "El protocolo solo revela Pokémon que entraron al campo; "
            "la selección completa de cuatro puede permanecer oculta."
        )
    if winner and battle.get("winner") != winner:
        warnings.append("El ganador del replay y el JSON usan nombres distintos.")
    return ReplayFacts(
        battle_tag=battle["battleTag"],
        players=players,
        winner=winner,
        protocol_side=protocol_side,
        roster=roster,
        leads=[leads_by_slot[key] for key in ("a", "b") if key in leads_by_slot],
        observed_pokemon=observed,
        move_counts=dict(move_counts.most_common()),
        turns=int(battle.get("turns", turn)),
        first_faint_turn=first_faint_turn,
        first_faint_conceded=first_faint_conceded,
        switches=switches,
        unable_to_move=unable_to_move,
        protective_moves=protective_moves,
        attacks_into_protection=attacks_into_protection,
        immunity_events=immunity_events,
        failed_move_events=failed_move_events,
        consecutive_protection=consecutive_protection,
        mechanics_used=mechanics,
        fields_started=fields,
        observable_events=observable_events,
        warnings=warnings,
    )


def load_result(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditError(f"No se pudo leer {path}: {error}") from error
    if result.get("mode") != "benchmark":
        raise AuditError("La auditoría requiere un resultado en modo benchmark.")
    if result.get("schemaVersion", 0) < 4:
        raise AuditError("La auditoría requiere schemaVersion 4 o posterior.")
    return result


def index_replays(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    indexed: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".html"):
            continue
        matches = BATTLE_TAG_PATTERN.findall(Path(info.filename).name)
        if not matches:
            continue
        battle_tag = matches[-1]
        if battle_tag in indexed:
            raise AuditError(f"El ZIP repite el replay {battle_tag}.")
        indexed[battle_tag] = info
    return indexed


def vgc_team_id(battle: dict[str, Any]) -> str:
    side = battle["benchmark"]["vgcBenchSide"]
    return battle["pairing"][f"{side}TeamId"]


def opponent_team_id(battle: dict[str, Any]) -> str:
    side = "beta" if battle["benchmark"]["vgcBenchSide"] == "alpha" else "alpha"
    return battle["pairing"][f"{side}TeamId"]


def pair_key(battle: dict[str, Any]) -> tuple[str, int]:
    benchmark = battle["benchmark"]
    return benchmark["baselineId"], int(benchmark["scheduleIndex"]) // 2


def _record_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(records)
    wins = sum(item["winnerAgent"] == "vgcBench" for item in records)
    losses = sum(item["winnerAgent"] == "baseline" for item in records)
    ties = len(records) - wins - losses
    return {
        "games": len(records),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "winPercent": round(wins / len(records) * 100, 2) if records else 0.0,
    }


def _facts_aggregate(
    battles: Sequence[dict[str, Any]], facts: dict[str, ReplayFacts]
) -> dict[str, Any]:
    lead_rows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    moves_all: Counter[str] = Counter()
    moves_wins: Counter[str] = Counter()
    moves_losses: Counter[str] = Counter()
    observed_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    lead_pokemon_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {"games": 0, "wins": 0, "losses": 0, "ties": 0}
    )
    mechanics_all: Counter[str] = Counter()
    mechanics_wins: Counter[str] = Counter()
    mechanics_losses: Counter[str] = Counter()
    event_patterns: Counter[str] = Counter()
    event_patterns_by_move: Counter[str] = Counter()
    indicators: Counter[str] = Counter()
    for battle in battles:
        replay = facts.get(battle["battleTag"])
        if replay is None:
            continue
        lead = lead_rows[replay.lead_key]
        lead["games"] += 1
        outcome = (
            "wins"
            if battle["winnerAgent"] == "vgcBench"
            else "losses"
            if battle["winnerAgent"] == "baseline"
            else "ties"
        )
        lead[outcome] += 1
        moves_all.update(replay.move_counts)
        (moves_wins if outcome == "wins" else moves_losses).update(replay.move_counts)
        for pokemon in replay.leads:
            lead_pokemon_rows[pokemon]["games"] += 1
            lead_pokemon_rows[pokemon][outcome] += 1
        for pokemon in replay.observed_pokemon:
            observed_rows[pokemon]["games"] += 1
            observed_rows[pokemon][outcome] += 1
        mechanics_all.update(replay.mechanics_used)
        (mechanics_wins if outcome == "wins" else mechanics_losses).update(
            replay.mechanics_used
        )
        for event in replay.observable_events:
            event_patterns[
                f"{event['type']}: {event['move']} -> {event['target']}"
            ] += 1
            event_patterns_by_move[f"{event['type']}: {event['move']}"] += 1
        if replay.turns <= 5 and outcome == "losses":
            indicators["shortLosses"] += 1
        if replay.first_faint_conceded and outcome == "losses":
            indicators["lossesConcedingFirstFaint"] += 1
        indicators["attacksIntoProtection"] += replay.attacks_into_protection
        indicators["immunityEvents"] += replay.immunity_events
        indicators["failedMoveEvents"] += replay.failed_move_events
        indicators["consecutiveProtection"] += replay.consecutive_protection
        indicators["unableToMove"] += replay.unable_to_move
    leads = []
    for label, row in lead_rows.items():
        leads.append(
            {
                "lead": label,
                **row,
                "winPercent": round(row["wins"] / row["games"] * 100, 2),
            }
        )
    leads.sort(key=lambda row: (-row["games"], -row["winPercent"], row["lead"]))
    lead_pokemon = [
        {
            "pokemon": pokemon,
            **row,
            "winPercent": round(row["wins"] / row["games"] * 100, 2),
        }
        for pokemon, row in lead_pokemon_rows.items()
    ]
    lead_pokemon.sort(
        key=lambda row: (-row["games"], -row["winPercent"], row["pokemon"])
    )
    observed_pokemon = [
        {
            "pokemon": pokemon,
            **row,
            "winPercent": round(row["wins"] / row["games"] * 100, 2),
        }
        for pokemon, row in observed_rows.items()
    ]
    observed_pokemon.sort(
        key=lambda row: (-row["games"], -row["winPercent"], row["pokemon"])
    )
    return {
        "leadPerformance": leads,
        "leadPokemonPerformance": lead_pokemon,
        "moveUsage": {
            "all": dict(moves_all.most_common()),
            "wins": dict(moves_wins.most_common()),
            "losses": dict(moves_losses.most_common()),
        },
        "observedPokemonPerformance": observed_pokemon,
        "mechanicsUsage": {
            "all": dict(mechanics_all.most_common()),
            "wins": dict(mechanics_wins.most_common()),
            "losses": dict(mechanics_losses.most_common()),
        },
        "eventPatterns": dict(event_patterns.most_common()),
        "eventPatternsByMove": dict(event_patterns_by_move.most_common()),
        "automaticIndicators": dict(indicators),
    }


def diagnostic_findings(
    *,
    record: dict[str, Any],
    paired: dict[str, Any],
    observations: dict[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    losses = record["losses"]
    if paired["lossMirrorWinPercent"] >= 60:
        findings.append(
            {
                "code": "mirror-asymmetry",
                "confidence": "alta",
                "evidence": (
                    f"{paired['lossMirrorWin']}/{paired['losses']} derrotas "
                    "cambiaron a victoria cuando VGC-Bench recibió el otro equipo."
                ),
                "interpretation": (
                    "El problema está fuertemente asociado con la compatibilidad "
                    "entre la política y este equipo."
                ),
            }
        )
    leads = observations["leadPerformance"]
    if leads:
        dominant = leads[0]
        share = dominant["games"] / record["games"] * 100
        if share >= 40 and dominant["winPercent"] < record["winPercent"]:
            findings.append(
                {
                    "code": "dominant-lead-underperforms",
                    "confidence": "alta",
                    "evidence": (
                        f"{dominant['lead']} apareció en {dominant['games']}/"
                        f"{record['games']} partidas ({share:.2f}%) y ganó "
                        f"{dominant['winPercent']:.2f}%."
                    ),
                    "interpretation": (
                        "Team Preview concentra demasiadas partidas en un lead "
                        "que rinde por debajo del registro del equipo."
                    ),
                }
            )
        alternatives = [
            row for row in leads[1:] if row["games"] >= 3 and row["winPercent"] >= 50
        ]
        if alternatives:
            best = max(alternatives, key=lambda row: row["winPercent"])
            findings.append(
                {
                    "code": "promising-alternative-lead",
                    "confidence": "moderada",
                    "evidence": (
                        f"{best['lead']} ganó {best['wins']}/{best['games']} "
                        f"({best['winPercent']:.2f}%)."
                    ),
                    "interpretation": (
                        "Hay una alternativa de Preview prometedora, aunque la "
                        "muestra todavía es pequeña."
                    ),
                }
            )
    first_faint = observations["automaticIndicators"].get(
        "lossesConcedingFirstFaint", 0
    )
    if losses and first_faint / losses >= 0.7:
        findings.append(
            {
                "code": "early-tempo-loss",
                "confidence": "alta",
                "evidence": (
                    f"VGC-Bench recibió la primera baja en {first_faint}/"
                    f"{losses} derrotas ({first_faint / losses * 100:.2f}%)."
                ),
                "interpretation": (
                    "El patrón empieza antes del cierre de partida y justifica "
                    "revisar selección inicial y primeros turnos."
                ),
            }
        )
    rarely_seen = [
        row
        for row in observations["observedPokemonPerformance"]
        if row["games"] / record["games"] <= 0.1
    ]
    if rarely_seen:
        details = ", ".join(
            f"{row['pokemon']} {row['games']}/{record['games']}" for row in rarely_seen
        )
        findings.append(
            {
                "code": "roster-underused",
                "confidence": "alta",
                "evidence": f"Uso observado muy bajo: {details}.",
                "interpretation": (
                    "La política explora una fracción estrecha del roster; el "
                    "replay no permite distinguir elección de cuatro y Pokémon "
                    "seleccionado que nunca entró."
                ),
            }
        )
    event_patterns = observations["eventPatternsByMove"]
    if event_patterns:
        top_patterns = list(event_patterns.items())[:3]
        findings.append(
            {
                "code": "observable-action-failures",
                "confidence": "alta",
                "evidence": "; ".join(
                    f"{pattern} ({count})" for pattern, count in top_patterns
                )
                + ".",
                "interpretation": (
                    "Estos turnos tienen evidencia protocolaria concreta y deben "
                    "encabezar la revisión manual."
                ),
            }
        )
    return findings


def _case_score(case: dict[str, Any], replay: ReplayFacts | None) -> int:
    score = 5 if case["mirrorWinnerAgent"] == "vgcBench" else 0
    if replay is None:
        return score
    score += 2 if replay.turns <= 5 else 0
    score += 2 if replay.first_faint_conceded else 0
    score += replay.attacks_into_protection
    score += replay.immunity_events * 3
    score += replay.failed_move_events * 2
    score += replay.consecutive_protection * 2
    return score


def select_representative_cases(
    cases: Sequence[dict[str, Any]], sample_size: int
) -> list[dict[str, Any]]:
    if sample_size < 1:
        raise AuditError("sample_size debe ser mayor que cero.")
    ranked = sorted(
        cases,
        key=lambda case: (
            -case["reviewScore"],
            case["opponentTeamId"],
            case["scheduleIndex"],
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_tags: set[str] = set()
    seen_opponents: set[str] = set()
    for case in ranked:
        if case["opponentTeamId"] in seen_opponents:
            continue
        selected.append(case)
        selected_tags.add(case["battleTag"])
        seen_opponents.add(case["opponentTeamId"])
        if len(selected) >= sample_size:
            return selected
    for case in ranked:
        if case["battleTag"] in selected_tags:
            continue
        selected.append(case)
        if len(selected) >= sample_size:
            break
    return selected


def build_audit(
    result: dict[str, Any],
    replay_html_by_tag: dict[str, str],
    *,
    team_id: str,
    baseline_id: str,
    sample_size: int,
) -> dict[str, Any]:
    teams = {item["id"]: item for item in result["teams"]["items"]}
    if team_id not in teams:
        raise AuditError(f"El resultado no contiene el equipo {team_id}.")
    opponents = result.get("benchmark", {}).get("opponents", {})
    if baseline_id not in opponents:
        raise AuditError(f"El resultado no contiene el baseline {baseline_id}.")

    baseline_battles = [
        item
        for item in result["battles"]["items"]
        if item["benchmark"]["baselineId"] == baseline_id
    ]
    pairs: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for battle in baseline_battles:
        pairs[pair_key(battle)].append(battle)
    target_battles = [
        battle for battle in baseline_battles if vgc_team_id(battle) == team_id
    ]
    if not target_battles:
        raise AuditError(
            f"VGC-Bench no utilizó {team_id} contra {baseline_id} en este resultado."
        )

    relevant_tags = {battle["battleTag"] for battle in target_battles}
    for battle in target_battles:
        relevant_tags.update(item["battleTag"] for item in pairs[pair_key(battle)])
    facts: dict[str, ReplayFacts] = {}
    parse_errors: list[dict[str, str]] = []
    battles_by_tag = {
        battle["battleTag"]: battle
        for battle in baseline_battles
        if battle["battleTag"] in relevant_tags
    }
    for battle_tag in sorted(relevant_tags):
        replay_html = replay_html_by_tag.get(battle_tag)
        if replay_html is None:
            parse_errors.append({"battleTag": battle_tag, "error": "Replay ausente"})
            continue
        try:
            facts[battle_tag] = parse_replay_html(
                replay_html, battles_by_tag[battle_tag]
            )
        except (AuditError, KeyError, ValueError) as error:
            parse_errors.append({"battleTag": battle_tag, "error": str(error)})

    losses = [
        battle
        for battle in target_battles
        if battle["winnerAgent"] == "baseline"
    ]
    all_cases: list[dict[str, Any]] = []
    mirror_wins = 0
    both_losses = 0
    mirror_ties = 0
    for battle in losses:
        mirror = next(
            (item for item in pairs[pair_key(battle)] if item is not battle), None
        )
        if mirror is None:
            mirror_winner = "missing"
        else:
            mirror_winner = mirror["winnerAgent"]
            if mirror_winner == "vgcBench":
                mirror_wins += 1
            elif mirror_winner == "baseline":
                both_losses += 1
            else:
                mirror_ties += 1
        replay = facts.get(battle["battleTag"])
        case = {
            "battleTag": battle["battleTag"],
            "mirrorBattleTag": mirror["battleTag"] if mirror else None,
            "scheduleIndex": battle["benchmark"]["scheduleIndex"],
            "pairIndex": battle["benchmark"]["scheduleIndex"] // 2,
            "opponentTeamId": opponent_team_id(battle),
            "vgcBenchSide": battle["benchmark"]["vgcBenchSide"],
            "turns": battle["turns"],
            "mirrorWinnerAgent": mirror_winner,
            "mirrorTurns": mirror["turns"] if mirror else None,
            "leads": replay.leads if replay else [],
            "observedPokemon": replay.observed_pokemon if replay else [],
            "moveCounts": replay.move_counts if replay else {},
            "firstFaintTurn": replay.first_faint_turn if replay else None,
            "firstFaintConceded": (
                replay.first_faint_conceded if replay else None
            ),
            "mechanicsUsed": replay.mechanics_used if replay else [],
            "fieldsStarted": replay.fields_started if replay else [],
            "observableEvents": replay.observable_events if replay else [],
            "evidenceFlags": replay.evidence_flags if replay else ["replay-ausente"],
        }
        if mirror_winner == "vgcBench":
            case["evidenceFlags"] = ["espejo-favorable", *case["evidenceFlags"]]
        case["reviewScore"] = _case_score(case, replay)
        all_cases.append(case)

    selected = select_representative_cases(all_cases, min(sample_size, len(all_cases)))
    metadata = teams[team_id]
    aggregate = _facts_aggregate(target_battles, facts)
    loss_count = len(losses)
    asymmetry_percent = mirror_wins / loss_count * 100 if loss_count else 0.0
    record = _record_summary(target_battles)
    paired_evidence = {
        "losses": loss_count,
        "lossMirrorWin": mirror_wins,
        "lossMirrorWinPercent": round(asymmetry_percent, 2),
        "bothVgcBenchLose": both_losses,
        "lossMirrorTieOrMissing": mirror_ties
        + sum(case["mirrorWinnerAgent"] == "missing" for case in all_cases),
        "interpretation": (
            "Señal fuerte de incompatibilidad política-equipo"
            if asymmetry_percent >= 60
            else "Señal moderada; requiere revisar los casos"
            if asymmetry_percent >= 40
            else "El espejo no aísla una incompatibilidad clara"
        ),
    }
    return {
        "schemaVersion": 1,
        "auditType": "paired-mirrored-replay-audit",
        "sourceRun": {
            "runId": result["runId"],
            "createdAt": result["createdAt"],
            "projectCommit": result.get("projectCommit"),
            "scheduleSha256": result["benchmark"]["schedule"]["sha256"],
        },
        "scope": {
            "teamId": team_id,
            "baselineId": baseline_id,
            "sampleSize": len(selected),
            "team": metadata,
            "baseline": opponents[baseline_id],
        },
        "record": record,
        "pairedEvidence": paired_evidence,
        "replayCoverage": {
            "expected": len(relevant_tags),
            "parsed": len(facts),
            "missingOrInvalid": len(parse_errors),
            "errors": parse_errors,
        },
        "observations": aggregate,
        "diagnosticFindings": diagnostic_findings(
            record=record, paired=paired_evidence, observations=aggregate
        ),
        "selectedCases": selected,
        "allLossCases": sorted(
            all_cases, key=lambda case: (-case["reviewScore"], case["scheduleIndex"])
        ),
        "limitations": [
            "Los indicadores automáticos son hechos observables, no juicios tácticos.",
            "El replay no conserva logits, valor de estado ni alternativas descartadas.",
            "Los Pokémon seleccionados que nunca entran al campo pueden quedar ocultos.",
            "El baseline usa el Team Preview aleatorio predeterminado de poke-env.",
            "El espejo intercambia equipos y lados, pero el RNG de Showdown no se fija.",
        ],
    }


def _markdown_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    rendered = ["| " + " | ".join(headers) + " |"]
    rendered.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        rendered.append(
            "| "
            + " | ".join(str(value).replace("|", "\\|") for value in row)
            + " |"
        )
    return "\n".join(rendered)


def _format_events(events: Sequence[dict[str, Any]]) -> str:
    return (
        "; ".join(
            "T{turn} {move} → {target} ({type})".format(**event)
            for event in events
        )
        or "ninguno"
    )


def render_markdown(audit: dict[str, Any]) -> str:
    scope = audit["scope"]
    record = audit["record"]
    paired = audit["pairedEvidence"]
    indicators = audit["observations"]["automaticIndicators"]
    leads = audit["observations"]["leadPerformance"]
    cases = audit["selectedCases"]
    return "\n".join(
        [
            f"# Auditoría {scope['teamId']} vs {scope['baselineId']}",
            "",
            "## Diagnóstico ejecutivo",
            "",
            f"- Registro: **{record['wins']}-{record['losses']}-{record['ties']}** "
            f"({record['winPercent']:.2f}% de victorias).",
            f"- {paired['lossMirrorWin']} de {paired['losses']} derrotas "
            f"({paired['lossMirrorWinPercent']:.2f}%) tuvieron victoria de "
            "VGC-Bench en el combate espejo.",
            f"- Lectura: **{paired['interpretation']}**.",
            f"- Cobertura: {audit['replayCoverage']['parsed']}/"
            f"{audit['replayCoverage']['expected']} replays relevantes.",
            "",
            "> Los indicadores son señales de revisión; no demuestran por sí solos que una decisión fue incorrecta.",
            "",
            "## Hallazgos diagnósticos",
            "",
            *[
                (
                    f"- **{item['code']} ({item['confidence']})**: "
                    f"{item['evidence']} {item['interpretation']}"
                )
                for item in audit["diagnosticFindings"]
            ],
            "",
            "## Indicadores observables",
            "",
            _markdown_table(
                ["Indicador", "Cantidad"],
                [(key, value) for key, value in indicators.items()],
            ),
            "",
            "## Leads observados",
            "",
            _markdown_table(
                ["Lead", "Partidas", "W-L-T", "Win rate"],
                [
                    (
                        row["lead"],
                        row["games"],
                        f"{row['wins']}-{row['losses']}-{row['ties']}",
                        f"{row['winPercent']:.2f}%",
                    )
                    for row in leads
                ],
            ),
            "",
            "## Casos prioritarios",
            "",
            _markdown_table(
                [
                    "Replay",
                    "Rival",
                    "Turnos",
                    "Espejo",
                    "Lead",
                    "Señales",
                    "Eventos",
                ],
                [
                    (
                        f"[{case['battleTag']}](replays/{case['battleTag']}.html)",
                        case["opponentTeamId"],
                        case["turns"],
                        case["mirrorWinnerAgent"],
                        " + ".join(case["leads"]) or "No recuperado",
                        ", ".join(case["evidenceFlags"]) or "sin señal automática",
                        _format_events(case["observableEvents"]),
                    )
                    for case in cases
                ],
            ),
            "",
            "## Límites",
            "",
            *[f"- {item}" for item in audit["limitations"]],
            "",
        ]
    )


def render_html(audit: dict[str, Any]) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    scope = audit["scope"]
    record = audit["record"]
    paired = audit["pairedEvidence"]
    indicators = audit["observations"]["automaticIndicators"]
    lead_rows = "".join(
        "<tr>"
        f"<td>{esc(row['lead'])}</td><td>{row['games']}</td>"
        f"<td>{row['wins']}-{row['losses']}-{row['ties']}</td>"
        f"<td>{row['winPercent']:.2f}%</td></tr>"
        for row in audit["observations"]["leadPerformance"]
    )
    indicator_cards = "".join(
        f'<div class="metric"><strong>{value}</strong><span>{esc(key)}</span></div>'
        for key, value in indicators.items()
    )
    finding_cards = "".join(
        (
            '<div class="finding">'
            f"<strong>{esc(item['code'])} · confianza {esc(item['confidence'])}</strong>"
            f"<p>{esc(item['evidence'])}</p><p>{esc(item['interpretation'])}</p>"
            "</div>"
        )
        for item in audit["diagnosticFindings"]
    )
    case_rows = "".join(
        "<tr>"
        f'<td><a href="replays/{esc(case["battleTag"])}.html">{esc(case["battleTag"])}</a></td>'
        f"<td>{esc(case['opponentTeamId'])}</td><td>{case['turns']}</td>"
        f'<td><a href="replays/{esc(case["mirrorBattleTag"])}.html">{esc(case["mirrorWinnerAgent"])}</a></td>'
        f"<td>{esc(' + '.join(case['leads']) or 'No recuperado')}</td>"
        f"<td>{esc(', '.join(case['evidenceFlags']) or 'sin señal automática')}</td>"
        f"<td>{esc(_format_events(case['observableEvents']))}</td>"
        "</tr>"
        for case in audit["selectedCases"]
    )
    limitations = "".join(f"<li>{esc(item)}</li>" for item in audit["limitations"])
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Auditoría {esc(scope['teamId'])} · Battle Lab</title>
<style>
:root{{--bg:#07111f;--panel:#0f172a;--line:#164e63;--text:#e2e8f0;--muted:#94a3b8;--cyan:#67e8f9;--gold:#fbbf24}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1180px;margin:auto;padding:32px 20px 70px}} h1{{color:var(--cyan);margin-bottom:4px}} h2{{margin-top:32px}}
.muted{{color:var(--muted)}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}}
.metric{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column}}
.metric strong{{color:var(--gold);font-size:24px}} .metric span{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}}
.callout{{background:#422006;border:1px solid #a16207;border-radius:12px;padding:14px}}
.findings{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}} .finding{{background:var(--panel);border-left:4px solid #8b5cf6;border-radius:10px;padding:14px}} .finding p{{margin:7px 0 0}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px}} table{{width:100%;border-collapse:collapse;background:var(--panel)}}
th,td{{padding:10px 12px;border-bottom:1px solid #1e293b;text-align:left;vertical-align:top}} th{{color:var(--muted)}} a{{color:var(--cyan)}} code{{color:#c4b5fd}}
</style></head><body><main>
<h1>🐉 Auditoría {esc(scope['teamId'])} vs {esc(scope['baselineId'])}</h1>
<div class="muted">Run <code>{esc(audit['sourceRun']['runId'])}</code> · muestra {scope['sampleSize']} casos</div>
<div class="cards">
<div class="metric"><strong>{record['wins']}-{record['losses']}</strong><span>registro con el equipo</span></div>
<div class="metric"><strong>{record['winPercent']:.2f}%</strong><span>victorias</span></div>
<div class="metric"><strong>{paired['lossMirrorWin']}/{paired['losses']}</strong><span>derrotas cuyo espejo ganó</span></div>
<div class="metric"><strong>{paired['lossMirrorWinPercent']:.2f}%</strong><span>asimetría espejada</span></div>
</div>
<div class="callout"><strong>{esc(paired['interpretation'])}.</strong> Los indicadores siguientes son evidencia observable para priorizar revisión; no son un veredicto táctico automático.</div>
<h2>Hallazgos diagnósticos</h2><div class="findings">{finding_cards}</div>
<h2>Indicadores observables</h2><div class="cards">{indicator_cards}</div>
<h2>Leads observados</h2><div class="table-wrap"><table><thead><tr><th>Lead</th><th>Partidas</th><th>W-L-T</th><th>Win rate</th></tr></thead><tbody>{lead_rows}</tbody></table></div>
<h2>Casos prioritarios</h2><div class="table-wrap"><table><thead><tr><th>Replay</th><th>Rival</th><th>Turnos</th><th>Espejo</th><th>Lead</th><th>Señales</th><th>Eventos</th></tr></thead><tbody>{case_rows}</tbody></table></div>
<h2>Límites de la lectura</h2><ul>{limitations}</ul>
</main></body></html>"""


def write_outputs(
    audit: dict[str, Any],
    output_dir: Path,
    replay_bytes_by_tag: dict[str, bytes],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    replay_dir = output_dir / "replays"
    replay_dir.mkdir(parents=True, exist_ok=True)
    selected_tags: set[str] = set()
    for case in audit["selectedCases"]:
        selected_tags.add(case["battleTag"])
        if case["mirrorBattleTag"]:
            selected_tags.add(case["mirrorBattleTag"])
    for battle_tag in sorted(selected_tags):
        if BATTLE_TAG_PATTERN.fullmatch(battle_tag) is None:
            raise AuditError(f"Battle tag no seguro: {battle_tag!r}")
        content = replay_bytes_by_tag.get(battle_tag)
        if content is not None:
            (replay_dir / f"{battle_tag}.html").write_bytes(content)

    json_path = output_dir / "audit.json"
    markdown_path = output_dir / "report.md"
    html_path = output_dir / "report.html"
    csv_path = output_dir / "cases.csv"
    json_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(audit), encoding="utf-8")
    html_path.write_text(render_html(audit), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "battleTag",
                "mirrorBattleTag",
                "opponentTeamId",
                "turns",
                "mirrorWinnerAgent",
                "leads",
                "evidenceFlags",
                "observableEvents",
                "reviewScore",
            ],
        )
        writer.writeheader()
        for case in audit["allLossCases"]:
            writer.writerow(
                {
                    **{key: case[key] for key in writer.fieldnames},
                    "leads": " + ".join(case["leads"]),
                    "evidenceFlags": ",".join(case["evidenceFlags"]),
                    "observableEvents": json.dumps(
                        case["observableEvents"], ensure_ascii=False
                    ),
                }
            )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "html": str(html_path),
        "csv": str(csv_path),
        "replays": str(replay_dir),
    }


def run_audit(
    *,
    result_json: Path,
    replays_zip: Path,
    output_dir: Path,
    team_id: str,
    baseline_id: str,
    sample_size: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    progress = AuditProgress()
    result = load_result(result_json)
    progress.advance(1, "Resultado validado")
    if not replays_zip.is_file():
        raise AuditError(f"No existe el ZIP de replays: {replays_zip}")
    with zipfile.ZipFile(replays_zip) as archive:
        indexed = index_replays(archive)
        progress.advance(2, f"{len(indexed)} replays indexados")
        relevant_battles = [
            item
            for item in result["battles"]["items"]
            if item["benchmark"]["baselineId"] == baseline_id
            and (
                vgc_team_id(item) == team_id
                or opponent_team_id(item) == team_id
            )
        ]
        relevant_tags = {item["battleTag"] for item in relevant_battles}
        replay_bytes = {
            tag: archive.read(indexed[tag])
            for tag in relevant_tags
            if tag in indexed
        }
    replay_html = {
        tag: content.decode("utf-8", errors="replace")
        for tag, content in replay_bytes.items()
    }
    progress.advance(3, f"{len(replay_html)} replays relevantes leídos")
    audit = build_audit(
        result,
        replay_html,
        team_id=team_id,
        baseline_id=baseline_id,
        sample_size=sample_size,
    )
    progress.advance(4, "Pares espejados y señales analizados")
    paths = write_outputs(audit, output_dir, replay_bytes)
    audit["artifacts"] = paths
    Path(paths["json"]).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    progress.advance(5, "Reporte HTML/JSON/CSV listo")
    return audit, paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--replays-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--team-id", default=DEFAULT_TEAM_ID)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE_ID)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sample_size < 1:
        raise SystemExit("--sample-size debe ser mayor que cero.")
    audit, paths = run_audit(
        result_json=args.result_json.resolve(),
        replays_zip=args.replays_zip.resolve(),
        output_dir=args.output_dir.resolve(),
        team_id=args.team_id.upper(),
        baseline_id=args.baseline,
        sample_size=args.sample_size,
    )
    record = audit["record"]
    paired = audit["pairedEvidence"]
    print("\n✅ Auditoría automática completada")
    print(
        f"   {audit['scope']['teamId']} vs {audit['scope']['baselineId']}: "
        f"{record['wins']}-{record['losses']}-{record['ties']} · "
        f"{record['winPercent']:.2f}%"
    )
    print(
        f"   Espejo favorable tras derrota: {paired['lossMirrorWin']}/"
        f"{paired['losses']} · {paired['lossMirrorWinPercent']:.2f}%"
    )
    print(f"   Lectura: {paired['interpretation']}")
    print(f"   Reporte: {paths['html']}")
    print(f"   Datos: {paths['json']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AuditError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Auditoría fallida: {error}") from error
