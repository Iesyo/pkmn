"""Stable teacher and Nana decision identities for adaptive layers.

``key`` remains the historical weights key while Nursery N2 is behavior-
preserving. Stricter behavior/policy/execution keys are exposed separately and
are marked unresolved rather than hashing sentinel values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from battle_lab import local_sparring_service as sparring
from battle_lab.mc_training import sha256_file
from battle_lab.nana_contracts import execution_key, nana_policy_key
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
    """Return the chronologically latest persisted teacher descriptor.

    Recorder session filenames contain random ids, so iterator/file order is not
    recency. Persisted ISO-8601 event timestamps are the source of truth; stream
    position is only a deterministic tie-breaker.
    """

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


def compact_teacher_descriptor(teacher: dict[str, Any] | None) -> dict[str, Any]:
    """Small reference safe to repeat in per-turn events."""

    teacher = teacher if isinstance(teacher, dict) else {}
    keys = (
        "schemaVersion",
        "key",
        "weightsKey",
        "behaviorKey",
        "behaviorKeyResolved",
        "nanaPolicyKey",
        "nanaPolicyKeyResolved",
        "executionKey",
        "executionKeyResolved",
        "legacyKey",
        "family",
        "format",
        "checkpointSha256",
        "adapterContractVersion",
    )
    return {key: teacher.get(key) for key in keys if key in teacher}


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
    behavior_resolved = bool(behavior_key)

    nana_contract = getattr(service, "_nana_policy_contract", None)
    if not isinstance(nana_contract, dict):
        nana_contract = {}
    policy_key = nana_policy_key(nana_contract)
    policy_resolved = bool(policy_key)

    combined_key = execution_key(
        teacher_behavior_key=behavior_key,
        nana_policy_key_value=policy_key,
    )
    execution_resolved = bool(combined_key)

    return {
        "schemaVersion": TEACHER_SCHEMA_VERSION,
        # Compatibility alias: live N2 trust still uses the historical weights key.
        "key": weights_key,
        "weightsKey": weights_key,
        "behaviorKey": behavior_key,
        "behaviorKeyResolved": behavior_resolved,
        "behaviorContract": behavior_contract,
        "nanaPolicyKey": policy_key,
        "nanaPolicyKeyResolved": policy_resolved,
        "nanaPolicyContract": nana_contract,
        "executionKey": combined_key,
        "executionKeyResolved": execution_resolved,
        "legacyKey": legacy_teacher_key(
            checkpoint=checkpoint_text,
            battle_format=battle_format,
        ),
        "family": TEACHER_FAMILY,
        "format": battle_format,
        "checkpoint": checkpoint_text,
        "checkpointSha256": checksum,
        "actionSpaceId": behavior_contract.get("actionSpaceId") or "",
        "featureSchemaId": behavior_contract.get("featureSchemaId") or "",
        "adapterContractVersion": behavior_contract["adapterContractVersion"],
        "selectionRule": behavior_contract["selectionRule"],
        "inferenceParams": behavior_contract["inferenceParams"],
        "capabilities": list(CAPABILITIES),
        "policy": "Battle Lab LIGHT M-C",
    }
