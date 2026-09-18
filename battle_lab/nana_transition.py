"""Auditable identity transition records for Nana.

M0 records contract changes without changing Nursery's live trust key. Future
probation/promotion logic can consume the same acts once behaviorKey becomes the
active L3 identity.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from battle_lab.nana_contracts import fingerprint_payload


TRANSITION_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity(teacher: dict[str, Any] | None) -> dict[str, str]:
    teacher = teacher if isinstance(teacher, dict) else {}
    return {
        "weightsKey": str(teacher.get("weightsKey") or teacher.get("key") or ""),
        "behaviorKey": str(teacher.get("behaviorKey") or ""),
        "nanaPolicyKey": str(teacher.get("nanaPolicyKey") or ""),
        "executionKey": str(teacher.get("executionKey") or ""),
    }


def build_transition(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    before = _identity(previous)
    after = _identity(current)
    has_previous = bool(any(before.values()))
    legacy_migration = has_previous and not before["behaviorKey"]
    weights_changed = has_previous and before["weightsKey"] != after["weightsKey"]
    behavior_changed = has_previous and before["behaviorKey"] != after["behaviorKey"]
    policy_changed = has_previous and before["nanaPolicyKey"] != after["nanaPolicyKey"]

    if not has_previous:
        kind = "initial-identity"
        record = False
    elif legacy_migration:
        kind = "legacy-contract-migration"
        record = True
    elif behavior_changed and policy_changed:
        kind = "teacher-and-nana-policy-change"
        record = True
    elif behavior_changed:
        kind = "teacher-behavior-change"
        record = True
    elif policy_changed:
        kind = "nana-policy-change"
        record = True
    else:
        kind = "no-change"
        record = False

    return {
        "schemaVersion": TRANSITION_SCHEMA_VERSION,
        "timestamp": timestamp or _now(),
        "kind": kind,
        "record": record,
        "legacyMigration": legacy_migration,
        "from": before,
        "to": after,
        "changes": {
            "weights": weights_changed,
            "teacherBehavior": behavior_changed,
            "nanaPolicy": policy_changed,
        },
        # Metadata only in M0/M1; live Nursery behavior remains unchanged.
        "requiresProbation": bool(legacy_migration or behavior_changed),
        "requiresPolicyRevalidation": bool(policy_changed),
        "liveBehaviorChangedByM0": False,
    }


def write_transition_act(profile_root: Path, transition: dict[str, Any]) -> Path:
    """Persist an immutable transition act under the profile's transitions tree."""

    root = Path(profile_root) / "transitions"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = str(transition.get("timestamp") or _now())
    safe_stamp = timestamp.replace(":", "-").replace("+", "_")
    digest = fingerprint_payload(transition)[:12]
    destination = root / f"{safe_stamp}-{digest}.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(transition, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination
