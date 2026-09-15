"""Nana 2.3 Nursery: first guarded live interventions.

LIGHT remains the teacher/fallback, but Nana may execute at most one near-LIGHT
alternative per BO1. Every real Nana intervention is recorded separately from
LIGHT and becomes observational experience for Nana's own self-critic on later
battles. Teacher checkpoint/regulation identity is persisted so future LIGHT
upgrades start a fresh trust track instead of inheriting stale conclusions.
"""

from __future__ import annotations

import copy
from typing import Any, Sequence

import numpy as np

from battle_lab import local_sparring_service as sparring
from battle_lab import nana_stage2_shadow_v2_runtime as stage2_v2
from battle_lab.nana_light_critic import trust_for
from battle_lab.nana_nursery import (
    MAX_INTERVENTIONS_PER_BATTLE,
    NURSERY_LAMBDA_CAP,
    NURSERY_MODEL_VERSION,
    SELF_PRIOR_TRUST,
    choose_candidate,
    promotion_status,
    rebuild_self_for_recorder,
)
from battle_lab.nana_policy import inspect_light_decision
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_shadow_v22_runtime import (
    LIGHT_CRITIC_MODEL_VERSION,
    STAGE2_MODEL_VERSION,
    install_light_critic_service,
)
from battle_lab.nana_teacher import descriptor_for_service


LIVE_INFLUENCE = 1.0


def _latest_teacher_key(events: Sequence[dict[str, Any]]) -> str:
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("type") != "nana_teacher_version":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
        key = str(teacher.get("key") or "")
        if key:
            return key
    return ""


def install_nursery_service(*, profile_id: str) -> type:
    service_class = install_light_critic_service(profile_id=profile_id)
    if getattr(service_class, "_nana_nursery_live_v1", False):
        service_class.nana_profile_id = profile_id
        return service_class

    original_init = service_class.__init__
    original_ensure_ready = service_class.ensure_ready
    original_start = service_class.start
    original_snapshot = service_class.snapshot
    original_finish = service_class._nana_finish

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        history = list(self.nana.iter_events())
        self._nana_previous_teacher_key = _latest_teacher_key(history)
        self._nana_teacher = descriptor_for_service(self)
        self._nana_allow_legacy_teacher_fallback = (
            not self._nana_previous_teacher_key
            or self._nana_previous_teacher_key == self._nana_teacher["key"]
        )
        self._nana_nursery_player_wrapped = False
        self._nana_nursery_interventions: dict[str, int] = {}
        self._nana_nursery_finished: set[str] = set()
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

    def _teacher_query_args(self: Any) -> dict[str, Any]:
        return {
            "teacher_key": self._nana_teacher["key"],
            "fallback_teacher_key": (
                self._nana_teacher.get("legacyKey")
                if self._nana_allow_legacy_teacher_fallback
                else None
            ),
        }

    async def ensure_ready(self: Any) -> None:
        await original_ensure_ready(self)
        # Refresh descriptor after runtime metadata has the canonical checkpoint hash.
        self._nana_teacher = descriptor_for_service(self)
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
                # Skip NanaStage2ShadowV2Player and NanaObservedPlayer so the
                # canonical LIGHT order is evaluated exactly once. Nursery
                # re-emits all recorder/shadow instrumentation itself below.
                return super(observed_class, self).choose_move(current)

            def choose_move(self, current: Any):
                turn = int(getattr(current, "turn", 0) or 0)
                session = service.active_session
                if session is None:
                    return self._raw_light_choose(current)
                try:
                    light = inspect_light_decision(
                        self,
                        current,
                        include_joint_scores=True,
                    )
                    if light.get("waiting") is True:
                        return self._raw_light_choose(current)
                    light = stage2_v2._enrich_joint_scores_strict(current, light)
                    generation = int(getattr(session, "generation", 0) or 0)
                    cached = service._nana_stage1_predictions.setdefault(
                        session.id, {}
                    ).get(generation)
                    state_turn = int((session.battle_state or {}).get("turn", 0) or 0)
                    turn_matched = (
                        cached is not None
                        and generation > 0
                        and session.phase == "waiting-choice"
                        and state_turn == turn
                    )
                    plan = stage2_v2.shadow_rerank_v2(
                        light,
                        cached,
                        prediction_source=("snapshot-cache" if cached is not None else "missing"),
                        prediction_generation=generation,
                        turn_matched=turn_matched,
                    )
                    model_state = sparring._battle_snapshot(current)
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
                    selection = choose_candidate(
                        plan,
                        light_trust=light_trust,
                        self_trust=self_trust,
                        lambda_cap=NURSERY_LAMBDA_CAP,
                    )

                    used = service._nana_nursery_interventions.get(session.id, 0)
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

                    canonical = light.get("canonicalAction") or {}
                    canonical_indices = canonical.get("indices") or []
                    if intervened:
                        candidate = selection.get("candidate") or {}
                        indices = candidate.get("indices") or []
                        if not (
                            isinstance(indices, list)
                            and len(indices) == 2
                            and all(isinstance(value, int) for value in indices)
                        ):
                            raise RuntimeError("Nursery candidate perdió sus índices legales.")
                        executed_order = stage2_v2.DoublesEnv.action_to_order(  # type: ignore[attr-defined]
                            np.asarray(indices, dtype=np.int64),
                            current,
                        )
                        executed_action = stage2_v2._structured_action(current, list(indices))
                        actor = "nana"
                        service._nana_nursery_interventions[session.id] = used + 1
                    else:
                        if not (
                            isinstance(canonical_indices, list)
                            and len(canonical_indices) == 2
                            and all(isinstance(value, int) for value in canonical_indices)
                        ):
                            return self._raw_light_choose(current)
                        executed_order = self._raw_light_choose(current)
                        executed_action = stage2_v2._structured_action(
                            current,
                            [int(canonical_indices[0]), int(canonical_indices[1])],
                        )
                        actor = "light"

                    service._nana_stage2_v2_note_light(session.id, turn, light)
                    service._nana_note_model_turn(
                        session.id,
                        turn,
                        {
                            **copy.deepcopy(light),
                            "_nurseryExecutedAction": copy.deepcopy(executed_action),
                            "_nurseryActor": actor,
                            "_nurseryTeacher": copy.deepcopy(service._nana_teacher),
                        },
                    )
                    service.nana.append_event(
                        session.id,
                        "nana_nursery_decision",
                        {
                            "modelVersion": NURSERY_MODEL_VERSION,
                            "shadowModel": STAGE2_MODEL_VERSION,
                            "generation": generation,
                            "turn": turn,
                            "teacher": copy.deepcopy(service._nana_teacher),
                            "intervened": intervened,
                            "actor": actor,
                            "interventionsUsed": service._nana_nursery_interventions.get(session.id, 0),
                            "interventionBudget": MAX_INTERVENTIONS_PER_BATTLE,
                            "lambdaCap": NURSERY_LAMBDA_CAP,
                            "lightCanonicalAction": copy.deepcopy(light.get("canonicalAction")),
                            "executedAction": copy.deepcopy(executed_action),
                            "lightTrust": copy.deepcopy(light_trust),
                            "selfTrust": copy.deepcopy(self_trust),
                            "selection": copy.deepcopy(selection),
                        },
                    )
                    return executed_order
                except Exception as error:
                    try:
                        service.nana.append_event(
                            session.id,
                            "nana_nursery_error",
                            {
                                "modelVersion": NURSERY_MODEL_VERSION,
                                "turn": turn,
                                "teacher": copy.deepcopy(service._nana_teacher),
                                "error": f"{type(error).__name__}: {error}",
                                "fallback": "LIGHT",
                            },
                        )
                    except Exception:
                        pass
                    # Fault isolation: parent shadow wrapper preserves canonical
                    # LIGHT + all previous observation instrumentation.
                    return super().choose_move(current)

        NanaNurseryPlayer.__name__ = "NanaNurseryPlayer"
        self.runtime.player_class = NanaNurseryPlayer
        self._nana_nursery_player_wrapped = True
        nana_meta = self.runtime_metadata.setdefault("nana", {})
        nana_meta.update(
            {
                "stage": 2.3,
                "mode": "nursery-live-v1",
                "influence": LIVE_INFLUENCE,
                "nurseryModel": NURSERY_MODEL_VERSION,
                "lambdaCap": NURSERY_LAMBDA_CAP,
                "maxInterventionsPerBattle": MAX_INTERVENTIONS_PER_BATTLE,
                "teacher": copy.deepcopy(self._nana_teacher),
            }
        )

    async def start(self: Any, request: Any):
        session = await original_start(self, request)
        self._nana_nursery_interventions[session.id] = 0
        teacher = descriptor_for_service(self)
        self._nana_teacher = teacher
        self.nana.append_event(
            session.id,
            "nana_teacher_version",
            {
                "teacher": copy.deepcopy(teacher),
                "reason": "nursery-live-session",
            },
        )
        self.nana.append_event(
            session.id,
            "nana_nursery_start",
            {
                "modelVersion": NURSERY_MODEL_VERSION,
                "teacher": copy.deepcopy(teacher),
                "live": True,
                "lambdaCap": NURSERY_LAMBDA_CAP,
                "maxInterventionsPerBattle": MAX_INTERVENTIONS_PER_BATTLE,
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
            "action": copy.deepcopy(executed if isinstance(executed, dict) else clean_light.get("canonicalAction")),
            "actor": actor,
            "teacher": copy.deepcopy(teacher if isinstance(teacher, dict) else self._nana_teacher),
            "light": clean_light,
        }
        self._nana_flush_turn(session_id, turn)

    def _nana_flush_turn(self: Any, session_id: str, turn: int) -> None:
        entry = self._nana_turn_entry(session_id, turn)
        human = entry.get("human")
        model = entry.get("model")
        if human is None or model is None:
            return
        self.nana.append_event(
            session_id,
            "turn_choice",
            {
                "turn": int(turn),
                "state": human["state"],
                "legalActions": human["legalActions"],
                "humanAction": human["action"],
                "modelAction": model["action"],
                "modelActor": model.get("actor") or "light",
                "teacher": copy.deepcopy(model.get("teacher") or self._nana_teacher),
                "lightCanonicalAction": copy.deepcopy((model.get("light") or {}).get("canonicalAction")),
                "light": model["light"],
            },
        )
        self._nana_pending.get(session_id, {}).pop(turn, None)

    def snapshot(self: Any, session: Any) -> dict[str, Any]:
        data = original_snapshot(self, session)
        used = self._nana_nursery_interventions.get(session.id, 0)
        data.setdefault("nana", {}).update(
            {
                "stage": 2.3,
                "mode": "nursery-live-v1",
                "influence": LIVE_INFLUENCE,
                "nursery": {
                    "modelVersion": NURSERY_MODEL_VERSION,
                    "teacher": copy.deepcopy(self._nana_teacher),
                    "lambdaCap": NURSERY_LAMBDA_CAP,
                    "maxInterventionsPerBattle": MAX_INTERVENTIONS_PER_BATTLE,
                    "interventionsUsed": used,
                    "fallback": "LIGHT",
                    "automaticPromotion": False,
                },
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
                    "teacher": copy.deepcopy(self._nana_teacher),
                    "selfObservationsBefore": before,
                    "selfObservationsAfter": after,
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
                        "teacher": copy.deepcopy(self._nana_teacher),
                        "error": f"{type(error).__name__}: {error}",
                        "phase": "post-battle-rebuild",
                    },
                )
            except Exception:
                pass
        finally:
            self._nana_nursery_interventions.pop(session.id, None)

    service_class.__init__ = __init__
    service_class._teacher_query_args = _teacher_query_args
    service_class.ensure_ready = ensure_ready
    service_class.start = start
    service_class._nana_note_model_turn = _nana_note_model_turn
    service_class._nana_flush_turn = _nana_flush_turn
    service_class.snapshot = snapshot
    service_class._nana_finish = _nana_finish
    service_class._nana_nursery_live_v1 = True
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
