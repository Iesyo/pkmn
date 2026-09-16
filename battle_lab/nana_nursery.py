"""Conservative live-learning helpers for Nana 2.3 Nursery.

Nursery may pick one near-LIGHT alternative per BO1 and then learn from the real
transition produced by that intervention. Removing the first training wheels is
an evidence gate, never an automatic side effect of playing more games.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

from battle_lab.nana_light_critic import trust_for
from battle_lab.nana_self_critic import build_actor_summary, extract_observations


NURSERY_MODEL_VERSION = "nana2.3-nursery-live-v1"
SELF_CRITIC_MODEL_VERSION = "nana-self-critic-v1"
NURSERY_LAMBDA_CAP = 0.15
MAX_INTERVENTIONS_PER_BATTLE = 1
MIN_PREDICTION_CONFIDENCE = 0.08
MIN_ALLOWED_LIGHT_REGRET_LOG = -0.08
HIGH_LIGHT_TRUST_VETO = 0.96
HIGH_LIGHT_TRUST_CONFIDENCE = 0.35
SELF_PRIOR_TRUST = 0.50
SELF_PRIOR_WEIGHT = 6.0
SELF_LOW_TRUST_VETO = 0.35
SELF_LOW_TRUST_CONFIDENCE = 0.25
PROMOTION_INTERVENTION_WINDOW = 20
_EPS = 1e-12


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    return rendered if math.isfinite(rendered) else default


def _chronological_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = [
        (index, event)
        for index, event in enumerate(events)
        if isinstance(event, dict)
    ]
    indexed.sort(
        key=lambda item: (
            str(item[1].get("timestamp") or ""),
            item[0],
        )
    )
    return [event for _, event in indexed]


def build_self_summary(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return build_actor_summary(
        events,
        actor="nana",
        prior_trust=SELF_PRIOR_TRUST,
        prior_weight=SELF_PRIOR_WEIGHT,
        model_version=SELF_CRITIC_MODEL_VERSION,
        influence=1.0,
    )


def write_self_summary(profile_root: Path, summary: dict[str, Any]) -> Path:
    destination = Path(profile_root) / "nursery_experience.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def rebuild_self_for_recorder(recorder: Any) -> dict[str, Any]:
    summary = build_self_summary(recorder.iter_events())
    write_self_summary(recorder.profile_root, summary)
    return summary


def choose_candidate(
    plan: dict[str, Any],
    *,
    light_trust: dict[str, Any],
    self_trust: dict[str, Any],
    lambda_cap: float = NURSERY_LAMBDA_CAP,
) -> dict[str, Any]:
    """Choose a live Nursery alternative or return an explicit fallback reason."""

    if plan.get("eligible") is not True:
        return {"intervene": False, "reason": str(plan.get("reason") or "not-eligible")}

    confidence = _safe_float(plan.get("confidence"))
    if confidence < MIN_PREDICTION_CONFIDENCE:
        return {
            "intervene": False,
            "reason": "human-prediction-confidence-low",
            "confidence": confidence,
        }

    light_trust_value = _safe_float(light_trust.get("trust"), 0.90)
    light_trust_confidence = _safe_float(light_trust.get("confidence"))
    if (
        light_trust_confidence >= HIGH_LIGHT_TRUST_CONFIDENCE
        and light_trust_value >= HIGH_LIGHT_TRUST_VETO
    ):
        return {
            "intervene": False,
            "reason": "light-critic-high-trust-veto",
            "lightTrust": light_trust_value,
            "lightTrustConfidence": light_trust_confidence,
        }

    self_trust_value = _safe_float(self_trust.get("trust"), SELF_PRIOR_TRUST)
    self_trust_confidence = _safe_float(self_trust.get("confidence"))
    if (
        self_trust_confidence >= SELF_LOW_TRUST_CONFIDENCE
        and self_trust_value <= SELF_LOW_TRUST_VETO
    ):
        return {
            "intervene": False,
            "reason": "nana-self-low-trust-veto",
            "selfTrust": self_trust_value,
            "selfTrustConfidence": self_trust_confidence,
        }

    canonical = plan.get("canonical") if isinstance(plan.get("canonical"), dict) else {}
    canonical_counter = _safe_float(canonical.get("expectedCounter"))
    confidence_scale = max(0.0, min(1.0, _safe_float(plan.get("confidenceScale"))))
    effective_lambda = max(0.0, lambda_cap) * confidence_scale
    canonical_score = effective_lambda * canonical_counter

    ranked: list[tuple[float, dict[str, Any], float]] = []
    for candidate in plan.get("candidatePool") or []:
        if not isinstance(candidate, dict) or candidate.get("selectedByLight") is True:
            continue
        regret = _safe_float(candidate.get("lightRegretLog"))
        if regret < MIN_ALLOWED_LIGHT_REGRET_LOG:
            continue
        delta_counter = _safe_float(candidate.get("expectedCounter")) - canonical_counter
        if delta_counter <= _EPS:
            continue
        candidate_score = regret + effective_lambda * _safe_float(candidate.get("expectedCounter"))
        margin = candidate_score - canonical_score
        if margin <= _EPS:
            continue
        ranked.append((margin, candidate, delta_counter))

    if not ranked:
        return {
            "intervene": False,
            "reason": "no-live-candidate-inside-nursery-cap",
            "lambdaCap": lambda_cap,
            "effectiveLambda": effective_lambda,
        }

    ranked.sort(
        key=lambda item: (
            item[0],
            item[2],
            _safe_float(item[1].get("probability")),
        ),
        reverse=True,
    )
    margin, candidate, delta_counter = ranked[0]
    required_effective = max(
        0.0,
        -_safe_float(candidate.get("lightRegretLog")) / delta_counter,
    )
    required_cap = (
        required_effective / confidence_scale
        if confidence_scale > _EPS
        else math.inf
    )
    return {
        "intervene": True,
        "reason": "nursery-live-near-light",
        "lambdaCap": lambda_cap,
        "effectiveLambda": effective_lambda,
        "margin": margin,
        "requiredLambdaCap": required_cap,
        "expectedCounterDelta": delta_counter,
        "candidate": candidate,
        "lightTrust": light_trust_value,
        "lightTrustConfidence": light_trust_confidence,
        "selfTrust": self_trust_value,
        "selfTrustConfidence": self_trust_confidence,
    }


def promotion_status(
    events: Iterable[dict[str, Any]],
    *,
    teacher_key: str,
) -> dict[str, Any]:
    """Return an evidence-only recommendation for removing first wheels.

    Runtime/instrumentation failures are scoped to the current teacher and to the
    evidence window that would justify promotion. A single ancient transient
    failure therefore cannot lock Nana forever, while any recent failure still
    blocks additional autonomy.
    """

    materialized = _chronological_events(events)
    session_teacher: dict[str, str] = {}
    intervention_rows: list[tuple[int, dict[str, Any]]] = []

    for index, event in enumerate(materialized):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        session_id = str(event.get("sessionId") or "")
        if event.get("type") == "nana_teacher_version":
            teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
            key = str(teacher.get("key") or "")
            if session_id and key:
                session_teacher[session_id] = key
        if event.get("type") != "nana_nursery_decision":
            continue
        teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
        if str(teacher.get("key") or "") != teacher_key:
            continue
        if payload.get("intervened") is True:
            intervention_rows.append((index, payload))

    decisions = [payload for _, payload in intervention_rows]
    if len(intervention_rows) >= PROMOTION_INTERVENTION_WINDOW:
        window_start_index = intervention_rows[-PROMOTION_INTERVENTION_WINDOW][0]
    else:
        window_start_index = 0

    errors = 0
    error_types: dict[str, int] = {}
    for index, event in enumerate(materialized):
        if index < window_start_index:
            continue
        event_type = str(event.get("type") or "")
        if event_type not in {"nana_nursery_error", "nana_nursery_recording_error"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
        event_teacher_key = str(teacher.get("key") or "")
        if not event_teacher_key:
            event_teacher_key = session_teacher.get(str(event.get("sessionId") or ""), "")
        if event_teacher_key != teacher_key:
            continue
        errors += 1
        error_types[event_type] = error_types.get(event_type, 0) + 1

    observations = [
        item
        for item in extract_observations(materialized, actor_filter="nana")
        if str(item.get("teacherKey") or "") == teacher_key
    ]
    informative = [item for item in observations if item.get("label") in {"positive", "negative"}]
    positives = sum(item.get("label") == "positive" for item in informative)
    negatives = sum(item.get("label") == "negative" for item in informative)
    recent = observations[-10:]
    recent_negatives = sum(item.get("label") == "negative" for item in recent)
    regrets = [
        _safe_float((item.get("selection") or {}).get("candidate", {}).get("lightRegretLog"))
        for item in decisions
        if isinstance(item.get("selection"), dict)
    ]
    mean_regret = (sum(regrets) / len(regrets)) if regrets else None
    mean_delta = (
        sum(_safe_float(item.get("delta")) for item in observations) / len(observations)
        if observations
        else None
    )
    orphan_observations = max(0, len(observations) - len(decisions))

    candidate = (
        len(decisions) >= 20
        and len(observations) >= 15
        and len(informative) >= 8
        and positives >= negatives
        and recent_negatives <= 3
        and errors == 0
        and orphan_observations == 0
        and mean_regret is not None
        and mean_regret >= -0.07
    )
    return {
        "teacherKey": teacher_key,
        "interventions": len(decisions),
        "observedInterventionOutcomes": len(observations),
        "informativeOutcomes": len(informative),
        "positive": positives,
        "negative": negatives,
        "recent10Negative": recent_negatives,
        "meanObservedBoardDelta": mean_delta,
        "errors": errors,
        "errorTypes": error_types,
        "errorWindowInterventions": min(
            len(decisions), PROMOTION_INTERVENTION_WINDOW
        ),
        "orphanObservedOutcomes": orphan_observations,
        "meanLightRegretLog": mean_regret,
        "candidateForMoreAutonomy": candidate,
        "automaticPromotion": False,
        "whyNotAutomatic": (
            "Observed outcomes are still not counterfactual proof that Nana beat LIGHT."
        ),
        "nextLevelIfPromoted": {
            "maxInterventionsPerBattle": 2,
            "lambdaCap": 0.20,
        },
    }
