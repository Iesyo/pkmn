"""Read-only aggregate report for Nana live telemetry across recent BO1s."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from battle_lab.local_sparring_service import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_recorder import safe_profile_id


def _safe_float(value: Any) -> float | None:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return None
    return rendered if math.isfinite(rendered) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _latest_timestamp(events: Iterable[dict[str, Any]]) -> str:
    return max((str(event.get("timestamp") or "") for event in events), default="")


def _session_result(events: Iterable[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") != "session_end":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        winner = str(result.get("winner") or "")
        return winner or "unknown"
    return "incomplete"


def _team_signature(events: Iterable[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") != "session_start":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        team = context.get("teamIdentity") if isinstance(context.get("teamIdentity"), dict) else {}
        return str(team.get("exactTeamSignature") or "")
    return ""


def _bottleneck(selection: dict[str, Any]) -> str:
    funnel = selection.get("candidateFunnel") if isinstance(selection.get("candidateFunnel"), dict) else {}
    alternatives = max(0, int(funnel.get("jointTotal") or 0) - 1)
    branch = int(funnel.get("poolAlternatives") or 0)
    regret = int(funnel.get("nurseryRegretPassed") or 0)
    counter = int(funnel.get("counterImproved") or 0)
    inside = int(funnel.get("insideCap") or 0)
    if alternatives <= 0:
        return "no-alternatives"
    if branch <= 0:
        return "branch-filter"
    if regret <= 0:
        return "regret-floor"
    if counter <= 0:
        return "counter-proxy"
    if inside <= 0:
        return "lambda-cap"
    return "ready"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def build_report(profile_root: Path, *, last: int = 10) -> dict[str, Any]:
    sessions_root = profile_root / "sessions"
    records: list[dict[str, Any]] = []
    for path in sessions_root.glob("*.jsonl"):
        events = _read_jsonl(path)
        if not events:
            continue
        timestamp = _latest_timestamp(events)
        decisions = [
            event for event in events
            if event.get("type") == "nana_nursery_decision"
            and isinstance(event.get("payload"), dict)
        ]
        if not decisions:
            continue
        result = _session_result(events)
        if result == "incomplete":
            continue
        records.append({
            "sessionId": path.stem,
            "timestamp": timestamp,
            "result": result,
            "team": _team_signature(events),
            "decisions": decisions,
        })

    records.sort(key=lambda item: item["timestamp"], reverse=True)
    records = records[: max(1, int(last))]

    reasons: Counter[str] = Counter()
    bottlenecks: Counter[str] = Counter()
    lambda_required: list[float] = []
    lambda_gap: list[float] = []
    interventions = 0
    decisions_total = 0
    results: Counter[str] = Counter()
    per_session: list[dict[str, Any]] = []

    for record in records:
        session_reasons: Counter[str] = Counter()
        session_bottlenecks: Counter[str] = Counter()
        session_interventions = 0
        session_required: list[float] = []
        decisions = record["decisions"]
        for event in decisions:
            payload = event["payload"]
            selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
            reason = str(selection.get("reason") or "unknown")
            reasons[reason] += 1
            session_reasons[reason] += 1
            bottleneck = _bottleneck(selection)
            bottlenecks[bottleneck] += 1
            session_bottlenecks[bottleneck] += 1
            if payload.get("intervened") is True:
                interventions += 1
                session_interventions += 1
            required = _safe_float(selection.get("requiredLambdaCap"))
            gap = _safe_float(selection.get("lambdaGap"))
            if required is not None:
                lambda_required.append(required)
                session_required.append(required)
            if gap is not None:
                lambda_gap.append(gap)
            decisions_total += 1

        results[str(record["result"])] += 1
        per_session.append({
            "sessionId": record["sessionId"],
            "timestamp": record["timestamp"],
            "result": record["result"],
            "team": record["team"],
            "decisions": len(decisions),
            "interventions": session_interventions,
            "dominantBottleneck": (
                session_bottlenecks.most_common(1)[0][0]
                if session_bottlenecks else "none"
            ),
            "reasons": dict(session_reasons),
            "minRequiredLambda": min(session_required) if session_required else None,
        })

    memory_path = profile_root / "team_memory.json"
    team_memory: dict[str, Any] = {}
    if memory_path.is_file():
        try:
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                team_memory = {
                    "modelVersion": payload.get("modelVersion"),
                    "observations": int(payload.get("observations") or 0),
                    "taggedSessions": int(payload.get("taggedSessions") or 0),
                    "ignoredLegacyTeamSessions": int(payload.get("ignoredLegacyTeamSessions") or 0),
                }
        except Exception:
            team_memory = {"error": "unreadable"}

    thresholds = {
        "<=0.15": sum(value <= 0.15 for value in lambda_required),
        "<=0.20": sum(value <= 0.20 for value in lambda_required),
        "<=0.25": sum(value <= 0.25 for value in lambda_required),
        "<=0.30": sum(value <= 0.30 for value in lambda_required),
        ">0.30": sum(value > 0.30 for value in lambda_required),
    }

    return {
        "sessions": len(records),
        "decisions": decisions_total,
        "interventions": interventions,
        "results": dict(results),
        "reasons": dict(reasons),
        "bottlenecks": dict(bottlenecks),
        "requiredLambda": {
            "samples": len(lambda_required),
            "min": min(lambda_required) if lambda_required else None,
            "p25": _percentile(lambda_required, 0.25),
            "median": statistics.median(lambda_required) if lambda_required else None,
            "p75": _percentile(lambda_required, 0.75),
            "max": max(lambda_required) if lambda_required else None,
            "thresholds": thresholds,
        },
        "lambdaGap": {
            "samples": len(lambda_gap),
            "median": statistics.median(lambda_gap) if lambda_gap else None,
        },
        "teamMemory": team_memory,
        "perSession": per_session,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    number = _safe_float(value)
    return "—" if number is None else f"{number:.{digits}f}"


def print_report(report: dict[str, Any]) -> None:
    print("Nana telemetry report")
    print(
        f"BO1={report['sessions']} · decisiones={report['decisions']} · "
        f"intervenciones={report['interventions']}"
    )
    print(f"Resultados: {report['results']}")
    print()
    print("Cuellos de botella:")
    for key, value in sorted(
        report["bottlenecks"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"  {key}: {value}")
    print()
    print("Razones finales:")
    for key, value in sorted(
        report["reasons"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        print(f"  {key}: {value}")

    required = report["requiredLambda"]
    print()
    print(
        "λ requerido: "
        f"n={required['samples']} · min={_fmt(required['min'])} · "
        f"p25={_fmt(required['p25'])} · median={_fmt(required['median'])} · "
        f"p75={_fmt(required['p75'])} · max={_fmt(required['max'])}"
    )
    print(f"Umbrales: {required['thresholds']}")
    print(f"Gap mediano al λ actual: {_fmt(report['lambdaGap']['median'])}")
    print()
    print(f"TeamMemory: {report['teamMemory']}")
    print()
    print("Por BO1:")
    for row in report["perSession"]:
        print(
            f"  {row['sessionId']} · {row['result']} · "
            f"dec={row['decisions']} · int={row['interventions']} · "
            f"bottleneck={row['dominantBottleneck']} · "
            f"min λ={_fmt(row['minRequiredLambda'])}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile_root = (
        Path(args.runtime_root).expanduser().resolve()
        / "nana"
        / "profiles"
        / safe_profile_id(args.nana_profile)
    )
    report = build_report(profile_root, last=args.last)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
