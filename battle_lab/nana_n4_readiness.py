"""Read-only readiness report for activating Nana Full Amiibo N4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from battle_lab.local_sparring_service import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_counter_calibration import fit_counter_calibration
from battle_lab.nana_recorder import NanaRecorder, safe_profile_id


def build_readiness(recorder: NanaRecorder) -> dict[str, Any]:
    events = list(recorder.iter_events())
    counter = fit_counter_calibration(events)

    legal_samples = 0
    legal_failures = 0
    legal_min_coverage: float | None = None
    legal_missing_max = 0
    n4_samples = 0
    n4_eligible = 0
    n4_would_change = 0
    common_score_max = 0

    for event in events:
        if not isinstance(event, dict) or event.get("type") != "nana_nursery_decision":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        legal = payload.get("legalOrders") if isinstance(payload.get("legalOrders"), dict) else None
        if legal is not None:
            legal_samples += 1
            if legal.get("resolved") is not True:
                legal_failures += 1
            else:
                coverage = legal.get("teacherCoverage")
                if isinstance(coverage, (int, float)):
                    value = float(coverage)
                    legal_min_coverage = value if legal_min_coverage is None else min(legal_min_coverage, value)
                missing = legal.get("missingFromTeacher")
                if isinstance(missing, int):
                    legal_missing_max = max(legal_missing_max, missing)

        n4 = payload.get("n4Shadow") if isinstance(payload.get("n4Shadow"), dict) else None
        if n4 is not None:
            n4_samples += 1
            if n4.get("eligible") is True:
                n4_eligible += 1
            if n4.get("wouldChange") is True:
                n4_would_change += 1
            common_score_max = max(common_score_max, int(n4.get("commonScoreAvailable") or 0))

    blockers: list[str] = []
    if not counter.resolved:
        blockers.append(f"counter-calibration:{counter.reason}")
    if legal_samples <= 0:
        blockers.append("legal-order-source:not-yet-observed-live")
    elif legal_failures > 0:
        blockers.append(f"legal-order-source:{legal_failures}-runtime-failures")
    if n4_samples <= 0:
        blockers.append("n4-shadow:not-yet-observed-live")
    elif n4_eligible <= 0:
        blockers.append("n4-shadow:no-eligible-turns")
    if common_score_max <= 0:
        blockers.append("n4-shadow:no-common-space-candidate-score")

    return {
        "counterCalibration": counter.public(),
        "legalOrderSource": {
            "samples": legal_samples,
            "failures": legal_failures,
            "teacherCoverageMin": legal_min_coverage,
            "missingFromTeacherMax": legal_missing_max,
        },
        "n4Shadow": {
            "samples": n4_samples,
            "eligible": n4_eligible,
            "wouldChange": n4_would_change,
            "commonScoreMax": common_score_max,
        },
        "runtimeReady": len(blockers) == 0,
        "activationReady": False,
        "activationNote": (
            "Runtime evidence may be ready, but N4 live activation remains manual "
            "until COL-99/review and explicit promotion."
        ),
        "blockers": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    recorder = NanaRecorder(
        runtime_root / "nana",
        profile_id=safe_profile_id(args.nana_profile),
    )
    report = build_readiness(recorder)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    counter = report["counterCalibration"]
    legal = report["legalOrderSource"]
    shadow = report["n4Shadow"]
    print("Nana N4 readiness")
    print(
        "Counter mapper: "
        f"{'READY' if counter['resolved'] else 'BLOCKED'} · "
        f"samples={counter['samples']} · slope={counter['slope']:.4f} · "
        f"r2={counter['r2']:.4f} · reason={counter['reason']}"
    )
    print(
        "LegalOrderSource: "
        f"samples={legal['samples']} · failures={legal['failures']} · "
        f"teacherCoverageMin={legal['teacherCoverageMin']} · "
        f"missingMax={legal['missingFromTeacherMax']}"
    )
    print(
        "N4 shadow: "
        f"samples={shadow['samples']} · eligible={shadow['eligible']} · "
        f"wouldChange={shadow['wouldChange']} · commonScoreMax={shadow['commonScoreMax']}"
    )
    print(f"Runtime ready: {report['runtimeReady']}")
    print(f"Activation ready: {report['activationReady']} (manual gate)")
    if report["blockers"]:
        print("Blockers:")
        for blocker in report["blockers"]:
            print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
