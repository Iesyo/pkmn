"""Contextual Bayesian-style human-action predictor for Nana 1.

Nana 1 is observational only. It estimates the human's next legal action from
append-only Nana history and current public battle context. It never mutates or
reranks LIGHT M-C.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

MIN_HISTORY_SAMPLES = 24
MIN_CONTEXT_SAMPLES = 6
PRIOR_ALPHA = 0.75
CONTEXT_ALPHA = 0.5
CONTEXT_WEIGHT = 0.7
PAIR_WEIGHT = 0.25
TOP_K = 3


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def half_signature(half: Any) -> str:
    if not isinstance(half, dict):
        return "unknown|||0|"
    kind = _text(half.get("kind")) or "unknown"
    value = _text(half.get("value"))
    target = int(half.get("target") or 0)
    flags = ",".join(
        sorted(_text(flag) for flag in (half.get("flags") or []) if _text(flag))
    )
    return f"{kind}|{value}|{target}|{flags}"


def action_signature(action: Any) -> str:
    if not isinstance(action, dict):
        return "unknown//unknown"
    return (
        f"{half_signature(action.get('first'))}//"
        f"{half_signature(action.get('second'))}"
    )


def _hp_bucket(value: Any) -> str:
    try:
        hp = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if hp <= 0:
        return "fainted"
    if hp <= 25:
        return "critical"
    if hp <= 50:
        return "low"
    if hp <= 75:
        return "mid"
    return "high"


def _species(mon: Any) -> str:
    if not isinstance(mon, dict):
        return "unknown"
    return _text(mon.get("species") or mon.get("name")) or "unknown"


def _fainted_count(team: Any) -> int:
    if not isinstance(team, list):
        return 0
    return sum(
        1 for mon in team if isinstance(mon, dict) and bool(mon.get("fainted"))
    )


def context_features(state: Any) -> tuple[str, ...]:
    if not isinstance(state, dict):
        return ("turn=unknown",)
    try:
        turn = int(state.get("turn") or 0)
    except (TypeError, ValueError):
        turn = 0
    if turn <= 1:
        turn_bucket = "1"
    elif turn <= 3:
        turn_bucket = "2-3"
    elif turn <= 6:
        turn_bucket = "4-6"
    else:
        turn_bucket = "7+"

    own_active = (
        state.get("ownActive") if isinstance(state.get("ownActive"), list) else []
    )
    opp_active = (
        state.get("opponentActive")
        if isinstance(state.get("opponentActive"), list)
        else []
    )
    own_team = state.get("ownTeam") if isinstance(state.get("ownTeam"), list) else []
    opp_team = (
        state.get("opponentTeam")
        if isinstance(state.get("opponentTeam"), list)
        else []
    )
    weather = (
        ",".join(
            sorted(_text(value) for value in (state.get("weather") or []) if _text(value))
        )
        or "none"
    )
    fields = (
        ",".join(
            sorted(_text(value) for value in (state.get("fields") or []) if _text(value))
        )
        or "none"
    )

    features = [
        f"turn={turn_bucket}",
        f"weather={weather}",
        f"fields={fields}",
        f"own_fainted={min(_fainted_count(own_team), 4)}",
        f"opp_fainted={min(_fainted_count(opp_team), 4)}",
    ]
    for index in range(2):
        own = own_active[index] if index < len(own_active) else {}
        opp = opp_active[index] if index < len(opp_active) else {}
        features.extend(
            [
                f"own{index}={_species(own)}",
                f"own{index}_hp={_hp_bucket(own.get('hp') if isinstance(own, dict) else None)}",
                f"opp{index}={_species(opp)}",
                f"opp{index}_hp={_hp_bucket(opp.get('hp') if isinstance(opp, dict) else None)}",
            ]
        )
    return tuple(features)


def _softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    maximum = max(scores)
    exps = [
        math.exp(max(-700.0, min(700.0, score - maximum))) for score in scores
    ]
    total = sum(exps)
    if total <= 0 or not math.isfinite(total):
        return [1.0 / len(scores)] * len(scores)
    return [value / total for value in exps]


def _normalized_entropy(probabilities: list[float]) -> float:
    if len(probabilities) <= 1:
        return 0.0
    entropy = -sum(
        probability * math.log(probability)
        for probability in probabilities
        if probability > 0
    )
    denominator = math.log(len(probabilities))
    return entropy / denominator if denominator > 0 else 0.0


class NanaPredictor:
    """Incremental contextual predictor over complete legal double actions."""

    def __init__(self) -> None:
        self.samples = 0
        self.slot_totals = [0, 0]
        self.slot_counts: list[Counter[str]] = [Counter(), Counter()]
        self.slot_feature_counts: list[dict[str, Counter[str]]] = [
            defaultdict(Counter),
            defaultdict(Counter),
        ]
        self.feature_totals: Counter[str] = Counter()
        self.pair_counts: Counter[tuple[str, str]] = Counter()

    @classmethod
    def from_events(cls, events: Iterable[dict[str, Any]]) -> "NanaPredictor":
        predictor = cls()
        by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            if isinstance(event, dict):
                by_session[str(event.get("sessionId") or "")].append(event)

        for session_events in by_session.values():
            observed = [
                event
                for event in session_events
                if event.get("type") == "human_choice_observed"
            ]
            source = observed or [
                event for event in session_events if event.get("type") == "turn_choice"
            ]
            for event in source:
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "human_choice_observed":
                    state = payload.get("state")
                    action = payload.get("action")
                else:
                    state = payload.get("state")
                    action = payload.get("humanAction")
                if isinstance(state, dict) and isinstance(action, dict):
                    predictor.observe(state, action)
        return predictor

    def observe(self, state: dict[str, Any], action: dict[str, Any]) -> None:
        features = context_features(state)
        halves = (action.get("first"), action.get("second"))
        tokens = tuple(half_signature(half) for half in halves)
        self.samples += 1
        for feature in features:
            self.feature_totals[feature] += 1
        for position, token in enumerate(tokens):
            self.slot_totals[position] += 1
            self.slot_counts[position][token] += 1
            for feature in features:
                self.slot_feature_counts[position][feature][token] += 1
        self.pair_counts[(tokens[0], tokens[1])] += 1

    def _slot_score(
        self,
        position: int,
        token: str,
        features: tuple[str, ...],
    ) -> float:
        counts = self.slot_counts[position]
        vocab = max(1, len(counts) + (0 if token in counts else 1))
        total = self.slot_totals[position]
        prior = (counts[token] + PRIOR_ALPHA) / (total + PRIOR_ALPHA * vocab)
        score = math.log(max(prior, 1e-12))
        for feature in features:
            feature_total = self.feature_totals[feature]
            if feature_total <= 0:
                continue
            conditioned = self.slot_feature_counts[position][feature][token]
            probability = (conditioned + CONTEXT_ALPHA) / (
                feature_total + CONTEXT_ALPHA * vocab
            )
            score += CONTEXT_WEIGHT * math.log(max(probability, 1e-12))
        return score

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
                "historySamples": self.samples,
                "contextSamples": 0,
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
        history_reliability = min(1.0, self.samples / MIN_HISTORY_SAMPLES)
        context_reliability = min(1.0, context_samples / MIN_CONTEXT_SAMPLES)
        reliability = history_reliability * (0.5 + 0.5 * context_reliability)

        # Cold-start calibration: a tiny personal history must not create a
        # falsely certain predictor. Blend toward uniform until the explicit
        # evidence gates are satisfied.
        uniform = 1.0 / len(candidates)
        probabilities = [
            reliability * probability + (1.0 - reliability) * uniform
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
            min(1.0, reliability * (0.65 * sharpness + 0.35 * margin)),
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
            "historySamples": self.samples,
            "contextSamples": context_samples,
            "ready": ready,
            "confidence": confidence,
            "confidenceBand": confidence_band,
            "entropy": entropy,
            "top": ranked[: min(TOP_K, len(ranked))],
            "candidates": ranked,
        }
