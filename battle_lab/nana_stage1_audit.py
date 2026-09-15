"""Read-only audit for Nana 1 observational prediction sessions."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from battle_lab.nana_audit import DEFAULT_RUNTIME_ROOT, audit_latest
from battle_lab.nana_predictor import action_signature


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _finite_probability(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def audit_stage1(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    base = audit_latest(runtime_root, profile_id)
    issues = list(base.get("issues") or [])
    warnings = list(base.get("warnings") or [])

    session_file = base.get("sessionFile")
    if not session_file:
        return {
            **base,
            "pass": False,
            "issues": issues + ["Nana 0 no produjo sessionFile para auditar Nana 1."],
            "warnings": warnings,
        }

    events = _load_jsonl(Path(session_file))
    stages = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("type") == "nana_stage"
    ]
    stage1 = [
        (index, event)
        for index, event in stages
        if _payload(event).get("stage") == 1
    ]
    if len(stage1) != 1:
        issues.append("Debe existir exactamente un evento nana_stage=1.")
    else:
        influence = _payload(stage1[0][1]).get("influence")
        if not isinstance(influence, (int, float)) or float(influence) != 0.0:
            issues.append("Nana 1 no conserva influence=0.0.")

    predictions: dict[int, tuple[int, dict[str, Any]]] = {}
    observations: dict[int, tuple[int, dict[str, Any]]] = {}

    for index, event in enumerate(events):
        event_type = event.get("type")
        payload = _payload(event)
        generation = payload.get("generation")
        if event_type not in {"human_prediction", "human_choice_observed"}:
            continue
        if not isinstance(generation, int) or generation <= 0:
            issues.append(f"{event_type} sin generation válida.")
            continue
        destination = predictions if event_type == "human_prediction" else observations
        if generation in destination:
            issues.append(f"{event_type} duplicado para generation={generation}.")
            continue
        destination[generation] = (index, payload)

    if not predictions:
        issues.append("No se registraron human_prediction.")
    if not observations:
        issues.append("No se registraron human_choice_observed.")
    if set(predictions) != set(observations):
        issues.append("Predicciones y observaciones no cubren las mismas generaciones.")

    ordered_history_samples: list[int] = []
    top1_hits = 0
    top3_hits = 0
    for generation in sorted(set(predictions) & set(observations)):
        prediction_index, prediction = predictions[generation]
        observation_index, observation = observations[generation]
        prefix = f"generation {generation}"
        if prediction_index >= observation_index:
            issues.append(f"{prefix}: la predicción no precede a la observación.")

        source = prediction.get("source")
        if source not in {"snapshot", "submit-fallback"}:
            issues.append(f"{prefix}: source de predicción inválido.")
        if source == "submit-fallback":
            warnings.append(
                f"{prefix}: predicción creada al recibir submit; válida para no-leakage "
                "del modelo, pero conviene confirmar polling previo."
            )

        history_samples = prediction.get("historySamples")
        if not isinstance(history_samples, int) or history_samples < 0:
            issues.append(f"{prefix}: historySamples inválido.")
        else:
            ordered_history_samples.append(history_samples)

        if not _finite_probability(prediction.get("confidence")):
            issues.append(f"{prefix}: confidence inválida.")
        entropy = prediction.get("entropy")
        if (
            not isinstance(entropy, (int, float))
            or not math.isfinite(float(entropy))
            or not 0.0 <= float(entropy) <= 1.0
        ):
            issues.append(f"{prefix}: entropy inválida.")

        candidate_count = prediction.get("candidateCount")
        if not isinstance(candidate_count, int) or candidate_count <= 0:
            issues.append(f"{prefix}: candidateCount inválido.")

        top = prediction.get("top")
        if not isinstance(top, list) or not top:
            issues.append(f"{prefix}: top de predicción vacío.")
            top = []
        elif len(top) > 3:
            issues.append(f"{prefix}: top contiene más de 3 candidatos.")

        legal = observation.get("legalActions")
        action = observation.get("action")
        if not isinstance(legal, list) or not legal:
            issues.append(f"{prefix}: observación sin legalActions.")
            legal = []
        if not isinstance(action, dict):
            issues.append(f"{prefix}: observación sin action.")
            action = {}
        legal_ids = {
            candidate.get("id")
            for candidate in legal
            if isinstance(candidate, dict) and candidate.get("id") is not None
        }
        if action.get("id") not in legal_ids:
            issues.append(f"{prefix}: acción observada no pertenece a legalActions.")

        signature = action_signature(action)
        if observation.get("actionSignature") != signature:
            issues.append(f"{prefix}: actionSignature no coincide con action.")

        top_ids = []
        for candidate in top:
            if not isinstance(candidate, dict):
                issues.append(f"{prefix}: candidato top inválido.")
                continue
            if candidate.get("id") not in legal_ids:
                issues.append(f"{prefix}: candidato top no era legal.")
            if not _finite_probability(candidate.get("probability")):
                issues.append(f"{prefix}: candidato top con probabilidad inválida.")
            top_ids.append(candidate.get("id"))

        evaluation = observation.get("prediction")
        if not isinstance(evaluation, dict):
            issues.append(f"{prefix}: falta evaluación de la predicción.")
            continue
        rank = evaluation.get("rank")
        if not isinstance(rank, int) or rank < 1:
            issues.append(f"{prefix}: rank de acción elegida inválido.")
        if not _finite_probability(evaluation.get("chosenProbability")):
            issues.append(f"{prefix}: chosenProbability inválida.")
        expected_top1 = rank == 1
        expected_top3 = isinstance(rank, int) and 1 <= rank <= 3
        if evaluation.get("top1Hit") is not expected_top1:
            issues.append(f"{prefix}: top1Hit inconsistente con rank.")
        if evaluation.get("top3Hit") is not expected_top3:
            issues.append(f"{prefix}: top3Hit inconsistente con rank.")
        if expected_top1:
            top1_hits += 1
        if expected_top3:
            top3_hits += 1
        if top_ids and expected_top1 and action.get("id") != top_ids[0]:
            issues.append(f"{prefix}: rank=1 no coincide con primer candidato top.")

    for previous, current in zip(ordered_history_samples, ordered_history_samples[1:]):
        if current != previous + 1:
            issues.append(
                "historySamples no crece exactamente +1 entre decisiones consecutivas."
            )
            break

    predictor_path = runtime_root / "nana" / "profiles" / profile_id / "predictor.json"
    predictor_summary: dict[str, Any] = {}
    if not predictor_path.is_file():
        issues.append("No existe predictor.json.")
    else:
        loaded = _load_json(predictor_path)
        predictor_summary = loaded if isinstance(loaded, dict) else {}
        if predictor_summary.get("profileId") != profile_id:
            issues.append("predictor.json pertenece a otro profileId.")
        if predictor_summary.get("stage") != 1:
            issues.append("predictor.json no está marcado como stage=1.")
        metrics = predictor_summary.get("metrics")
        if not isinstance(metrics, dict):
            issues.append("predictor.json no contiene metrics.")
        else:
            evaluated = metrics.get("evaluated")
            if not isinstance(evaluated, int) or evaluated < len(observations):
                issues.append(
                    "predictor.json evaluó menos observaciones que la sesión auditada."
                )

    return {
        **base,
        "pass": not issues,
        "issues": issues,
        "warnings": warnings,
        "stage": 1,
        "predictions": len(predictions),
        "observations": len(observations),
        "top1Hits": top1_hits,
        "top3Hits": top3_hits,
        "predictor": predictor_summary,
    }


def _print_report(report: dict[str, Any]) -> None:
    status = "PASS" if report.get("pass") else "FAIL"
    observations = int(report.get("observations") or 0)
    top1_hits = int(report.get("top1Hits") or 0)
    top3_hits = int(report.get("top3Hits") or 0)
    print(f"Nana 1 audit: {status}")
    print(f"Profile: {report.get('profileId')}")
    print(
        "Recorder/LIGHT: "
        f"session={report.get('sessionId')} · turns={report.get('turnChoices')} · "
        f"LIGHT={report.get('lightDecisionEvents')}"
    )
    print(
        "Predictor: "
        f"predictions={report.get('predictions')} · observations={observations} · "
        f"top1={top1_hits}/{observations} · top3={top3_hits}/{observations}"
    )
    predictor = report.get("predictor")
    if isinstance(predictor, dict):
        print(
            "Profile predictor: "
            f"historySamples={predictor.get('historySamples')} · "
            f"metrics={predictor.get('metrics')}"
        )
    for warning in report.get("warnings") or []:
        print(f"WARN: {warning}")
    for issue in report.get("issues") or []:
        print(f"ERROR: {issue}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audita la última sesión Nana 1 sin modificarla."
    )
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_stage1(args.runtime_root.expanduser().resolve(), args.nana_profile)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
