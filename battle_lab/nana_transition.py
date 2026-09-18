"""Auditable identity transition records for Nana.

Only fully resolved teacher + Nana policy identities may produce a transition
act. Unresolved fingerprints are surfaced as deferred metadata, never hashed as
if they were authoritative.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from battle_lab.nana_contracts import fingerprint_payload
from battle_lab.nana_recorder import utc_now


TRANSITION_SCHEMA_VERSION = 1


def _identity(teacher: dict[str, Any] | None) -> dict[str, Any]:
    teacher = teacher if isinstance(teacher, dict) else {}
    behavior_key = str(teacher.get("behaviorKey") or "")
    policy_key = str(teacher.get("nanaPolicyKey") or "")
    return {
        "weightsKey": str(teacher.get("weightsKey") or teacher.get("key") or ""),
        "behaviorKey": behavior_key,
        "behaviorKeyResolved": bool(
            teacher.get("behaviorKeyResolved", bool(behavior_key))
        ),
        "nanaPolicyKey": policy_key,
        "nanaPolicyKeyResolved": bool(
            teacher.get("nanaPolicyKeyResolved", bool(policy_key))
        ),
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
    has_previous = bool(any(before.get(key) for key in ("weightsKey", "behaviorKey", "nanaPolicyKey")))
    current_resolved = bool(
        after["behaviorKeyResolved"] and after["nanaPolicyKeyResolved"]
    )
    legacy_migration = bool(
        has_previous
        and not before["behaviorKeyResolved"]
        and current_resolved
    )
    weights_changed = bool(
        has_previous and before["weightsKey"] != after["weightsKey"]
    )
    behavior_changed = bool(
        has_previous
        and before["behaviorKeyResolved"]
        and after["behaviorKeyResolved"]
        and before["behaviorKey"] != after["behaviorKey"]
    )
    policy_changed = bool(
        has_previous
        and before["nanaPolicyKeyResolved"]
        and after["nanaPolicyKeyResolved"]
        and before["nanaPolicyKey"] != after["nanaPolicyKey"]
    )

    if not current_resolved:
        kind = "identity-unresolved"
        record = False
    elif not has_previous:
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
        "timestamp": timestamp or utc_now(),
        "kind": kind,
        "record": record,
        "resolved": current_resolved,
        "legacyMigration": legacy_migration,
        "from": before,
        "to": after,
        "fromDescriptor": copy.deepcopy(previous or {}),
        "toDescriptor": copy.deepcopy(current),
        "changes": {
            "weights": weights_changed,
            "teacherBehavior": behavior_changed,
            "nanaPolicy": policy_changed,
        },
        "requiresProbation": bool(
            current_resolved and (legacy_migration or behavior_changed)
        ),
        "requiresPolicyRevalidation": bool(
            current_resolved and policy_changed
        ),
        "liveBehaviorChangedByM0": False,
    }


def write_transition_act(profile_root: Path, transition: dict[str, Any]) -> Path:
    if transition.get("resolved") is not True:
        raise ValueError("No se puede persistir un acta con identidad no resuelta.")

    root = Path(profile_root) / "transitions"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = str(transition.get("timestamp") or utc_now())
    safe_stamp = timestamp.replace(":", "-")
    digest = fingerprint_payload(transition)[:12]
    destination = root / f"{safe_stamp}-{digest}.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(transition, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination
