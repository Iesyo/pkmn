"""Read-only Nana 2 shadow v2.2 LIGHT-critic report."""

from __future__ import annotations

import json
from typing import Sequence

from battle_lab import nana_stage2_shadow_v2_report as _report
from battle_lab import nana_stage2_shadow_v2_runtime as _runtime

STAGE2_MODEL_VERSION = "nana2-shadow-v2.2-light-critic"
_runtime.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION
_report.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION


def main(argv: Sequence[str] | None = None) -> int:
    args = _report.parse_args(argv)
    report = _report.build_report(
        args.runtime_root.expanduser().resolve(),
        args.nana_profile,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report.get("error"):
            print(f"Nana 2 shadow v2.2 report: ERROR\n{report['error']}")
        else:
            print(
                f"Nana 2 shadow v2.2 report: "
                f"{'PASS' if report.get('pass') else 'FAIL'}"
            )
            print(
                f"Profile: {report['profileId']} · model={report['shadowModel']} · "
                f"sessions={report['sessions']} · decisions={report['decisions']} · "
                f"eligible={report['eligibleDecisions']} · gate={report['gate']}"
            )
            print(
                f"Instrumentation errors isolated: "
                f"{report['shadowInstrumentationErrors']}"
            )
            if report.get("ineligibleReasons"):
                print(f"Ineligible: {report['ineligibleReasons']}")
            for key in sorted(report.get("byLambda") or {}, key=float):
                metric = report["byLambda"][key]
                print(
                    f"λcap={key}: eligible={metric['eligible']} · "
                    f"changed={metric['changed']} "
                    f"({_report._pct(metric['interventionRate'])}) · "
                    f"proxy coverage={_report._pct(metric['proxyCoverage'])}"
                )
                print(
                    f"  Human predictor: "
                    f"top1={_report._pct(metric['humanPredictionTop1Accuracy'])} · "
                    f"top3={_report._pct(metric['humanPredictionTop3Accuracy'])}"
                )
                print(
                    f"  Changed proxy: W/L/T={metric['proxyWins']}/"
                    f"{metric['proxyLosses']}/{metric['proxyTies']} · "
                    f"actual Δ={_report._num(metric['meanActualCounterDeltaChanged'])} · "
                    f"net eligible Δ={_report._num(metric['netActualCounterDeltaEligible'])} · "
                    f"expected Δ={_report._num(metric['meanExpectedCounterDeltaChanged'])} · "
                    f"LIGHT regret={_report._num(metric['meanLightRegretLogChanged'])}"
                )
            for issue in report.get("issues") or []:
                print(f"ERROR: {issue}")
    return 1 if report.get("error") or not report.get("pass", False) else 0


if __name__ == "__main__":
    raise SystemExit(main())
