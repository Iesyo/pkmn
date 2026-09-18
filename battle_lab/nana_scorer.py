"""Score-space contract for future NanaScorer.

The live Nursery path still uses the legacy N2 reranker. N3/N4 MUST migrate to
this mapped common-space API before activation: raw teacher regret and
response_utility values are not board-delta and may never be added directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


SCORER_CONTRACT_VERSION = 4
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
        "candidateRanking": "evidence-shrunk-common-space-v2",
        "evidenceShrink": {
            "rule": "score = rawScore * min(1, effectiveWeight)",
            "neutralReference": 0.0,
        },
        "referenceFallback": {
            "teacherMayBreakCommonScoreTies": True,
            "teacherCannotFilterCandidates": True,
            "minimumCommonScoreImprovement": True,
        },
        "blindPick": {
            "requiresEvidencedReference": True,
            "marginGate": True,
        },
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


@dataclass(frozen=True)
class CandidateEvidence:
    """Common-space evidence for one model-agnostic legal order."""

    key: str
    terms: tuple[_CommonScoreTerm, ...]
    teacher_represented: bool
    context_evidence: bool


@dataclass(frozen=True)
class CandidateScore:
    key: str
    score: float | None
    raw_score: float | None
    effective_weight: float
    blind: bool
    terms: tuple[dict[str, Any], ...]

    def public(self) -> dict[str, Any]:
        return {
            "orderKey": self.key,
            "scoreSpace": COMMON_SCORE_SPACE,
            "score": self.score,
            "rawScore": self.raw_score,
            "effectiveWeight": self.effective_weight,
            "evidenceStrength": max(0.0, min(1.0, self.effective_weight)),
            "blind": self.blind,
            "terms": [dict(term) for term in self.terms],
        }


class NanaScorer:
    """Deterministic scorer over model-agnostic legal order keys."""

    def __init__(
        self,
        *,
        blind_uncertainty_penalty: float = 0.15,
        blind_margin: float = 0.25,
        min_improvement_margin: float = 0.05,
    ) -> None:
        self.blind_uncertainty_penalty = max(
            0.0,
            _finite(
                blind_uncertainty_penalty,
                field_name="blind_uncertainty_penalty",
            ),
        )
        self.blind_margin = max(
            0.0,
            _finite(blind_margin, field_name="blind_margin"),
        )
        self.min_improvement_margin = max(
            0.0,
            _finite(
                min_improvement_margin,
                field_name="min_improvement_margin",
            ),
        )

    def score(self, evidence: CandidateEvidence) -> CandidateScore:
        combined = combine_common_terms(evidence.terms)
        raw = combined["score"]
        effective_weight = float(combined["effectiveWeight"])
        blind = not evidence.teacher_represented and not evidence.context_evidence

        # Evidence-adjusted score: a common-space estimate with tiny support must
        # remain close to the neutral reference instead of ranking as if its raw
        # point estimate had full confidence. Weight >= 1.0 reaches full strength.
        evidence_strength = max(0.0, min(1.0, effective_weight))
        scored = (
            None
            if raw is None
            else float(raw) * evidence_strength
            - (self.blind_uncertainty_penalty if blind else 0.0)
        )
        return CandidateScore(
            key=str(evidence.key),
            score=scored,
            raw_score=None if raw is None else float(raw),
            effective_weight=effective_weight,
            blind=blind,
            terms=tuple(combined["terms"]),
        )

    def rank(self, candidates: Iterable[CandidateEvidence]) -> list[CandidateScore]:
        scored = [self.score(candidate) for candidate in candidates]
        scored.sort(
            key=lambda item: (
                item.score is not None,
                float("-inf") if item.score is None else item.score,
                item.effective_weight,
                item.key,
            ),
            reverse=True,
        )
        return scored

    def select(
        self,
        candidates: Iterable[CandidateEvidence],
        *,
        reference_key: str | None = None,
    ) -> dict[str, Any]:
        ranked = self.rank(candidates)
        viable = [item for item in ranked if item.score is not None]
        if not viable:
            return {
                "selected": None,
                "reason": "insufficient-evidence",
                "ranked": [item.public() for item in ranked],
            }

        best = viable[0]
        reference = next(
            (
                item
                for item in viable
                if reference_key is not None and item.key == reference_key
            ),
            None,
        )

        if reference is not None and best.key != reference.key:
            margin = float(best.score) - float(reference.score)
            required = max(
                self.min_improvement_margin,
                self.blind_margin if best.blind else 0.0,
            )
            if margin < required:
                return {
                    "selected": reference.public(),
                    "reason": (
                        "blind-margin-not-met"
                        if best.blind
                        else "common-margin-not-met"
                    ),
                    "challenger": best.public(),
                    "margin": margin,
                    "requiredMargin": required,
                    "ranked": [item.public() for item in ranked],
                }

        if best.blind:
            if reference is None:
                reference = next((item for item in viable if not item.blind), None)
            if reference is None:
                return {
                    "selected": None,
                    "reason": "blind-without-evidenced-reference",
                    "ranked": [item.public() for item in ranked],
                }
            margin = float(best.score) - float(reference.score)
            if margin < self.blind_margin:
                return {
                    "selected": reference.public(),
                    "reason": "blind-margin-not-met",
                    "challenger": best.public(),
                    "margin": margin,
                    "requiredMargin": self.blind_margin,
                    "ranked": [item.public() for item in ranked],
                }
            return {
                "selected": best.public(),
                "reason": "blind-margin-met",
                "reference": reference.public(),
                "margin": margin,
                "requiredMargin": self.blind_margin,
                "ranked": [item.public() for item in ranked],
            }

        return {
            "selected": best.public(),
            "reason": "best-common-score",
            "reference": reference.public() if reference is not None else None,
            "ranked": [item.public() for item in ranked],
        }


def assert_common_scorer_ready_for_level(level: str) -> None:
    if str(level).upper() in {"N3", "N4"} and not LIVE_NURSERY_USES_COMMON_SCORER:
        raise RuntimeError(
            "N3/N4 bloqueado: choose_candidate todavía usa el scorer legacy N2 "
            "y debe migrarse a términos mapeados antes de aumentar autonomía."
        )
