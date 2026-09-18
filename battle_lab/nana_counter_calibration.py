"""Empirical mapper from Nana response_utility to board-delta units.

The mapper is model-agnostic: it is fitted from executed model actions, the
human action actually observed on the same prompt, and the next causal board
state. It only learns from response_utility cases marked relevant; irrelevant
turns do not pretend that a zero proxy score explains unrelated board changes.

A positive through-origin slope is intentional: raw counter utility 0 must map
to 0 incremental board-delta contribution. If the historical relationship is
weak or inverted, calibration remains unresolved and N4 gives this term zero
confidence.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from battle_lab import nana_light_critic as critic
from battle_lab.nana_stage2_shadow_common import response_utility
from battle_lab.nana_recorder import utc_now


COUNTER_CALIBRATION_VERSION = 1
COUNTER_SOURCE_SPACE = "response-utility-v1"
COUNTER_MAPPER_ID = "response-utility-to-board-delta-wls-origin-v1"
MIN_COUNTER_SAMPLES = 12
MIN_COUNTER_R2 = 0.05
MIN_X_ENERGY = 0.25


def _finite(value: Any) -> float | None:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return None
    return rendered if math.isfinite(rendered) else None


@dataclass(frozen=True)
class CounterCalibration:
    version: int
    mapper_id: str
    source_space: str
    target_space: str
    resolved: bool
    reason: str
    samples: int
    slope: float
    r2: float
    confidence: float
    x_energy: float
    fitted_at: str

    def map(self, value: float) -> float:
        rendered = float(value)
        if not math.isfinite(rendered):
            raise ValueError("counter value must be finite")
        if not self.resolved:
            raise RuntimeError(f"counter calibration unresolved: {self.reason}")
        return self.slope * rendered

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _turn_payloads(events: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    rendered: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "turn_choice":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        human = payload.get("humanAction")
        model = payload.get("modelAction")
        if not isinstance(human, dict) or not isinstance(model, dict):
            continue
        key = (
            str(event.get("sessionId") or ""),
            str(event.get("timestamp") or ""),
        )
        if key[0] and key[1]:
            rendered[key] = {
                "humanAction": human,
                "modelAction": model,
            }
    return rendered


def calibration_pairs(events: Iterable[dict[str, Any]]) -> list[dict[str, float]]:
    materialized = [event for event in events if isinstance(event, dict)]
    turns = _turn_payloads(materialized)
    observations = critic.extract_observations(materialized, actor_filter=None)
    pairs: list[dict[str, float]] = []

    for observation in observations:
        key = (
            str(observation.get("sessionId") or ""),
            str(observation.get("timestamp") or ""),
        )
        turn = turns.get(key)
        if not isinstance(turn, dict):
            continue
        utility = response_utility(
            turn["modelAction"],
            turn["humanAction"],
        )
        if int(utility.get("relevant") or 0) <= 0:
            continue
        x = _finite(utility.get("score"))
        y = _finite(observation.get("delta"))
        weight = _finite(observation.get("baseWeight"))
        if x is None or y is None:
            continue
        pairs.append(
            {
                "x": x,
                "y": y,
                "weight": max(0.05, weight if weight is not None else 0.10),
            }
        )
    return pairs


def fit_counter_calibration(
    events: Iterable[dict[str, Any]],
    *,
    min_samples: int = MIN_COUNTER_SAMPLES,
    min_r2: float = MIN_COUNTER_R2,
) -> CounterCalibration:
    pairs = calibration_pairs(events)
    n = len(pairs)
    if n < int(min_samples):
        return CounterCalibration(
            version=COUNTER_CALIBRATION_VERSION,
            mapper_id=COUNTER_MAPPER_ID,
            source_space=COUNTER_SOURCE_SPACE,
            target_space="board-delta-v1",
            resolved=False,
            reason=f"insufficient-samples:{n}/{int(min_samples)}",
            samples=n,
            slope=0.0,
            r2=0.0,
            confidence=0.0,
            x_energy=0.0,
            fitted_at=utc_now(),
        )

    xx = sum(item["weight"] * item["x"] * item["x"] for item in pairs)
    xy = sum(item["weight"] * item["x"] * item["y"] for item in pairs)
    if xx < MIN_X_ENERGY:
        return CounterCalibration(
            version=COUNTER_CALIBRATION_VERSION,
            mapper_id=COUNTER_MAPPER_ID,
            source_space=COUNTER_SOURCE_SPACE,
            target_space="board-delta-v1",
            resolved=False,
            reason=f"insufficient-x-energy:{xx:.6f}",
            samples=n,
            slope=0.0,
            r2=0.0,
            confidence=0.0,
            x_energy=xx,
            fitted_at=utc_now(),
        )

    slope = xy / xx
    if not math.isfinite(slope) or slope <= 0:
        return CounterCalibration(
            version=COUNTER_CALIBRATION_VERSION,
            mapper_id=COUNTER_MAPPER_ID,
            source_space=COUNTER_SOURCE_SPACE,
            target_space="board-delta-v1",
            resolved=False,
            reason=f"non-positive-slope:{slope:.6f}",
            samples=n,
            slope=float(slope) if math.isfinite(slope) else 0.0,
            r2=0.0,
            confidence=0.0,
            x_energy=xx,
            fitted_at=utc_now(),
        )

    weighted_y2 = sum(item["weight"] * item["y"] * item["y"] for item in pairs)
    residual = sum(
        item["weight"] * (item["y"] - slope * item["x"]) ** 2
        for item in pairs
    )
    r2 = 1.0 - residual / weighted_y2 if weighted_y2 > 1e-12 else 0.0
    r2 = max(-1.0, min(1.0, r2))
    if r2 < float(min_r2):
        return CounterCalibration(
            version=COUNTER_CALIBRATION_VERSION,
            mapper_id=COUNTER_MAPPER_ID,
            source_space=COUNTER_SOURCE_SPACE,
            target_space="board-delta-v1",
            resolved=False,
            reason=f"weak-fit-r2:{r2:.6f}",
            samples=n,
            slope=slope,
            r2=r2,
            confidence=0.0,
            x_energy=xx,
            fitted_at=utc_now(),
        )

    sample_confidence = min(1.0, n / 40.0)
    fit_confidence = max(0.0, min(1.0, r2))
    confidence = sample_confidence * fit_confidence
    return CounterCalibration(
        version=COUNTER_CALIBRATION_VERSION,
        mapper_id=COUNTER_MAPPER_ID,
        source_space=COUNTER_SOURCE_SPACE,
        target_space="board-delta-v1",
        resolved=True,
        reason="ok",
        samples=n,
        slope=slope,
        r2=r2,
        confidence=confidence,
        x_energy=xx,
        fitted_at=utc_now(),
    )


def write_counter_calibration(profile_root: Path, calibration: CounterCalibration) -> Path:
    destination = Path(profile_root) / "counter_calibration.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(calibration.public(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def rebuild_counter_calibration(recorder: Any) -> CounterCalibration:
    calibration = fit_counter_calibration(recorder.iter_events())
    write_counter_calibration(recorder.profile_root, calibration)
    return calibration
