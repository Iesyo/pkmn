"""Read-only readiness report for activating Nana Full Amiibo N4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from battle_lab.local_sparring_service import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_autonomy import envelope
from battle_lab.nana_counter_calibration import fit_counter_calibration
from battle_lab.nana_n4_shadow import N4_SHADOW_VERSION
from battle_lab.nana_recorder import NanaRecorder, safe_profile_id
from battle_lab.nana_scorer import N4_COMMON_SCORER_READY


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
    safety_samples = 0
    safety_authorized = 0
    safety_failures = 0
    legal_ms: list[float] = []
    planning_ms: list[float] = []
    total_ms: list[float] = []

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
        if n4 is not None and n4.get("version") == N4_SHADOW_VERSION:
            n4_samples += 1
            if n4.get("eligible") is True:
                n4_eligible += 1
            if n4.get("wouldChange") is True:
                n4_would_change += 1
            common_score_max = max(common_score_max, int(n4.get("commonScoreAvailable") or 0))

            safety = n4.get("safetyGate") if isinstance(n4.get("safetyGate"), dict) else None
            if safety is not None:
                safety_samples += 1
                if safety.get("authorized") is True:
                    safety_authorized += 1
                else:
                    safety_failures += 1

            for key, bucket in (
                ("legalOrderMs", legal_ms),
                ("planningMs", planning_ms),
                ("totalDecisionMs", total_ms),
            ):
                value = n4.get(key)
                if isinstance(value, (int, float)):
                    bucket.append(float(value))

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
    if safety_samples <= 0:
        blockers.append("safety-gate:not-yet-observed")
    elif safety_failures > 0:
        blockers.append(f"safety-gate:{safety_failures}-authorization-failures")

    runtime_ready = len(blockers) == 0
    activation_ready = (
        runtime_ready
        and envelope("N4").activation_ready
        and N4_COMMON_SCORER_READY
    )

    return {
        "counterCalibration": counter.public(),
        "legalOrderSource": {
            "samples": legal_samples,
            "failures": legal_failures,
            "teacherCoverageMin": legal_min_coverage,
            "missingFromTeacherMax": legal_missing_max,
        },
        "n4Shadow": {
            "version": N4_SHADOW_VERSION,
            "samples": n4_samples,
            "eligible": n4_eligible,
            "wouldChange": n4_would_change,
            "commonScoreMax": common_score_max,
            "safetyGate": {
                "samples": safety_samples,
                "authorized": safety_authorized,
                "failures": safety_failures,
            },
            "timingMs": {
                "legalMax": max(legal_ms) if legal_ms else None,
                "planningMax": max(planning_ms) if planning_ms else None,
                "totalMax": max(total_ms) if total_ms else None,
                "totalMedian": (
                    sorted(total_ms)[len(total_ms)//2]
                    if total_ms
                    else None
                ),
            },
        },
        "runtimeReady": runtime_ready,
        "activationReady": activation_ready,
        "activationNote": (
            "N4 live is promoted when runtime evidence is current and both "
            "autonomy/scorer gates are enabled."
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
        f"version={shadow['version']} · samples={shadow['samples']} · "
        f"eligible={shadow['eligible']} · wouldChange={shadow['wouldChange']} · "
        f"commonScoreMax={shadow['commonScoreMax']}"
    )
    safety = shadow["safetyGate"]
    timing = shadow["timingMs"]
    print(
        "SafetyGate dry-run: "
        f"samples={safety['samples']} · authorized={safety['authorized']} · "
        f"failures={safety['failures']}"
    )
    print(
        "Timing ms: "
        f"legalMax={timing['legalMax']} · planningMax={timing['planningMax']} · "
        f"totalMax={timing['totalMax']} · totalMedian={timing['totalMedian']}"
    )
    print(f"Runtime ready: {report['runtimeReady']}")
    print(f"Activation ready: {report['activationReady']}")
    if report["blockers"]:
        print("Blockers:")
        for blocker in report["blockers"]:
            print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
