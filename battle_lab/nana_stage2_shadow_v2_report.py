"""Read-only audit/report for Nana 2 shadow v2.

Only v2-marked sessions are considered. The report checks chronology/integrity,
keeps lambda=0 as a permanent control, reports proxy coverage and the LIGHT
regret paid by interventions, and deliberately defines no automatic live gate.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_stage2_shadow_v2_runtime import (
    LAMBDA_CAPS,
    PRIMARY_LAMBDA_CAP,
    STAGE2_MODEL_VERSION,
)


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
                raise RuntimeError(f"JSONL corrupto en {path}:{line_number}: {error.msg}") from error
            if isinstance(event, dict):
                events.append(event)
    return events


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _session_events(root: Path) -> Iterable[list[dict[str, Any]]]:
    for path in sorted(root.glob("*.jsonl")):
        events = _load_jsonl(path)
        if any(
            event.get("type") == "nana_stage2_shadow_v2"
            and _payload(event).get("shadowModel") == STAGE2_MODEL_VERSION
            for event in events
        ):
            yield events


def _key(event: dict[str, Any]) -> tuple[str, int] | None:
    payload = _payload(event)
    generation = payload.get("generation")
    session_id = str(event.get("sessionId") or "")
    if not session_id or not isinstance(generation, int) or generation <= 0:
        return None
    return session_id, generation


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    sessions_root = runtime_root / "nana" / "profiles" / profile_id / "sessions"
    if not sessions_root.is_dir():
        return {"profileId": profile_id, "shadowModel": STAGE2_MODEL_VERSION, "error": f"No existe {sessions_root}"}

    sessions = 0
    plans: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    evaluations: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    observed: dict[tuple[str, int], int] = {}
    errors: list[str] = []
    shadow_errors = 0
    index = 0

    for events in _session_events(sessions_root):
        sessions += 1
        marker = next((_payload(event) for event in events if event.get("type") == "nana_stage2_shadow_v2"), {})
        if _safe_float(marker.get("influence")) != 0.0:
            errors.append("Una sesión v2 no conserva influence=0.0.")
        for event in events:
            index += 1
            key = _key(event)
            event_type = event.get("type")
            if event_type == "nana_stage2_shadow_error":
                shadow_errors += 1
            elif event_type == "human_choice_observed" and key is not None:
                observed[key] = index
            elif event_type == "nana_stage2_shadow_v2_plan" and key is not None:
                if key in plans:
                    errors.append(f"Plan v2 duplicado para {key}.")
                plans[key] = (index, _payload(event))
            elif event_type == "nana_stage2_shadow_v2_evaluation" and key is not None:
                if key in evaluations:
                    errors.append(f"Evaluación v2 duplicada para {key}.")
                evaluations[key] = (index, _payload(event))

    if sessions == 0:
        return {"profileId": profile_id, "shadowModel": STAGE2_MODEL_VERSION, "sessions": 0, "error": "No hay sesiones Nana 2 shadow v2."}

    if set(plans) != set(evaluations):
        errors.append("Planes y evaluaciones v2 no cubren las mismas generaciones.")

    rows: list[dict[str, Any]] = []
    for key in sorted(set(plans) & set(evaluations)):
        plan_index, plan_payload = plans[key]
        evaluation_index, evaluation_payload = evaluations[key]
        observed_index = observed.get(key)
        if observed_index is None or not plan_index < observed_index < evaluation_index:
            errors.append(f"Cronología no auditable para {key}.")
        if _safe_float(plan_payload.get("influence")) != 0.0:
            errors.append(f"Plan {key} no conserva influence=0.0.")

        source = str(plan_payload.get("predictionSource") or "")
        generation = int(plan_payload.get("predictionGeneration") or 0)
        turn_matched = plan_payload.get("turnMatched") is True
        plan = plan_payload.get("plan") if isinstance(plan_payload.get("plan"), dict) else {}
        evaluation = evaluation_payload.get("evaluation") if isinstance(evaluation_payload.get("evaluation"), dict) else {}

        if plan.get("eligible") is True:
            if source != "snapshot-cache":
                errors.append(f"Plan elegible {key} no usa predicción pre-choice.")
            if generation != key[1]:
                errors.append(f"Plan elegible {key} tiene generation inconsistente.")
            if not turn_matched:
                errors.append(f"Plan elegible {key} no tiene turn match exacto.")
            zero = next((s for s in plan.get("sweeps") or [] if isinstance(s, dict) and abs(_safe_float(s.get("lambdaCap"))) < 1e-12), None)
            if not isinstance(zero, dict) or zero.get("changed") is True:
                errors.append(f"Control λ=0 divergió de LIGHT en {key}.")
            canonical = plan.get("canonical") or {}
            if abs(_safe_float(canonical.get("lightRegretLog"))) > 1e-9:
                errors.append(f"Canónica con regret no cero en {key}.")

        rows.append({
            "sessionId": key[0],
            "generation": key[1],
            "eligible": plan.get("eligible") is True,
            "reason": str(plan.get("reason") or "unknown"),
            "predictionSource": source,
            "turnMatched": turn_matched,
            "plan": plan,
            "evaluation": evaluation,
        })

    by_lambda: dict[str, dict[str, Any]] = {}
    for lambda_cap in LAMBDA_CAPS:
        eligible = changed = relevant = 0
        wins = losses = ties = 0
        actual_delta_sum = expected_delta_sum = regret_sum = 0.0
        top1 = top3 = 0
        for row in rows:
            if not row["eligible"]:
                continue
            eligible += 1
            evaluation = row["evaluation"]
            if evaluation.get("humanPredictionTop1") is True:
                top1 += 1
            if evaluation.get("humanPredictionTop3") is True:
                top3 += 1
            plan = row["plan"]
            canonical = plan.get("canonical") or {}
            plan_sweep = next((s for s in plan.get("sweeps") or [] if isinstance(s, dict) and abs(_safe_float(s.get("lambdaCap")) - lambda_cap) < 1e-9), None)
            eval_sweep = next((s for s in evaluation.get("sweeps") or [] if isinstance(s, dict) and abs(_safe_float(s.get("lambdaCap")) - lambda_cap) < 1e-9), None)
            if not isinstance(plan_sweep, dict) or not isinstance(eval_sweep, dict):
                errors.append(f"Falta sweep λ={lambda_cap:.2f} en {row['sessionId']}:{row['generation']}.")
                continue
            if eval_sweep.get("counterRelevant") is True:
                relevant += 1
            if plan_sweep.get("changed") is not True:
                continue
            changed += 1
            verdict = str(eval_sweep.get("proxyVerdict") or "tie")
            wins += verdict == "win"
            losses += verdict == "loss"
            ties += verdict == "tie"
            actual_delta_sum += _safe_float(eval_sweep.get("actualCounterDelta"))
            recommended = plan_sweep.get("recommended") or {}
            expected_delta_sum += _safe_float(recommended.get("expectedCounter")) - _safe_float(canonical.get("expectedCounter"))
            regret_sum += _safe_float(recommended.get("lightRegretLog"))

        by_lambda[f"{lambda_cap:.2f}"] = {
            "lambdaCap": lambda_cap,
            "eligible": eligible,
            "changed": changed,
            "interventionRate": (changed / eligible) if eligible else None,
            "relevantDecisions": relevant,
            "proxyCoverage": (relevant / eligible) if eligible else None,
            "humanPredictionTop1Accuracy": (top1 / eligible) if eligible else None,
            "humanPredictionTop3Accuracy": (top3 / eligible) if eligible else None,
            "proxyWins": wins,
            "proxyLosses": losses,
            "proxyTies": ties,
            "meanActualCounterDeltaChanged": (actual_delta_sum / changed) if changed else None,
            "netActualCounterDeltaEligible": (actual_delta_sum / eligible) if eligible else None,
            "meanExpectedCounterDeltaChanged": (expected_delta_sum / changed) if changed else None,
            "meanLightRegretLogChanged": (regret_sum / changed) if changed else None,
        }

    reasons = Counter(row["reason"] for row in rows if not row["eligible"])
    return {
        "profileId": profile_id,
        "shadowModel": STAGE2_MODEL_VERSION,
        "sessions": sessions,
        "decisions": len(rows),
        "eligibleDecisions": sum(1 for row in rows if row["eligible"]),
        "ineligibleReasons": dict(reasons),
        "shadowInstrumentationErrors": shadow_errors,
        "primaryLambdaCap": PRIMARY_LAMBDA_CAP,
        "gate": "collecting-auditable-no-live-gate-defined",
        "byLambda": by_lambda,
        "issues": errors,
        "pass": not errors,
    }


def _pct(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value)*100:.1f}%"


def _num(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):+.3f}"


def _print(report: dict[str, Any]) -> None:
    if report.get("error"):
        print(f"Nana 2 shadow v2 report: ERROR\n{report['error']}")
        return
    print(f"Nana 2 shadow v2 report: {'PASS' if report.get('pass') else 'FAIL'}")
    print(
        f"Profile: {report['profileId']} · model={report['shadowModel']} · sessions={report['sessions']} · "
        f"decisions={report['decisions']} · eligible={report['eligibleDecisions']} · gate={report['gate']}"
    )
    print(f"Instrumentation errors isolated: {report['shadowInstrumentationErrors']}")
    if report.get("ineligibleReasons"):
        print(f"Ineligible: {report['ineligibleReasons']}")
    for key in sorted(report.get("byLambda") or {}, key=float):
        m = report["byLambda"][key]
        print(f"λcap={key}: eligible={m['eligible']} · changed={m['changed']} ({_pct(m['interventionRate'])}) · proxy coverage={_pct(m['proxyCoverage'])}")
        print(f"  Human predictor: top1={_pct(m['humanPredictionTop1Accuracy'])} · top3={_pct(m['humanPredictionTop3Accuracy'])}")
        print(
            f"  Changed proxy: W/L/T={m['proxyWins']}/{m['proxyLosses']}/{m['proxyTies']} · "
            f"actual Δ={_num(m['meanActualCounterDeltaChanged'])} · net eligible Δ={_num(m['netActualCounterDeltaEligible'])} · "
            f"expected Δ={_num(m['meanExpectedCounterDeltaChanged'])} · LIGHT regret={_num(m['meanLightRegretLogChanged'])}"
        )
    for issue in report.get("issues") or []:
        print(f"ERROR: {issue}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audita Nana 2 shadow v2 sin modificar LIGHT.")
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
    return 1 if report.get("error") or not report.get("pass", False) else 0


if __name__ == "__main__":
    raise SystemExit(main())
