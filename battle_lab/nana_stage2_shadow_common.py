"""Shared, narrow Nana 2 response proxy.

This is deliberately not a full battle-value model. It only captures direct,
interpretable Protect/Feint interactions used by shadow diagnostics.
"""

from __future__ import annotations

from typing import Any

_PROTECT_LIKE = {
    "protect",
    "detect",
    "kingsshield",
    "spikyshield",
    "banefulbunker",
    "burningbulwark",
    "obstruct",
    "silktrap",
}
_PROTECT_BREAKERS = {"feint"}


def _token(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _is_protect_like(half: Any) -> bool:
    return isinstance(half, dict) and half.get("kind") == "move" and _token(half.get("value")) in _PROTECT_LIKE


def _is_protect_breaker(half: Any) -> bool:
    return isinstance(half, dict) and half.get("kind") == "move" and _token(half.get("value")) in _PROTECT_BREAKERS


def _opponent_target_slot(half: Any) -> int | None:
    if not isinstance(half, dict) or half.get("kind") != "move":
        return None
    try:
        target = int(half.get("target") or 0)
    except (TypeError, ValueError):
        return None
    # poke-env/Showdown doubles: positive positions are foes; negative are allies.
    if target not in {1, 2}:
        return None
    return target - 1


def response_utility(model_action: Any, human_action: Any) -> dict[str, Any]:
    if not isinstance(model_action, dict) or not isinstance(human_action, dict):
        return {"score": 0.0, "relevant": 0, "components": []}

    model_halves = [model_action.get("first"), model_action.get("second")]
    human_halves = [human_action.get("first"), human_action.get("second")]
    components: list[dict[str, Any]] = []
    total = 0.0
    relevant = 0

    for human_slot, human_half in enumerate(human_halves):
        if not _is_protect_like(human_half):
            continue
        for model_slot, model_half in enumerate(model_halves):
            if _opponent_target_slot(model_half) != human_slot:
                continue
            relevant += 1
            delta = 1.0 if _is_protect_breaker(model_half) else -1.0
            total += delta
            components.append({
                "rule": "predicted-human-protect",
                "humanSlot": human_slot,
                "modelSlot": model_slot,
                "delta": delta,
            })

    for human_slot, human_half in enumerate(human_halves):
        target_slot = _opponent_target_slot(human_half)
        if target_slot is None:
            continue
        model_half = model_halves[target_slot] if target_slot < len(model_halves) else None
        if not _is_protect_like(model_half):
            continue
        relevant += 1
        delta = -1.0 if _is_protect_breaker(human_half) else 1.0
        total += delta
        components.append({
            "rule": "predicted-direct-attack",
            "humanSlot": human_slot,
            "modelSlot": target_slot,
            "delta": delta,
        })

    if not relevant:
        return {"score": 0.0, "relevant": 0, "components": []}
    return {
        "score": max(-1.0, min(1.0, total / relevant)),
        "relevant": relevant,
        "components": components,
    }
