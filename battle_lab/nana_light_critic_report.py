"""Read-only report for Nana's observational LIGHT critic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_light_critic import PRIOR_TRUST, build_summary
from battle_lab.nana_recorder import NanaRecorder


def _pct(value: Any) -> str:
    return (
        "n/a"
        if not isinstance(value, (int, float))
        else f"{float(value) * 100:.1f}%"
    )


def _num(value: Any) -> str:
    return (
        "n/a"
        if not isinstance(value, (int, float))
        else f"{float(value):+.3f}"
    )


def _interesting_contexts(
    summary: dict[str, Any],
    *,
    low: bool,
) -> list[dict[str, Any]]:
    buckets = (
        summary.get("buckets")
        if isinstance(summary.get("buckets"), dict)
        else {}
    )
    candidates = [
        bucket
        for bucket in buckets.values()
        if isinstance(bucket, dict)
        and bucket.get("level") in {"coarse", "matchup", "exact"}
        and int(bucket.get("samples") or 0) >= 2
        and float(bucket.get("effectiveWeight") or 0.0) >= 0.75
    ]
    candidates.sort(
        key=lambda item: (
            float(item.get("trust") or PRIOR_TRUST),
            -float(item.get("effectiveWeight") or 0.0),
        ),
        reverse=not low,
    )
    return candidates[:5]


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    recorder = NanaRecorder(runtime_root / "nana", profile_id=profile_id)
    summary = build_summary(recorder.iter_events())
    return {"profileId": recorder.profile_id, **summary}


def _print(report: dict[str, Any]) -> None:
    print("Nana LIGHT critic report: PASS")
    print(
        f"Profile: {report['profileId']} · model={report['modelVersion']} · "
        f"observations={report['observations']} · causal={report['causalStatus']} · "
        f"influence={report['influence']:.1f}"
    )
    global_stats = report.get("global") or {}
    print(
        f"Global LIGHT trust: {_pct(global_stats.get('trust'))} · "
        f"confidence={_pct(global_stats.get('confidence'))} · "
        f"mean local Δ={_num(global_stats.get('meanDelta'))}"
    )
    labels = report.get("labels") or {}
    print(
        f"Observed local outcomes: +/=/− = {labels.get('positive', 0)}/"
        f"{labels.get('neutral', 0)}/{labels.get('negative', 0)}"
    )
    for name in ("recent10", "recent30"):
        recent = report.get(name) or {}
        print(
            f"{name}: n={recent.get('n', 0)} · "
            f"mean Δ={_num(recent.get('meanDelta'))} · "
            f"outcome score={_pct(recent.get('meanOutcomeScore'))} · "
            f"+/=/−={recent.get('positive', 0)}/"
            f"{recent.get('neutral', 0)}/{recent.get('negative', 0)}"
        )

    low = _interesting_contexts(report, low=True)
    high = _interesting_contexts(report, low=False)
    if low:
        print(
            "Lower-trust contexts (observational; not causal proof of mistakes):"
        )
        for item in low:
            print(
                f"  trust={_pct(item.get('trust'))} · "
                f"conf={_pct(item.get('confidence'))} · "
                f"n={item.get('samples')} · Δ={_num(item.get('meanDelta'))} · "
                f"{item.get('key')}"
            )
    if high:
        print("Higher-trust contexts:")
        for item in high:
            print(
                f"  trust={_pct(item.get('trust'))} · "
                f"conf={_pct(item.get('confidence'))} · "
                f"n={item.get('samples')} · Δ={_num(item.get('meanDelta'))} · "
                f"{item.get('key')}"
            )
    print(
        "NOTE: trust is learned from the played LIGHT branch only. A negative "
        "outcome is evidence, not proof that an unplayed alternative would have "
        "been better."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        args.runtime_root.expanduser().resolve(),
        args.nana_profile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
