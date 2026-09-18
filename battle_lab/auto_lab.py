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
import math
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

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


@dataclass(frozen=True)
class AdaptiveSamplingPlan:
    """Two-stage budget: broad screening followed by targeted confirmation."""

    initial_battles_per_opponent: int
    deep_dive_opponents: int
    additional_battles_per_deep_dive: int

    def validate(self, opponent_count: int) -> None:
        if (
            not 2 <= self.initial_battles_per_opponent <= 20
            or self.initial_battles_per_opponent % 2
        ):
            raise ValueError("initial_battles_per_opponent debe ser par y estar entre 2 y 20.")
        if not 0 <= self.deep_dive_opponents <= opponent_count:
            raise ValueError(
                "deep_dive_opponents debe estar entre 0 y la cantidad de rivales."
            )
        if self.deep_dive_opponents == 0:
            if self.additional_battles_per_deep_dive != 0:
                raise ValueError(
                    "additional_battles_per_deep_dive debe ser 0 sin rivales profundizados."
                )
        elif (
            not 2 <= self.additional_battles_per_deep_dive <= 40
            or self.additional_battles_per_deep_dive % 2
        ):
            raise ValueError(
                "additional_battles_per_deep_dive debe ser par y estar entre 2 y 40."
            )

    def battles_per_candidate(self, opponent_count: int) -> int:
        self.validate(opponent_count)
        return (
            opponent_count * self.initial_battles_per_opponent
            + self.deep_dive_opponents * self.additional_battles_per_deep_dive
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


def _confidence95(row: Mapping[str, int | float]) -> dict[str, float]:
    """Wilson interval for match points, with ties worth half a point."""

    games = int(row.get("games", 0) or 0)
    if games < 1:
        return {"low": 0.0, "high": 100.0, "width": 100.0}
    points = float(row.get("wins", 0) or 0) + 0.5 * float(row.get("ties", 0) or 0)
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


def summarize_candidate(
    candidate_id: str,
    summaries: Sequence[dict[str, Any]],
    *,
    deep_dive_ids: Sequence[str] = (),
) -> dict[str, Any]:
    wins = losses = ties = 0
    by_opponent: dict[str, dict[str, int | float]] = {}
    deep_dive_set = set(deep_dive_ids)

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
        row["confidence95"] = _confidence95(row)
    for opponent_id, row in by_opponent.items():
        deep_dive = opponent_id in deep_dive_set
        row["deepDive"] = deep_dive
        row["evidenceLevel"] = "confirmed" if deep_dive else "screening"
    return {**score, "byOpponent": by_opponent}


def select_deep_dive_opponents(
    candidate_report: Mapping[str, Any],
    opponents: Mapping[str, Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Rank stage-one matchups by recurrent risk plus useful uncertainty.

    The primary risk term is prevalence × severity × repeatability × confidence.
    A smaller exploration term retains uncertain matchups, and a greedy diversity
    bonus prevents the confirmation budget from collapsing into one archetype.
    """

    by_opponent = candidate_report.get("byOpponent", {})
    if limit <= 0 or not isinstance(by_opponent, Mapping):
        return []
    opponent_ids = [str(value) for value in by_opponent]
    if not opponent_ids:
        return []

    overall = float(candidate_report.get("scorePercent", 0.0) or 0.0) / 100
    archetype_members: dict[str, list[str]] = {}
    weak_by_archetype: Counter[str] = Counter()
    for opponent_id in opponent_ids:
        meta = opponents.get(opponent_id, {})
        tags = [str(tag) for tag in meta.get("archetypes", [])] or ["Balance / Other"]
        score = float(by_opponent[opponent_id].get("scorePercent", 0.0) or 0.0) / 100
        for tag in tags:
            archetype_members.setdefault(tag, []).append(opponent_id)
            if score < max(0.45, overall - 0.05):
                weak_by_archetype[tag] += 1

    ranked: list[dict[str, Any]] = []
    for opponent_id in opponent_ids:
        row = by_opponent[opponent_id]
        meta = opponents.get(opponent_id, {})
        tags = [str(tag) for tag in meta.get("archetypes", [])] or ["Balance / Other"]
        score = float(row.get("scorePercent", 0.0) or 0.0) / 100
        interval = row.get("confidence95")
        if not isinstance(interval, Mapping):
            interval = _confidence95(row)
        width = float(interval.get("width", 100.0) or 100.0) / 100
        severity = min(1.0, max(0.0, (max(0.5, overall) - score) / 0.5))
        prevalence = max(
            len(archetype_members.get(tag, [])) / len(opponent_ids) for tag in tags
        )
        repeatability = max(
            weak_by_archetype[tag] / max(1, len(archetype_members.get(tag, [])))
            for tag in tags
        )
        confidence = max(0.0, min(1.0, 1 - width))
        recurrent_risk = prevalence * severity * repeatability * confidence
        priority = 0.65 * recurrent_risk + 0.2 * severity + 0.15 * width
        reasons: list[str] = []
        if severity >= 0.35:
            reasons.append(
                f"Severidad: {score * 100:.1f}% vs {overall * 100:.1f}% global."
            )
        recurrent_tag = max(
            tags,
            key=lambda tag: (
                weak_by_archetype[tag] / max(1, len(archetype_members.get(tag, []))),
                len(archetype_members.get(tag, [])),
                tag,
            ),
        )
        recurrent_count = weak_by_archetype[recurrent_tag]
        member_count = len(archetype_members.get(recurrent_tag, []))
        if recurrent_count >= 2:
            reasons.append(
                f"Repetición: {recurrent_count}/{member_count} rivales {recurrent_tag} quedaron bajo la referencia."
            )
        if width >= 0.45:
            reasons.append(
                f"Incertidumbre: IC95% {float(interval.get('low', 0)):.1f}–{float(interval.get('high', 100)):.1f}%."
            )
        if not reasons:
            reasons.append("Confirmación de cobertura para evitar depender de una sola pasada.")
        ranked.append(
            {
                "id": opponent_id,
                "label": meta.get("label", opponent_id),
                "archetypes": tags,
                "priorityScore": round(priority * 100, 2),
                "recurrentRiskScore": round(recurrent_risk * 100, 2),
                "components": {
                    "prevalence": round(prevalence, 4),
                    "severity": round(severity, 4),
                    "repeatability": round(repeatability, 4),
                    "confidence": round(confidence, 4),
                    "uncertainty": round(width, 4),
                },
                "screening": {
                    "games": int(row.get("games", 0) or 0),
                    "wins": int(row.get("wins", 0) or 0),
                    "losses": int(row.get("losses", 0) or 0),
                    "ties": int(row.get("ties", 0) or 0),
                    "scorePercent": round(score * 100, 2),
                    "confidence95": dict(interval),
                },
                "reasons": reasons,
            }
        )

    selected: list[dict[str, Any]] = []
    covered_archetypes: Counter[str] = Counter()
    remaining = list(ranked)
    target_count = min(limit, len(remaining))
    while remaining and len(selected) < target_count:
        def selection_key(item: Mapping[str, Any]) -> tuple[float, float, str]:
            tags = [str(tag) for tag in item.get("archetypes", [])]
            diversity_bonus = 16.0 if any(covered_archetypes[tag] == 0 for tag in tags) else 0.0
            concentration_penalty = 1.5 * min(
                (covered_archetypes[tag] for tag in tags), default=0
            )
            adjusted = float(item.get("priorityScore", 0.0)) + diversity_bonus - concentration_penalty
            return (adjusted, float(item.get("priorityScore", 0.0)), str(item.get("id", "")))

        chosen = max(remaining, key=selection_key)
        remaining.remove(chosen)
        chosen["selectionRank"] = len(selected) + 1
        selected.append(chosen)
        for tag in chosen["archetypes"]:
            covered_archetypes[str(tag)] += 1
    return selected


def _score_delta_confidence95(
    baseline: Mapping[str, int | float],
    candidate: Mapping[str, int | float],
) -> dict[str, float]:
    """Approximate a 95% interval for two independent match-point means.

    A win is 1 point, a tie is 0.5 and a loss is 0. Showdown's internal RNG is
    not paired across candidates, so the comparison deliberately does not claim
    a paired-battle interval even though pool, sides and Preview seeds align.
    """

    def moments(row: Mapping[str, int | float]) -> tuple[int, float, float]:
        games = int(row.get("games", 0) or 0)
        if games < 1:
            return 0, 0.0, 0.0
        wins = float(row.get("wins", 0) or 0)
        ties = float(row.get("ties", 0) or 0)
        mean = (wins + 0.5 * ties) / games
        second_moment = (wins + 0.25 * ties) / games
        population_variance = max(0.0, second_moment - mean * mean)
        sample_variance = (
            population_variance * games / (games - 1)
            if games > 1
            else 0.0
        )
        return games, mean, sample_variance

    baseline_games, baseline_mean, baseline_variance = moments(baseline)
    candidate_games, candidate_mean, candidate_variance = moments(candidate)
    delta = candidate_mean - baseline_mean
    if baseline_games < 2 or candidate_games < 2:
        return {
            "low": -100.0,
            "high": 100.0,
            "width": 200.0,
        }
    standard_error = math.sqrt(
        baseline_variance / baseline_games
        + candidate_variance / candidate_games
    )
    margin = 1.95996398454 * standard_error
    low = max(-1.0, delta - margin) * 100
    high = min(1.0, delta + margin) * 100
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "width": round(high - low, 2),
    }


def _matchup_directions(
    baseline_matchups: Mapping[str, Any],
    candidate_matchups: Mapping[str, Any],
    *,
    only: set[str] | None = None,
) -> dict[str, int]:
    common = sorted(set(baseline_matchups) & set(candidate_matchups))
    if only is not None:
        common = [opponent_id for opponent_id in common if opponent_id in only]
    improved = regressed = tied = 0
    for opponent_id in common:
        before = float(baseline_matchups[opponent_id]["scorePercent"])
        after = float(candidate_matchups[opponent_id]["scorePercent"])
        if after > before:
            improved += 1
        elif after < before:
            regressed += 1
        else:
            tied += 1
    return {
        "improved": improved,
        "regressed": regressed,
        "tied": tied,
    }


def compare_with_baseline(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_pool = baseline.get("poolEstimate", baseline)
    candidate_pool = candidate.get("poolEstimate", candidate)
    delta = round(
        float(candidate_pool["scorePercent"]) - float(baseline_pool["scorePercent"]),
        2,
    )
    delta95 = _score_delta_confidence95(baseline_pool, candidate_pool)
    baseline_matchups = baseline.get("screeningByOpponent", baseline.get("byOpponent", {}))
    candidate_matchups = candidate.get("screeningByOpponent", candidate.get("byOpponent", {}))
    directions = _matchup_directions(baseline_matchups, candidate_matchups)
    improved = directions["improved"]
    regressed = directions["regressed"]
    tied = directions["tied"]

    baseline_combined = baseline.get("byOpponent", {})
    candidate_combined = candidate.get("byOpponent", {})
    critical_ids = {
        str(opponent_id)
        for opponent_id, row in baseline_combined.items()
        if bool(row.get("deepDive"))
    }
    critical = _matchup_directions(
        baseline_combined,
        candidate_combined,
        only=critical_ids,
    )

    if delta95["low"] > 0 and improved >= regressed:
        evidence = "confirmed-improvement"
        verdict = "improved"
    elif delta > 0 and improved >= regressed:
        evidence = "directional-improvement"
        verdict = "improved"
    elif delta95["high"] < 0 and regressed > improved:
        evidence = "confirmed-regression"
        verdict = "regressed"
    elif delta < 0 and regressed > improved:
        evidence = "directional-regression"
        verdict = "regressed"
    elif delta == 0 and improved == regressed:
        evidence = "inconclusive"
        verdict = "mixed"
    else:
        evidence = "mixed"
        verdict = "mixed"
    return {
        "deltaPercentagePoints": delta,
        "delta95": delta95,
        "opponentsImproved": improved,
        "opponentsRegressed": regressed,
        "opponentsTied": tied,
        "criticalOpponents": len(critical_ids),
        "criticalOpponentsImproved": critical["improved"],
        "criticalOpponentsRegressed": critical["regressed"],
        "criticalOpponentsTied": critical["tied"],
        "evidence": evidence,
        "verdict": verdict,
        "promotion": "candidate" if evidence == "confirmed-improvement" else "hold",
        "caveat": (
            "Benchmark relativo LIGHT-vs-LIGHT. Baseline y variante usan el mismo pool, lados y semillas de Preview; "
            "el IC95% del delta trata el RNG interno de Showdown como independiente porque no queda pareado."
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
    preview_index_offset: int = 0,
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
            preview_index = preview_index_offset + index
            _seed_torch(runtime, _preview_seed(opponent_id, preview_index))

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
            summary["previewSeed"] = _preview_seed(opponent_id, preview_index)
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
    sampling_plan: AdaptiveSamplingPlan,
    timeout: float,
    replay_root: Path,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Evaluate the baseline, optional full-set variants, and audit the baseline."""

    if len(variants) > 8:
        raise ValueError("Auto Lab admite como máximo 8 variantes por ejecución.")
    if not opponents:
        raise ValueError("Auto Lab requiere al menos un rival.")
    if len(opponents) > 100:
        raise ValueError("Auto Lab admite como máximo 100 rivales por ejecución.")
    sampling_plan.validate(len(opponents))

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

    total_battles = len(candidate_records) * sampling_plan.battles_per_candidate(
        len(opponent_records)
    )
    completed_battles = 0
    reports: dict[str, dict[str, Any]] = {}
    summaries_by_candidate: dict[str, list[dict[str, Any]]] = {}
    opponent_meta = {
        record.id: {
            "id": record.id,
            "label": record.description,
            "roster": list(record.roster),
            "archetypes": classify_archetypes(record.team_text),
        }
        for record in opponent_records
    }

    async def run_stage(
        candidate: TeamRecord,
        stage_opponents: Sequence[TeamRecord],
        *,
        battles_per_opponent: int,
        preview_index_offset: int,
        sampling_stage: str,
    ) -> list[dict[str, Any]]:
        nonlocal completed_battles
        stage_summaries: list[dict[str, Any]] = []
        for opponent in stage_opponents:
            await _notify(
                progress,
                {
                    "phase": "running",
                    "samplingStage": sampling_stage,
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
            summaries = await run_candidate_battles(
                runtime=runtime,
                port=port,
                battle_format=battle_format,
                schedule=schedule,
                candidate_id=candidate.id,
                timeout=timeout,
                replay_dir=replay_root / candidate.id / opponent.id,
                preview_index_offset=preview_index_offset,
            )
            for summary in summaries:
                summary["samplingStage"] = sampling_stage
            stage_summaries.extend(summaries)
            completed_battles += len(summaries)
            await _notify(
                progress,
                {
                    "phase": "running",
                    "samplingStage": sampling_stage,
                    "candidateId": candidate.id,
                    "candidateLabel": candidate.description,
                    "opponentId": opponent.id,
                    "completedBattles": completed_battles,
                    "totalBattles": total_battles,
                },
            )
        return stage_summaries

    baseline_record = candidate_records[0]
    await _notify(
        progress,
        {
            "phase": "running",
            "samplingStage": "screening",
            "candidateId": baseline_record.id,
            "candidateLabel": baseline_record.description,
            "candidateIndex": 0,
            "candidateCount": len(candidate_records),
            "completedBattles": completed_battles,
            "totalBattles": total_battles,
        },
    )
    baseline_screening = await run_stage(
        baseline_record,
        opponent_records,
        battles_per_opponent=sampling_plan.initial_battles_per_opponent,
        preview_index_offset=0,
        sampling_stage="screening",
    )
    screening_report = summarize_candidate(baseline.id, baseline_screening)
    deep_dive_selection = select_deep_dive_opponents(
        screening_report,
        opponent_meta,
        sampling_plan.deep_dive_opponents,
    )
    deep_dive_ids = [str(item["id"]) for item in deep_dive_selection]
    records_by_id = {record.id: record for record in opponent_records}
    deep_dive_records = [records_by_id[opponent_id] for opponent_id in deep_dive_ids]
    baseline_deepening = (
        await run_stage(
            baseline_record,
            deep_dive_records,
            battles_per_opponent=sampling_plan.additional_battles_per_deep_dive,
            preview_index_offset=sampling_plan.initial_battles_per_opponent,
            sampling_stage="deepening",
        )
        if deep_dive_records
        else []
    )
    baseline_summaries = [*baseline_screening, *baseline_deepening]
    summaries_by_candidate[baseline.id] = baseline_summaries
    baseline_combined_report = summarize_candidate(
        baseline.id,
        baseline_summaries,
        deep_dive_ids=deep_dive_ids,
    )
    reports[baseline.id] = {
        "id": baseline.id,
        "label": baseline.label,
        **baseline_combined_report,
        "poolEstimate": {
            key: value
            for key, value in screening_report.items()
            if key != "byOpponent"
        },
        "adaptiveCombined": {
            key: value
            for key, value in baseline_combined_report.items()
            if key != "byOpponent"
        },
        "screeningByOpponent": screening_report["byOpponent"],
    }

    for candidate_index, candidate in enumerate(candidate_records[1:], start=1):
        await _notify(
            progress,
            {
                "phase": "running",
                "samplingStage": "screening",
                "candidateId": candidate.id,
                "candidateLabel": candidate.description,
                "candidateIndex": candidate_index,
                "candidateCount": len(candidate_records),
                "completedBattles": completed_battles,
                "totalBattles": total_battles,
            },
        )
        screening = await run_stage(
            candidate,
            opponent_records,
            battles_per_opponent=sampling_plan.initial_battles_per_opponent,
            preview_index_offset=0,
            sampling_stage="screening",
        )
        deepening = (
            await run_stage(
                candidate,
                deep_dive_records,
                battles_per_opponent=sampling_plan.additional_battles_per_deep_dive,
                preview_index_offset=sampling_plan.initial_battles_per_opponent,
                sampling_stage="deepening",
            )
            if deep_dive_records
            else []
        )
        all_summaries = [*screening, *deepening]
        summaries_by_candidate[candidate.id] = all_summaries
        screening_summary = summarize_candidate(candidate.id, screening)
        combined_summary = summarize_candidate(
            candidate.id,
            all_summaries,
            deep_dive_ids=deep_dive_ids,
        )
        reports[candidate.id] = {
            "id": candidate.id,
            "label": candidate.description,
            **combined_summary,
            "poolEstimate": {
                key: value
                for key, value in screening_summary.items()
                if key != "byOpponent"
            },
            "adaptiveCombined": {
                key: value
                for key, value in combined_summary.items()
                if key != "byOpponent"
            },
            "screeningByOpponent": screening_summary["byOpponent"],
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
            -float(item.get("poolEstimate", item)["scorePercent"]),
            str(item["label"]),
        )
    )

    sampling = {
        "strategy": "adaptive-two-stage",
        "heuristic": (
            "prevalence × severity × repeatability × confidence, con una cuota "
            "menor de incertidumbre y diversidad de arquetipos"
        ),
        "screening": {
            "opponents": len(opponent_records),
            "battlesPerOpponent": sampling_plan.initial_battles_per_opponent,
            "battlesPerCandidate": (
                len(opponent_records) * sampling_plan.initial_battles_per_opponent
            ),
        },
        "deepDive": {
            "opponents": len(deep_dive_ids),
            "additionalBattlesPerOpponent": (
                sampling_plan.additional_battles_per_deep_dive
            ),
            "battlesPerCandidate": (
                len(deep_dive_ids) * sampling_plan.additional_battles_per_deep_dive
            ),
            "selected": deep_dive_selection,
        },
        "battlesPerCandidate": sampling_plan.battles_per_candidate(
            len(opponent_records)
        ),
        "selectionSource": "baseline-screening",
        "sameTargetsAcrossCandidates": True,
    }
    audit = await asyncio.to_thread(
        build_auto_lab_audit,
        candidate_id=baseline.id,
        candidate_roster=list(candidate_records[0].roster),
        candidate_team_text=candidate_records[0].team_text,
        summaries=summaries_by_candidate[baseline.id],
        candidate_report=baseline_report,
        opponents=opponent_meta,
        replay_root=replay_root / baseline.id,
        sampling=sampling,
    )

    return {
        "schemaVersion": 3,
        "benchmark": "light-mc-team-gauntlet",
        "policy": f"{runtime.metadata.get('modelLabel', 'LIGHT M-C')} turns deterministic; candidate Team Preview sampled",
        "model": {key: runtime.metadata.get(key) for key in ("modelId", "modelLabel", "checkpointSha256")},
        "previewExploration": {
            "candidateOnly": True,
            "turnPolicyDeterministic": True,
            "seedAlignedAcrossCandidates": True,
        },
        "sampling": sampling,
        "opponents": list(opponent_meta.values()),
        "totalBattles": total_battles,
        "baseline": baseline_report,
        "variants": variant_reports,
        "bestVariantId": next(
            (
                report["id"]
                for report in variant_reports
                if report["comparison"]["promotion"] == "candidate"
            ),
            None,
        ),
        "audit": audit,
        "caveat": (
            "El score y la auditoría describen compatibilidad modelo-equipo contra este pool; "
            "no estiman el win rate real del jugador en ladder o torneo."
        ),
    }
