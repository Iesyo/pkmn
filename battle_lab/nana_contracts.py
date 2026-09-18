"""Canonical, model-agnostic identity contracts for Nana.

This module intentionally has no VGC-Bench, poke-env or Nursery imports. Live
runtimes inject their actual decision/autonomy contract so persistent identity
changes whenever material policy semantics change.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


FINGERPRINT_SPEC_VERSION = 1
ORDER_KEY_SPEC_VERSION = 1
NANA_POLICY_CONTRACT_VERSION = 5


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _safe_target(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canonical_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def canonical_action_half(half: Any) -> dict[str, Any]:
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
    digest = fingerprint_payload(canonical_order_payload(action))
    return f"order:v{ORDER_KEY_SPEC_VERSION}:{digest}"


def build_nana_policy_contract(
    *,
    decision_mode: str,
    scorer_contract: str,
    score_spaces: dict[str, str],
    governor_contract: dict[str, Any],
    memory_contract: dict[str, Any],
    legal_order_contract: str,
) -> dict[str, Any]:
    """Build identity from live runtime values, including the full governor."""

    if not isinstance(governor_contract, dict) or not governor_contract:
        raise ValueError("governor_contract es obligatorio")
    if not isinstance(memory_contract, dict) or not memory_contract:
        raise ValueError("memory_contract es obligatorio")
    return {
        "fingerprintSpecVersion": FINGERPRINT_SPEC_VERSION,
        "nanaPolicyContractVersion": NANA_POLICY_CONTRACT_VERSION,
        "orderKeySpecVersion": ORDER_KEY_SPEC_VERSION,
        "decisionMode": str(decision_mode),
        "scorerContract": str(scorer_contract),
        "scoreSpaces": dict(score_spaces),
        "governor": dict(governor_contract),
        "memory": dict(memory_contract),
        "legalOrderContract": str(legal_order_contract),
    }


def nana_policy_key(contract: dict[str, Any]) -> str:
    if not isinstance(contract, dict) or not contract:
        return ""
    return (
        f"nana-policy:v{NANA_POLICY_CONTRACT_VERSION}:"
        f"{fingerprint_payload(contract)}"
    )


def execution_key(*, teacher_behavior_key: str, nana_policy_key_value: str) -> str:
    if not teacher_behavior_key or not nana_policy_key_value:
        return ""
    payload = {
        "fingerprintSpecVersion": FINGERPRINT_SPEC_VERSION,
        "teacherBehaviorKey": str(teacher_behavior_key),
        "nanaPolicyKey": str(nana_policy_key_value),
    }
    return f"nana-execution:v1:{fingerprint_payload(payload)}"
