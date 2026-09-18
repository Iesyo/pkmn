"""Stable teacher identity for Nana's adaptive layers.

M0 introduces two identities without changing Nursery behavior yet:
``key`` remains the historical weights key so existing N2 evidence keeps its
current semantics; ``behaviorKey`` is the stricter teacher identity that future
calibration/autonomy gates must use once the transition migration is enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from battle_lab import local_sparring_service as sparring
from battle_lab.mc_training import sha256_file
from battle_lab.nana_contracts import (
    current_nana_policy_contract,
    execution_key,
    nana_policy_key,
)
from battle_lab.nana_teacher_adapter import (
    CAPABILITIES,
    TEACHER_FAMILY,
    teacher_behavior_contract,
    teacher_behavior_key,
)


TEACHER_SCHEMA_VERSION = 2


def legacy_teacher_key(*, checkpoint: str, battle_format: str) -> str:
    return f"legacy|format={battle_format}|checkpoint={checkpoint}"


def weights_teacher_key(*, checkpoint_sha256: str, battle_format: str) -> str:
    return (
        f"format={battle_format}|"
        f"checkpointSha256={checkpoint_sha256 or 'unknown'}"
    )


def latest_teacher_from_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return the chronologically latest persisted teacher descriptor."""

    latest: dict[str, Any] = {}
    latest_marker: tuple[str, int] = ("", -1)
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("type") != "nana_teacher_version":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
        if not (teacher.get("key") or teacher.get("weightsKey") or teacher.get("behaviorKey")):
            continue
        marker = (str(event.get("timestamp") or ""), index)
        if marker >= latest_marker:
            latest_marker = marker
            latest = teacher
    return latest


def descriptor_for_service(service: Any) -> dict[str, Any]:
    checkpoint = Path(service.checkpoint).expanduser().resolve()
    battle_format = str(
        (getattr(service, "runtime_metadata", {}) or {}).get("format")
        or sparring.DEFAULT_FORMAT
    )
    metadata = getattr(service, "runtime_metadata", {}) or {}
    checksum = str(metadata.get("checkpointSha256") or "")
    if not checksum and checkpoint.is_file():
        checksum = sha256_file(checkpoint)
    checkpoint_text = str(checkpoint)

    weights_key = weights_teacher_key(
        checkpoint_sha256=checksum,
        battle_format=battle_format,
    )
    behavior_contract = teacher_behavior_contract(
        service=service,
        battle_format=battle_format,
        checkpoint_sha256=checksum,
    )
    behavior_key = teacher_behavior_key(behavior_contract)
    nana_contract = current_nana_policy_contract()
    policy_key = nana_policy_key(nana_contract)

    return {
        "schemaVersion": TEACHER_SCHEMA_VERSION,
        # Compatibility alias: M0/M1 does not change the live N2 trust key yet.
        "key": weights_key,
        "weightsKey": weights_key,
        "behaviorKey": behavior_key,
        "behaviorContract": behavior_contract,
        "nanaPolicyKey": policy_key,
        "nanaPolicyContract": nana_contract,
        "executionKey": execution_key(
            teacher_behavior_key=behavior_key,
            nana_policy_key_value=policy_key,
        ),
        "legacyKey": legacy_teacher_key(
            checkpoint=checkpoint_text,
            battle_format=battle_format,
        ),
        "family": TEACHER_FAMILY,
        "format": battle_format,
        "checkpoint": checkpoint_text,
        "checkpointSha256": checksum,
        "actionSpaceId": behavior_contract["actionSpaceId"],
        "featureSchemaId": behavior_contract["featureSchemaId"],
        "adapterContractVersion": behavior_contract["adapterContractVersion"],
        "selectionRule": behavior_contract["selectionRule"],
        "inferenceParams": behavior_contract["inferenceParams"],
        "capabilities": list(CAPABILITIES),
        "policy": "Battle Lab LIGHT M-C",
    }
