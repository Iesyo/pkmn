"""Nana 1 observational predictor runtime.

This module layers the human-action predictor on top of the already validated
Nana 0 recorder/runtime. LIGHT M-C remains the only policy that chooses model
moves; Nana 1 has influence=0.0.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Sequence

from battle_lab import local_sparring_service as sparring
from battle_lab.nana_predictor import (
    MIN_CONTEXT_SAMPLES,
    MIN_HISTORY_SAMPLES,
    NanaPredictor,
    action_signature,
)
from battle_lab.nana_recorder import utc_now
from battle_lab.nana_runtime import (
    install_nana_service,
    install_reusable_viewer,
    parse_nana_args,
)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _prediction_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    observed = [
        event
        for event in events
        if isinstance(event, dict) and event.get("type") == "human_choice_observed"
    ]
    evaluated = 0
    top1_hits = 0
    top3_hits = 0
    confidence_total = 0.0
    chosen_probability_total = 0.0
    cold_start = 0

    for event in observed:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        prediction = payload.get("prediction")
        if not isinstance(prediction, dict):
            continue
        evaluated += 1
        rank = prediction.get("rank")
        if rank == 1:
            top1_hits += 1
        if isinstance(rank, int) and 1 <= rank <= 3:
            top3_hits += 1
        confidence = prediction.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence_total += float(confidence)
        chosen_probability = prediction.get("chosenProbability")
        if isinstance(chosen_probability, (int, float)):
            chosen_probability_total += float(chosen_probability)
        if prediction.get("confidenceBand") == "cold-start":
            cold_start += 1

    return {
        "evaluated": evaluated,
        "top1Hits": top1_hits,
        "top3Hits": top3_hits,
        "top1Accuracy": (top1_hits / evaluated) if evaluated else None,
        "top3Accuracy": (top3_hits / evaluated) if evaluated else None,
        "meanConfidence": (confidence_total / evaluated) if evaluated else None,
        "meanChosenProbability": (
            chosen_probability_total / evaluated if evaluated else None
        ),
        "coldStartPredictions": cold_start,
    }


def install_nana_stage1_service(*, profile_id: str) -> type:
    """Layer the observational predictor over the validated Nana 0 service."""

    base_service_class = install_nana_service(profile_id=profile_id)
    if getattr(base_service_class, "_nana_stage1_service", False):
        base_service_class.nana_profile_id = profile_id
        return base_service_class

    class NanaStage1BattleLabLocalService(base_service_class):
        _nana_stage1_service = True
        nana_profile_id = profile_id

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.nana_predictor = NanaPredictor.from_events(self.nana.iter_events())
            self._nana_stage1_predictions: dict[
                str, dict[int, dict[str, Any]]
            ] = {}

        async def ensure_ready(self) -> None:
            await super().ensure_ready()
            self.runtime_metadata = {
                **self.runtime_metadata,
                "nana": {
                    "enabled": True,
                    "stage": 1,
                    "profileId": self.nana.profile_id,
                    "influence": 0.0,
                    "predictor": {
                        "type": "contextual-bayes",
                        "historySamples": self.nana_predictor.samples,
                        "minHistorySamples": MIN_HISTORY_SAMPLES,
                        "minContextSamples": MIN_CONTEXT_SAMPLES,
                    },
                },
            }

        async def start(self, request: Any):
            session = await super().start(request)
            self._nana_stage1_predictions[session.id] = {}
            self.nana.append_event(
                session.id,
                "nana_stage",
                {
                    "stage": 1,
                    "mode": "Nana 1 predictor observational",
                    "influence": 0.0,
                    "predictor": "contextual-bayes",
                    "historySamples": self.nana_predictor.samples,
                    "minHistorySamples": MIN_HISTORY_SAMPLES,
                    "minContextSamples": MIN_CONTEXT_SAMPLES,
                },
            )
            return session

        def _stage1_prediction(
            self,
            session: Any,
            *,
            source: str,
        ) -> dict[str, Any] | None:
            if (
                session.phase != "waiting-choice"
                or not session.legal_actions
                or int(getattr(session, "generation", 0) or 0) <= 0
            ):
                return None
            generation = int(session.generation)
            cache = self._nana_stage1_predictions.setdefault(session.id, {})
            cached = cache.get(generation)
            if cached is not None:
                return cached

            state = copy.deepcopy(session.battle_state)
            legal_actions = copy.deepcopy(session.legal_actions)
            turn = int(state.get("turn", 0) or 0)
            prediction = self.nana_predictor.predict(state, legal_actions)
            cache[generation] = prediction
            self.nana.append_event(
                session.id,
                "human_prediction",
                {
                    "generation": generation,
                    "turn": turn,
                    "source": source,
                    "historySamples": prediction["historySamples"],
                    "contextSamples": prediction["contextSamples"],
                    "ready": prediction["ready"],
                    "confidence": prediction["confidence"],
                    "confidenceBand": prediction["confidenceBand"],
                    "entropy": prediction["entropy"],
                    "candidateCount": len(prediction["candidates"]),
                    "top": copy.deepcopy(prediction["top"]),
                },
            )
            return prediction

        async def submit_choice(self, session_id: str, choice_id: str):
            session = self.get_session(session_id)
            prediction = self._stage1_prediction(session, source="submit-fallback")
            generation = int(getattr(session, "generation", 0) or 0)
            state = copy.deepcopy(session.battle_state)
            legal_actions = copy.deepcopy(session.legal_actions)
            turn = int(state.get("turn", 0) or 0)
            action = next(
                (
                    copy.deepcopy(candidate)
                    for candidate in legal_actions
                    if candidate.get("id") == choice_id
                ),
                None,
            )

            result = await super().submit_choice(session_id, choice_id)
            if action is None:
                return result

            signature = action_signature(action)
            rank = None
            chosen_probability = 0.0
            if isinstance(prediction, dict):
                for index, candidate in enumerate(
                    prediction.get("candidates") or [],
                    start=1,
                ):
                    if candidate.get("signature") == signature:
                        rank = index
                        chosen_probability = float(
                            candidate.get("probability") or 0.0
                        )
                        break

            observed = {
                "generation": generation,
                "turn": turn,
                "state": state,
                "legalActions": legal_actions,
                "action": action,
                "actionSignature": signature,
                "prediction": {
                    "rank": rank,
                    "chosenProbability": chosen_probability,
                    "top1Hit": rank == 1,
                    "top3Hit": isinstance(rank, int) and 1 <= rank <= 3,
                    "confidence": (
                        prediction.get("confidence")
                        if isinstance(prediction, dict)
                        else 0.0
                    ),
                    "confidenceBand": (
                        prediction.get("confidenceBand")
                        if isinstance(prediction, dict)
                        else "cold-start"
                    ),
                    "historySamples": (
                        prediction.get("historySamples")
                        if isinstance(prediction, dict)
                        else self.nana_predictor.samples
                    ),
                    "contextSamples": (
                        prediction.get("contextSamples")
                        if isinstance(prediction, dict)
                        else 0
                    ),
                },
            }
            self.nana.append_event(session_id, "human_choice_observed", observed)
            self.nana_predictor.observe(state, action)
            return result

        def snapshot(self, session: Any) -> dict[str, Any]:
            prediction = self._stage1_prediction(session, source="snapshot")
            data = super().snapshot(session)
            data["nana"] = {
                "enabled": True,
                "stage": 1,
                "profileId": self.nana.profile_id,
                "influence": 0.0,
                "predictor": {
                    "enabled": True,
                    "type": "contextual-bayes",
                    "historySamples": self.nana_predictor.samples,
                    "ready": bool(prediction and prediction.get("ready")),
                    "confidenceBand": (
                        prediction.get("confidenceBand")
                        if isinstance(prediction, dict)
                        else "cold-start"
                    ),
                    # Deliberately do not expose predicted actions to the human UI.
                    "predictionRecorded": prediction is not None,
                },
            }
            return data

        def _write_predictor_summary(self) -> None:
            events = list(self.nana.iter_events())
            payload = {
                "schemaVersion": 1,
                "profileId": self.nana.profile_id,
                "stage": 1,
                "updatedAt": utc_now(),
                "model": "contextual-bayes",
                "historySamples": self.nana_predictor.samples,
                "minHistorySamples": MIN_HISTORY_SAMPLES,
                "minContextSamples": MIN_CONTEXT_SAMPLES,
                "metrics": _prediction_metrics(events),
            }
            _atomic_json(self.nana.profile_root / "predictor.json", payload)

        def _nana_finish(self, session: Any) -> None:
            super()._nana_finish(session)
            self._write_predictor_summary()

    sparring.BattleLabLocalService = NanaStage1BattleLabLocalService
    return NanaStage1BattleLabLocalService


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_nana_stage1_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
