"""Nana 2.3 Nursery: first guarded live interventions.

LIGHT remains teacher/fallback, while Nana may execute at most one near-LIGHT
alternative per BO1. Real Nana interventions are recorded distinctly and become
experience for Nana's self-critic on later battles. Teacher checkpoint/regulation
identity prevents stale trust from leaking across future LIGHT upgrades.
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any, Sequence

import numpy as np
from poke_env.environment import DoublesEnv

from battle_lab import local_sparring_service as sparring
from battle_lab import nana_stage2_shadow_v2_runtime as stage2_v2
from battle_lab.nana_autonomy import (
    assert_level_activation_ready,
    assert_live_nursery_matches_n2,
    full_amiibo_contract,
    live_nursery_contract,
)
from battle_lab.nana_light_critic import trust_for
from battle_lab.nana_nursery import (
    ALLOW_UNREPRESENTED_ORDERS,
    AUTOMATIC_PROMOTION,
    HIGH_LIGHT_TRUST_CONFIDENCE,
    HIGH_LIGHT_TRUST_VETO,
    MAX_INTERVENTIONS_PER_BATTLE,
    MIN_ALLOWED_LIGHT_REGRET_LOG,
    MIN_PREDICTION_CONFIDENCE,
    NURSERY_LAMBDA_CAP,
    NURSERY_MODEL_VERSION,
    PROMOTION_INTERVENTION_WINDOW,
    SELF_LOW_TRUST_CONFIDENCE,
    SELF_LOW_TRUST_VETO,
    SELF_PRIOR_TRUST,
    choose_candidate,
    promotion_status,
    rebuild_self_for_recorder,
)
from battle_lab.nana_contracts import build_nana_policy_contract, order_key
from battle_lab.nana_counter_calibration import (
    fit_counter_calibration,
    rebuild_counter_calibration,
)
from battle_lab.nana_legal_orders import LegalOrderSource, SafetyGate
from battle_lab.nana_n4_shadow import build_n4_shadow_plan
from battle_lab.nana_policy import inspect_light_decision
from battle_lab.nana_scorer import assert_common_scorer_ready_for_level
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_shadow_v22_runtime import (
    STAGE2_MODEL_VERSION,
    install_light_critic_service,
)
from battle_lab.nana_teacher import (
    compact_teacher_descriptor,
    descriptor_for_service,
    latest_teacher_from_events,
)
from battle_lab.nana_team_memory import (
    blend_self_with_team,
    rebuild_team_memory_for_recorder,
    team_memory_contract,
    team_trust_for,
)
from battle_lab.nana_transition import build_transition, write_transition_act


LIVE_INFLUENCE = 1.0
PRECHOICE_TIMEOUT_SECONDS = 1.0
PRECHOICE_POLL_SECONDS = 0.005


async def _await_prechoice_prediction(
    service: Any,
    session: Any,
    turn: int,
    *,
    consumed_generation: int,
    timeout: float = PRECHOICE_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]] | None:
    """Wait cooperatively until the human request is ready, then predict pre-choice.

    poke-env dispatches the human and model requests concurrently. Nursery used to
    depend on an HTTP snapshot racing ahead of the model callback, which made most
    turns ineligible as ``prediction-not-prechoice``. ``Player.choose_move`` may
    return an awaitable in the pinned poke-env fork, so the model can yield to the
    human-side callback without blocking the event loop. As soon as the human
    request has published its legal actions/state, the prediction is computed and
    cached before the user's actual choice is consumed.

    ``consumed_generation`` prevents a forced-switch retry on the same Showdown
    turn from reusing the previous prompt's prediction.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    while True:
        generation = int(getattr(session, "generation", 0) or 0)
        state_turn = int((getattr(session, "battle_state", {}) or {}).get("turn", 0) or 0)
        if (
            generation > int(consumed_generation)
            and getattr(session, "phase", "") == "waiting-choice"
            and state_turn == int(turn)
            and bool(getattr(session, "legal_actions", None))
        ):
            prediction = service._stage1_prediction(
                session,
                source="nursery-model-prechoice",
            )
            if isinstance(prediction, dict):
                return generation, prediction

        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(PRECHOICE_POLL_SECONDS, remaining))


def _live_autonomy_contract() -> dict[str, Any]:
    return live_nursery_contract(
        lambda_cap=NURSERY_LAMBDA_CAP,
        max_interventions_per_battle=MAX_INTERVENTIONS_PER_BATTLE,
        min_prediction_confidence=MIN_PREDICTION_CONFIDENCE,
        min_allowed_light_regret_log=MIN_ALLOWED_LIGHT_REGRET_LOG,
        high_light_trust_veto=HIGH_LIGHT_TRUST_VETO,
        high_light_trust_confidence=HIGH_LIGHT_TRUST_CONFIDENCE,
        self_low_trust_veto=SELF_LOW_TRUST_VETO,
        self_low_trust_confidence=SELF_LOW_TRUST_CONFIDENCE,
        promotion_intervention_window=PROMOTION_INTERVENTION_WINDOW,
        allow_unrepresented_orders=ALLOW_UNREPRESENTED_ORDERS,
        automatic_promotion=AUTOMATIC_PROMOTION,
    )


def _authorize_n4_selection(
    legal_set: Any,
    n4_plan: dict[str, Any],
):
    """Resolve one N4 selectedKey through the current-turn SafetyGate."""

    if legal_set is None or getattr(legal_set, "resolved", False) is not True:
        raise RuntimeError("N4 no tiene un conjunto legal resuelto.")
    if n4_plan.get("eligible") is not True:
        raise RuntimeError(
            f"N4 plan no elegible: {n4_plan.get('reason') or 'unknown'}"
        )
    selected_key = str(n4_plan.get("selectedKey") or "")
    if not selected_key:
        raise RuntimeError("N4 plan no produjo selectedKey.")
    return SafetyGate(legal_set).authorize_key(selected_key)


def _compact_telemetry_candidate(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    action = candidate.get("action") if isinstance(candidate.get("action"), dict) else None
    indices = candidate.get("indices")
    return {
        "action": copy.deepcopy(action),
        "indices": copy.deepcopy(indices) if isinstance(indices, list) else None,
        "probability": candidate.get("probability"),
        "lightRegretLog": candidate.get("lightRegretLog"),
        "expectedCounter": candidate.get("expectedCounter"),
    }


def install_nursery_service(
    *,
    profile_id: str,
    full_amiibo_live: bool = False,
) -> type:
    if full_amiibo_live:
        assert_level_activation_ready("N4")
        assert_common_scorer_ready_for_level("N4")
    else:
        assert_live_nursery_matches_n2(
            lambda_cap=NURSERY_LAMBDA_CAP,
            max_interventions_per_battle=MAX_INTERVENTIONS_PER_BATTLE,
            min_prediction_confidence=MIN_PREDICTION_CONFIDENCE,
            min_allowed_light_regret_log=MIN_ALLOWED_LIGHT_REGRET_LOG,
            high_light_trust_veto=HIGH_LIGHT_TRUST_VETO,
            high_light_trust_confidence=HIGH_LIGHT_TRUST_CONFIDENCE,
            self_low_trust_veto=SELF_LOW_TRUST_VETO,
            self_low_trust_confidence=SELF_LOW_TRUST_CONFIDENCE,
            promotion_intervention_window=PROMOTION_INTERVENTION_WINDOW,
            allow_unrepresented_orders=ALLOW_UNREPRESENTED_ORDERS,
            automatic_promotion=AUTOMATIC_PROMOTION,
        )
    service_class = install_light_critic_service(profile_id=profile_id)
    if getattr(service_class, "_nana_nursery_live_v1", False):
        installed_mode = bool(
            getattr(service_class, "_nana_full_amiibo_live", False)
        )
        if installed_mode != bool(full_amiibo_live):
            raise RuntimeError(
                "Nana service ya fue instalado en otro modo de autonomía."
            )
        service_class.nana_profile_id = profile_id
        return service_class

    original_init = service_class.__init__
    original_ensure_ready = service_class.ensure_ready
    original_start = service_class.start
    original_snapshot = service_class.snapshot
    original_finish = service_class._nana_finish

    def _live_policy_contract() -> dict[str, Any]:
        if full_amiibo_live:
            return build_nana_policy_contract(
                decision_mode="nana-full-amiibo-n4-v1",
                scorer_contract="nana-scorer-v5-evidence-floor-common-space",
                score_spaces={
                    "experience": "board-delta-v1",
                    "counter": "board-delta-v1",
                    "teacherPrior": "reference-only-unmapped",
                },
                governor_contract=full_amiibo_contract(),
                memory_contract=team_memory_contract(),
                legal_order_contract=LegalOrderSource.contract_id,
            )
        return build_nana_policy_contract(
            decision_mode=NURSERY_MODEL_VERSION,
            scorer_contract="legacy-n2-light-regret-plus-response-utility-v1",
            score_spaces={
                "teacherPrior": "teacher-log-regret-v1",
                "counter": "response-utility-v1",
            },
            governor_contract=_live_autonomy_contract(),
            memory_contract=team_memory_contract(),
            legal_order_contract="vgc-bench-indexed-order-v1",
        )

    def _teacher_ref(teacher: dict[str, Any] | None) -> dict[str, Any]:
        return compact_teacher_descriptor(teacher)

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._nana_policy_contract = _live_policy_contract()
        history = list(self.nana.iter_events())
        latest_teacher = latest_teacher_from_events(history)
        self._nana_previous_teacher = copy.deepcopy(latest_teacher)
        self._nana_previous_teacher_key = str(
            latest_teacher.get("key") or latest_teacher.get("weightsKey") or ""
        )
        self._nana_teacher = descriptor_for_service(self)
        self._nana_allow_legacy_teacher_fallback = (
            not self._nana_previous_teacher_key
            or self._nana_previous_teacher_key == self._nana_teacher["key"]
        )
        self._nana_nursery_player_wrapped = False
        self._nana_nursery_interventions: dict[str, int] = {}
        self._nana_nursery_model_generation: dict[str, int] = {}
        self._nana_nursery_finished: set[str] = set()
        self._nana_identity_recorded_sessions: set[str] = set()
        try:
            self.nana_self_summary = rebuild_self_for_recorder(self.nana)
        except Exception:
            self.nana_self_summary = {
                "modelVersion": "nana-self-critic-v1",
                "observations": 0,
                "prior": {"trust": SELF_PRIOR_TRUST, "weight": 6.0},
                "buckets": {},
                "teachers": {},
            }
        try:
            self.nana_team_memory = rebuild_team_memory_for_recorder(self.nana)
        except Exception:
            self.nana_team_memory = {
                "modelVersion": "nana-team-memory-v1",
                "observations": 0,
                "buckets": {},
            }
        try:
            self.nana_counter_calibration = rebuild_counter_calibration(self.nana)
        except Exception:
            self.nana_counter_calibration = fit_counter_calibration([])

    def _teacher_query_args(self: Any) -> dict[str, Any]:
        return {
            "teacher_key": self._nana_teacher["key"],
            "fallback_teacher_key": (
                self._nana_teacher.get("legacyKey")
                if self._nana_allow_legacy_teacher_fallback
                else None
            ),
        }

    def _apply_nursery_metadata(self: Any) -> None:
        if full_amiibo_live:
            self.runtime_metadata.setdefault("nana", {}).update(
                {
                    "stage": 4,
                    "mode": "full-amiibo-live-v1",
                    "influence": LIVE_INFLUENCE,
                    "autonomyLevel": "N4",
                    "lambdaCap": None,
                    "maxInterventionsPerBattle": None,
                    "autonomy": full_amiibo_contract(),
                    "teacher": _teacher_ref(self._nana_teacher),
                    "teacherRole": "advisor-fallback",
                }
            )
            return
        self.runtime_metadata.setdefault("nana", {}).update(
            {
                "stage": 2.3,
                "mode": "nursery-live-v1",
                "influence": LIVE_INFLUENCE,
                "autonomyLevel": "N2",
                "nurseryModel": NURSERY_MODEL_VERSION,
                "lambdaCap": NURSERY_LAMBDA_CAP,
                "maxInterventionsPerBattle": MAX_INTERVENTIONS_PER_BATTLE,
                "autonomy": _live_autonomy_contract(),
                "teacher": _teacher_ref(self._nana_teacher),
            }
        )

    async def ensure_ready(self: Any) -> None:
        await original_ensure_ready(self)
        self._nana_policy_contract = _live_policy_contract()
        self._nana_teacher = descriptor_for_service(self)

        session = self.active_session
        if (
            session is not None
            and session.id not in self._nana_identity_recorded_sessions
            and self._nana_teacher.get("behaviorKeyResolved") is True
            and self._nana_teacher.get("nanaPolicyKeyResolved") is True
        ):
            transition_resolved = False
            try:
                transition = build_transition(
                    getattr(self, "_nana_previous_teacher", {}),
                    self._nana_teacher,
                )
                if transition.get("record") is True:
                    act_path = write_transition_act(self.nana.profile_root, transition)
                    self.nana.append_event(
                        session.id,
                        "nana_teacher_change",
                        {
                            **transition,
                            "actPath": str(act_path.relative_to(self.nana.profile_root)),
                        },
                    )
                self.nana.append_event(
                    session.id,
                    "nana_teacher_version",
                    {
                        "teacher": copy.deepcopy(self._nana_teacher),
                        "reason": "nursery-live-resolved-identity",
                    },
                )
                transition_resolved = True
            except Exception as error:
                try:
                    self.nana.append_event(
                        session.id,
                        "nana_transition_error",
                        {
                            "error": f"{type(error).__name__}: {error}",
                            "liveBehaviorChanged": False,
                        },
                    )
                except Exception:
                    pass
            if transition_resolved:
                self._nana_previous_teacher = copy.deepcopy(self._nana_teacher)
                self._nana_previous_teacher_key = str(
                    self._nana_teacher.get("key")
                    or self._nana_teacher.get("weightsKey")
                    or ""
                )
                self._nana_identity_recorded_sessions.add(session.id)

        _apply_nursery_metadata(self)
        if self._nana_nursery_player_wrapped:
            return
        assert self.runtime is not None
        parent_player_class = self.runtime.player_class
        observed_class = next(
            (
                cls
                for cls in parent_player_class.mro()
                if cls.__dict__.get("_nana_observed_player") is True
            ),
            None,
        )
        if observed_class is None:
            raise RuntimeError("Nana Nursery no encontró el wrapper observacional de LIGHT.")
        service = self

        class NanaNurseryPlayer(parent_player_class):
            _nana_nursery_player = True

            def _raw_light_choose(self, current: Any):
                # Skip both Nana wrappers and call frozen LIGHT exactly once.
                return super(observed_class, self).choose_move(current)

            async def choose_move(self, current: Any):
                # Preserve the ancestor's Team Preview escape hatch. Inspecting
                # regular move masks during preview is invalid and would create a
                # fake Nursery error on every battle.
                if getattr(current, "teampreview", False):
                    return self._raw_light_choose(current)

                turn = int(getattr(current, "turn", 0) or 0)
                session = service.active_session
                if session is None:
                    return self._raw_light_choose(current)

                # The human and model requests arrive concurrently. Yield until
                # the human request has published the *next* generation, then
                # compute Nana's human prediction before the actual choice can be
                # consumed. This removes the browser-snapshot race without ever
                # peeking at the user's submitted action.
                consumed_generation = service._nana_nursery_model_generation.get(
                    session.id, 0
                )
                prechoice = await _await_prechoice_prediction(
                    service,
                    session,
                    turn,
                    consumed_generation=consumed_generation,
                )
                if prechoice is None:
                    try:
                        service.nana.append_event(
                            session.id,
                            "nana_nursery_error",
                            {
                                "modelVersion": NURSERY_MODEL_VERSION,
                                "turn": turn,
                                "teacher": _teacher_ref(service._nana_teacher),
                                "error": "prechoice synchronization timed out",
                                "fallback": "LIGHT",
                                "phase": "prechoice-sync-timeout",
                            },
                        )
                    except Exception:
                        pass
                    return super().choose_move(current)

                generation, cached = prechoice
                service._nana_nursery_model_generation[session.id] = generation

                # Phase 1: select a legal order with zero persistent side effects.
                # Any failure here may safely fall back to the old shadow/LIGHT
                # wrapper because Nana has not claimed an intervention yet.
                try:
                    light = inspect_light_decision(
                        self,
                        current,
                        include_joint_scores=True,
                    )
                    if light.get("waiting") is True:
                        return self._raw_light_choose(current)
                    light = stage2_v2._enrich_joint_scores_strict(current, light)

                    # N4 migration telemetry: enumerate legal orders directly from
                    # poke-env and compare them with the teacher's diagnostic joint
                    # catalog. This does not affect N2 decisions.
                    legal_order_diag: dict[str, Any]
                    legal_set = None
                    legal_started = time.perf_counter()
                    try:
                        legal_set = LegalOrderSource().enumerate(current)
                        legal_elapsed_ms = (time.perf_counter() - legal_started) * 1000.0
                        teacher_keys = {
                            order_key(item.get("action"))
                            for item in (light.get("jointScores") or [])
                            if isinstance(item, dict)
                            and isinstance(item.get("action"), dict)
                        }
                        legal_keys = set(legal_set.keys)
                        covered = legal_keys & teacher_keys
                        missing_from_teacher = legal_keys - teacher_keys
                        extra_teacher = teacher_keys - legal_keys
                        legal_order_diag = {
                            "resolved": legal_set.resolved,
                            "reason": legal_set.reason,
                            "contractId": LegalOrderSource.contract_id,
                            "totalLegal": len(legal_keys),
                            "teacherJointTotal": len(teacher_keys),
                            "coveredByTeacher": len(covered),
                            "teacherCoverage": (
                                len(covered) / len(legal_keys)
                                if legal_keys else 0.0
                            ),
                            "missingFromTeacher": len(missing_from_teacher),
                            "extraTeacher": len(extra_teacher),
                            "representable": sum(
                                candidate.representable
                                for candidate in legal_set.candidates
                            ),
                            "unrepresentable": sum(
                                not candidate.representable
                                for candidate in legal_set.candidates
                            ),
                            "individualCounts": list(legal_set.individual_counts),
                            "joinedCount": legal_set.joined_count,
                            "elapsedMs": legal_elapsed_ms,
                        }
                    except Exception as error:
                        legal_elapsed_ms = (time.perf_counter() - legal_started) * 1000.0
                        legal_order_diag = {
                            "resolved": False,
                            "reason": f"{type(error).__name__}: {error}",
                            "contractId": LegalOrderSource.contract_id,
                            "totalLegal": 0,
                            "teacherJointTotal": len(light.get("jointScores") or []),
                            "coveredByTeacher": 0,
                            "teacherCoverage": 0.0,
                            "missingFromTeacher": None,
                            "extraTeacher": None,
                            "elapsedMs": legal_elapsed_ms,
                        }

                    state_turn = int((session.battle_state or {}).get("turn", 0) or 0)
                    turn_matched = (
                        cached is not None
                        and generation > 0
                        and state_turn == turn
                    )
                    plan = stage2_v2.shadow_rerank_v2(
                        light,
                        cached,
                        prediction_source="snapshot-cache",
                        prediction_generation=generation,
                        turn_matched=turn_matched,
                    )

                    model_state = sparring._battle_snapshot(current)
                    n4_started = time.perf_counter()
                    try:
                        n4_shadow = (
                            build_n4_shadow_plan(
                                legal_orders=legal_set,
                                light=light,
                                prediction=cached,
                                model_state=model_state,
                                self_summary=service.nana_self_summary,
                                counter_calibration=service.nana_counter_calibration,
                            )
                            if legal_set is not None and legal_set.resolved
                            else {
                                "eligible": False,
                                "reason": "legal-orders-unresolved",
                            }
                        )
                    except Exception as error:
                        n4_shadow = {
                            "eligible": False,
                            "reason": f"{type(error).__name__}: {error}",
                        }
                    n4_elapsed_ms = (time.perf_counter() - n4_started) * 1000.0
                    n4_shadow["planningMs"] = n4_elapsed_ms
                    n4_shadow["legalOrderMs"] = legal_order_diag.get("elapsedMs")
                    n4_shadow["totalDecisionMs"] = (
                        n4_elapsed_ms + float(legal_order_diag.get("elapsedMs") or 0.0)
                    )

                    # Exercise the exact N4 execution boundary in dry-run mode:
                    # selected orderKey -> current LegalOrderSet -> SafetyGate.
                    safety_gate_diag = {
                        "authorized": False,
                        "reason": "no-selection",
                        "elapsedMs": 0.0,
                    }
                    selected_key = str(n4_shadow.get("selectedKey") or "")
                    if selected_key:
                        gate_started = time.perf_counter()
                        try:
                            authorized = _authorize_n4_selection(
                                legal_set,
                                n4_shadow,
                            )
                            safety_gate_diag = {
                                "authorized": True,
                                "reason": "ok",
                                "orderKey": authorized.key,
                                "representable": authorized.representable,
                                "elapsedMs": (
                                    time.perf_counter() - gate_started
                                ) * 1000.0,
                            }
                        except Exception as error:
                            safety_gate_diag = {
                                "authorized": False,
                                "reason": f"{type(error).__name__}: {error}",
                                "orderKey": selected_key,
                                "elapsedMs": (
                                    time.perf_counter() - gate_started
                                ) * 1000.0,
                            }
                    n4_shadow["safetyGate"] = copy.deepcopy(safety_gate_diag)

                    teacher_args = service._teacher_query_args()
                    light_trust = trust_for(
                        service.light_critic_summary,
                        model_state,
                        light.get("canonicalAction") if isinstance(light, dict) else None,
                        light,
                        **teacher_args,
                    )
                    neutral_self = {
                        "trust": SELF_PRIOR_TRUST,
                        "confidence": 0.0,
                        "teacherSource": "fresh-prior",
                    }
                    provisional = choose_candidate(
                        plan,
                        light_trust=light_trust,
                        self_trust=neutral_self,
                        lambda_cap=NURSERY_LAMBDA_CAP,
                    )
                    self_trust = neutral_self
                    if provisional.get("intervene") is True:
                        candidate = provisional.get("candidate") or {}
                        self_trust = trust_for(
                            service.nana_self_summary,
                            model_state,
                            candidate.get("action") if isinstance(candidate, dict) else None,
                            light,
                            **teacher_args,
                        )
                        team_context = (
                            service._nana_session_team_context.get(session.id, {})
                            if hasattr(service, "_nana_session_team_context")
                            else {}
                        )
                        team_trust = team_trust_for(
                            service.nana_team_memory,
                            team_context,
                        )
                        self_trust = blend_self_with_team(self_trust, team_trust)
                    selection = choose_candidate(
                        plan,
                        light_trust=light_trust,
                        self_trust=self_trust,
                        lambda_cap=NURSERY_LAMBDA_CAP,
                    )

                    used = service._nana_nursery_interventions.get(session.id, 0)
                    if full_amiibo_live:
                        # N4 has no λ cap and no per-battle intervention budget.
                        # Whether it differs from LIGHT is determined only after
                        # the SafetyGate-authorized N4 selection below.
                        intervened = False
                    else:
                        intervened = (
                            selection.get("intervene") is True
                            and used < MAX_INTERVENTIONS_PER_BATTLE
                        )
                        if selection.get("intervene") is True and not intervened:
                            selection = {
                                **selection,
                                "intervene": False,
                                "reason": "nursery-per-battle-budget-exhausted",
                            }

                    telemetry_candidate = (
                        selection.get("candidate")
                        if isinstance(selection.get("candidate"), dict)
                        else selection.get("nearestCandidate")
                    )
                    team_memory_from_self = (
                        self_trust.get("teamMemory")
                        if isinstance(self_trust, dict)
                        and isinstance(self_trust.get("teamMemory"), dict)
                        else {}
                    )
                    session.nana_telemetry = {
                        "turn": turn,
                        "generation": generation,
                        "reason": (
                            f"n4:{n4_shadow.get('reason') or 'unknown'}"
                            if full_amiibo_live
                            else str(selection.get("reason") or "unknown")
                        ),
                        "intervened": intervened,
                        "autonomyLevel": "N4" if full_amiibo_live else "N2",
                        "lambdaCap": (
                            None
                            if full_amiibo_live
                            else selection.get("lambdaCap", NURSERY_LAMBDA_CAP)
                        ),
                        "effectiveLambda": (
                            None if full_amiibo_live else selection.get("effectiveLambda")
                        ),
                        "requiredLambdaCap": selection.get("requiredLambdaCap"),
                        "lambdaGap": selection.get("lambdaGap"),
                        "predictionConfidence": selection.get(
                            "confidence", plan.get("confidence")
                        ),
                        "confidenceScale": selection.get(
                            "confidenceScale", plan.get("confidenceScale")
                        ),
                        "candidateCountEvaluated": int(
                            selection.get("candidateCountEvaluated") or 0
                        ),
                        "candidateFunnel": copy.deepcopy(
                            selection.get("candidateFunnel") or {}
                        ),
                        "legalOrders": copy.deepcopy(legal_order_diag),
                        "n4Shadow": copy.deepcopy(n4_shadow),
                        "discarded": {
                            "branchRegret": _compact_telemetry_candidate(
                                selection.get("closestBranchReject")
                            ),
                            "nurseryRegret": _compact_telemetry_candidate(
                                selection.get("closestNurseryRegretReject")
                            ),
                            "counterMiss": _compact_telemetry_candidate(
                                selection.get("closestCounterMiss")
                            ),
                        },
                        "expectedCounterDelta": selection.get("expectedCounterDelta"),
                        "margin": selection.get("margin"),
                        "candidate": _compact_telemetry_candidate(telemetry_candidate),
                        "lightTrust": {
                            "trust": light_trust.get("trust"),
                            "confidence": light_trust.get("confidence"),
                        },
                        "selfTrust": {
                            "trust": self_trust.get("trust")
                            if isinstance(self_trust, dict)
                            else None,
                            "confidence": self_trust.get("confidence")
                            if isinstance(self_trust, dict)
                            else None,
                            "teamMemoryBlend": self_trust.get("teamMemoryBlend")
                            if isinstance(self_trust, dict)
                            else None,
                            "teamMemoryEffect": self_trust.get("teamMemoryEffect")
                            if isinstance(self_trust, dict)
                            else None,
                        },
                        "teamMemoryAtDecision": copy.deepcopy(team_memory_from_self),
                        "interventionsUsed": (
                            None
                            if full_amiibo_live
                            else used + (1 if intervened else 0)
                        ),
                        "interventionBudget": (
                            None
                            if full_amiibo_live
                            else MAX_INTERVENTIONS_PER_BATTLE
                        ),
                    }

                    canonical = light.get("canonicalAction") or {}
                    canonical_indices = canonical.get("indices") or []
                    if full_amiibo_live:
                        selected_key = str(n4_shadow.get("selectedKey") or "")
                        if (
                            legal_set is not None
                            and legal_set.resolved is True
                            and n4_shadow.get("eligible") is True
                            and selected_key
                        ):
                            authorized = _authorize_n4_selection(
                                legal_set,
                                n4_shadow,
                            )
                            executed_order = authorized.order
                            executed_action = copy.deepcopy(authorized.action)
                            actor = "nana"
                            intervened = bool(n4_shadow.get("wouldChange"))
                        else:
                            if not (
                                isinstance(canonical_indices, list)
                                and len(canonical_indices) == 2
                                and all(isinstance(value, int) for value in canonical_indices)
                            ):
                                raise RuntimeError(
                                    "N4 fallback perdió LIGHT canonicalAction."
                                )
                            executed_order = self._raw_light_choose(current)
                            executed_action = stage2_v2._structured_action(
                                current,
                                [
                                    int(canonical_indices[0]),
                                    int(canonical_indices[1]),
                                ],
                            )
                            actor = "light"
                            intervened = False
                    elif intervened:
                        candidate = selection.get("candidate") or {}
                        indices = candidate.get("indices") or []
                        if not (
                            isinstance(indices, list)
                            and len(indices) == 2
                            and all(isinstance(value, int) for value in indices)
                        ):
                            raise RuntimeError("Nursery candidate perdió sus índices legales.")
                        executed_order = DoublesEnv.action_to_order(
                            np.asarray(indices, dtype=np.int64),
                            current,
                        )
                        executed_action = stage2_v2._structured_action(current, list(indices))
                        actor = "nana"
                    else:
                        if not (
                            isinstance(canonical_indices, list)
                            and len(canonical_indices) == 2
                            and all(isinstance(value, int) for value in canonical_indices)
                        ):
                            raise RuntimeError("LIGHT canonicalAction perdió sus índices legales.")
                        executed_order = self._raw_light_choose(current)
                        executed_action = stage2_v2._structured_action(
                            current,
                            [int(canonical_indices[0]), int(canonical_indices[1])],
                        )
                        actor = "light"
                except Exception as error:
                    try:
                        service.nana.append_event(
                            session.id,
                            "nana_nursery_error",
                            {
                                "modelVersion": NURSERY_MODEL_VERSION,
                                "turn": turn,
                                "teacher": _teacher_ref(service._nana_teacher),
                                "error": f"{type(error).__name__}: {error}",
                                "fallback": "LIGHT",
                                "phase": "pre-commit",
                            },
                        )
                    except Exception:
                        pass
                    # Pre-commit fault isolation: the parent shadow wrapper owns
                    # canonical LIGHT plus its existing instrumentation.
                    return super().choose_move(current)

                # Commit point: from here the already-built order is the order we
                # will return. Recording failures must NEVER switch the executed
                # action back to LIGHT, otherwise history could claim Nana played
                # a move that was not actually sent to Showdown.
                if intervened and not full_amiibo_live:
                    service._nana_nursery_interventions[session.id] = used + 1

                recording_errors: list[str] = []
                try:
                    service._nana_stage2_v2_note_light(session.id, turn, light)
                except Exception as error:
                    recording_errors.append(
                        f"shadow-note:{type(error).__name__}: {error}"
                    )
                try:
                    service._nana_note_model_turn(
                        session.id,
                        turn,
                        {
                            **copy.deepcopy(light),
                            "_nurseryExecutedAction": copy.deepcopy(executed_action),
                            "_nurseryActor": actor,
                            "_nurseryTeacher": _teacher_ref(service._nana_teacher),
                        },
                    )
                except Exception as error:
                    recording_errors.append(
                        f"turn-record:{type(error).__name__}: {error}"
                    )
                    service._nana_pending.get(session.id, {}).pop(turn, None)
                try:
                    service.nana.append_event(
                        session.id,
                        "nana_nursery_decision",
                        {
                            "modelVersion": (
                                "nana-full-amiibo-n4-v1"
                                if full_amiibo_live
                                else NURSERY_MODEL_VERSION
                            ),
                            "autonomyLevel": "N4" if full_amiibo_live else "N2",
                            "shadowModel": STAGE2_MODEL_VERSION,
                            "generation": generation,
                            "turn": turn,
                            "teacher": _teacher_ref(service._nana_teacher),
                            "intervened": intervened,
                            "actor": actor,
                            "interventionsUsed": service._nana_nursery_interventions.get(session.id, 0),
                            "interventionBudget": MAX_INTERVENTIONS_PER_BATTLE,
                            "lambdaCap": NURSERY_LAMBDA_CAP,
                            "lightCanonicalAction": copy.deepcopy(light.get("canonicalAction")),
                            "executedAction": copy.deepcopy(executed_action),
                            "lightTrust": copy.deepcopy(light_trust),
                            "selfTrust": copy.deepcopy(self_trust),
                            "teamMemory": copy.deepcopy(
                                (self_trust.get("teamMemory") or {})
                                if isinstance(self_trust, dict)
                                else {}
                            ),
                            "legalOrders": copy.deepcopy(legal_order_diag),
                            "n4Shadow": copy.deepcopy(n4_shadow),
                            "selection": copy.deepcopy(selection),
                        },
                    )
                except Exception as error:
                    recording_errors.append(
                        f"decision-record:{type(error).__name__}: {error}"
                    )

                if recording_errors:
                    try:
                        service.nana.append_event(
                            session.id,
                            "nana_nursery_recording_error",
                            {
                                "modelVersion": NURSERY_MODEL_VERSION,
                                "turn": turn,
                                "teacher": _teacher_ref(service._nana_teacher),
                                "actor": actor,
                                "intervened": intervened,
                                "errors": recording_errors,
                                "executedAction": copy.deepcopy(executed_action),
                                "fallback": False,
                            },
                        )
                    except Exception:
                        pass
                return executed_order

        NanaNurseryPlayer.__name__ = "NanaNurseryPlayer"
        self.runtime.player_class = NanaNurseryPlayer
        self._nana_nursery_player_wrapped = True
        _apply_nursery_metadata(self)

    async def start(self: Any, request: Any):
        session = await original_start(self, request)
        self._nana_nursery_interventions[session.id] = 0
        self._nana_nursery_model_generation[session.id] = 0
        session.nana_telemetry = {
            "turn": 0,
            "generation": 0,
            "reason": "waiting-first-decision",
            "intervened": False,
            "lambdaCap": None if full_amiibo_live else NURSERY_LAMBDA_CAP,
            "requiredLambdaCap": None,
            "lambdaGap": None,
            "candidateCountEvaluated": 0,
            "interventionsUsed": None if full_amiibo_live else 0,
            "interventionBudget": (
                None if full_amiibo_live else MAX_INTERVENTIONS_PER_BATTLE
            ),
            "autonomyLevel": "N4" if full_amiibo_live else "N2",
        }
        self._nana_policy_contract = _live_policy_contract()
        self._nana_teacher = descriptor_for_service(self)
        _apply_nursery_metadata(self)
        # Strict behavior identity is recorded only after ensure_ready resolves the
        # VGC-Bench action/feature contracts. Start keeps compatibility metadata.
        self.nana.append_event(
            session.id,
            "nana_full_amiibo_start" if full_amiibo_live else "nana_nursery_start",
            {
                "modelVersion": (
                    "nana-full-amiibo-n4-v1"
                    if full_amiibo_live
                    else NURSERY_MODEL_VERSION
                ),
                "teacher": _teacher_ref(self._nana_teacher),
                "teacherRole": (
                    "advisor-fallback" if full_amiibo_live else "teacher-fallback"
                ),
                "autonomyLevel": "N4" if full_amiibo_live else "N2",
                "live": True,
                "lambdaCap": None if full_amiibo_live else NURSERY_LAMBDA_CAP,
                "maxInterventionsPerBattle": (
                    None if full_amiibo_live else MAX_INTERVENTIONS_PER_BATTLE
                ),
                "fallback": "LIGHT",
                "automaticPromotion": False,
            },
        )
        return session

    def _nana_note_model_turn(
        self: Any,
        session_id: str,
        turn: int,
        light: dict[str, Any],
    ) -> None:
        entry = self._nana_turn_entry(session_id, turn)
        executed = light.get("_nurseryExecutedAction")
        actor = str(light.get("_nurseryActor") or "light")
        teacher = light.get("_nurseryTeacher")
        clean_light = copy.deepcopy(light)
        for key in ("_nurseryExecutedAction", "_nurseryActor", "_nurseryTeacher"):
            clean_light.pop(key, None)
        entry["model"] = {
            "action": copy.deepcopy(
                executed if isinstance(executed, dict) else clean_light.get("canonicalAction")
            ),
            "actor": actor,
            "teacher": _teacher_ref(
                teacher if isinstance(teacher, dict) else self._nana_teacher
            ),
            "light": clean_light,
        }
        self._nana_flush_turn(session_id, turn)

    def _nana_flush_turn(self: Any, session_id: str, turn: int) -> None:
        entry = self._nana_turn_entry(session_id, turn)
        human = entry.get("human")
        model = entry.get("model")
        if human is None or model is None:
            return
        try:
            generation = int(getattr(self.get_session(session_id), "generation", 0) or 0)
        except Exception:
            generation = 0
        self.nana.append_event(
            session_id,
            "turn_choice",
            {
                "generation": generation,
                "turn": int(turn),
                "state": human["state"],
                "legalActions": human["legalActions"],
                "humanAction": human["action"],
                "modelAction": model["action"],
                "modelActor": model.get("actor") or "light",
                "teacher": _teacher_ref(model.get("teacher") or self._nana_teacher),
                "lightCanonicalAction": copy.deepcopy(
                    (model.get("light") or {}).get("canonicalAction")
                ),
                "light": model["light"],
            },
        )
        self._nana_pending.get(session_id, {}).pop(turn, None)

    def snapshot(self: Any, session: Any) -> dict[str, Any]:
        data = original_snapshot(self, session)
        used = self._nana_nursery_interventions.get(session.id, 0)
        team_context = (
            self._nana_session_team_context.get(session.id, {})
            if hasattr(self, "_nana_session_team_context")
            else {}
        )
        try:
            current_team_memory = team_trust_for(
                self.nana_team_memory,
                team_context,
            )
        except Exception:
            current_team_memory = {
                "eligible": False,
                "reason": "team-memory-unavailable",
                "components": [],
            }
        telemetry = copy.deepcopy(
            getattr(session, "nana_telemetry", {})
            if isinstance(getattr(session, "nana_telemetry", {}), dict)
            else {}
        )
        telemetry["teamMemory"] = copy.deepcopy(current_team_memory)
        telemetry["teamMemorySummary"] = {
            "modelVersion": self.nana_team_memory.get("modelVersion")
            if isinstance(self.nana_team_memory, dict)
            else None,
            "observations": int(self.nana_team_memory.get("observations") or 0)
            if isinstance(self.nana_team_memory, dict)
            else 0,
            "taggedSessions": int(self.nana_team_memory.get("taggedSessions") or 0)
            if isinstance(self.nana_team_memory, dict)
            else 0,
            "ignoredLegacyTeamSessions": int(
                self.nana_team_memory.get("ignoredLegacyTeamSessions") or 0
            )
            if isinstance(self.nana_team_memory, dict)
            else 0,
        }
        data.setdefault("nana", {}).update(
            {
                "stage": 4 if full_amiibo_live else 2.3,
                "mode": (
                    "full-amiibo-live-v1"
                    if full_amiibo_live
                    else "nursery-live-v1"
                ),
                "autonomyLevel": "N4" if full_amiibo_live else "N2",
                "influence": LIVE_INFLUENCE,
                "nursery": {
                    "modelVersion": (
                        "nana-full-amiibo-n4-v1"
                        if full_amiibo_live
                        else NURSERY_MODEL_VERSION
                    ),
                    "teacher": _teacher_ref(self._nana_teacher),
                    "teacherRole": (
                        "advisor-fallback"
                        if full_amiibo_live
                        else "teacher-fallback"
                    ),
                    "lambdaCap": None if full_amiibo_live else NURSERY_LAMBDA_CAP,
                    "maxInterventionsPerBattle": (
                        None if full_amiibo_live else MAX_INTERVENTIONS_PER_BATTLE
                    ),
                    "autonomy": (
                        full_amiibo_contract()
                        if full_amiibo_live
                        else _live_autonomy_contract()
                    ),
                    "interventionsUsed": None if full_amiibo_live else used,
                    "fallback": "LIGHT",
                    "automaticPromotion": False,
                },
                "telemetry": telemetry,
            }
        )
        return data

    def _nana_finish(self: Any, session: Any) -> None:
        if session.id in self._nana_nursery_finished:
            return
        self._nana_nursery_finished.add(session.id)
        original_finish(self, session)
        try:
            before = int(self.nana_self_summary.get("observations") or 0)
            self.nana_self_summary = rebuild_self_for_recorder(self.nana)
            self.nana_team_memory = rebuild_team_memory_for_recorder(self.nana)
            self.nana_counter_calibration = rebuild_counter_calibration(self.nana)
            after = int(self.nana_self_summary.get("observations") or 0)
            promotion = promotion_status(
                self.nana.iter_events(),
                teacher_key=self._nana_teacher["key"],
            )
            self.nana.append_event(
                session.id,
                "nana_nursery_rebuild",
                {
                    "modelVersion": NURSERY_MODEL_VERSION,
                    "teacher": _teacher_ref(self._nana_teacher),
                    "selfObservationsBefore": before,
                    "selfObservationsAfter": after,
                    "teamMemoryObservations": int(
                        self.nana_team_memory.get("observations") or 0
                    ),
                    "counterCalibration": self.nana_counter_calibration.public(),
                    "promotion": promotion,
                },
            )
        except Exception as error:
            try:
                self.nana.append_event(
                    session.id,
                    "nana_nursery_error",
                    {
                        "modelVersion": NURSERY_MODEL_VERSION,
                        "teacher": _teacher_ref(self._nana_teacher),
                        "error": f"{type(error).__name__}: {error}",
                        "phase": "post-battle-rebuild",
                    },
                )
            except Exception:
                pass
        finally:
            self._nana_nursery_interventions.pop(session.id, None)
            self._nana_nursery_model_generation.pop(session.id, None)

    service_class.__init__ = __init__
    service_class._teacher_query_args = _teacher_query_args
    service_class._apply_nursery_metadata = _apply_nursery_metadata
    service_class.ensure_ready = ensure_ready
    service_class.start = start
    service_class._nana_note_model_turn = _nana_note_model_turn
    service_class._nana_flush_turn = _nana_flush_turn
    service_class.snapshot = snapshot
    service_class._nana_finish = _nana_finish
    service_class._nana_nursery_live_v1 = True
    service_class._nana_full_amiibo_live = bool(full_amiibo_live)
    service_class.nana_profile_id = profile_id
    return service_class


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_nursery_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
