"""Read-only learning report for Nana 1.

Separates cold-start/not-ready observations from predictions that had crossed
Nana 1's explicit readiness gate. Metrics are compared against a uniform random
baseline using the actual number of legal actions for every decision.

This module never changes LIGHT, Nana history, or predictor state.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_predictor import MIN_CONTEXT_SAMPLES, MIN_HISTORY_SAMPLES

_EPS = 1e-12


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"JSONL corrupto en {path}:{line_number}: {error.msg}."
                ) from error
            if not isinstance(event, dict):
                raise RuntimeError(
                    f"Evento inválido en {path}:{line_number}: se esperaba objeto JSON."
                )
            events.append(event)
    return events


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rendered = float(value)
    return rendered if math.isfinite(rendered) else None


def _iter_profile_events(sessions_root: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(sessions_root.glob("*.jsonl")):
        yield from _load_jsonl(path)


def _decision_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    predictions: dict[tuple[str, int], dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []

    materialized = [event for event in events if isinstance(event, dict)]
    for event in materialized:
        if event.get("type") != "human_prediction":
            continue
        payload = _payload(event)
        generation = payload.get("generation")
        if not isinstance(generation, int) or generation <= 0:
            continue
        key = (str(event.get("sessionId") or ""), generation)
        predictions[key] = payload

    for event in materialized:
        if event.get("type") != "human_choice_observed":
            continue
        payload = _payload(event)
        generation = payload.get("generation")
        if not isinstance(generation, int) or generation <= 0:
            continue
        session_id = str(event.get("sessionId") or "")
        prediction = predictions.get((session_id, generation), {})
        evaluation = payload.get("prediction")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        legal = payload.get("legalActions")
        legal = legal if isinstance(legal, list) else []
        candidate_count = len([item for item in legal if isinstance(item, dict)])
        if candidate_count <= 0:
            candidate_count = int(prediction.get("candidateCount") or 0)
        if candidate_count <= 0:
            continue

        history_samples = prediction.get("historySamples")
        if not isinstance(history_samples, int):
            history_samples = evaluation.get("historySamples")
        context_samples = prediction.get("contextSamples")
        if not isinstance(context_samples, int):
            context_samples = evaluation.get("contextSamples")

        exact_ready = prediction.get("ready")
        if isinstance(exact_ready, bool):
            ready = exact_ready
        else:
            ready = bool(
                isinstance(history_samples, int)
                and history_samples >= MIN_HISTORY_SAMPLES
                and isinstance(context_samples, int)
                and context_samples >= MIN_CONTEXT_SAMPLES
            )

        rank = evaluation.get("rank")
        chosen_probability = _finite(evaluation.get("chosenProbability"))
        confidence = _finite(evaluation.get("confidence"))
        rows_key = str(event.get("timestamp") or "")
        observations.append(
            {
                "timestamp": rows_key,
                "sessionId": session_id,
                "generation": generation,
                "turn": payload.get("turn"),
                "historySamples": history_samples,
                "contextSamples": context_samples,
                "ready": ready,
                "confidenceBand": evaluation.get("confidenceBand")
                or prediction.get("confidenceBand"),
                "candidateCount": candidate_count,
                "rank": rank if isinstance(rank, int) and rank >= 1 else None,
                "chosenProbability": max(0.0, min(1.0, chosen_probability or 0.0)),
                "confidence": max(0.0, min(1.0, confidence or 0.0)),
            }
        )

    observations.sort(key=lambda row: (row["timestamp"], row["sessionId"], row["generation"]))
    return observations


def _metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        return {
            "count": 0,
            "top1Hits": 0,
            "top3Hits": 0,
            "top1Accuracy": None,
            "top3Accuracy": None,
            "uniformTop1": None,
            "uniformTop3": None,
            "top1LiftPp": None,
            "top3LiftPp": None,
            "meanReciprocalRank": None,
            "meanConfidence": None,
            "meanChosenProbability": None,
            "uniformChosenProbability": None,
            "chosenProbabilityLift": None,
            "logLoss": None,
            "uniformLogLoss": None,
            "logLossGain": None,
        }

    top1_hits = 0
    top3_hits = 0
    reciprocal_rank_total = 0.0
    confidence_total = 0.0
    chosen_probability_total = 0.0
    uniform_probability_total = 0.0
    uniform_top3_total = 0.0
    log_loss_total = 0.0
    uniform_log_loss_total = 0.0

    for row in rows:
        candidate_count = max(1, int(row["candidateCount"]))
        rank = row.get("rank")
        if rank == 1:
            top1_hits += 1
        if isinstance(rank, int) and 1 <= rank <= 3:
            top3_hits += 1
        if isinstance(rank, int) and rank >= 1:
            reciprocal_rank_total += 1.0 / rank

        confidence_total += float(row.get("confidence") or 0.0)
        chosen_probability = max(_EPS, float(row.get("chosenProbability") or 0.0))
        chosen_probability_total += float(row.get("chosenProbability") or 0.0)
        uniform_probability = 1.0 / candidate_count
        uniform_probability_total += uniform_probability
        uniform_top3_total += min(3, candidate_count) / candidate_count
        log_loss_total += -math.log(chosen_probability)
        uniform_log_loss_total += math.log(candidate_count)

    top1_accuracy = top1_hits / count
    top3_accuracy = top3_hits / count
    uniform_top1 = uniform_probability_total / count
    uniform_top3 = uniform_top3_total / count
    mean_chosen_probability = chosen_probability_total / count
    uniform_chosen_probability = uniform_probability_total / count
    log_loss = log_loss_total / count
    uniform_log_loss = uniform_log_loss_total / count

    return {
        "count": count,
        "top1Hits": top1_hits,
        "top3Hits": top3_hits,
        "top1Accuracy": top1_accuracy,
        "top3Accuracy": top3_accuracy,
        "uniformTop1": uniform_top1,
        "uniformTop3": uniform_top3,
        "top1LiftPp": (top1_accuracy - uniform_top1) * 100.0,
        "top3LiftPp": (top3_accuracy - uniform_top3) * 100.0,
        "meanReciprocalRank": reciprocal_rank_total / count,
        "meanConfidence": confidence_total / count,
        "meanChosenProbability": mean_chosen_probability,
        "uniformChosenProbability": uniform_chosen_probability,
        "chosenProbabilityLift": mean_chosen_probability - uniform_chosen_probability,
        "logLoss": log_loss,
        "uniformLogLoss": uniform_log_loss,
        # Positive is better: the personal predictor assigned more probability
        # to the action that actually happened than a uniform baseline.
        "logLossGain": uniform_log_loss - log_loss,
    }


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    sessions_root = runtime_root / "nana" / "profiles" / profile_id / "sessions"
    if not sessions_root.is_dir():
        return {
            "profileId": profile_id,
            "error": f"No existe el directorio de sesiones: {sessions_root}",
        }

    rows = _decision_rows(_iter_profile_events(sessions_root))
    ready = [row for row in rows if row["ready"]]
    not_ready = [row for row in rows if not row["ready"]]
    recent_ready = ready[-10:]

    return {
        "profileId": profile_id,
        "thresholds": {
            "minHistorySamples": MIN_HISTORY_SAMPLES,
            "minContextSamples": MIN_CONTEXT_SAMPLES,
        },
        "decisions": len(rows),
        "readyDecisions": len(ready),
        "notReadyDecisions": len(not_ready),
        "latestHistorySamples": rows[-1].get("historySamples") if rows else None,
        "overall": _metrics(rows),
        "notReady": _metrics(not_ready),
        "ready": _metrics(ready),
        "recentReady10": _metrics(recent_ready),
    }


def _pct(value: Any) -> str:
    rendered = _finite(value)
    return "n/a" if rendered is None else f"{rendered * 100.0:.1f}%"


def _num(value: Any, digits: int = 3) -> str:
    rendered = _finite(value)
    return "n/a" if rendered is None else f"{rendered:.{digits}f}"


def _print_segment(label: str, metrics: dict[str, Any]) -> None:
    count = int(metrics.get("count") or 0)
    print(f"{label}: n={count}")
    if not count:
        return
    print(
        "  Top-1: "
        f"{_pct(metrics.get('top1Accuracy'))} vs uniform {_pct(metrics.get('uniformTop1'))} "
        f"(lift {float(metrics.get('top1LiftPp') or 0.0):+.1f} pp)"
    )
    print(
        "  Top-3: "
        f"{_pct(metrics.get('top3Accuracy'))} vs uniform {_pct(metrics.get('uniformTop3'))} "
        f"(lift {float(metrics.get('top3LiftPp') or 0.0):+.1f} pp)"
    )
    print(
        "  Chosen P: "
        f"{_pct(metrics.get('meanChosenProbability'))} vs uniform "
        f"{_pct(metrics.get('uniformChosenProbability'))}"
    )
    print(
        "  Log-loss: "
        f"{_num(metrics.get('logLoss'))} vs uniform {_num(metrics.get('uniformLogLoss'))} "
        f"(gain {_num(metrics.get('logLossGain'))}; >0 is better)"
    )
    print(
        "  MRR/mean confidence: "
        f"{_num(metrics.get('meanReciprocalRank'))} / {_pct(metrics.get('meanConfidence'))}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara Nana 1 contra baseline uniforme y separa predicciones ready."
    )
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.runtime_root.expanduser().resolve(), args.nana_profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("error") else 0
    if report.get("error"):
        print(f"Nana 1 learning report: ERROR\n{report['error']}")
        return 1

    print("Nana 1 learning report")
    print(
        f"Profile: {report['profileId']} · decisions={report['decisions']} · "
        f"ready={report['readyDecisions']} · not-ready={report['notReadyDecisions']} · "
        f"latestHistorySamples={report['latestHistorySamples']}"
    )
    _print_segment("Overall", report["overall"])
    _print_segment("Ready-only", report["ready"])
    _print_segment("Recent ready (max 10)", report["recentReady10"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
