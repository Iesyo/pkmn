"""Shared, conservative Nana 2 response proxy.

This is deliberately not a full battle-value model. It captures only direct,
interpretable Protect/Feint interactions plus a partial treatment of damaging
spread moves. The proxy is diagnostic evidence for shadow mode, never a claim
about counterfactual battle win rate.
"""

from __future__ import annotations

from functools import lru_cache
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
_SPREAD_TARGET_TYPES = {"ALL_ADJACENT", "ALL_ADJACENT_FOES"}
_SPREAD_PROTECT_WEIGHT = 0.5


def _token(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


@lru_cache(maxsize=512)
def _move_metadata(move_id: str) -> tuple[str, float]:
    """Return conservative static target/base-power metadata for a move id.

    Runtime action payloads historically did not persist poke-env's Target enum,
    so shadow v2.1 lazily resolves it from the same poke-env move catalog. If the
    installed poke-env cannot resolve a move, the proxy simply keeps treating it
    as unknown instead of guessing.
    """

    normalized = _token(move_id)
    if not normalized:
        return "", 0.0
    try:
        try:
            from poke_env.environment import Move  # older poke-env re-export
        except ImportError:
            from poke_env.battle import Move

        try:
            move = Move(normalized, gen=9)
        except TypeError:
            move = Move(normalized, 9)
        target = getattr(getattr(move, "target", None), "name", "")
        try:
            base_power = float(getattr(move, "base_power", 0) or 0)
        except (TypeError, ValueError):
            base_power = 0.0
        return str(target or ""), max(0.0, base_power)
    except Exception:
        return "", 0.0


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


def _target_type(half: Any) -> str:
    if not isinstance(half, dict) or half.get("kind") != "move":
        return ""
    explicit = str(half.get("targetType") or "").strip().upper()
    if explicit:
        return explicit
    target, _base_power = _move_metadata(str(half.get("value") or ""))
    return target.upper()


def _base_power(half: Any) -> float:
    if not isinstance(half, dict) or half.get("kind") != "move":
        return 0.0
    explicit = half.get("basePower")
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except (TypeError, ValueError):
            pass
    _target, base_power = _move_metadata(str(half.get("value") or ""))
    return base_power


def _is_damaging_spread(half: Any) -> bool:
    return (
        isinstance(half, dict)
        and half.get("kind") == "move"
        and _base_power(half) > 0
        and _target_type(half) in _SPREAD_TARGET_TYPES
    )


def response_utility(model_action: Any, human_action: Any) -> dict[str, Any]:
    if not isinstance(model_action, dict) or not isinstance(human_action, dict):
        return {"score": 0.0, "relevant": 0, "components": []}

    model_halves = [model_action.get("first"), model_action.get("second")]
    human_halves = [human_action.get("first"), human_action.get("second")]
    components: list[dict[str, Any]] = []
    total = 0.0
    relevant = 0

    # Predicted human Protect: a direct attack into that slot is bad unless it
    # is Feint. A damaging spread move is only partially penalized because the
    # partner can still be hit, so it contributes -0.5 rather than -1.0.
    for human_slot, human_half in enumerate(human_halves):
        if not _is_protect_like(human_half):
            continue
        for model_slot, model_half in enumerate(model_halves):
            if _opponent_target_slot(model_half) == human_slot:
                relevant += 1
                delta = 1.0 if _is_protect_breaker(model_half) else -1.0
                total += delta
                components.append({
                    "rule": "predicted-human-protect",
                    "humanSlot": human_slot,
                    "modelSlot": model_slot,
                    "delta": delta,
                })
                continue
            if _is_damaging_spread(model_half):
                relevant += 1
                delta = -_SPREAD_PROTECT_WEIGHT
                total += delta
                components.append({
                    "rule": "predicted-human-protect-vs-spread",
                    "humanSlot": human_slot,
                    "modelSlot": model_slot,
                    "delta": delta,
                    "targetType": _target_type(model_half),
                })

    # Predicted direct human attack: Protect on exactly that model slot is a
    # clear defensive response. Feint is the explicit breaker modeled here.
    for human_slot, human_half in enumerate(human_halves):
        target_slot = _opponent_target_slot(human_half)
        if target_slot is not None:
            model_half = model_halves[target_slot] if target_slot < len(model_halves) else None
            if _is_protect_like(model_half):
                relevant += 1
                delta = -1.0 if _is_protect_breaker(human_half) else 1.0
                total += delta
                components.append({
                    "rule": "predicted-direct-attack",
                    "humanSlot": human_slot,
                    "modelSlot": target_slot,
                    "delta": delta,
                })

        # Predicted damaging spread: Protect on either model slot blocks that
        # slot's share of the spread while the partner remains exposed. Reward
        # it only partially to avoid pretending that the whole spread vanished.
        if _is_damaging_spread(human_half):
            for model_slot, model_half in enumerate(model_halves):
                if not _is_protect_like(model_half):
                    continue
                relevant += 1
                delta = _SPREAD_PROTECT_WEIGHT
                total += delta
                components.append({
                    "rule": "predicted-human-spread-vs-protect",
                    "humanSlot": human_slot,
                    "modelSlot": model_slot,
                    "delta": delta,
                    "targetType": _target_type(human_half),
                })

    if not relevant:
        return {"score": 0.0, "relevant": 0, "components": []}
    return {
        "score": max(-1.0, min(1.0, total / relevant)),
        "relevant": relevant,
        "components": components,
    }
