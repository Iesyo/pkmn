"""Nana 2 shadow reranking runtime.

Nana 2 starts in *shadow* mode: it computes conservative alternatives on top
of calibrated Nana 1 + frozen LIGHT M-C, records what it would have changed,
but the real battle order still comes from LIGHT unchanged.

The first response model is intentionally narrow and interpretable. It only
rewards/penalizes direct interactions we can reason about safely from the
predicted human order (for example Protect avoidance / Protect response). It
is not presented as a full battle-value model.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Sequence

import numpy as np

from battle_lab import local_sparring_service as sparring
from battle_lab.nana_policy import inspect_light_decision
from battle_lab.nana_predictor import action_signature
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage1_calibrated_runtime import (
    MODEL_VERSION as PREDICTOR_MODEL_VERSION,
    install_calibrated_stage1_service,
)


STAGE2_MODEL_VERSION = "nana2-shadow-v1"
LAMBDA_CAPS = (0.05, 0.10, 0.20)
PRIMARY_LAMBDA_CAP = 0.10
MIN_SHADOW_CONFIDENCE = 0.03
CONFIDENCE_REFERENCE = 0.15
MIN_LIGHT_PROBABILITY_RATIO = 0.50
_EPS = 1e-12

# Self-protection moves whose effect is clear enough for the first shadow
# response proxy. Keep this list deliberately small rather than pretending to
# model every defensive interaction in VGC.
_PROTECT_LIKE = {
    "protect",
    "detect",
    "kingsshield",
    "spikyshield",
    "banefulbunker",
    "burningbulwark",
    "obstruct",
    "silktrap",
}
_PROTECT_BREAKERS = {"feint"}


def _token(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _is_protect_like(half: Any) -> bool:
    return isinstance(half, dict) and half.get("kind") == "move" and _token(half.get("value")) in _PROTECT_LIKE


def _is_protect_breaker(half: Any) -> bool:
    return isinstance(half, dict) and half.get("kind") == "move" and _token(half.get("value")) in _PROTECT_BREAKERS


def _opponent_target_slot(half: Any) -> int | None:
    """Map Showdown/poke-env direct foe target -1/-2 to zero-based slot.

    Spread/self/ally/implicit targets intentionally return None; the shadow
    heuristic must not guess at target semantics it cannot establish safely.
    """

    if not isinstance(half, dict) or half.get("kind") != "move":
        return None
    try:
        target = int(half.get("target") or 0)
    except (TypeError, ValueError):
        return None
    if target not in {-1, -2}:
        return None
    return abs(target) - 1


def response_utility(model_action: Any, human_action: Any) -> dict[str, Any]:
    """Return a conservative, interpretable response proxy in [-1, 1].

    Positive means the model action better counters the supplied human action
    under the tiny rule set we are willing to trust in Nana 2 shadow v1.
    """

    if not isinstance(model_action, dict) or not isinstance(human_action, dict):
        return {"score": 0.0, "relevant": 0, "components": []}

    model_halves = [model_action.get("first"), model_action.get("second")]
    human_halves = [human_action.get("first"), human_action.get("second")]
    components: list[dict[str, Any]] = []
    total = 0.0
    relevant = 0

    # If the human is predicted to Protect a slot, direct attacks into that
    # slot are bad; Feint is the one explicit breaker modeled in v1.
    for human_slot, human_half in enumerate(human_halves):
        if not _is_protect_like(human_half):
            continue
        for model_slot, model_half in enumerate(model_halves):
            if _opponent_target_slot(model_half) != human_slot:
                continue
            relevant += 1
            delta = 1.0 if _is_protect_breaker(model_half) else -1.0
            total += delta
            components.append(
                {
                    "rule": "predicted-human-protect",
                    "humanSlot": human_slot,
                    "modelSlot": model_slot,
                    "delta": delta,
                }
            )

    # If the human is predicted to use a direct move into one model slot,
    # Protect-like actions on exactly that slot are a clear defensive response.
    for human_slot, human_half in enumerate(human_halves):
        target_slot = _opponent_target_slot(human_half)
        if target_slot is None:
            continue
        model_half = model_halves[target_slot] if target_slot < len(model_halves) else None
        if not _is_protect_like(model_half):
            continue
        relevant += 1
        delta = -1.0 if _is_protect_breaker(human_half) else 1.0
        total += delta
        components.append(
            {
                "rule": "predicted-direct-attack",
                "humanSlot": human_slot,
                "modelSlot": target_slot,
                "delta": delta,
            }
        )

    if not relevant:
        return {"score": 0.0, "relevant": 0, "components": []}
    score = max(-1.0, min(1.0, total / relevant))
    return {"score": score, "relevant": relevant, "components": components}


def _expected_response_utility(
    model_action: dict[str, Any],
    human_candidates: list[dict[str, Any]],
) -> float:
    weighted = 0.0
    probability_total = 0.0
    for candidate in human_candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            probability = float(candidate.get("probability") or 0.0)
        except (TypeError, ValueError):
            continue
        if probability <= 0:
            continue
        utility = response_utility(model_action, candidate)["score"]
        weighted += probability * float(utility)
        probability_total += probability
    if probability_total <= 0:
        return 0.0
    return weighted / probability_total


def _enrich_joint_scores(battle: Any, light: dict[str, Any]) -> dict[str, Any]:
    """Attach structured legal model actions to LIGHT's joint diagnostics."""

    from poke_env.environment import DoublesEnv

    enriched = copy.deepcopy(light)
    rendered: list[dict[str, Any]] = []
    for candidate in enriched.get("jointScores") or []:
        if not isinstance(candidate, dict):
            continue
        indices = candidate.get("indices")
        if not (
            isinstance(indices, list)
            and len(indices) == 2
            and all(isinstance(value, int) for value in indices)
        ):
            continue
        order = DoublesEnv.action_to_order(np.asarray(indices, dtype=np.int64), battle)
        item = copy.deepcopy(candidate)
        item["action"] = {
            "first": sparring._single_order_payload(order.first_order),
            "second": sparring._single_order_payload(order.second_order),
        }
        rendered.append(item)
    enriched["jointScores"] = rendered
    return enriched


def shadow_rerank(
    light: dict[str, Any] | None,
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute a lambda sweep without changing the actual LIGHT action."""

    if not isinstance(light, dict) or light.get("waiting") is not False:
        return {"eligible": False, "reason": "no-light-decision", "sweeps": []}
    if not isinstance(prediction, dict):
        return {"eligible": False, "reason": "no-human-prediction", "sweeps": []}
    if prediction.get("ready") is not True:
        return {"eligible": False, "reason": "predictor-not-ready", "sweeps": []}

    try:
        confidence = float(prediction.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < MIN_SHADOW_CONFIDENCE:
        return {
            "eligible": False,
            "reason": "confidence-below-shadow-floor",
            "confidence": confidence,
            "sweeps": [],
        }

    joint = [item for item in light.get("jointScores") or [] if isinstance(item, dict)]
    canonical = next((item for item in joint if item.get("selectedByLight") is True), None)
    if not isinstance(canonical, dict):
        return {"eligible": False, "reason": "canonical-joint-missing", "sweeps": []}
    try:
        canonical_probability = float(canonical.get("probability") or 0.0)
    except (TypeError, ValueError):
        canonical_probability = 0.0
    if canonical_probability <= 0:
        return {"eligible": False, "reason": "canonical-probability-zero", "sweeps": []}

    pool = []
    for candidate in joint:
        action = candidate.get("action")
        if not isinstance(action, dict):
            continue
        try:
            probability = float(candidate.get("probability") or 0.0)
            log_probability = float(candidate.get("logProbability"))
        except (TypeError, ValueError):
            continue
        if probability <= 0:
            continue
        if (
            candidate.get("selectedByLight") is not True
            and probability < canonical_probability * MIN_LIGHT_PROBABILITY_RATIO
        ):
            continue
        expected_counter = _expected_response_utility(
            action,
            [item for item in prediction.get("candidates") or [] if isinstance(item, dict)],
        )
        pool.append(
            {
                "indices": copy.deepcopy(candidate.get("indices")),
                "labels": copy.deepcopy(candidate.get("labels")),
                "probability": probability,
                "logProbability": log_probability,
                "selectedByLight": candidate.get("selectedByLight") is True,
                "action": copy.deepcopy(action),
                "expectedCounter": expected_counter,
            }
        )

    if not pool:
        return {"eligible": False, "reason": "candidate-pool-empty", "sweeps": []}

    canonical_indices = canonical.get("indices")
    confidence_scale = max(0.0, min(1.0, confidence / CONFIDENCE_REFERENCE))
    sweeps = []
    for lambda_cap in LAMBDA_CAPS:
        effective_lambda = lambda_cap * confidence_scale
        scored = []
        for candidate in pool:
            combined = float(candidate["logProbability"]) + effective_lambda * float(
                candidate["expectedCounter"]
            )
            scored.append((combined, candidate))
        scored.sort(
            key=lambda item: (
                item[0],
                item[1].get("selectedByLight") is True,
                item[1].get("probability") or 0.0,
            ),
            reverse=True,
        )
        best_score, best = scored[0]
        sweeps.append(
            {
                "lambdaCap": lambda_cap,
                "effectiveLambda": effective_lambda,
                "changed": best.get("indices") != canonical_indices,
                "combinedScore": best_score,
                "recommended": copy.deepcopy(best),
            }
        )

    return {
        "eligible": True,
        "reason": "shadow-only",
        "confidence": confidence,
        "confidenceScale": confidence_scale,
        "canonical": {
            "indices": copy.deepcopy(canonical.get("indices")),
            "labels": copy.deepcopy(canonical.get("labels")),
            "probability": canonical_probability,
            "logProbability": float(canonical.get("logProbability") or math.log(canonical_probability)),
            "action": copy.deepcopy(canonical.get("action")),
            "expectedCounter": _expected_response_utility(
                canonical.get("action") or {},
                [item for item in prediction.get("candidates") or [] if isinstance(item, dict)],
            ),
        },
        "candidatePool": pool,
        "sweeps": sweeps,
    }


def _find_lambda_sweep(plan: dict[str, Any], lambda_cap: float) -> dict[str, Any] | None:
    for sweep in plan.get("sweeps") or []:
        if not isinstance(sweep, dict):
            continue
        try:
            value = float(sweep.get("lambdaCap"))
        except (TypeError, ValueError):
            continue
        if abs(value - lambda_cap) < 1e-9:
            return sweep
    return None


def _evaluate_shadow(
    plan: dict[str, Any],
    actual_human_action: dict[str, Any],
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    if not plan.get("eligible"):
        return {"eligible": False, "reason": plan.get("reason")}

    canonical_action = (plan.get("canonical") or {}).get("action") or {}
    canonical_actual = response_utility(canonical_action, actual_human_action)
    evaluations = []
    for sweep in plan.get("sweeps") or []:
        if not isinstance(sweep, dict):
            continue
        recommended = sweep.get("recommended") or {}
        shadow_actual = response_utility(recommended.get("action") or {}, actual_human_action)
        delta = float(shadow_actual.get("score") or 0.0) - float(
            canonical_actual.get("score") or 0.0
        )
        evaluations.append(
            {
                "lambdaCap": sweep.get("lambdaCap"),
                "effectiveLambda": sweep.get("effectiveLambda"),
                "changed": sweep.get("changed") is True,
                "actualCounterLight": canonical_actual.get("score"),
                "actualCounterShadow": shadow_actual.get("score"),
                "actualCounterDelta": delta,
                "proxyVerdict": "win" if delta > _EPS else "loss" if delta < -_EPS else "tie",
            }
        )

    human_rank = None
    human_signature = action_signature(actual_human_action)
    if isinstance(prediction, dict):
        for index, candidate in enumerate(prediction.get("candidates") or [], start=1):
            if isinstance(candidate, dict) and candidate.get("signature") == human_signature:
                human_rank = index
                break

    return {
        "eligible": True,
        "humanActionSignature": human_signature,
        "humanPredictionRank": human_rank,
        "humanPredictionTop1": human_rank == 1,
        "humanPredictionTop3": isinstance(human_rank, int) and 1 <= human_rank <= 3,
        "canonicalActual": canonical_actual,
        "sweeps": evaluations,
    }


def install_nana_stage2_shadow_service(*, profile_id: str) -> type:
    """Layer shadow reranking over calibrated Nana 1 without influencing LIGHT."""

    base_service_class = install_calibrated_stage1_service(profile_id=profile_id)
    if getattr(base_service_class, "_nana_stage2_shadow_service", False):
        base_service_class.nana_profile_id = profile_id
        return base_service_class

    class NanaStage2ShadowService(base_service_class):
        _nana_stage2_shadow_service = True
        nana_profile_id = profile_id

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._nana_stage2_player_wrapped = False
            self._nana_stage2_light_queue: dict[str, list[dict[str, Any]]] = {}

        async def ensure_ready(self) -> None:
            await super().ensure_ready()
            if not self._nana_stage2_player_wrapped:
                assert self.runtime is not None
                original_player_class = self.runtime.player_class
                service = self

                class NanaStage2ShadowPlayer(original_player_class):
                    _nana_stage2_shadow_player = True

                    def choose_move(self, current: Any):
                        full_light = inspect_light_decision(
                            self,
                            current,
                            include_joint_scores=True,
                        )
                        if full_light.get("waiting") is False:
                            full_light = _enrich_joint_scores(current, full_light)
                        # Critical shadow guardrail: the actual battle order is
                        # still delegated to the already validated LIGHT path.
                        order = super().choose_move(current)
                        session = service.active_session
                        if session is not None and full_light.get("waiting") is False:
                            service._nana_stage2_note_light(
                                session.id,
                                int(getattr(current, "turn", 0) or 0),
                                full_light,
                            )
                        return order

                NanaStage2ShadowPlayer.__name__ = "NanaStage2ShadowPlayer"
                self.runtime.player_class = NanaStage2ShadowPlayer
                self._nana_stage2_player_wrapped = True

            self.runtime_metadata = {
                **self.runtime_metadata,
                "nana": {
                    "enabled": True,
                    "stage": 2,
                    "mode": "shadow",
                    "profileId": self.nana.profile_id,
                    "influence": 0.0,
                    "predictorModel": PREDICTOR_MODEL_VERSION,
                    "shadowModel": STAGE2_MODEL_VERSION,
                    "lambdaCaps": list(LAMBDA_CAPS),
                    "primaryLambdaCap": PRIMARY_LAMBDA_CAP,
                },
            }

        async def start(self, request: Any):
            session = await super().start(request)
            self._nana_stage2_light_queue[session.id] = []
            self.nana.append_event(
                session.id,
                "nana_stage2_shadow",
                {
                    "stage": 2,
                    "mode": "shadow",
                    "influence": 0.0,
                    "shadowModel": STAGE2_MODEL_VERSION,
                    "predictorModel": PREDICTOR_MODEL_VERSION,
                    "lambdaCaps": list(LAMBDA_CAPS),
                    "primaryLambdaCap": PRIMARY_LAMBDA_CAP,
                    "minShadowConfidence": MIN_SHADOW_CONFIDENCE,
                    "confidenceReference": CONFIDENCE_REFERENCE,
                    "minLightProbabilityRatio": MIN_LIGHT_PROBABILITY_RATIO,
                },
            )
            return session

        def _nana_stage2_note_light(
            self,
            session_id: str,
            turn: int,
            light: dict[str, Any],
        ) -> None:
            self._nana_stage2_light_queue.setdefault(session_id, []).append(
                {"turn": turn, "light": copy.deepcopy(light)}
            )

        def _nana_stage2_take_light(
            self,
            session_id: str,
            turn: int,
        ) -> dict[str, Any] | None:
            queue = self._nana_stage2_light_queue.setdefault(session_id, [])
            for index, item in enumerate(queue):
                if item.get("turn") == turn:
                    return queue.pop(index).get("light")
            if queue:
                return queue.pop(0).get("light")
            return None

        async def submit_choice(self, session_id: str, choice_id: str):
            session = self.get_session(session_id)
            generation = int(getattr(session, "generation", 0) or 0)
            turn = int((session.battle_state or {}).get("turn", 0) or 0)
            actual_action = next(
                (
                    copy.deepcopy(candidate)
                    for candidate in session.legal_actions
                    if candidate.get("id") == choice_id
                ),
                None,
            )
            prediction = self._stage1_prediction(session, source="submit-fallback")
            light = self._nana_stage2_take_light(session_id, turn)
            plan = shadow_rerank(light, prediction)
            self.nana.append_event(
                session_id,
                "nana_stage2_shadow_plan",
                {
                    "generation": generation,
                    "turn": turn,
                    "influence": 0.0,
                    "shadowModel": STAGE2_MODEL_VERSION,
                    "prediction": {
                        "historySamples": prediction.get("historySamples") if isinstance(prediction, dict) else None,
                        "contextSamples": prediction.get("contextSamples") if isinstance(prediction, dict) else None,
                        "ready": prediction.get("ready") if isinstance(prediction, dict) else False,
                        "confidence": prediction.get("confidence") if isinstance(prediction, dict) else 0.0,
                    },
                    "plan": copy.deepcopy(plan),
                },
            )

            result = await super().submit_choice(session_id, choice_id)
            if isinstance(actual_action, dict):
                evaluation = _evaluate_shadow(plan, actual_action, prediction)
                self.nana.append_event(
                    session_id,
                    "nana_stage2_shadow_evaluation",
                    {
                        "generation": generation,
                        "turn": turn,
                        "shadowModel": STAGE2_MODEL_VERSION,
                        "evaluation": evaluation,
                    },
                )
            return result

        def snapshot(self, session: Any) -> dict[str, Any]:
            data = super().snapshot(session)
            data["nana"] = {
                "enabled": True,
                "stage": 2,
                "mode": "shadow",
                "profileId": self.nana.profile_id,
                "influence": 0.0,
                "predictorModel": PREDICTOR_MODEL_VERSION,
                "shadowModel": STAGE2_MODEL_VERSION,
                # No shadow recommendation is exposed to the human UI.
                "shadowRecorded": True,
            }
            return data

    sparring.BattleLabLocalService = NanaStage2ShadowService
    return NanaStage2ShadowService


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_nana_stage2_shadow_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
