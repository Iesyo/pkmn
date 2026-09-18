"""Score-space contract for future NanaScorer.

The current Nursery scorer remains untouched. M3 only defines the invariant
needed before N4: raw values from different semantic spaces must never be added
without an explicit mapper into one common score space.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


SCORER_CONTRACT_VERSION = 1
COMMON_SCORE_SPACE = "board-delta-v1"


@dataclass(frozen=True)
class ScoreTerm:
    name: str
    value: float
    confidence: float
    score_space: str
    weight: float = 1.0

    def normalized_confidence(self) -> float:
        return max(0.0, min(1.0, float(self.confidence)))


def scorer_contract() -> dict[str, Any]:
    return {
        "contractVersion": SCORER_CONTRACT_VERSION,
        "commonScoreSpace": COMMON_SCORE_SPACE,
        "terms": {
            "teacherPrior": {
                "sourceSpace": "teacher-log-regret-v1",
                "requiresMapper": True,
            },
            "experience": {
                "sourceSpace": COMMON_SCORE_SPACE,
                "requiresMapper": False,
            },
            "counter": {
                "sourceSpace": "response-utility-v1",
                "requiresMapper": True,
            },
            "coach": {
                "sourceSpace": "coach-advice-v1",
                "requiresMapper": True,
            },
            "heuristicFloor": {
                "sourceSpace": COMMON_SCORE_SPACE,
                "requiresMapper": False,
            },
        },
    }


def combine_common_terms(
    terms: Iterable[ScoreTerm],
    *,
    common_score_space: str = COMMON_SCORE_SPACE,
) -> dict[str, Any]:
    """Combine only already-mapped terms; reject raw cross-space arithmetic."""

    accepted: list[dict[str, Any]] = []
    numerator = 0.0
    denominator = 0.0
    for term in terms:
        if term.score_space != common_score_space:
            raise ValueError(
                f"{term.name} está en {term.score_space}; "
                f"se requiere mapper a {common_score_space}"
            )
        confidence = term.normalized_confidence()
        effective_weight = max(0.0, float(term.weight)) * confidence
        if effective_weight <= 0:
            continue
        numerator += float(term.value) * effective_weight
        denominator += effective_weight
        accepted.append(
            {
                "name": term.name,
                "value": float(term.value),
                "confidence": confidence,
                "weight": float(term.weight),
                "effectiveWeight": effective_weight,
            }
        )
    return {
        "scoreSpace": common_score_space,
        "score": numerator / denominator if denominator > 0 else 0.0,
        "effectiveWeight": denominator,
        "terms": accepted,
    }
