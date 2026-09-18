"""Canonical, model-agnostic identity contracts for Nana.

These helpers deliberately know nothing about VGC-Bench or poke-env. They are
used to make persistent keys survive model swaps without depending on native
model action indices or incidental JSON formatting.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


FINGERPRINT_SPEC_VERSION = 1
ORDER_KEY_SPEC_VERSION = 1
NANA_POLICY_CONTRACT_VERSION = 1


def canonical_json(value: Any) -> str:
    """Render deterministic UTF-8 JSON suitable for hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_payload(value: Any) -> str:
    """SHA-256 of the canonical JSON representation of ``value``."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _safe_target(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canonical_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def canonical_action_half(half: Any) -> dict[str, Any]:
    """Normalize one structured action half without model-native indices."""

    half = half if isinstance(half, dict) else {}
    flags = sorted(
        {
            _canonical_id(flag)
            for flag in half.get("flags") or []
            if _canonical_id(flag)
        }
    )
    return {
        "kind": _canonical_id(half.get("kind")),
        "value": _canonical_id(half.get("value")),
        "target": _safe_target(half.get("target")),
        "flags": flags,
    }


def canonical_order_payload(action: Any) -> dict[str, Any]:
    action = action if isinstance(action, dict) else {}
    return {
        "specVersion": ORDER_KEY_SPEC_VERSION,
        "first": canonical_action_half(action.get("first")),
        "second": canonical_action_half(action.get("second")),
    }


def order_key(action: Any) -> str:
    """Stable key for a structured doubles order across model catalogs."""

    digest = fingerprint_payload(canonical_order_payload(action))
    return f"order:v{ORDER_KEY_SPEC_VERSION}:{digest}"


def current_nana_policy_contract() -> dict[str, Any]:
    """Describe the current Nana decision contract without changing behavior.

    M0/M1 only makes this identity explicit. Nursery remains the active policy;
    future scorer/governor work must bump this contract when decision semantics
    change so old promotion evidence is never reused silently.
    """

    return {
        "fingerprintSpecVersion": FINGERPRINT_SPEC_VERSION,
        "nanaPolicyContractVersion": NANA_POLICY_CONTRACT_VERSION,
        "orderKeySpecVersion": ORDER_KEY_SPEC_VERSION,
        "decisionMode": "nana2.3-nursery-live-v1",
        "scorerContract": "light-regret-plus-response-utility-v1",
        "scoreSpaces": {
            "teacherPrior": "teacher-log-regret-v1",
            "counter": "response-utility-v1",
        },
        "governorContract": "lambda-cap-0.15-max-1-v1",
        "legalOrderContract": "vgc-bench-indexed-order-v1",
    }


def nana_policy_key(contract: dict[str, Any] | None = None) -> str:
    payload = contract or current_nana_policy_contract()
    return (
        f"nana-policy:v{NANA_POLICY_CONTRACT_VERSION}:"
        f"{fingerprint_payload(payload)}"
    )


def execution_key(*, teacher_behavior_key: str, nana_policy_key_value: str) -> str:
    payload = {
        "fingerprintSpecVersion": FINGERPRINT_SPEC_VERSION,
        "teacherBehaviorKey": str(teacher_behavior_key or "unknown"),
        "nanaPolicyKey": str(nana_policy_key_value or "unknown"),
    }
    return f"nana-execution:v1:{fingerprint_payload(payload)}"
