"""Read-only flip-margin diagnostic for Nana 2 shadow v2.1.

This report does not rerank, mutate, or replay anything. It reads the already
recorded v2.1 shadow plans and measures how much lambda *would* have been needed
for a positive Nana proxy preference to overcome LIGHT's recorded sequential
regret. It is intended to diagnose why a safe shadow sweep produced zero
interventions before changing any runtime parameter.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT

STAGE2_MODEL_VERSION = "nana2-shadow-v2.1-spread-aware"
MARKER_EVENT = "nana_stage2_shadow_v2"
PLAN_EVENT = "nana_stage2_shadow_v2_plan"
CURRENT_LAMBDA_CAP = 0.20
DIAGNOSTIC_CAPS = (0.20, 0.30, 0.50, 1.00)
_EPS = 1e-12


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    return rendered if math.isfinite(rendered) else default


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"JSONL corrupto en {path}:{line_number}: {error.msg}"
                ) from error
            if isinstance(value, dict):
                events.append(value)
    return events


def _session_events(root: Path) -> Iterable[tuple[Path, list[dict[str, Any]]]]:
    for path in sorted(root.glob("*.jsonl")):
        events = _load_jsonl(path)
        if any(
            event.get("type") == MARKER_EVENT
            and _payload(event).get("shadowModel") == STAGE2_MODEL_VERSION
            for event in events
        ):
            yield path, events


def analyze_plan(plan: dict[str, Any], *, current_cap: float = CURRENT_LAMBDA_CAP) -> dict[str, Any]:
    """Return the closest positive-signal alternative and its lambda threshold."""

    if plan.get("eligible") is not True:
        return {"eligible": False, "reason": str(plan.get("reason") or "unknown")}

    canonical = plan.get("canonical")
    canonical = canonical if isinstance(canonical, dict) else {}
    canonical_counter = _safe_float(canonical.get("expectedCounter"))
    confidence_scale = max(0.0, _safe_float(plan.get("confidenceScale")))
    pool = [item for item in plan.get("candidatePool") or [] if isinstance(item, dict)]
    alternatives = [item for item in pool if item.get("selectedByLight") is not True]

    positive: list[dict[str, Any]] = []
    closest_margin = None
    closest_any: dict[str, Any] | None = None
    for candidate in alternatives:
        delta_counter = _safe_float(candidate.get("expectedCounter")) - canonical_counter
        regret = _safe_float(candidate.get("lightRegretLog"))
        margin_at_current = regret + current_cap * confidence_scale * delta_counter
        if closest_margin is None or margin_at_current > closest_margin:
            closest_margin = margin_at_current
            closest_any = candidate
        if delta_counter <= _EPS:
            continue
        required_effective_lambda = max(0.0, -regret / delta_counter)
        required_cap = (
            required_effective_lambda / confidence_scale
            if confidence_scale > _EPS
            else math.inf
        )
        positive.append(
            {
                "indices": candidate.get("indices"),
                "labels": candidate.get("labels"),
                "deltaCounter": delta_counter,
                "lightRegretLog": regret,
                "firstRegretLog": _safe_float(candidate.get("firstRegretLog")),
                "secondRegretLog": _safe_float(candidate.get("secondRegretLog")),
                "requiredEffectiveLambda": required_effective_lambda,
                "requiredLambdaCap": required_cap,
                "marginAtCurrentCap": margin_at_current,
                "expectedRelevantProbability": _safe_float(
                    candidate.get("expectedRelevantProbability")
                ),
            }
        )

    positive.sort(
        key=lambda item: (
            item["requiredLambdaCap"],
            -item["deltaCounter"],
            -item["lightRegretLog"],
        )
    )
    best = positive[0] if positive else None
    reason = "positive-proxy-alternative" if best is not None else (
        "no-alternative-in-pool" if not alternatives else "no-positive-proxy-advantage"
    )
    return {
        "eligible": True,
        "reason": reason,
        "poolSize": len(pool),
        "alternativeCount": len(alternatives),
        "positiveAlternativeCount": len(positive),
        "confidenceScale": confidence_scale,
        "canonicalExpectedCounter": canonical_counter,
        "best": best,
        "closestMarginAtCurrentCap": closest_margin,
        "closestAnyIndices": closest_any.get("indices") if isinstance(closest_any, dict) else None,
    }


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    sessions_root = runtime_root / "nana" / "profiles" / profile_id / "sessions"
    if not sessions_root.is_dir():
        return {
            "profileId": profile_id,
            "shadowModel": STAGE2_MODEL_VERSION,
            "error": f"No existe {sessions_root}",
        }

    session_count = 0
    rows: list[dict[str, Any]] = []
    for path, events in _session_events(sessions_root):
        session_count += 1
        for event in events:
            if event.get("type") != PLAN_EVENT:
                continue
            payload = _payload(event)
            if payload.get("shadowModel") != STAGE2_MODEL_VERSION:
                continue
            plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            diagnostic = analyze_plan(plan)
            rows.append(
                {
                    "session": path.stem,
                    "generation": payload.get("generation"),
                    "turn": payload.get("turn"),
                    **diagnostic,
                }
            )

    if session_count == 0:
        return {
            "profileId": profile_id,
            "shadowModel": STAGE2_MODEL_VERSION,
            "sessions": 0,
            "error": "No hay sesiones Nana 2 shadow v2.1.",
        }

    eligible = [row for row in rows if row.get("eligible") is True]
    positive_rows = [
        row
        for row in eligible
        if isinstance(row.get("best"), dict)
        and math.isfinite(_safe_float(row["best"].get("requiredLambdaCap"), math.inf))
    ]
    required_caps = [
        _safe_float(row["best"]["requiredLambdaCap"])
        for row in positive_rows
    ]
    closest_margins = [
        _safe_float(row.get("closestMarginAtCurrentCap"))
        for row in eligible
        if isinstance(row.get("closestMarginAtCurrentCap"), (int, float))
    ]
    reasons = Counter(str(row.get("reason") or "unknown") for row in eligible)

    threshold_counts = {
        f"{cap:.2f}": sum(
            1
            for value in required_caps
            if value < cap - _EPS
        )
        for cap in DIAGNOSTIC_CAPS
    }

    detail = sorted(
        positive_rows,
        key=lambda row: _safe_float(row["best"].get("requiredLambdaCap"), math.inf),
    )
    return {
        "profileId": profile_id,
        "shadowModel": STAGE2_MODEL_VERSION,
        "sessions": session_count,
        "plans": len(rows),
        "eligible": len(eligible),
        "withAlternatives": sum(1 for row in eligible if int(row.get("alternativeCount") or 0) > 0),
        "withPositiveProxyAlternative": len(positive_rows),
        "reasonCounts": dict(reasons),
        "thresholdFlipCounts": threshold_counts,
        "minRequiredLambdaCap": min(required_caps) if required_caps else None,
        "medianRequiredLambdaCap": statistics.median(required_caps) if required_caps else None,
        "meanClosestMarginAtCurrentCap": (
            statistics.fmean(closest_margins) if closest_margins else None
        ),
        "details": detail[:10],
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _print(report: dict[str, Any]) -> None:
    if report.get("error"):
        print(f"Nana 2 v2.1 flip report: ERROR\n{report['error']}")
        return
    print("Nana 2 v2.1 flip-margin report: PASS")
    print(
        f"Profile: {report['profileId']} · model={report['shadowModel']} · "
        f"sessions={report['sessions']} · plans={report['plans']} · eligible={report['eligible']}"
    )
    print(
        f"Pool: with alternatives={report['withAlternatives']}/{report['eligible']} · "
        f"positive proxy alternative={report['withPositiveProxyAlternative']}/{report['eligible']}"
    )
    print(f"Reasons: {report['reasonCounts']}")
    print(
        "Required λcap: "
        f"min={_fmt(report.get('minRequiredLambdaCap'))} · "
        f"median={_fmt(report.get('medianRequiredLambdaCap'))} · "
        f"mean closest margin at current 0.20={_fmt(report.get('meanClosestMarginAtCurrentCap'))}"
    )
    counts = report.get("thresholdFlipCounts") or {}
    print(
        "Would strictly flip by cap (diagnostic only): "
        + " · ".join(f"≤~{key}: {value}" for key, value in counts.items())
    )
    for row in report.get("details") or []:
        best = row.get("best") or {}
        print(
            f"  gen={row.get('generation')} turn={row.get('turn')} pool={row.get('poolSize')} "
            f"scale={_fmt(row.get('confidenceScale'))} · required λcap={_fmt(best.get('requiredLambdaCap'))} "
            f"· proxy Δ={_fmt(best.get('deltaCounter'))} · LIGHT regret={_fmt(best.get('lightRegretLog'))} "
            f"· margin@.20={_fmt(best.get('marginAtCurrentCap'))}"
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnostica cuánto λ necesitaría Nana 2 v2.1 sin cambiar el runtime."
    )
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.runtime_root.expanduser().resolve(), args.nana_profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _print(report)
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
