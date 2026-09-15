"""Read-only report for Nana 1 calibrated predictor v2.

Only sessions explicitly marked with the v2 predictor marker are included in
current-model metrics, so old Nana 1 predictions remain available for history
without contaminating calibration evaluation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_stage1_calibrated_runtime import MODEL_VERSION
from battle_lab.nana_stage1_report import (
    _decision_rows,
    _iter_profile_events,
    _metrics,
    _print_segment,
)

MIN_READY_EVAL = 24


def build_calibration_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    sessions_root = runtime_root / "nana" / "profiles" / profile_id / "sessions"
    if not sessions_root.is_dir():
        return {
            "profileId": profile_id,
            "modelVersion": MODEL_VERSION,
            "error": f"No existe el directorio de sesiones: {sessions_root}",
        }

    events = list(_iter_profile_events(sessions_root))
    model_sessions = {
        str(event.get("sessionId") or "")
        for event in events
        if event.get("type") == "nana_predictor_version"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("modelVersion") == MODEL_VERSION
    }
    model_sessions.discard("")

    all_rows = _decision_rows(events)
    rows = [row for row in all_rows if row.get("sessionId") in model_sessions]
    ready = [row for row in rows if row.get("ready")]
    recent_ready = ready[-10:]

    ready_metrics = _metrics(ready)
    enough = len(ready) >= MIN_READY_EVAL
    positive_distribution = bool(
        ready_metrics.get("logLossGain") is not None
        and float(ready_metrics["logLossGain"]) > 0
        and ready_metrics.get("chosenProbabilityLift") is not None
        and float(ready_metrics["chosenProbabilityLift"]) > 0
    )
    positive_ranking = bool(
        ready_metrics.get("top3LiftPp") is not None
        and float(ready_metrics["top3LiftPp"]) > 0
    )

    if not rows:
        gate = "no-v2-data"
    elif not enough:
        gate = "collecting"
    elif positive_distribution and positive_ranking:
        gate = "candidate"
    else:
        gate = "not-ready"

    return {
        "profileId": profile_id,
        "modelVersion": MODEL_VERSION,
        "markedSessions": len(model_sessions),
        "decisions": len(rows),
        "readyDecisions": len(ready),
        "minReadyEval": MIN_READY_EVAL,
        "gate": gate,
        "overall": _metrics(rows),
        "ready": ready_metrics,
        "recentReady10": _metrics(recent_ready),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evalúa exclusivamente Nana 1 calibrated v2 contra baseline uniforme."
    )
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_calibration_report(
        args.runtime_root.expanduser().resolve(),
        args.nana_profile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("error") else 0
    if report.get("error"):
        print(f"Nana 1 calibration report: ERROR\n{report['error']}")
        return 1

    print("Nana 1 calibration report")
    print(
        f"Profile: {report['profileId']} · model={report['modelVersion']} · "
        f"sessions={report['markedSessions']} · decisions={report['decisions']} · "
        f"ready={report['readyDecisions']}/{report['minReadyEval']} · gate={report['gate']}"
    )
    _print_segment("Current model overall", report["overall"])
    _print_segment("Current model ready-only", report["ready"])
    _print_segment("Recent current ready (max 10)", report["recentReady10"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
