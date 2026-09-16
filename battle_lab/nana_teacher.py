"""Stable teacher identity for Nana's adaptive layers.

The user-facing concept is simple: Nana may keep learning the player across
LIGHT upgrades, but evidence about how trustworthy one LIGHT checkpoint was
must not silently leak into a different checkpoint/regulation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from battle_lab import local_sparring_service as sparring
from battle_lab.mc_training import sha256_file


TEACHER_SCHEMA_VERSION = 1


def legacy_teacher_key(*, checkpoint: str, battle_format: str) -> str:
    return f"legacy|format={battle_format}|checkpoint={checkpoint}"


def latest_teacher_from_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return the chronologically latest persisted teacher descriptor.

    NanaRecorder.iter_events() walks session files by filename, and session ids are
    random. Never infer recency from iterator order. Recorder timestamps are
    ISO-8601 UTC strings, so lexical ordering is chronological for our persisted
    format. The original stream position is only a deterministic tie-breaker.
    """

    latest: dict[str, Any] = {}
    latest_marker: tuple[str, int] = ("", -1)
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("type") != "nana_teacher_version":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        teacher = payload.get("teacher") if isinstance(payload.get("teacher"), dict) else {}
        if not teacher.get("key"):
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
    key = f"format={battle_format}|checkpointSha256={checksum or 'unknown'}"
    return {
        "schemaVersion": TEACHER_SCHEMA_VERSION,
        "key": key,
        "legacyKey": legacy_teacher_key(
            checkpoint=checkpoint_text,
            battle_format=battle_format,
        ),
        "format": battle_format,
        "checkpoint": checkpoint_text,
        "checkpointSha256": checksum,
        "policy": metadata.get("modelLabel", "Battle Lab LIGHT M-C"),
    }
