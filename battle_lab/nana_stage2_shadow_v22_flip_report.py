"""Read-only flip-margin diagnostic for Nana 2 shadow v2.2 LIGHT critic."""

from __future__ import annotations

import json
from typing import Sequence

from battle_lab import nana_stage2_shadow_v21_flip_report as _report

STAGE2_MODEL_VERSION = "nana2-shadow-v2.2-light-critic"
_report.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION


def main(argv: Sequence[str] | None = None) -> int:
    args = _report.parse_args(argv)
    report = _report.build_report(
        args.runtime_root.expanduser().resolve(),
        args.nana_profile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        if report.get("error"):
            print(f"Nana 2 v2.2 flip report: ERROR\n{report['error']}")
        else:
            print("Nana 2 v2.2 flip-margin report: PASS")
            print(
                f"Profile: {report['profileId']} · model={report['shadowModel']} · "
                f"sessions={report['sessions']} · plans={report['plans']} · "
                f"eligible={report['eligible']}"
            )
            print(
                f"Pool: with alternatives={report['withAlternatives']}/"
                f"{report['eligible']} · positive proxy alternative="
                f"{report['withPositiveProxyAlternative']}/{report['eligible']}"
            )
            print(f"Reasons: {report['reasonCounts']}")
            print(
                "Required λcap: "
                f"min={_report._fmt(report.get('minRequiredLambdaCap'))} · "
                f"median={_report._fmt(report.get('medianRequiredLambdaCap'))} · "
                f"mean closest margin at current 0.20="
                f"{_report._fmt(report.get('meanClosestMarginAtCurrentCap'))}"
            )
            counts = report.get("thresholdFlipCounts") or {}
            print(
                "Would strictly flip by cap (diagnostic only): "
                + " · ".join(
                    f"≤~{key}: {value}" for key, value in counts.items()
                )
            )
            for row in report.get("details") or []:
                best = row.get("best") or {}
                print(
                    f"  gen={row.get('generation')} turn={row.get('turn')} "
                    f"pool={row.get('poolSize')} "
                    f"scale={_report._fmt(row.get('confidenceScale'))} · "
                    f"required λcap="
                    f"{_report._fmt(best.get('requiredLambdaCap'))} · "
                    f"proxy Δ={_report._fmt(best.get('deltaCounter'))} · "
                    f"LIGHT regret={_report._fmt(best.get('lightRegretLog'))} · "
                    f"margin@.20={_report._fmt(best.get('marginAtCurrentCap'))}"
                )
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
