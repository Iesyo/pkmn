"""Read-only Full Amiibo N4 planner over the complete legal order set.

This planner is intentionally independent of Nursery's lambda/branch filters.
It scores every LegalOrderSource candidate with common-space evidence that is
actually available:

- experience: observed board delta from Nana's own contextual history.
- counter: expected response_utility mapped empirically into board delta.
- teacher: reference/tie-break only until a validated regret->delta mapper exists.

No order is executed here. The result is shadow telemetry used to validate N4
before switching the live decision boundary.
"""

from __future__ import annotations

from typing import Any

from battle_lab import nana_light_critic as critic
from battle_lab.nana_contracts import order_key
from battle_lab.nana_counter_calibration import CounterCalibration
from battle_lab.nana_legal_orders import LegalOrderSet
from battle_lab.nana_scorer import (
    CandidateEvidence,
    NanaScorer,
    board_delta_term,
    map_to_board_delta,
)
from battle_lab.nana_stage2_shadow_v2_runtime import _expected_response_stats


N4_SHADOW_VERSION = "n4-shadow-common-score-v1"
EXPERIENCE_MIN_SAMPLES = 3
EXPERIENCE_LEVEL_ORDER = ("exact", "matchup", "coarse", "global")
REFERENCE_CONFIDENCE = 0.10
REFERENCE_WEIGHT = 0.25


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    return rendered if rendered == rendered and abs(rendered) != float("inf") else default


def _teacher_reference_key(light: dict[str, Any] | None) -> str | None:
    light = light if isinstance(light, dict) else {}
    for candidate in light.get("jointScores") or []:
        if (
            isinstance(candidate, dict)
            and candidate.get("selectedByLight") is True
            and isinstance(candidate.get("action"), dict)
        ):
            return order_key(candidate["action"])
    return None


def _experience_term(
    summary: dict[str, Any],
    state: dict[str, Any],
    action: dict[str, Any],
    light: dict[str, Any],
):
    keys = critic.context_keys(state, action, light)
    buckets = summary.get("buckets") if isinstance(summary.get("buckets"), dict) else {}
    for level in EXPERIENCE_LEVEL_ORDER:
        bucket = buckets.get(f"{level}:{keys[level]}")
        if not isinstance(bucket, dict):
            continue
        samples = int(bucket.get("samples") or 0)
        confidence = max(0.0, min(1.0, _safe_float(bucket.get("confidence"))))
        if samples < EXPERIENCE_MIN_SAMPLES or confidence <= 0:
            continue
        return board_delta_term(
            name="experience",
            value=_safe_float(bucket.get("meanDelta")),
            confidence=confidence,
            weight=1.0,
            source=f"nana-self-{level}-v1",
        )
    return None


def build_n4_shadow_plan(
    *,
    legal_orders: LegalOrderSet,
    light: dict[str, Any],
    prediction: dict[str, Any] | None,
    model_state: dict[str, Any],
    self_summary: dict[str, Any],
    counter_calibration: CounterCalibration,
    top_n: int = 5,
) -> dict[str, Any]:
    if legal_orders.resolved is not True:
        return {
            "version": N4_SHADOW_VERSION,
            "eligible": False,
            "reason": f"legal-orders-unresolved:{legal_orders.reason}",
        }

    reference_key = _teacher_reference_key(light)
    if reference_key is None or reference_key not in legal_orders.keys:
        return {
            "version": N4_SHADOW_VERSION,
            "eligible": False,
            "reason": "teacher-reference-not-in-legal-set",
            "legalTotal": len(legal_orders.candidates),
        }

    human_candidates = (
        [item for item in prediction.get("candidates") or [] if isinstance(item, dict)]
        if isinstance(prediction, dict)
        else []
    )
    prediction_confidence = max(
        0.0,
        min(1.0, _safe_float(prediction.get("confidence")) if isinstance(prediction, dict) else 0.0),
    )

    evidence: list[CandidateEvidence] = []
    diagnostics: dict[str, dict[str, Any]] = {}

    for candidate in legal_orders.candidates:
        terms = []
        sources: list[str] = []

        experience = _experience_term(
            self_summary,
            model_state,
            candidate.action,
            light,
        )
        if experience is not None:
            terms.append(experience)
            sources.append("experience")

        counter = _expected_response_stats(candidate.action, human_candidates)
        relevant_probability = max(
            0.0,
            min(1.0, _safe_float(counter.get("relevantProbability"))),
        )
        counter_confidence = (
            counter_calibration.confidence
            * prediction_confidence
            * relevant_probability
            if counter_calibration.resolved
            else 0.0
        )
        if counter_calibration.resolved and counter_confidence > 0:
            terms.append(
                map_to_board_delta(
                    name="counter",
                    source_value=_safe_float(counter.get("score")),
                    confidence=counter_confidence,
                    source_space=counter_calibration.source_space,
                    mapper_id=counter_calibration.mapper_id,
                    mapper=counter_calibration.map,
                    weight=1.0,
                )
            )
            sources.append("counter")

        # LIGHT is a fallback reference, not a filter. A tiny neutral floor keeps
        # the canonical order rankable when all common-space terms are absent.
        if candidate.key == reference_key:
            terms.append(
                board_delta_term(
                    name="heuristicFloor",
                    value=0.0,
                    confidence=REFERENCE_CONFIDENCE,
                    weight=REFERENCE_WEIGHT,
                    source="teacher-reference-neutral-v1",
                )
            )
            sources.append("reference")

        evidence.append(
            CandidateEvidence(
                key=candidate.key,
                terms=tuple(terms),
                # Only the canonical LIGHT order is an evidenced fallback
                # reference before regret->delta calibration exists. Other
                # teacher-representable orders receive no common-space credit.
                teacher_represented=candidate.key == reference_key,
                context_evidence=any(source != "reference" for source in sources),
            )
        )
        diagnostics[candidate.key] = {
            "orderKey": candidate.key,
            "action": candidate.action,
            "sources": sources,
            "counterRaw": _safe_float(counter.get("score")),
            "counterRelevantProbability": relevant_probability,
        }

    scorer = NanaScorer()
    selection = scorer.select(evidence, reference_key=reference_key)
    selected = selection.get("selected") if isinstance(selection.get("selected"), dict) else None
    selected_key = str(selected.get("orderKey") or "") if selected else ""
    reference = next(
        candidate for candidate in legal_orders.candidates if candidate.key == reference_key
    )

    ranked = selection.get("ranked") if isinstance(selection.get("ranked"), list) else []
    top = []
    for item in ranked[: max(1, int(top_n))]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("orderKey") or "")
        detail = diagnostics.get(key, {})
        top.append({**item, **detail})

    selected_candidate = (
        next(
            (candidate for candidate in legal_orders.candidates if candidate.key == selected_key),
            None,
        )
        if selected_key
        else None
    )
    return {
        "version": N4_SHADOW_VERSION,
        "eligible": True,
        "reason": str(selection.get("reason") or "unknown"),
        "legalTotal": len(legal_orders.candidates),
        "referenceKey": reference_key,
        "referenceAction": reference.action,
        "selectedKey": selected_key or None,
        "selectedAction": selected_candidate.action if selected_candidate is not None else None,
        "wouldChange": bool(selected_key and selected_key != reference_key),
        "commonScoreAvailable": sum(
            1 for item in ranked if isinstance(item, dict) and item.get("score") is not None
        ),
        "counterCalibration": counter_calibration.public(),
        "predictionConfidence": prediction_confidence,
        "top": top,
    }
