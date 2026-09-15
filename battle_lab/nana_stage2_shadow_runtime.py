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
    return (
        isinstance(half, dict)
        and half.get("kind") == "move"
        and _token(half.get("value")) in _PROTECT_LIKE
    )


def _is_protect_breaker(half: Any) -> bool:
    return (
        isinstance(half, dict)
        and half.get("kind") == "move"
        and _token(half.get("value")) in _PROTECT_BREAKERS
    )


def _opponent_target_slot(half: Any) -> int | None:
    """Map Showdown/poke-env direct foe target +1/+2 to zero-based slot.

    Pokémon Showdown uses positive target positions for foes and negative
    positions for allies in Doubles. Spread/self/ally/implicit targets return
    None so shadow v1 never guesses at ambiguous target semantics.
    """

    if not isinstance(half, dict) or half.get("kind") != "move":
        return None
    try:
        target = int(half.get("target") or 0)
    except (TypeError, ValueError):
        return None
    if target not in {1, 2}:
        return None
    return target - 1


def response_utility(model_action: Any, human_action: Any) -> dict[str, Any]:
    """Return a conservative, interpretable response proxy in [-1, 1]."""

    if not isinstance(model_action, dict) or not isinstance(human_action, dict):
        return {"score": 0.0, "relevant": 0, "components": []}

    model_halves = [model_action.get("first"), model_action.get("second")]
    human_halves = [human_action.get("first"), human_action.get("second")]
    components: list[dict[str, Any]] = []
    total = 0.0
    relevant = 0

    # Human Protect: avoid direct targeting unless the move is Feint.
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

    # Human direct attack: Protect on the targeted model slot is a clear reply.
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
        weighted += probability * float(response_utility(model_action, candidate)["score"])
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


def _light_sequential_regrets(light: dict[str, Any]) -> dict[tuple[int, int], dict[str, float]]:
    """Measure candidate regret relative to LIGHT's sequential-greedy rule.

    LIGHT chooses branch 1 greedily, then branch 2 greedily under the mask
    induced by branch 1. Joint probability sorting is *not* LIGHT's rule.
    This regret construction guarantees that the canonical sequential choice
    has score 0 and every strictly worse branch choice has score <= 0.
    """

    branches = light.get("branches") or []
    if not isinstance(branches, list) or not branches:
        return {}
    first_branch = branches[0] if isinstance(branches[0], dict) else {}
    first_scores = {
        int(item["index"]): float(item.get("probability") or 0.0)
        for item in first_branch.get("scores") or []
        if isinstance(item, dict)
        and isinstance(item.get("index"), int)
        and float(item.get("probability") or 0.0) > 0
    }
    canonical = light.get("canonicalAction") or {}
    canonical_indices = canonical.get("indices") or []
    if not (
        isinstance(canonical_indices, list)
        and len(canonical_indices) == 2
        and isinstance(canonical_indices[0], int)
    ):
        return {}
    canonical_first_probability = first_scores.get(int(canonical_indices[0]), 0.0)
    if canonical_first_probability <= 0:
        return {}

    joint = [item for item in light.get("jointScores") or [] if isinstance(item, dict)]
    conditional_second: dict[tuple[int, int], float] = {}
    max_second_by_first: dict[int, float] = {}
    for candidate in joint:
        indices = candidate.get("indices")
        if not (
            isinstance(indices, list)
            and len(indices) == 2
            and all(isinstance(value, int) for value in indices)
        ):
            continue
        first_index, second_index = int(indices[0]), int(indices[1])
        first_probability = first_scores.get(first_index, 0.0)
        if first_probability <= 0:
            continue
        joint_probability = float(candidate.get("probability") or 0.0)
        if joint_probability <= 0:
            continue
        second_probability = min(1.0, joint_probability / first_probability)
        conditional_second[(first_index, second_index)] = second_probability
        max_second_by_first[first_index] = max(
            max_second_by_first.get(first_index, 0.0), second_probability
        )

    rendered: dict[tuple[int, int], dict[str, float]] = {}
    for (first_index, second_index), second_probability in conditional_second.items():
        first_probability = first_scores[first_index]
        max_second = max_second_by_first.get(first_index, 0.0)
        if max_second <= 0:
            continue
        first_regret = math.log(max(first_probability, _EPS)) - math.log(
            max(canonical_first_probability, _EPS)
        )
        second_regret = math.log(max(second_probability, _EPS)) - math.log(
            max(max_second, _EPS)
        )
        rendered[(first_index, second_index)] = {
            "firstProbability": first_probability,
            "secondConditionalProbability": second_probability,
            "firstRegretLog": min(0.0, first_regret),
            "secondRegretLog": min(0.0, second_regret),
            "lightRegretLog": min(0.0, first_regret) + min(0.0, second_regret),
        }
    return rendered


def shadow_rerank(
    light: dict[str, Any] | None,
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute a lambda sweep while preserving LIGHT exactly at lambda=0."""

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

    regrets = _light_sequential_regrets(light)
    if not regrets:
        return {"eligible": False, "reason": "light-regret-unavailable", "sweeps": []}

    human_candidates = [
        item for item in prediction.get("candidates") or [] if isinstance(item, dict)
    ]
    min_regret = math.log(MIN_LIGHT_PROBABILITY_RATIO)
    pool = []
    for candidate in joint:
        action = candidate.get("action")
        indices = candidate.get("indices")
        if not (
            isinstance(action, dict)
            and isinstance(indices, list)
            and len(indices) == 2
            and all(isinstance(value, int) for value in indices)
        ):
            continue
        regret = regrets.get((int(indices[0]), int(indices[1])))
        if not isinstance(regret, dict):
            continue
        light_regret = float(regret["lightRegretLog"])
        if candidate.get("selectedByLight") is not True and light_regret < min_regret:
            continue
        pool.append(
            {
                "indices": copy.deepcopy(indices),
                "labels": copy.deepcopy(candidate.get("labels")),
                "probability": float(candidate.get("probability") or 0.0),
                "selectedByLight": candidate.get("selectedByLight") is True,
                "action": copy.deepcopy(action),
                "firstProbability": regret["firstProbability"],
                "secondConditionalProbability": regret["secondConditionalProbability"],
                "firstRegretLog": regret["firstRegretLog"],
                "secondRegretLog": regret["secondRegretLog"],
                "lightRegretLog": light_regret,
                "expectedCounter": _expected_response_utility(action, human_candidates),
            }
        )

    canonical_pool = next((item for item in pool if item.get("selectedByLight") is True), None)
    if not isinstance(canonical_pool, dict):
        return {"eligible": False, "reason": "canonical-pool-missing", "sweeps": []}

    canonical_indices = canonical_pool.get("indices")
    confidence_scale = max(0.0, min(1.0, confidence / CONFIDENCE_REFERENCE))
    sweeps = []
    for lambda_cap in LAMBDA_CAPS:
        effective_lambda = lambda_cap * confidence_scale
        scored = []
        for candidate in pool:
            combined = float(candidate["lightRegretLog"]) + effective_lambda * float(
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
        "canonical": copy.deepcopy(canonical_pool),
        "candidatePool": pool,
        "sweeps": sweeps,
    }


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
                        # Critical guardrail: actual battle order stays on LIGHT.
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
                    "lightBaseline": "sequential-greedy-regret",
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
                self.nana.append_event(
                    session_id,
                    "nana_stage2_shadow_evaluation",
                    {
                        "generation": generation,
                        "turn": turn,
                        "shadowModel": STAGE2_MODEL_VERSION,
                        "evaluation": _evaluate_shadow(plan, actual_action, prediction),
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
