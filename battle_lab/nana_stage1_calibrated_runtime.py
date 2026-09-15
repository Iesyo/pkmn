"""Calibrated Nana 1 observational runtime.

This module keeps the validated Nana 1 ranking model and LIGHT M-C untouched,
but slows probability confidence growth after the readiness threshold. The
original Nana 1 runtime remains available for audit/reproducibility.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Sequence

from battle_lab import nana_stage1_runtime as stage1
from battle_lab.nana_predictor import (
    MIN_CONTEXT_SAMPLES,
    MIN_HISTORY_SAMPLES,
    PAIR_WEIGHT,
    TOP_K,
    NanaPredictor as NanaPredictorV1,
    _normalized_entropy,
    _softmax,
    context_features,
    half_signature,
)
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args


MODEL_VERSION = "contextual-bayes-v2"
HISTORY_EVIDENCE_K = 48.0
CONTEXT_EVIDENCE_K = 12.0
MAX_CALIBRATION_STRENGTH = 0.85


class CalibratedNanaPredictor(NanaPredictorV1):
    """Same Nana 1 ranking, with conservative evidence-based calibration.

    The affine blend with a uniform distribution preserves candidate ordering
    whenever calibration strength is positive. It only reduces how aggressively
    probabilities move away from uniform while the personal sample is small.
    """

    def predict(
        self,
        state: dict[str, Any],
        legal_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        candidates = [
            candidate for candidate in legal_actions if isinstance(candidate, dict)
        ]
        if not candidates:
            return {
                "modelVersion": MODEL_VERSION,
                "historySamples": self.samples,
                "contextSamples": 0,
                "calibrationStrength": 0.0,
                "ready": False,
                "confidence": 0.0,
                "confidenceBand": "cold-start",
                "entropy": 1.0,
                "top": [],
                "candidates": [],
            }

        features = context_features(state)
        scores: list[float] = []
        signatures: list[str] = []
        for candidate in candidates:
            first = half_signature(candidate.get("first"))
            second = half_signature(candidate.get("second"))
            signature = f"{first}//{second}"
            signatures.append(signature)
            score = self._slot_score(0, first, features) + self._slot_score(
                1, second, features
            )
            pair_count = self.pair_counts[(first, second)]
            if pair_count:
                score += PAIR_WEIGHT * math.log1p(pair_count)
            scores.append(score)

        raw_probabilities = _softmax(scores)
        strong_features = [
            feature
            for feature in features
            if feature.startswith(("own0=", "own1=", "opp0=", "opp1="))
        ]
        context_samples = max(
            (self.feature_totals[feature] for feature in strong_features),
            default=0,
        )

        history_strength = (
            self.samples / (self.samples + HISTORY_EVIDENCE_K)
            if self.samples > 0
            else 0.0
        )
        context_strength = (
            context_samples / (context_samples + CONTEXT_EVIDENCE_K)
            if context_samples > 0
            else 0.0
        )
        calibration_strength = min(
            MAX_CALIBRATION_STRENGTH,
            history_strength * (0.5 + 0.5 * context_strength),
        )

        uniform = 1.0 / len(candidates)
        probabilities = [
            calibration_strength * probability
            + (1.0 - calibration_strength) * uniform
            for probability in raw_probabilities
        ]
        ranked_indices = sorted(
            range(len(candidates)),
            key=lambda index: (-probabilities[index], index),
        )
        entropy = _normalized_entropy(probabilities)
        sharpness = max(0.0, min(1.0, 1.0 - entropy))
        top_probability = probabilities[ranked_indices[0]]
        second_probability = (
            probabilities[ranked_indices[1]] if len(ranked_indices) > 1 else 0.0
        )
        margin = max(0.0, min(1.0, top_probability - second_probability))
        confidence = max(
            0.0,
            min(
                1.0,
                calibration_strength * (0.65 * sharpness + 0.35 * margin),
            ),
        )
        ready = (
            self.samples >= MIN_HISTORY_SAMPLES
            and context_samples >= MIN_CONTEXT_SAMPLES
        )
        if confidence < 0.15:
            confidence_band = "cold-start"
        elif confidence < 0.35:
            confidence_band = "low"
        elif confidence < 0.60:
            confidence_band = "medium"
        else:
            confidence_band = "high"

        rendered = []
        for index, candidate in enumerate(candidates):
            rendered.append(
                {
                    "id": candidate.get("id"),
                    "signature": signatures[index],
                    "probability": probabilities[index],
                    "first": candidate.get("first"),
                    "second": candidate.get("second"),
                }
            )
        ranked = [rendered[index] for index in ranked_indices]
        return {
            "modelVersion": MODEL_VERSION,
            "historySamples": self.samples,
            "contextSamples": context_samples,
            "calibrationStrength": calibration_strength,
            "ready": ready,
            "confidence": confidence,
            "confidenceBand": confidence_band,
            "entropy": entropy,
            "top": ranked[: min(TOP_K, len(ranked))],
            "candidates": ranked,
        }


def _rewrite_predictor_summary(service: Any) -> None:
    path = service.nana.profile_root / "predictor.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return
    payload["model"] = MODEL_VERSION
    payload["calibration"] = {
        "type": "evidence-shrinkage",
        "historyEvidenceK": HISTORY_EVIDENCE_K,
        "contextEvidenceK": CONTEXT_EVIDENCE_K,
        "maxStrength": MAX_CALIBRATION_STRENGTH,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def install_calibrated_stage1_service(*, profile_id: str) -> type:
    """Install Nana 1 with calibrated predictor while retaining stage-1 logic."""

    stage1.NanaPredictor = CalibratedNanaPredictor
    service_class = stage1.install_nana_stage1_service(profile_id=profile_id)
    if getattr(service_class, "_nana_stage1_calibrated_v2", False):
        return service_class

    original_start = service_class.start
    original_ensure_ready = service_class.ensure_ready
    original_write_summary = service_class._write_predictor_summary

    async def ensure_ready(self: Any) -> None:
        await original_ensure_ready(self)
        nana_meta = self.runtime_metadata.setdefault("nana", {})
        predictor_meta = nana_meta.setdefault("predictor", {})
        predictor_meta["modelVersion"] = MODEL_VERSION
        predictor_meta["calibration"] = "evidence-shrinkage"

    async def start(self: Any, request: Any):
        session = await original_start(self, request)
        self.nana.append_event(
            session.id,
            "nana_predictor_version",
            {
                "modelVersion": MODEL_VERSION,
                "calibration": "evidence-shrinkage",
                "historyEvidenceK": HISTORY_EVIDENCE_K,
                "contextEvidenceK": CONTEXT_EVIDENCE_K,
                "maxCalibrationStrength": MAX_CALIBRATION_STRENGTH,
                "historySamples": self.nana_predictor.samples,
                "influence": 0.0,
            },
        )
        return session

    def write_predictor_summary(self: Any) -> None:
        original_write_summary(self)
        _rewrite_predictor_summary(self)

    service_class.ensure_ready = ensure_ready
    service_class.start = start
    service_class._write_predictor_summary = write_predictor_summary
    service_class._nana_stage1_calibrated_v2 = True
    return service_class


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_calibrated_stage1_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
