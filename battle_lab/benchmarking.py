"""Pure scheduling and rating helpers for the Battle Lab benchmark suite."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from battle_lab.team_corpus import TeamPairing


DEFAULT_BENCHMARK_BATTLES_PER_BASELINE = 500
INTERNAL_ELO_ANCHOR = 1500
ELO_SCALE = 400


@dataclass(frozen=True)
class BaselineSpec:
    id: str
    label: str
    class_name: str
    description: str

    def result_metadata(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "className": self.class_name,
            "description": self.description,
        }


BASELINE_SPECS = (
    BaselineSpec(
        id="random",
        label="Random",
        class_name="RandomPlayer",
        description="Elige una acción legal al azar.",
    ),
    BaselineSpec(
        id="max-base-power",
        label="Max Base Power",
        class_name="MaxBasePowerPlayer",
        description="Prioriza el movimiento con mayor potencia base.",
    ),
    BaselineSpec(
        id="simple-heuristics",
        label="Simple Heuristics",
        class_name="SimpleHeuristicsPlayer",
        description=(
            "Puntúa daño, precisión, STAB, tipos, HP, boosts y cambios con "
            "la lógica de dobles de poke-env."
        ),
    ),
)
BASELINE_BY_ID = {spec.id: spec for spec in BASELINE_SPECS}


@dataclass(frozen=True)
class BenchmarkBattlePlan:
    pairing: TeamPairing
    vgc_bench_side: Literal["alpha", "beta"]

    def result_metadata(self) -> dict[str, str]:
        return {
            "pairingId": self.pairing.canonical_id,
            "alphaTeamId": self.pairing.alpha.id,
            "betaTeamId": self.pairing.beta.id,
            "vgcBenchSide": self.vgc_bench_side,
        }


def build_mirrored_benchmark_schedule(
    pairings: Sequence[TeamPairing], *, count: int
) -> list[BenchmarkBattlePlan]:
    """Mirror each pairing so every policy plays both teams and both sides."""

    if count < 1:
        raise ValueError("count debe ser mayor que cero")
    required_pairings = (count + 1) // 2
    if len(pairings) < required_pairings:
        raise ValueError(
            f"se requieren {required_pairings} parejas base para {count} combates"
        )

    schedule: list[BenchmarkBattlePlan] = []
    for pairing in pairings[:required_pairings]:
        schedule.append(BenchmarkBattlePlan(pairing, "alpha"))
        if len(schedule) < count:
            schedule.append(BenchmarkBattlePlan(pairing, "beta"))
    return schedule


def benchmark_schedule_statistics(
    schedule: Sequence[BenchmarkBattlePlan],
) -> dict[str, Any]:
    """Return an auditable fingerprint and policy/team balance statistics."""

    if not schedule:
        raise ValueError("La agenda de benchmark no puede estar vacía.")
    serialized = [plan.result_metadata() for plan in schedule]
    assignments: dict[str, dict[str, int]] = {}
    for plan in schedule:
        for side, team in (
            ("alpha", plan.pairing.alpha),
            ("beta", plan.pairing.beta),
        ):
            policy = "vgcBench" if side == plan.vgc_bench_side else "baseline"
            entry = assignments.setdefault(
                team.id,
                {"total": 0, "vgcBench": 0, "baseline": 0, "alpha": 0, "beta": 0},
            )
            entry["total"] += 1
            entry[policy] += 1
            entry[side] += 1
    schedule_hash = hashlib.sha256(
        json.dumps(
            serialized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "mode": "paired-mirrored",
        "battlesPerBaseline": len(schedule),
        "basePairings": (len(schedule) + 1) // 2,
        "uniquePairings": len({plan.pairing.canonical_id for plan in schedule}),
        "vgcBenchSides": {
            "alpha": sum(plan.vgc_bench_side == "alpha" for plan in schedule),
            "beta": sum(plan.vgc_bench_side == "beta" for plan in schedule),
        },
        "teamAssignments": assignments,
        "sha256": schedule_hash,
    }


def _elo_difference(score: float) -> float | None:
    if score <= 0 or score >= 1:
        return None
    return ELO_SCALE * math.log10(score / (1 - score))


def _wilson_interval(
    points: float, games: int, z: float = 1.95996398454
) -> tuple[float, float]:
    score = points / games
    denominator = 1 + z * z / games
    center = (score + z * z / (2 * games)) / denominator
    margin = (
        z
        * math.sqrt(score * (1 - score) / games + z * z / (4 * games * games))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def summarize_vgc_bench_record(
    *, wins: int, losses: int, ties: int, anchor: int = INTERNAL_ELO_ANCHOR
) -> dict[str, Any]:
    """Summarize results and derive an explicitly internal Elo performance."""

    if min(wins, losses, ties) < 0:
        raise ValueError("wins, losses y ties no pueden ser negativos")
    games = wins + losses + ties
    if games < 1:
        raise ValueError("se requiere al menos un combate")
    points = wins + ties * 0.5
    score = points / games
    # One virtual draw prevents an infinite point estimate after a clean sweep.
    smoothed_score = (points + 0.5) / (games + 1)
    difference = _elo_difference(smoothed_score)
    assert difference is not None
    low_score, high_score = _wilson_interval(points, games)
    low_difference = _elo_difference(low_score)
    high_difference = _elo_difference(high_score)
    return {
        "games": games,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "points": round(points, 3),
        "score": round(score, 6),
        "scorePercent": round(score * 100, 2),
        "elo": {
            "model": "logistic-400",
            "opponentAnchor": anchor,
            "smoothing": "one-virtual-draw",
            "difference": round(difference, 1),
            "performanceRating": round(anchor + difference, 1),
            "confidence95": {
                "scoreLow": round(low_score, 6),
                "scoreHigh": round(high_score, 6),
                "differenceLow": (
                    round(low_difference, 1) if low_difference is not None else None
                ),
                "differenceHigh": (
                    round(high_difference, 1) if high_difference is not None else None
                ),
            },
        },
    }
