"""Score-space contract for future NanaScorer.

The live Nursery path still uses the legacy N2 reranker. N3/N4 MUST migrate to
this mapped common-space API before activation: raw teacher regret and
response_utility values are not board-delta and may never be added directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


SCORER_CONTRACT_VERSION = 2
COMMON_SCORE_SPACE = "board-delta-v1"
LIVE_NURSERY_USES_COMMON_SCORER = False
_COMMON_TERM_PROOF = object()


@dataclass(frozen=True)
class _CommonScoreTerm:
    name: str
    value: float
    confidence: float
    weight: float
    source_space: str
    mapper_id: str
    _proof: object = field(repr=False, compare=False)

    def normalized_confidence(self) -> float:
        value = float(self.confidence)
        if not math.isfinite(value):
            return 0.0
        return max(0.0, min(1.0, value))


def scorer_contract() -> dict[str, Any]:
    return {
        "contractVersion": SCORER_CONTRACT_VERSION,
        "commonScoreSpace": COMMON_SCORE_SPACE,
        "liveNurseryUsesCommonScorer": LIVE_NURSERY_USES_COMMON_SCORER,
        "activationPrerequisite": (
            "N3/N4 cannot activate until choose_candidate is migrated to mapped "
            "common-score terms and no raw regret/response_utility arithmetic remains."
        ),
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


def _finite(value: float, *, field_name: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{field_name} debe ser finito")
    return rendered


def board_delta_term(
    *,
    name: str,
    value: float,
    confidence: float,
    weight: float = 1.0,
    source: str = "board-delta-native",
) -> _CommonScoreTerm:
    """Create a common-space term from a value already measured in board delta."""

    return _CommonScoreTerm(
        name=str(name),
        value=_finite(value, field_name="value"),
        confidence=_finite(confidence, field_name="confidence"),
        weight=max(0.0, _finite(weight, field_name="weight")),
        source_space=COMMON_SCORE_SPACE,
        mapper_id=str(source),
        _proof=_COMMON_TERM_PROOF,
    )


def map_to_board_delta(
    *,
    name: str,
    source_value: float,
    confidence: float,
    source_space: str,
    mapper_id: str,
    mapper: Callable[[float], float],
    weight: float = 1.0,
) -> _CommonScoreTerm:
    """Map a non-common semantic space into board delta through an explicit mapper."""

    source_space = str(source_space)
    if not source_space or source_space == COMMON_SCORE_SPACE:
        raise ValueError("usa board_delta_term para valores ya expresados en board delta")
    mapper_id = str(mapper_id or "").strip()
    if not mapper_id:
        raise ValueError("mapper_id es obligatorio")
    source = _finite(source_value, field_name="source_value")
    mapped = _finite(mapper(source), field_name="mapped_value")
    return _CommonScoreTerm(
        name=str(name),
        value=mapped,
        confidence=_finite(confidence, field_name="confidence"),
        weight=max(0.0, _finite(weight, field_name="weight")),
        source_space=source_space,
        mapper_id=mapper_id,
        _proof=_COMMON_TERM_PROOF,
    )


def combine_common_terms(terms: Iterable[_CommonScoreTerm]) -> dict[str, Any]:
    """Combine only terms created by this module's mapping constructors."""

    accepted: list[dict[str, Any]] = []
    numerator = 0.0
    denominator = 0.0
    for term in terms:
        if not isinstance(term, _CommonScoreTerm) or term._proof is not _COMMON_TERM_PROOF:
            raise ValueError("término no autorizado: usa board_delta_term/map_to_board_delta")
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
                "sourceSpace": term.source_space,
                "mapperId": term.mapper_id,
            }
        )

    insufficient = denominator <= 0.0
    return {
        "scoreSpace": COMMON_SCORE_SPACE,
        "score": None if insufficient else numerator / denominator,
        "effectiveWeight": denominator,
        "insufficientEvidence": insufficient,
        "terms": accepted,
    }


def assert_common_scorer_ready_for_level(level: str) -> None:
    if str(level).upper() in {"N3", "N4"} and not LIVE_NURSERY_USES_COMMON_SCORER:
        raise RuntimeError(
            "N3/N4 bloqueado: choose_candidate todavía usa el scorer legacy N2 "
            "y debe migrarse a términos mapeados antes de aumentar autonomía."
        )
