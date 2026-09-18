"""Explicit autonomy envelopes for Nana.

The live N2 contract captures every value that materially changes whether
Nursery may intervene. Future levels remain non-activatable until their values
and scorer/legal-order prerequisites are explicitly promoted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


AUTONOMY_CONTRACT_VERSION = 2


@dataclass(frozen=True)
class AutonomyEnvelope:
    level: str
    name: str
    lambda_cap: float | None
    max_interventions_per_battle: int | None
    min_prediction_confidence: float | None
    min_allowed_light_regret_log: float | None
    high_light_trust_veto: float | None
    high_light_trust_confidence: float | None
    self_low_trust_veto: float | None
    self_low_trust_confidence: float | None
    promotion_intervention_window: int | None
    allow_unrepresented_orders: bool
    automatic_promotion: bool
    activation_ready: bool

    def as_contract(self) -> dict[str, Any]:
        return {
            "contractVersion": AUTONOMY_CONTRACT_VERSION,
            **asdict(self),
        }


LEVELS: dict[str, AutonomyEnvelope] = {
    "N0": AutonomyEnvelope(
        "N0", "observational",
        0.0, 0,
        None, None, None, None, None, None, None,
        False, False, True,
    ),
    "N1": AutonomyEnvelope(
        "N1", "shadow",
        0.0, 0,
        None, None, None, None, None, None, None,
        False, False, True,
    ),
    "N2": AutonomyEnvelope(
        "N2", "nursery",
        0.15, 1,
        0.08, -0.08,
        0.96, 0.35,
        0.35, 0.25,
        20,
        False, False, True,
    ),
    # N3/N4 are architectural targets, not active policy constants yet.
    "N3": AutonomyEnvelope(
        "N3", "apprentice",
        None, None,
        None, None, None, None, None, None, None,
        False, False, False,
    ),
    "N4": AutonomyEnvelope(
        "N4", "full-amiibo",
        None, None,
        None, None, None, None, None, None, None,
        True, False, False,
    ),
}


def envelope(level: str) -> AutonomyEnvelope:
    try:
        return LEVELS[str(level).upper()]
    except KeyError as error:
        raise ValueError(f"nivel de autonomía desconocido: {level}") from error


def live_nursery_contract(
    *,
    lambda_cap: float,
    max_interventions_per_battle: int,
    min_prediction_confidence: float,
    min_allowed_light_regret_log: float,
    high_light_trust_veto: float,
    high_light_trust_confidence: float,
    self_low_trust_veto: float,
    self_low_trust_confidence: float,
    promotion_intervention_window: int,
    allow_unrepresented_orders: bool,
    automatic_promotion: bool,
) -> dict[str, Any]:
    """Bind the complete live N2 intervention/promotion policy."""

    live = {
        "lambdaCap": float(lambda_cap),
        "maxInterventionsPerBattle": int(max_interventions_per_battle),
        "minPredictionConfidence": float(min_prediction_confidence),
        "minAllowedLightRegretLog": float(min_allowed_light_regret_log),
        "highLightTrustVeto": float(high_light_trust_veto),
        "highLightTrustConfidence": float(high_light_trust_confidence),
        "selfLowTrustVeto": float(self_low_trust_veto),
        "selfLowTrustConfidence": float(self_low_trust_confidence),
        "promotionInterventionWindow": int(promotion_intervention_window),
        "allowUnrepresentedOrders": bool(allow_unrepresented_orders),
        "automaticPromotion": bool(automatic_promotion),
    }
    canonical = envelope("N2")
    expected = {
        "lambdaCap": canonical.lambda_cap,
        "maxInterventionsPerBattle": canonical.max_interventions_per_battle,
        "minPredictionConfidence": canonical.min_prediction_confidence,
        "minAllowedLightRegretLog": canonical.min_allowed_light_regret_log,
        "highLightTrustVeto": canonical.high_light_trust_veto,
        "highLightTrustConfidence": canonical.high_light_trust_confidence,
        "selfLowTrustVeto": canonical.self_low_trust_veto,
        "selfLowTrustConfidence": canonical.self_low_trust_confidence,
        "promotionInterventionWindow": canonical.promotion_intervention_window,
        "allowUnrepresentedOrders": canonical.allow_unrepresented_orders,
        "automaticPromotion": canonical.automatic_promotion,
    }
    return {
        "contractVersion": AUTONOMY_CONTRACT_VERSION,
        "level": "N2",
        "name": canonical.name,
        **live,
        "matchesCanonicalN2": live == expected,
        "activationReady": canonical.activation_ready,
    }


def assert_live_nursery_matches_n2(**live_values: Any) -> None:
    contract = live_nursery_contract(**live_values)
    if contract["matchesCanonicalN2"] is not True:
        raise RuntimeError(
            "Nursery live ya no coincide con el contrato N2 completo; "
            "cualquier cambio de elegibilidad, veto, promoción o autonomía "
            "requiere gate explícito y nueva identidad de política."
        )


def assert_level_activation_ready(level: str) -> None:
    target = envelope(level)
    if not target.activation_ready:
        raise RuntimeError(
            f"{target.level} todavía es arquitectura, no una política aprobada para activar."
        )
