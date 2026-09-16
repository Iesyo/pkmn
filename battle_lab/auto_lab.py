"""Headless LIGHT-vs-LIGHT gauntlet used by War Room Auto Lab.

Auto Lab compares one baseline team and small variants against the exact same
opponent pool and side allocation. The score is an internal relative benchmark
under the frozen LIGHT M-C policy; it is deliberately not presented as a ladder
win-rate prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from battle_lab import vgc_bench_battle as battle
from battle_lab.benchmarking import summarize_vgc_bench_record
from battle_lab.team_corpus import (
    TeamPairing,
    TeamRecord,
    extract_roster,
    normalize_team_text,
    team_sha256,
)


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class AutoLabTeam:
    id: str
    label: str
    team_text: str

    def record(self, *, origin: str) -> TeamRecord:
        normalized = normalize_team_text(self.team_text)
        roster = extract_roster(normalized)
        if len(roster) != 6:
            raise ValueError(f"{self.label} contiene {len(roster)} Pokémon; se requieren 6.")
        return TeamRecord(
            id=self.id,
            description=self.label,
            player="War Room Auto Lab",
            team_text=normalized,
            sha256=team_sha256(normalized),
            roster=roster,
            origin=origin,
            path=Path(f"auto-lab/{self.id}.team"),
            metadata={"autoLab": True},
        )


def build_candidate_schedule(
    candidate: TeamRecord,
    opponents: Sequence[TeamRecord],
    *,
    battles_per_opponent: int,
) -> list[TeamPairing]:
    """Give every candidate the same opponent order and balanced Alpha/Beta sides."""

    if not opponents:
        raise ValueError("Auto Lab requiere al menos un rival.")
    if battles_per_opponent < 2 or battles_per_opponent % 2:
        raise ValueError("battles_per_opponent debe ser par y >= 2.")

    schedule: list[TeamPairing] = []
    for opponent in opponents:
        for battle_index in range(battles_per_opponent):
            if battle_index % 2 == 0:
                schedule.append(TeamPairing(alpha=candidate, beta=opponent))
            else:
                schedule.append(TeamPairing(alpha=opponent, beta=candidate))
    return schedule


def summarize_candidate(
    candidate_id: str,
    summaries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    wins = losses = ties = 0
    by_opponent: dict[str, dict[str, int]] = {}

    for summary in summaries:
        pairing = summary.get("pairing", {})
        alpha_id = str(pairing.get("alphaTeamId") or "")
        beta_id = str(pairing.get("betaTeamId") or "")
        if candidate_id not in {alpha_id, beta_id}:
            raise RuntimeError(
                f"La batalla {summary.get('battleTag', '<sin tag>')} no contiene al candidato {candidate_id}."
            )
        candidate_side = "alpha" if alpha_id == candidate_id else "beta"
        opponent_id = beta_id if candidate_side == "alpha" else alpha_id
        row = by_opponent.setdefault(opponent_id, {"games": 0, "wins": 0, "losses": 0, "ties": 0})
        row["games"] += 1
        winner_side = str(summary.get("winnerSide") or "tie")
        if winner_side == candidate_side:
            wins += 1
            row["wins"] += 1
        elif winner_side == "tie":
            ties += 1
            row["ties"] += 1
        else:
            losses += 1
            row["losses"] += 1

    score = summarize_vgc_bench_record(wins=wins, losses=losses, ties=ties)
    for row in by_opponent.values():
        games = row["games"]
        row["scorePercent"] = round(100 * (row["wins"] + 0.5 * row["ties"]) / games, 2) if games else 0.0

    return {
        **score,
        "byOpponent": by_opponent,
    }


def compare_with_baseline(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    delta = round(float(candidate["scorePercent"]) - float(baseline["scorePercent"]), 2)
    common = sorted(set(baseline.get("byOpponent", {})) & set(candidate.get("byOpponent", {})))
    improved = regressed = tied = 0
    for opponent_id in common:
        before = float(baseline["byOpponent"][opponent_id]["scorePercent"])
        after = float(candidate["byOpponent"][opponent_id]["scorePercent"])
        if after > before:
            improved += 1
        elif after < before:
            regressed += 1
        else:
            tied += 1

    # This is intentionally a screening gate, not a statistical-significance claim.
    # Auto Lab never mutates the saved team automatically; a positive result becomes
    # a promotion candidate that can be confirmed with a larger rerun.
    if delta > 0 and improved >= regressed:
        verdict = "improved"
    elif delta < 0 and regressed > improved:
        verdict = "regressed"
    else:
        verdict = "mixed"

    return {
        "deltaPercentagePoints": delta,
        "opponentsImproved": improved,
        "opponentsRegressed": regressed,
        "opponentsTied": tied,
        "verdict": verdict,
        "promotion": "candidate" if verdict == "improved" else "hold",
        "caveat": (
            "Benchmark relativo LIGHT-vs-LIGHT. Baseline y variante usan el mismo pool y lados; "
            "el RNG interno de Showdown no queda pareado entre ejecuciones."
        ),
    }


async def _notify(callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(payload)
    if result is not None:
        await result


async def run_auto_lab_gauntlet(
    *,
    runtime: battle.ModelRuntime,
    port: int,
    battle_format: str,
    baseline: AutoLabTeam,
    variants: Sequence[AutoLabTeam],
    opponents: Sequence[AutoLabTeam],
    battles_per_opponent: int,
    timeout: float,
    replay_root: Path,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Evaluate baseline + variants against identical opponents with frozen LIGHT."""

    if not variants:
        raise ValueError("Auto Lab requiere al menos una variante.")
    if len(variants) > 8:
        raise ValueError("Auto Lab admite como máximo 8 variantes por ejecución.")
    if len(opponents) > 24:
        raise ValueError("Auto Lab admite como máximo 24 rivales por ejecución.")

    candidate_records = [baseline.record(origin="auto-lab-baseline")]
    candidate_records.extend(item.record(origin="auto-lab-variant") for item in variants)
    opponent_records = [item.record(origin="auto-lab-opponent") for item in opponents]

    ids = [record.id for record in [*candidate_records, *opponent_records]]
    if len(ids) != len(set(ids)):
        raise ValueError("Los IDs de baseline, variantes y rivales deben ser únicos.")

    total_battles = len(candidate_records) * len(opponent_records) * battles_per_opponent
    completed_battles = 0
    reports: dict[str, dict[str, Any]] = {}

    for candidate_index, candidate in enumerate(candidate_records):
        all_summaries: list[dict[str, Any]] = []
        await _notify(progress, {
            "phase": "running",
            "candidateId": candidate.id,
            "candidateLabel": candidate.description,
            "candidateIndex": candidate_index,
            "candidateCount": len(candidate_records),
            "completedBattles": completed_battles,
            "totalBattles": total_battles,
        })

        for opponent in opponent_records:
            schedule = build_candidate_schedule(
                candidate,
                [opponent],
                battles_per_opponent=battles_per_opponent,
            )
            replay_dir = replay_root / candidate.id / opponent.id
            replay_dir.mkdir(parents=True, exist_ok=True)
            summaries, _wins, _aliases = await battle.run_vgc_bench_battles(
                runtime=runtime,
                port=port,
                battle_format=battle_format,
                schedule=schedule,
                timeout=timeout,
                replay_dir=replay_dir,
            )
            all_summaries.extend(summaries)
            completed_battles += len(summaries)
            await _notify(progress, {
                "phase": "running",
                "candidateId": candidate.id,
                "candidateLabel": candidate.description,
                "opponentId": opponent.id,
                "completedBattles": completed_battles,
                "totalBattles": total_battles,
            })

        reports[candidate.id] = {
            "id": candidate.id,
            "label": candidate.description,
            **summarize_candidate(candidate.id, all_summaries),
        }

    baseline_report = reports[baseline.id]
    variant_reports: list[dict[str, Any]] = []
    for variant in variants:
        report = reports[variant.id]
        variant_reports.append({
            **report,
            "comparison": compare_with_baseline(baseline_report, report),
        })
    variant_reports.sort(
        key=lambda item: (
            -float(item["comparison"]["deltaPercentagePoints"]),
            -float(item["scorePercent"]),
            str(item["label"]),
        )
    )

    return {
        "schemaVersion": 1,
        "benchmark": "light-mc-team-gauntlet",
        "policy": "LIGHT M-C on both sides",
        "battlesPerOpponent": battles_per_opponent,
        "opponents": [{"id": item.id, "label": item.label} for item in opponents],
        "totalBattles": total_battles,
        "baseline": baseline_report,
        "variants": variant_reports,
        "bestVariantId": variant_reports[0]["id"] if variant_reports and variant_reports[0]["comparison"]["verdict"] == "improved" else None,
        "caveat": (
            "El score sirve para comparar Team A contra Team A' bajo la misma política y pool; "
            "no estima el win rate real del jugador en ladder o torneo."
        ),
    }
