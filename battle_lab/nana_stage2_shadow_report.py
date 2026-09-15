"""Read-only report for Nana 2 shadow reranking.

The report evaluates only sessions explicitly marked as Nana 2 shadow. It never
changes Nana history, LIGHT, or predictor state. "Proxy" metrics refer only to
the narrow response_utility used by shadow v1; they are not counterfactual
battle win-rate claims.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT
from battle_lab.nana_stage2_shadow_runtime import (
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
                raise RuntimeError(
                    f"JSONL corrupto en {path}:{line_number}: {error.msg}."
                ) from error
            if isinstance(event, dict):
                events.append(event)
    return events


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _session_events(sessions_root: Path) -> Iterable[tuple[Path, list[dict[str, Any]]]]:
    for path in sorted(sessions_root.glob("*.jsonl")):
        events = _load_jsonl(path)
        if any(
            event.get("type") == "nana_stage2_shadow"
            and _payload(event).get("shadowModel") == STAGE2_MODEL_VERSION
            for event in events
        ):
            yield path, events


def _key(event: dict[str, Any]) -> tuple[str, int] | None:
    payload = _payload(event)
    generation = payload.get("generation")
    session_id = str(event.get("sessionId") or "")
    if not session_id or not isinstance(generation, int) or generation <= 0:
        return None
    return session_id, generation


def build_report(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    sessions_root = runtime_root / "nana" / "profiles" / profile_id / "sessions"
    if not sessions_root.is_dir():
        return {
            "profileId": profile_id,
            "shadowModel": STAGE2_MODEL_VERSION,
            "error": f"No existe el directorio de sesiones: {sessions_root}",
        }

    session_count = 0
    plans: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    evaluations: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    observed_indices: dict[tuple[str, int], int] = {}
    issues: list[str] = []

    global_index = 0
    for _path, events in _session_events(sessions_root):
        session_count += 1
        marker = next(
            (
                _payload(event)
                for event in events
                if event.get("type") == "nana_stage2_shadow"
            ),
            {},
        )
        if float(marker.get("influence", -1.0)) != 0.0:
            issues.append("Una sesión shadow no conserva influence=0.0.")
        for event in events:
            global_index += 1
            event_type = event.get("type")
            key = _key(event)
            if event_type == "human_choice_observed" and key is not None:
                observed_indices[key] = global_index
                continue
            if event_type == "nana_stage2_shadow_plan" and key is not None:
                if key in plans:
                    issues.append(f"Plan shadow duplicado para {key}.")
                plans[key] = (global_index, _payload(event))
                continue
            if event_type == "nana_stage2_shadow_evaluation" and key is not None:
                if key in evaluations:
                    issues.append(f"Evaluación shadow duplicada para {key}.")
                evaluations[key] = (global_index, _payload(event))

    if session_count == 0:
        return {
            "profileId": profile_id,
            "shadowModel": STAGE2_MODEL_VERSION,
            "sessions": 0,
            "error": "No hay sesiones Nana 2 shadow v1 todavía.",
        }

    if set(plans) != set(evaluations):
        issues.append("Planes y evaluaciones shadow no cubren las mismas generaciones.")

    rows: list[dict[str, Any]] = []
    for key in sorted(set(plans) & set(evaluations)):
        plan_index, plan_payload = plans[key]
        evaluation_index, evaluation_payload = evaluations[key]
        observed_index = observed_indices.get(key)
        if observed_index is not None:
            if not plan_index < observed_index < evaluation_index:
                issues.append(
                    f"Cronología inválida para {key}: plan debe preceder observación y evaluación."
                )
        elif plan_index >= evaluation_index:
            issues.append(f"Cronología inválida para {key}: evaluación precede al plan.")

        if float(plan_payload.get("influence", -1.0)) != 0.0:
            issues.append(f"Plan {key} no conserva influence=0.0.")
        plan = plan_payload.get("plan")
        plan = plan if isinstance(plan, dict) else {}
        evaluation = evaluation_payload.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        rows.append(
            {
                "sessionId": key[0],
                "generation": key[1],
                "turn": plan_payload.get("turn"),
                "eligible": plan.get("eligible") is True,
                "reason": plan.get("reason"),
                "humanPredictionTop1": evaluation.get("humanPredictionTop1") is True,
                "humanPredictionTop3": evaluation.get("humanPredictionTop3") is True,
                "plan": plan,
                "evaluation": evaluation,
            }
        )

    by_lambda: dict[str, dict[str, Any]] = {}
    for lambda_cap in LAMBDA_CAPS:
        eligible = 0
        changed = 0
        proxy = Counter()
        actual_delta_total = 0.0
        expected_delta_total = 0.0
        human_top1 = 0
        human_top3 = 0

        for row in rows:
            if not row["eligible"]:
                continue
            eligible += 1
            if row["humanPredictionTop1"]:
                human_top1 += 1
            if row["humanPredictionTop3"]:
                human_top3 += 1

            plan = row["plan"]
            canonical = plan.get("canonical") or {}
            plan_sweep = next(
                (
                    item
                    for item in plan.get("sweeps") or []
                    if isinstance(item, dict)
                    and abs(float(item.get("lambdaCap") or -1.0) - lambda_cap) < 1e-9
                ),
                None,
            )
            evaluation_sweep = next(
                (
                    item
                    for item in (row["evaluation"].get("sweeps") or [])
                    if isinstance(item, dict)
                    and abs(float(item.get("lambdaCap") or -1.0) - lambda_cap) < 1e-9
                ),
                None,
            )
            if not isinstance(plan_sweep, dict) or not isinstance(evaluation_sweep, dict):
                issues.append(
                    f"Falta sweep λ={lambda_cap:.2f} en {row['sessionId']}:{row['generation']}."
                )
                continue
            is_changed = plan_sweep.get("changed") is True
            if is_changed:
                changed += 1
                verdict = str(evaluation_sweep.get("proxyVerdict") or "tie")
                proxy[verdict] += 1
                actual_delta_total += float(
                    evaluation_sweep.get("actualCounterDelta") or 0.0
                )
                recommended = plan_sweep.get("recommended") or {}
                expected_delta_total += float(recommended.get("expectedCounter") or 0.0) - float(
                    canonical.get("expectedCounter") or 0.0
                )

        changed_denominator = changed if changed else 1
        by_lambda[f"{lambda_cap:.2f}"] = {
            "lambdaCap": lambda_cap,
            "eligible": eligible,
            "changed": changed,
            "interventionRate": (changed / eligible) if eligible else None,
            "humanPredictionTop1": human_top1,
            "humanPredictionTop3": human_top3,
            "humanPredictionTop1Accuracy": (human_top1 / eligible) if eligible else None,
            "humanPredictionTop3Accuracy": (human_top3 / eligible) if eligible else None,
            "proxyWins": proxy["win"],
            "proxyLosses": proxy["loss"],
            "proxyTies": proxy["tie"],
            "proxyWinRateChanged": (proxy["win"] / changed) if changed else None,
            "meanActualCounterDeltaChanged": actual_delta_total / changed_denominator if changed else None,
            "meanExpectedCounterDeltaChanged": expected_delta_total / changed_denominator if changed else None,
        }

    primary = by_lambda[f"{PRIMARY_LAMBDA_CAP:.2f}"]
    # This is only a shadow-candidate gate. Passing it never enables live
    # influence automatically; it merely says there is enough evidence to
    # design the next guarded experiment.
    gate = "collecting"
    if (
        primary["eligible"] >= 24
        and primary["changed"] >= 6
        and primary["proxyWins"] > primary["proxyLosses"]
        and float(primary["meanActualCounterDeltaChanged"] or 0.0) > 0.0
    ):
        gate = "candidate"

    reasons = Counter(str(row.get("reason") or "unknown") for row in rows if not row["eligible"])
    return {
        "profileId": profile_id,
        "shadowModel": STAGE2_MODEL_VERSION,
        "sessions": session_count,
        "decisions": len(rows),
        "eligibleDecisions": sum(1 for row in rows if row["eligible"]),
        "ineligibleReasons": dict(reasons),
        "primaryLambdaCap": PRIMARY_LAMBDA_CAP,
        "gate": gate,
        "byLambda": by_lambda,
        "issues": issues,
        "pass": not issues,
    }


def _pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value) * 100.0:.1f}%"


def _num(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):+.3f}"


def _print_report(report: dict[str, Any]) -> None:
    if report.get("error"):
        print(f"Nana 2 shadow report: ERROR\n{report['error']}")
        return
    print(f"Nana 2 shadow report: {'PASS' if report.get('pass') else 'FAIL'}")
    print(
        f"Profile: {report.get('profileId')} · model={report.get('shadowModel')} · "
        f"sessions={report.get('sessions')} · decisions={report.get('decisions')} · "
        f"eligible={report.get('eligibleDecisions')} · gate={report.get('gate')}"
    )
    if report.get("ineligibleReasons"):
        print(f"Ineligible: {report.get('ineligibleReasons')}")
    for key in sorted(report.get("byLambda") or {}, key=float):
        metrics = report["byLambda"][key]
        print(
            f"λcap={key}: eligible={metrics['eligible']} · changed={metrics['changed']} "
            f"({_pct(metrics.get('interventionRate'))})"
        )
        print(
            "  Human predictor: "
            f"top1={_pct(metrics.get('humanPredictionTop1Accuracy'))} · "
            f"top3={_pct(metrics.get('humanPredictionTop3Accuracy'))}"
        )
        print(
            "  Changed proxy: "
            f"W/L/T={metrics['proxyWins']}/{metrics['proxyLosses']}/{metrics['proxyTies']} · "
            f"winrate={_pct(metrics.get('proxyWinRateChanged'))} · "
            f"actual Δ={_num(metrics.get('meanActualCounterDeltaChanged'))} · "
            f"expected Δ={_num(metrics.get('meanExpectedCounterDeltaChanged'))}"
        )
    for issue in report.get("issues") or []:
        print(f"ERROR: {issue}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita Nana 2 shadow sin modificar LIGHT ni el historial."
    )
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
        _print_report(report)
    if report.get("error") or not report.get("pass", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
