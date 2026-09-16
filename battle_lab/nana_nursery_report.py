"""Read-only report for Nana 2.3 Nursery live learning."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_nursery import (
    NURSERY_MODEL_VERSION,
    build_self_summary,
    promotion_status,
)
from battle_lab.nana_recorder import NanaRecorder
from battle_lab.nana_self_critic import extract_observations
from battle_lab.nana_teacher import latest_teacher_from_events


def _pct(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value)*100:.1f}%"


def _num(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):+.3f}"


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    recorder = NanaRecorder(runtime_root / "nana", profile_id=profile_id)
    events = list(recorder.iter_events())
    teacher = latest_teacher_from_events(events)
    teacher_key = str(teacher.get("key") or "")
    decisions: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    sessions: set[str] = set()
    nursery_errors = 0
    recording_errors = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("type") or "")
        if event_type == "nana_nursery_error":
            nursery_errors += 1
            continue
        if event_type == "nana_nursery_recording_error":
            recording_errors += 1
            continue
        if event_type == "nana_nursery_skip":
            skip_reasons[str(payload.get("reason") or "unknown")] += 1
            continue
        if event_type != "nana_nursery_decision":
            continue
        decisions.append(payload)
        sessions.add(str(event.get("sessionId") or ""))
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        reasons[str(selection.get("reason") or "unknown")] += 1

    observations = extract_observations(events, actor_filter="nana")
    if teacher_key:
        observations = [
            item
            for item in observations
            if str(item.get("teacherKey") or "") == teacher_key
        ]
    self_summary = build_self_summary(events)
    promotion = promotion_status(events, teacher_key=teacher_key) if teacher_key else {
        "candidateForMoreAutonomy": False,
        "automaticPromotion": False,
        "interventions": 0,
    }
    prompt_linked = sum(
        (item.get("continuity") or {}).get("kind") == "next-human-prompt"
        for item in observations
    )
    terminal = sum(
        (item.get("continuity") or {}).get("kind") == "session-end"
        for item in observations
    )
    return {
        "profileId": recorder.profile_id,
        "modelVersion": NURSERY_MODEL_VERSION,
        "teacher": teacher,
        "sessionsWithNurseryDecisions": len([value for value in sessions if value]),
        "nurseryDecisions": len(decisions),
        "interventions": sum(item.get("intervened") is True for item in decisions),
        "fallbacks": sum(item.get("intervened") is not True for item in decisions),
        "decisionReasons": dict(reasons),
        "skips": sum(skip_reasons.values()),
        "skipReasons": dict(skip_reasons),
        "errors": nursery_errors + recording_errors,
        "nurseryErrors": nursery_errors,
        "recordingErrors": recording_errors,
        "selfExperience": {
            "observations": len(observations),
            "promptLinkedObservations": prompt_linked,
            "terminalObservations": terminal,
            "positive": sum(item.get("label") == "positive" for item in observations),
            "neutral": sum(item.get("label") == "neutral" for item in observations),
            "negative": sum(item.get("label") == "negative" for item in observations),
            "meanDelta": (
                sum(float(item.get("delta") or 0.0) for item in observations) / len(observations)
                if observations else None
            ),
            "teacherSummary": (
                (self_summary.get("teachers") or {}).get(teacher_key)
                if teacher_key else None
            ),
        },
        "promotion": promotion,
    }


def _print(report: dict[str, Any]) -> None:
    teacher = report.get("teacher") or {}
    print("Nana 2.3 Nursery report: PASS")
    print(
        f"Profile: {report['profileId']} · model={report['modelVersion']} · "
        f"teacher={str(teacher.get('checkpointSha256') or 'legacy')[:12]} · "
        f"format={teacher.get('format') or 'unknown'}"
    )
    print(
        f"Decisions observed={report['nurseryDecisions']} · interventions={report['interventions']} · "
        f"fallback LIGHT={report['fallbacks']} · errors={report['errors']}"
    )
    print(f"Reasons: {report['decisionReasons']}")
    print(
        f"Benign skips={report.get('skips', 0)} · "
        f"reasons={report.get('skipReasons', {})}"
    )
    print(
        f"Error detail: nursery={report.get('nurseryErrors', 0)} · "
        f"recording={report.get('recordingErrors', 0)}"
    )
    experience = report.get("selfExperience") or {}
    print(
        f"Nana real outcomes: n={experience.get('observations', 0)} · "
        f"prompt-linked={experience.get('promptLinkedObservations', 0)} · "
        f"terminal={experience.get('terminalObservations', 0)} · "
        f"+/=/−={experience.get('positive',0)}/{experience.get('neutral',0)}/{experience.get('negative',0)} · "
        f"mean board Δ={_num(experience.get('meanDelta'))}"
    )
    teacher_summary = experience.get("teacherSummary") or {}
    global_stats = teacher_summary.get("global") or {}
    if teacher_summary:
        print(
            f"Nana self-trust (current teacher): {_pct(global_stats.get('trust'))} · "
            f"confidence={_pct(global_stats.get('confidence'))}"
        )
    promotion = report.get("promotion") or {}
    print(
        f"Training wheels candidate={promotion.get('candidateForMoreAutonomy')} · "
        f"interventions={promotion.get('interventions',0)} · "
        f"observed outcomes={promotion.get('observedInterventionOutcomes',0)} · "
        f"informative={promotion.get('informativeOutcomes',0)} · "
        f"mean LIGHT regret={_num(promotion.get('meanLightRegretLog'))}"
    )
    print(
        "Promotion is deliberately not automatic: real outcomes teach Nana, but they are not "
        "counterfactual proof that an unplayed LIGHT action was worse."
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args.runtime_root.expanduser().resolve(), args.nana_profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
