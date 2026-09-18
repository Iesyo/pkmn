"""Explicit autonomy envelopes for Nana.

M3 makes the training wheels a versioned contract instead of scattered magic
numbers. This module does not promote Nana and does not choose battle actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


AUTONOMY_CONTRACT_VERSION = 1


@dataclass(frozen=True)
class AutonomyEnvelope:
    level: str
    name: str
    lambda_cap: float
    max_interventions_per_battle: int | None
    allow_unrepresented_orders: bool
    automatic_promotion: bool = False

    def as_contract(self) -> dict[str, Any]:
        return {
            "contractVersion": AUTONOMY_CONTRACT_VERSION,
            **asdict(self),
        }


LEVELS: dict[str, AutonomyEnvelope] = {
    "N0": AutonomyEnvelope("N0", "observational", 0.0, 0, False),
    "N1": AutonomyEnvelope("N1", "shadow", 0.0, 0, False),
    "N2": AutonomyEnvelope("N2", "nursery", 0.15, 1, False),
    "N3": AutonomyEnvelope("N3", "apprentice", 0.25, 3, False),
    "N4": AutonomyEnvelope("N4", "full-amiibo", 1.0, None, True),
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
) -> dict[str, Any]:
    """Bind the actual live N2 values without changing them."""

    canonical = envelope("N2")
    return {
        "contractVersion": AUTONOMY_CONTRACT_VERSION,
        "level": "N2",
        "name": canonical.name,
        "lambdaCap": float(lambda_cap),
        "maxInterventionsPerBattle": int(max_interventions_per_battle),
        "allowUnrepresentedOrders": False,
        "automaticPromotion": False,
        "matchesCanonicalN2": (
            abs(float(lambda_cap) - canonical.lambda_cap) < 1e-12
            and int(max_interventions_per_battle)
            == canonical.max_interventions_per_battle
        ),
    }


def assert_live_nursery_matches_n2(
    *,
    lambda_cap: float,
    max_interventions_per_battle: int,
) -> None:
    contract = live_nursery_contract(
        lambda_cap=lambda_cap,
        max_interventions_per_battle=max_interventions_per_battle,
    )
    if contract["matchesCanonicalN2"] is not True:
        raise RuntimeError(
            "Nursery live ya no coincide con el contrato N2; "
            "cualquier cambio de autonomía requiere gate explícito."
        )
