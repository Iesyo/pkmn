"""Headless LIGHT-vs-LIGHT Gauntlet and empirical audit for War Room Auto Lab.

Auto Lab keeps the frozen LIGHT M-C policy for turn decisions. Only the candidate
Team Preview is sampled so the same team is exercised through different brings
and leads. Baseline and complete-set variants face the same opponent pool, side
allocation and preview seeds. Results are compatibility benchmarks, not ladder
win-rate predictions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from battle_lab import vgc_bench_battle as battle
from battle_lab.auto_lab_audit import build_auto_lab_audit, classify_archetypes
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


def summarize_candidate(candidate_id: str, summaries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = ties = 0
    by_opponent: dict[str, dict[str, int | float]] = {}

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
        row["games"] = int(row["games"]) + 1
        winner_side = str(summary.get("winnerSide") or "tie")
        if winner_side == candidate_side:
            wins += 1
            row["wins"] = int(row["wins"]) + 1
        elif winner_side == "tie":
            ties += 1
            row["ties"] = int(row["ties"]) + 1
        else:
            losses += 1
            row["losses"] = int(row["losses"]) + 1

    score = summarize_vgc_bench_record(wins=wins, losses=losses, ties=ties)
    for row in by_opponent.values():
        games = int(row["games"])
        row["scorePercent"] = round(
            100 * (int(row["wins"]) + 0.5 * int(row["ties"])) / games,
            2,
        ) if games else 0.0
    return {**score, "byOpponent": by_opponent}


def compare_with_baseline(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
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
            "Benchmark relativo LIGHT-vs-LIGHT. Baseline y variante usan el mismo pool, lados y semillas de Preview; "
            "el RNG interno de Showdown no queda pareado."
        ),
    }


def _preview_seed(opponent_id: str, battle_index: int) -> int:
    digest = hashlib.sha256(
        f"auto-lab-preview-v2|{opponent_id}|{battle_index}".encode()
    ).hexdigest()
    return int(digest[:8], 16)


def _seed_torch(runtime: battle.ModelRuntime, seed: int) -> None:
    runtime.torch.manual_seed(seed)
    cuda = getattr(runtime.torch, "cuda", None)
    if cuda is not None and callable(getattr(cuda, "is_available", None)) and cuda.is_available():
        cuda.manual_seed_all(seed)


def _selected_preview(current_battle: Any) -> list[str]:
    output: list[str] = []
    for pokemon in getattr(current_battle, "team", {}).values():
        if not bool(getattr(pokemon, "selected_in_teampreview", False)):
            continue
        species = str(
            getattr(pokemon, "species", "")
            or getattr(pokemon, "name", "")
            or ""
        )
        if species and species not in output:
            output.append(species)
    return output[:4]


async def run_candidate_battles(
    *,
    runtime: battle.ModelRuntime,
    port: int,
    battle_format: str,
    schedule: Sequence[TeamPairing],
    candidate_id: str,
    timeout: float,
    replay_dir: Path,
) -> list[dict[str, Any]]:
    """Run one candidate schedule while sampling only that candidate's Team Preview."""

    from poke_env import AccountConfiguration, ServerConfiguration

    if not schedule:
        raise ValueError("La agenda de Auto Lab no puede estar vacía.")
    parent_class = runtime.player_class

    class AutoLabPreviewPlayer(parent_class):
        def __init__(self, *args: Any, **kwargs: Any):
            self.auto_lab_preview_sampling = False
            super().__init__(*args, **kwargs)

        def teampreview(self, current_battle: Any):
            if not self.auto_lab_preview_sampling:
                return super().teampreview(current_battle)
            previous = bool(getattr(self, "deterministic", True))
            self.deterministic = False
            try:
                return super().teampreview(current_battle)
            finally:
                self.deterministic = previous

    suffix = hashlib.sha256(f"{time.time_ns()}|{candidate_id}".encode()).hexdigest()[:6]
    server_configuration = ServerConfiguration(
        f"ws://127.0.0.1:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )
    common = {
        "battle_format": battle_format,
        "server_configuration": server_configuration,
        "max_concurrent_battles": 1,
        "accept_open_team_sheet": True,
        "log_level": logging.WARNING,
        "policy": runtime.policy,
        "deterministic": True,
    }
    replay_dir.mkdir(parents=True, exist_ok=True)
    player_a = AutoLabPreviewPlayer(
        account_configuration=AccountConfiguration(f"AutoAlpha{suffix}", None),
        team=schedule[0].alpha.team_text,
        save_replays=str(replay_dir),
        **common,
    )
    player_b = AutoLabPreviewPlayer(
        account_configuration=AccountConfiguration(f"AutoBeta{suffix}", None),
        team=schedule[0].beta.team_text,
        **common,
    )

    summaries: list[dict[str, Any]] = []
    try:
        for index, pairing in enumerate(schedule):
            player_a.update_team(pairing.alpha.team_text)
            player_b.update_team(pairing.beta.team_text)
            player_a.auto_lab_preview_sampling = pairing.alpha.id == candidate_id
            player_b.auto_lab_preview_sampling = pairing.beta.id == candidate_id
            opponent_id = (
                pairing.beta.id if pairing.alpha.id == candidate_id else pairing.alpha.id
            )
            _seed_torch(runtime, _preview_seed(opponent_id, index))

            previous_tags = set(player_a.battles)
            started = time.monotonic()
            await asyncio.wait_for(
                player_a.battle_against(player_b, n_battles=1),
                timeout=timeout,
            )
            new_tags = set(player_a.battles) - previous_tags
            if len(new_tags) != 1:
                raise RuntimeError(
                    f"Se esperaba una batalla y aparecieron {len(new_tags)}: {sorted(new_tags)}"
                )
            tag = new_tags.pop()
            alpha_battle = player_a.battles[tag]
            if not alpha_battle.finished:
                raise RuntimeError(f"La batalla {tag} no terminó.")
            beta_battle = player_b.battles.get(tag)
            summary = battle.battle_summary(
                alpha_battle,
                time.monotonic() - started,
                player_a.username,
                player_b.username,
            )
            summary["winnerSide"] = (
                "alpha"
                if summary["winner"] == player_a.username
                else "beta"
                if summary["winner"] == player_b.username
                else "tie"
            )
            summary["pairing"] = {
                "id": pairing.canonical_id,
                "alphaTeamId": pairing.alpha.id,
                "betaTeamId": pairing.beta.id,
            }
            summary["players"] = {
                "alpha": player_a.username,
                "beta": player_b.username,
            }
            summary["teamPreview"] = {
                "alpha": _selected_preview(alpha_battle),
                "beta": _selected_preview(beta_battle) if beta_battle is not None else [],
            }
            summary["previewSeed"] = _preview_seed(opponent_id, index)
            summaries.append(summary)
            player_a.reset_battles()
            player_b.reset_battles()
    finally:
        with suppress(Exception):
            await player_a.ps_client.stop_listening()
        with suppress(Exception):
            await player_b.ps_client.stop_listening()
    return summaries


async def _notify(
    callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
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
    """Evaluate the baseline, optional full-set variants, and audit the baseline."""

    if len(variants) > 8:
        raise ValueError("Auto Lab admite como máximo 8 variantes por ejecución.")
    if len(opponents) > 100:
        raise ValueError("Auto Lab admite como máximo 100 rivales por ejecución.")

    candidate_records = [baseline.record(origin="auto-lab-baseline")]
    candidate_records.extend(
        item.record(origin="auto-lab-variant") for item in variants
    )
    opponent_records = [
        item.record(origin="auto-lab-opponent") for item in opponents
    ]
    ids = [record.id for record in [*candidate_records, *opponent_records]]
    if len(ids) != len(set(ids)):
        raise ValueError("Los IDs de baseline, variantes y rivales deben ser únicos.")

    total_battles = (
        len(candidate_records) * len(opponent_records) * battles_per_opponent
    )
    completed_battles = 0
    reports: dict[str, dict[str, Any]] = {}
    summaries_by_candidate: dict[str, list[dict[str, Any]]] = {}

    for candidate_index, candidate in enumerate(candidate_records):
        all_summaries: list[dict[str, Any]] = []
        await _notify(
            progress,
            {
                "phase": "running",
                "candidateId": candidate.id,
                "candidateLabel": candidate.description,
                "candidateIndex": candidate_index,
                "candidateCount": len(candidate_records),
                "completedBattles": completed_battles,
                "totalBattles": total_battles,
            },
        )
        for opponent in opponent_records:
            await _notify(
                progress,
                {
                    "phase": "running",
                    "candidateId": candidate.id,
                    "candidateLabel": candidate.description,
                    "opponentId": opponent.id,
                    "completedBattles": completed_battles,
                    "totalBattles": total_battles,
                },
            )
            schedule = build_candidate_schedule(
                candidate,
                [opponent],
                battles_per_opponent=battles_per_opponent,
            )
            opponent_replays = replay_root / candidate.id / opponent.id
            summaries = await run_candidate_battles(
                runtime=runtime,
                port=port,
                battle_format=battle_format,
                schedule=schedule,
                candidate_id=candidate.id,
                timeout=timeout,
                replay_dir=opponent_replays,
            )
            all_summaries.extend(summaries)
            completed_battles += len(summaries)
            await _notify(
                progress,
                {
                    "phase": "running",
                    "candidateId": candidate.id,
                    "candidateLabel": candidate.description,
                    "opponentId": opponent.id,
                    "completedBattles": completed_battles,
                    "totalBattles": total_battles,
                },
            )
        summaries_by_candidate[candidate.id] = all_summaries
        reports[candidate.id] = {
            "id": candidate.id,
            "label": candidate.description,
            **summarize_candidate(candidate.id, all_summaries),
        }

    await _notify(
        progress,
        {
            "phase": "finalizing",
            "candidateId": baseline.id,
            "candidateLabel": baseline.label,
            "opponentId": "",
            "completedBattles": completed_battles,
            "totalBattles": total_battles,
        },
    )

    baseline_report = reports[baseline.id]
    variant_reports: list[dict[str, Any]] = []
    for variant in variants:
        report = reports[variant.id]
        variant_reports.append(
            {
                **report,
                "comparison": compare_with_baseline(baseline_report, report),
            }
        )
    variant_reports.sort(
        key=lambda item: (
            -float(item["comparison"]["deltaPercentagePoints"]),
            -float(item["scorePercent"]),
            str(item["label"]),
        )
    )

    opponent_meta = {
        record.id: {
            "id": record.id,
            "label": record.description,
            "roster": list(record.roster),
            "archetypes": classify_archetypes(record.team_text),
        }
        for record in opponent_records
    }
    audit = await asyncio.to_thread(
        build_auto_lab_audit,
        candidate_id=baseline.id,
        candidate_roster=list(candidate_records[0].roster),
        summaries=summaries_by_candidate[baseline.id],
        candidate_report=baseline_report,
        opponents=opponent_meta,
        replay_root=replay_root / baseline.id,
    )

    return {
        "schemaVersion": 2,
        "benchmark": "light-mc-team-gauntlet",
        "policy": "LIGHT M-C turns deterministic; candidate Team Preview sampled",
        "previewExploration": {
            "candidateOnly": True,
            "turnPolicyDeterministic": True,
            "seedAlignedAcrossCandidates": True,
        },
        "battlesPerOpponent": battles_per_opponent,
        "opponents": list(opponent_meta.values()),
        "totalBattles": total_battles,
        "baseline": baseline_report,
        "variants": variant_reports,
        "bestVariantId": (
            variant_reports[0]["id"]
            if variant_reports
            and variant_reports[0]["comparison"]["verdict"] == "improved"
            else None
        ),
        "audit": audit,
        "caveat": (
            "El score y la auditoría describen compatibilidad LIGHT-equipo contra este pool; "
            "no estiman el win rate real del jugador en ladder o torneo."
        ),
    }
