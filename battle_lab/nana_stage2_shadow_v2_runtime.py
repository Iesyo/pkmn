"""Auditable Nana 2 shadow runtime.

This is the hardened successor of nana2-shadow-v1. It remains observational:
LIGHT M-C always supplies the real battle order. Shadow instrumentation is
fault-isolated and may only record what Nana *would* have preferred.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable, Sequence

from battle_lab import local_sparring_service as sparring
from battle_lab.nana_policy import inspect_light_decision
from battle_lab.nana_predictor import action_signature
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage1_calibrated_runtime import (
    MODEL_VERSION as PREDICTOR_MODEL_VERSION,
    install_calibrated_stage1_service,
)
from battle_lab.nana_stage2_shadow_runtime import response_utility

STAGE2_MODEL_VERSION = "nana2-shadow-v2-auditable"
LAMBDA_CAPS = (0.0, 0.05, 0.10, 0.20)
PRIMARY_LAMBDA_CAP = 0.10
MIN_SHADOW_CONFIDENCE = 0.03
CONFIDENCE_REFERENCE = 0.15
MIN_BRANCH_PROBABILITY_RATIO = 0.90
_EPS = 1e-12
_TOL = 1e-9


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    return rendered if math.isfinite(rendered) else default


def _safe_after_light(
    real_choose: Callable[[], Any],
    shadow_collect: Callable[[Any], None],
) -> tuple[Any, str | None]:
    """Return LIGHT's order even when every shadow diagnostic explodes."""

    order = real_choose()
    try:
        shadow_collect(order)
        return order, None
    except Exception as error:  # shadow must never break the real battle
        return order, f"{type(error).__name__}: {error}"


def _structured_action(battle: Any, indices: list[int]) -> dict[str, Any]:
    import numpy as np
    from poke_env.environment import DoublesEnv

    order = DoublesEnv.action_to_order(np.asarray(indices, dtype=np.int64), battle)
    return {
        "first": sparring._single_order_payload(order.first_order),
        "second": sparring._single_order_payload(order.second_order),
    }


def _enrich_joint_scores_strict(battle: Any, light: dict[str, Any]) -> dict[str, Any]:
    enriched = copy.deepcopy(light)
    rendered: list[dict[str, Any]] = []
    first_coverage: dict[str, int] = {}
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
        item = copy.deepcopy(candidate)
        item["action"] = _structured_action(battle, [int(indices[0]), int(indices[1])])
        rendered.append(item)
        key = str(int(indices[0]))
        first_coverage[key] = first_coverage.get(key, 0) + 1
    enriched["jointScores"] = rendered
    enriched["jointCoverage"] = {
        "source": "inspect-light-exhaustive-enumeration",
        "firstCounts": first_coverage,
    }
    return enriched


def _strict_sequential_regrets(
    light: dict[str, Any],
) -> tuple[dict[tuple[int, int], dict[str, float]], dict[str, Any] | None]:
    """Compute exact sequential regret or return an explicit integrity error."""

    branches = light.get("branches") or []
    if not isinstance(branches, list) or not branches:
        return {}, {"reason": "light-branches-missing"}
    first_branch = branches[0] if isinstance(branches[0], dict) else {}
    first_scores = {
        int(item["index"]): _safe_float(item.get("probability"))
        for item in first_branch.get("scores") or []
        if isinstance(item, dict)
        and isinstance(item.get("index"), int)
        and _safe_float(item.get("probability")) > 0
    }
    canonical = light.get("canonicalAction") or {}
    canonical_indices = canonical.get("indices") or []
    if not (
        isinstance(canonical_indices, list)
        and len(canonical_indices) == 2
        and all(isinstance(value, int) for value in canonical_indices)
    ):
        return {}, {"reason": "canonical-indices-missing"}
    canonical_first, canonical_second = map(int, canonical_indices)
    canonical_first_probability = first_scores.get(canonical_first, 0.0)
    if canonical_first_probability <= 0:
        return {}, {"reason": "canonical-first-probability-zero"}

    max_first = max(first_scores.values(), default=0.0)
    if canonical_first_probability + _TOL < max_first:
        return {}, {"reason": "canonical-not-greedy-first"}

    joint = [item for item in light.get("jointScores") or [] if isinstance(item, dict)]
    conditional: dict[tuple[int, int], float] = {}
    counts: dict[int, int] = {}
    max_second: dict[int, float] = {}
    for candidate in joint:
        indices = candidate.get("indices")
        if not (
            isinstance(indices, list)
            and len(indices) == 2
            and all(isinstance(value, int) for value in indices)
        ):
            continue
        first_index, second_index = map(int, indices)
        first_probability = first_scores.get(first_index, 0.0)
        joint_probability = _safe_float(candidate.get("probability"))
        if first_probability <= 0 or joint_probability <= 0:
            continue
        second_probability = min(1.0, joint_probability / first_probability)
        conditional[(first_index, second_index)] = second_probability
        counts[first_index] = counts.get(first_index, 0) + 1
        max_second[first_index] = max(max_second.get(first_index, 0.0), second_probability)

    missing_first = sorted(set(first_scores) - set(counts))
    if missing_first:
        return {}, {"reason": "joint-coverage-missing-first", "missing": missing_first}

    canonical_second_probability = conditional.get((canonical_first, canonical_second), 0.0)
    canonical_second_max = max_second.get(canonical_first, 0.0)
    if canonical_second_probability <= 0:
        return {}, {"reason": "canonical-second-probability-zero"}
    if canonical_second_probability + _TOL < canonical_second_max:
        return {}, {"reason": "canonical-not-greedy-second"}

    rendered: dict[tuple[int, int], dict[str, float]] = {}
    for (first_index, second_index), second_probability in conditional.items():
        first_probability = first_scores[first_index]
        first_regret = math.log(max(first_probability, _EPS)) - math.log(
            max(canonical_first_probability, _EPS)
        )
        second_regret = math.log(max(second_probability, _EPS)) - math.log(
            max(max_second[first_index], _EPS)
        )
        if first_regret > _TOL or second_regret > _TOL:
            return {}, {"reason": "positive-light-regret"}
        first_regret = min(first_regret, 0.0)
        second_regret = min(second_regret, 0.0)
        rendered[(first_index, second_index)] = {
            "firstProbability": first_probability,
            "secondConditionalProbability": second_probability,
            "firstRegretLog": first_regret,
            "secondRegretLog": second_regret,
            "lightRegretLog": first_regret + second_regret,
            "firstCoverageCount": float(counts[first_index]),
        }

    canonical_regret = rendered.get((canonical_first, canonical_second))
    if not isinstance(canonical_regret, dict) or abs(canonical_regret["lightRegretLog"]) > _TOL:
        return {}, {"reason": "canonical-regret-not-zero"}
    return rendered, None


def _expected_response_stats(
    model_action: dict[str, Any],
    human_candidates: list[dict[str, Any]],
) -> dict[str, float]:
    weighted_score = 0.0
    probability_total = 0.0
    relevant_probability = 0.0
    for candidate in human_candidates:
        if not isinstance(candidate, dict):
            continue
        probability = _safe_float(candidate.get("probability"))
        if probability <= 0:
            continue
        utility = response_utility(model_action, candidate)
        weighted_score += probability * _safe_float(utility.get("score"))
        probability_total += probability
        if int(utility.get("relevant") or 0) > 0:
            relevant_probability += probability
    if probability_total <= 0:
        return {"score": 0.0, "relevantProbability": 0.0}
    return {
        "score": weighted_score / probability_total,
        "relevantProbability": relevant_probability / probability_total,
    }


def shadow_rerank_v2(
    light: dict[str, Any] | None,
    prediction: dict[str, Any] | None,
    *,
    prediction_source: str,
    prediction_generation: int,
    turn_matched: bool,
) -> dict[str, Any]:
    if prediction_source != "snapshot-cache":
        return {"eligible": False, "reason": "prediction-not-prechoice", "sweeps": []}
    if not turn_matched:
        return {"eligible": False, "reason": "light-turn-not-matched", "sweeps": []}
    if not isinstance(light, dict) or light.get("waiting") is not False:
        return {"eligible": False, "reason": "no-light-decision", "sweeps": []}
    if not isinstance(prediction, dict):
        return {"eligible": False, "reason": "no-human-prediction", "sweeps": []}
    if prediction.get("ready") is not True:
        return {"eligible": False, "reason": "predictor-not-ready", "sweeps": []}

    confidence = _safe_float(prediction.get("confidence"))
    if confidence < MIN_SHADOW_CONFIDENCE:
        return {
            "eligible": False,
            "reason": "confidence-below-shadow-floor",
            "confidence": confidence,
            "sweeps": [],
        }

    regrets, integrity_error = _strict_sequential_regrets(light)
    if integrity_error is not None:
        return {"eligible": False, **integrity_error, "sweeps": []}

    joint = [item for item in light.get("jointScores") or [] if isinstance(item, dict)]
    canonical = next((item for item in joint if item.get("selectedByLight") is True), None)
    if not isinstance(canonical, dict):
        return {"eligible": False, "reason": "canonical-joint-missing", "sweeps": []}

    human_candidates = [
        item for item in prediction.get("candidates") or [] if isinstance(item, dict)
    ]
    min_branch_regret = math.log(MIN_BRANCH_PROBABILITY_RATIO)
    pool: list[dict[str, Any]] = []
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
        key = (int(indices[0]), int(indices[1]))
        regret = regrets.get(key)
        if not isinstance(regret, dict):
            continue
        if candidate.get("selectedByLight") is not True and (
            regret["firstRegretLog"] < min_branch_regret
            or regret["secondRegretLog"] < min_branch_regret
        ):
            continue
        counter = _expected_response_stats(action, human_candidates)
        pool.append(
            {
                "indices": copy.deepcopy(indices),
                "labels": copy.deepcopy(candidate.get("labels")),
                "probability": _safe_float(candidate.get("probability")),
                "selectedByLight": candidate.get("selectedByLight") is True,
                "action": copy.deepcopy(action),
                **regret,
                "expectedCounter": counter["score"],
                "expectedRelevantProbability": counter["relevantProbability"],
            }
        )

    canonical_pool = next((item for item in pool if item.get("selectedByLight") is True), None)
    if not isinstance(canonical_pool, dict):
        return {"eligible": False, "reason": "canonical-pool-missing", "sweeps": []}

    canonical_indices = canonical_pool.get("indices")
    confidence_scale = max(0.0, min(1.0, confidence / CONFIDENCE_REFERENCE))
    sweeps: list[dict[str, Any]] = []
    for lambda_cap in LAMBDA_CAPS:
        effective_lambda = lambda_cap * confidence_scale
        scored = []
        for candidate in pool:
            combined = _safe_float(candidate["lightRegretLog"]) + effective_lambda * _safe_float(
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
        changed = best.get("indices") != canonical_indices
        if lambda_cap == 0.0 and changed:
            return {"eligible": False, "reason": "lambda-zero-diverged", "sweeps": []}
        sweeps.append(
            {
                "lambdaCap": lambda_cap,
                "effectiveLambda": effective_lambda,
                "changed": changed,
                "combinedScore": best_score,
                "recommended": copy.deepcopy(best),
            }
        )

    return {
        "eligible": True,
        "reason": "shadow-only",
        "predictionSource": prediction_source,
        "predictionGeneration": prediction_generation,
        "turnMatched": turn_matched,
        "confidence": confidence,
        "confidenceScale": confidence_scale,
        "jointCoverage": copy.deepcopy(light.get("jointCoverage") or {}),
        "canonical": copy.deepcopy(canonical_pool),
        "candidatePool": pool,
        "sweeps": sweeps,
    }


def _evaluate_shadow_v2(
    plan: dict[str, Any],
    actual_human_action: dict[str, Any],
    prediction: dict[str, Any] | None,
) -> dict[str, Any]:
    if not plan.get("eligible"):
        return {"eligible": False, "reason": plan.get("reason")}

    canonical = plan.get("canonical") or {}
    canonical_actual = response_utility(canonical.get("action") or {}, actual_human_action)
    evaluations: list[dict[str, Any]] = []
    for sweep in plan.get("sweeps") or []:
        if not isinstance(sweep, dict):
            continue
        recommended = sweep.get("recommended") or {}
        shadow_actual = response_utility(recommended.get("action") or {}, actual_human_action)
        delta = _safe_float(shadow_actual.get("score")) - _safe_float(canonical_actual.get("score"))
        evaluations.append(
            {
                "lambdaCap": sweep.get("lambdaCap"),
                "effectiveLambda": sweep.get("effectiveLambda"),
                "changed": sweep.get("changed") is True,
                "lightRegretLog": _safe_float(recommended.get("lightRegretLog")),
                "actualCounterLight": canonical_actual.get("score"),
                "actualCounterShadow": shadow_actual.get("score"),
                "actualCounterDelta": delta,
                "counterRelevant": bool(
                    int(canonical_actual.get("relevant") or 0) > 0
                    or int(shadow_actual.get("relevant") or 0) > 0
                ),
                "proxyVerdict": "win" if delta > _EPS else "loss" if delta < -_EPS else "tie",
            }
        )

    human_signature = action_signature(actual_human_action)
    human_rank = None
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


def install_nana_stage2_shadow_v2_service(*, profile_id: str) -> type:
    base_service_class = install_calibrated_stage1_service(profile_id=profile_id)
    if getattr(base_service_class, "_nana_stage2_shadow_v2_service", False):
        base_service_class.nana_profile_id = profile_id
        return base_service_class

    class NanaStage2ShadowV2Service(base_service_class):
        _nana_stage2_shadow_v2_service = True
        nana_profile_id = profile_id

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._nana_stage2_v2_player_wrapped = False
            self._nana_stage2_v2_light_queue: dict[str, list[dict[str, Any]]] = {}

        async def ensure_ready(self) -> None:
            await super().ensure_ready()
            if not self._nana_stage2_v2_player_wrapped:
                assert self.runtime is not None
                original_player_class = self.runtime.player_class
                service = self

                class NanaStage2ShadowV2Player(original_player_class):
                    def choose_move(self, current: Any):
                        turn = int(getattr(current, "turn", 0) or 0)

                        def real_choose():
                            return super(NanaStage2ShadowV2Player, self).choose_move(current)

                        def collect(_order: Any) -> None:
                            light = inspect_light_decision(self, current, include_joint_scores=True)
                            if light.get("waiting") is False:
                                light = _enrich_joint_scores_strict(current, light)
                            session = service.active_session
                            if session is not None and light.get("waiting") is False:
                                service._nana_stage2_v2_note_light(session.id, turn, light)

                        order, error = _safe_after_light(real_choose, collect)
                        if error:
                            service._nana_stage2_v2_note_error(turn, error)
                        return order

                NanaStage2ShadowV2Player.__name__ = "NanaStage2ShadowV2Player"
                self.runtime.player_class = NanaStage2ShadowV2Player
                self._nana_stage2_v2_player_wrapped = True

            self.runtime_metadata = {
                **self.runtime_metadata,
                "nana": {
                    "enabled": True,
                    "stage": 2,
                    "mode": "shadow-v2-auditable",
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
            self._nana_stage2_v2_light_queue[session.id] = []
            try:
                self.nana.append_event(
                    session.id,
                    "nana_stage2_shadow_v2",
                    {
                        "stage": 2,
                        "mode": "shadow-v2-auditable",
                        "influence": 0.0,
                        "shadowModel": STAGE2_MODEL_VERSION,
                        "predictorModel": PREDICTOR_MODEL_VERSION,
                        "lambdaCaps": list(LAMBDA_CAPS),
                        "primaryLambdaCap": PRIMARY_LAMBDA_CAP,
                        "minBranchProbabilityRatio": MIN_BRANCH_PROBABILITY_RATIO,
                    },
                )
            except Exception:
                pass
            return session

        def _nana_stage2_v2_note_error(self, turn: int, error: str) -> None:
            session = self.active_session
            if session is None:
                return
            try:
                self.nana.append_event(
                    session.id,
                    "nana_stage2_shadow_error",
                    {"turn": turn, "error": error, "influence": 0.0},
                )
            except Exception:
                pass

        def _nana_stage2_v2_note_light(self, session_id: str, turn: int, light: dict[str, Any]) -> None:
            self._nana_stage2_v2_light_queue.setdefault(session_id, []).append(
                {"turn": turn, "light": copy.deepcopy(light)}
            )

        def _nana_stage2_v2_take_light(
            self, session_id: str, turn: int
        ) -> tuple[dict[str, Any] | None, bool]:
            queue = self._nana_stage2_v2_light_queue.setdefault(session_id, [])
            for index, item in enumerate(queue):
                if item.get("turn") == turn:
                    return queue.pop(index).get("light"), True
            return None, False

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

            cache = self._nana_stage1_predictions.setdefault(session.id, {})
            was_precomputed = generation in cache
            prediction = self._stage1_prediction(session, source="submit-fallback")
            prediction_source = "snapshot-cache" if was_precomputed else "submit-fallback"
            light, turn_matched = self._nana_stage2_v2_take_light(session_id, turn)

            try:
                plan = shadow_rerank_v2(
                    light,
                    prediction,
                    prediction_source=prediction_source,
                    prediction_generation=generation,
                    turn_matched=turn_matched,
                )
            except Exception as error:
                plan = {"eligible": False, "reason": "shadow-error", "error": f"{type(error).__name__}: {error}", "sweeps": []}

            try:
                self.nana.append_event(
                    session_id,
                    "nana_stage2_shadow_v2_plan",
                    {
                        "generation": generation,
                        "turn": turn,
                        "influence": 0.0,
                        "shadowModel": STAGE2_MODEL_VERSION,
                        "predictionSource": prediction_source,
                        "predictionGeneration": generation,
                        "turnMatched": turn_matched,
                        "plan": copy.deepcopy(plan),
                    },
                )
            except Exception:
                pass

            # Real action is submitted regardless of any shadow failure above.
            result = await super().submit_choice(session_id, choice_id)

            if isinstance(actual_action, dict):
                try:
                    evaluation = _evaluate_shadow_v2(plan, actual_action, prediction)
                    self.nana.append_event(
                        session_id,
                        "nana_stage2_shadow_v2_evaluation",
                        {
                            "generation": generation,
                            "turn": turn,
                            "shadowModel": STAGE2_MODEL_VERSION,
                            "evaluation": evaluation,
                        },
                    )
                except Exception:
                    pass
            return result

        def snapshot(self, session: Any) -> dict[str, Any]:
            data = super().snapshot(session)
            data["nana"] = {
                "enabled": True,
                "stage": 2,
                "mode": "shadow-v2-auditable",
                "profileId": self.nana.profile_id,
                "influence": 0.0,
                "predictorModel": PREDICTOR_MODEL_VERSION,
                "shadowModel": STAGE2_MODEL_VERSION,
                "shadowRecorded": True,
            }
            return data

        def _nana_finish(self, session: Any) -> None:
            try:
                super()._nana_finish(session)
            finally:
                self._nana_stage2_v2_light_queue.pop(session.id, None)

    sparring.BattleLabLocalService = NanaStage2ShadowV2Service
    return NanaStage2ShadowV2Service


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_nana_stage2_shadow_v2_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
