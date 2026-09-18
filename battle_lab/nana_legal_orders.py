"""Model-agnostic legal order enumeration and execution gate for Nana N4.

This module is the single Nana boundary that touches poke-env legal-order APIs.
It does not import VGC-Bench, action_map, logits, policy probabilities or any
teacher-specific catalog.

Source of truth:
- battle.valid_orders[pos] for each doubles slot.
- DoubleBattleOrder.join_orders for global compatibility.
- DoublesEnv.order_to_action -> action_to_order strict round-trip as an
  executable equivalence check against the pinned poke-env environment.

If any invariant diverges, enumeration fails closed. N4 must then fall back to
teacher-only behavior rather than risk an illegal order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from battle_lab.nana_contracts import order_key


LEGAL_ORDER_CONTRACT_VERSION = 1
LEGAL_ORDER_CONTRACT_ID = "poke-env-valid-orders-v1"


class LegalOrderSourceError(RuntimeError):
    """Raised when poke-env legal-order invariants cannot be proven."""


def _single_order_payload(order: Any) -> dict[str, Any]:
    subject = getattr(order, "order", None)
    cls_name = type(subject).__name__.lower()

    if cls_name == "move":
        kind = "move"
        value = str(getattr(subject, "id", "") or getattr(subject, "name", ""))
        label = str(getattr(subject, "name", "") or value).replace("-", " ").title()
    elif cls_name == "pokemon":
        kind = "switch"
        value = str(getattr(subject, "species", "") or getattr(subject, "name", ""))
        label = f"Cambiar a {value}"
    else:
        raw = str(subject or "")
        kind = "pass" if "pass" in raw.lower() else "other"
        value = raw
        label = "Pasar" if kind == "pass" else raw

    flags: list[str] = []
    if bool(getattr(order, "mega", False)):
        flags.append("Mega")
    if bool(getattr(order, "z_move", False)):
        flags.append("Z-Move")
    if bool(getattr(order, "dynamax", False)):
        flags.append("Dynamax")
    if bool(getattr(order, "terastallize", False)):
        flags.append("Tera")

    target = int(getattr(order, "move_target", 0) or 0)
    if target:
        label = f"{label} · objetivo {target}"
    if flags:
        label = f"{label} · {' + '.join(flags)}"

    return {
        "kind": kind,
        "value": value,
        "label": label,
        "target": target,
        "flags": flags,
    }


def structured_order(order: Any) -> dict[str, Any]:
    """Normalize a poke-env DoubleBattleOrder without teacher-native indices."""

    return {
        "first": _single_order_payload(getattr(order, "first_order", None)),
        "second": _single_order_payload(getattr(order, "second_order", None)),
    }


@dataclass(frozen=True)
class NormalizedCandidate:
    """One executable doubles order with stable model-agnostic identity."""

    key: str
    action: dict[str, Any]
    message: str
    action_indices: tuple[int, int] | None
    order: Any = field(repr=False, compare=False)
    representable: bool = True
    representation_error: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "orderKey": self.key,
            "action": self.action,
            "message": self.message,
            # Transport indices belong to poke-env, not to any teacher.
            "pokeEnvAction": (
                list(self.action_indices)
                if self.action_indices is not None
                else None
            ),
            "representable": self.representable,
            "representationError": self.representation_error or None,
        }


@dataclass(frozen=True)
class LegalOrderSet:
    resolved: bool
    reason: str
    battle_tag: str
    turn: int
    candidates: tuple[NormalizedCandidate, ...]
    individual_counts: tuple[int, int]
    joined_count: int
    deduplicated_count: int

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(candidate.key for candidate in self.candidates)

    def public(self) -> dict[str, Any]:
        return {
            "contractVersion": LEGAL_ORDER_CONTRACT_VERSION,
            "contractId": LEGAL_ORDER_CONTRACT_ID,
            "resolved": self.resolved,
            "reason": self.reason,
            "battleTag": self.battle_tag,
            "turn": self.turn,
            "individualCounts": list(self.individual_counts),
            "joinedCount": self.joined_count,
            "deduplicatedCount": self.deduplicated_count,
            "total": len(self.candidates),
            "representable": sum(candidate.representable for candidate in self.candidates),
            "unrepresentable": sum(not candidate.representable for candidate in self.candidates),
            "candidates": [candidate.public() for candidate in self.candidates],
        }


class LegalOrderSource:
    """Enumerate the complete current doubles order set from poke-env."""

    contract_version = LEGAL_ORDER_CONTRACT_VERSION
    contract_id = LEGAL_ORDER_CONTRACT_ID

    def enumerate(self, battle: Any) -> LegalOrderSet:
        from poke_env.environment import DoublesEnv
        from poke_env.player.battle_order import DoubleBattleOrder

        battle_tag = str(getattr(battle, "battle_tag", "") or "")
        turn = int(getattr(battle, "turn", 0) or 0)

        if bool(getattr(battle, "_wait", False)):
            return LegalOrderSet(
                resolved=False,
                reason="showdown-wait",
                battle_tag=battle_tag,
                turn=turn,
                candidates=(),
                individual_counts=(0, 0),
                joined_count=0,
                deduplicated_count=0,
            )
        if bool(getattr(battle, "teampreview", False)):
            return LegalOrderSet(
                resolved=False,
                reason="team-preview-not-in-n4-order-source",
                battle_tag=battle_tag,
                turn=turn,
                candidates=(),
                individual_counts=(0, 0),
                joined_count=0,
                deduplicated_count=0,
            )

        valid_orders = getattr(battle, "valid_orders", None)
        if not isinstance(valid_orders, (list, tuple)) or len(valid_orders) != 2:
            raise LegalOrderSourceError(
                "poke-env no expuso battle.valid_orders con dos posiciones."
            )

        first_orders = list(valid_orders[0] or [])
        second_orders = list(valid_orders[1] or [])
        joined = list(DoubleBattleOrder.join_orders(first_orders, second_orders))
        if not joined:
            return LegalOrderSet(
                resolved=False,
                reason="no-compatible-orders",
                battle_tag=battle_tag,
                turn=turn,
                candidates=(),
                individual_counts=(len(first_orders), len(second_orders)),
                joined_count=0,
                deduplicated_count=0,
            )

        by_key: dict[str, NormalizedCandidate] = {}
        messages_by_key: dict[str, str] = {}

        for order in joined:
            payload = structured_order(order)
            key = order_key(payload)
            message = str(order)

            action_indices: tuple[int, int] | None = None
            representable = True
            representation_error = ""
            try:
                action = DoublesEnv.order_to_action(
                    order,
                    battle,
                    fake=False,
                    strict=True,
                )
                action = np.asarray(action, dtype=np.int64)
                if action.shape != (2,):
                    raise ValueError(
                        f"poke-env devolvió action shape inesperada: {action.shape}."
                    )
                roundtrip = DoublesEnv.action_to_order(
                    action,
                    battle,
                    fake=False,
                    strict=True,
                )
                if str(roundtrip) != message:
                    # A successful transport mapping that reconstructs a different
                    # legal order is an integrity divergence, not mere lack of
                    # teacher representability. Fail closed.
                    raise LegalOrderSourceError(
                        "Round-trip poke-env divergió: "
                        f"original={message!r}, reconstruida={str(roundtrip)!r}."
                    )
                action_indices = (int(action[0]), int(action[1]))
            except LegalOrderSourceError:
                raise
            except Exception as error:
                # A legal order may be absent from poke-env's transport index view
                # (e.g. unusual revealed moves). It remains legal and rankable by
                # N4, but cannot receive teacher-index evidence.
                representable = False
                representation_error = f"{type(error).__name__}: {error}"

            previous_message = messages_by_key.get(key)
            if previous_message is not None and previous_message != message:
                raise LegalOrderSourceError(
                    "Colisión de orderKey entre órdenes distintas: "
                    f"{previous_message!r} vs {message!r}."
                )
            if key in by_key:
                continue

            messages_by_key[key] = message
            by_key[key] = NormalizedCandidate(
                key=key,
                action=payload,
                message=message,
                action_indices=action_indices,
                order=order,
                representable=representable,
                representation_error=representation_error,
            )

        candidates = tuple(
            sorted(
                by_key.values(),
                key=lambda candidate: (candidate.key, candidate.message),
            )
        )
        if not candidates:
            raise LegalOrderSourceError("La deduplicación dejó cero órdenes legales.")

        return LegalOrderSet(
            resolved=True,
            reason="ok",
            battle_tag=battle_tag,
            turn=turn,
            candidates=candidates,
            individual_counts=(len(first_orders), len(second_orders)),
            joined_count=len(joined),
            deduplicated_count=len(candidates),
        )


class SafetyGate:
    """Authorize execution only from the current LegalOrderSource snapshot."""

    def __init__(self, legal_orders: LegalOrderSet) -> None:
        if legal_orders.resolved is not True:
            raise LegalOrderSourceError(
                f"SafetyGate requiere legalidad resuelta, no {legal_orders.reason!r}."
            )
        self.legal_orders = legal_orders
        self._by_key = {candidate.key: candidate for candidate in legal_orders.candidates}

    def authorize_key(self, key: str) -> NormalizedCandidate:
        candidate = self._by_key.get(str(key or ""))
        if candidate is None:
            raise LegalOrderSourceError(
                "SafetyGate rechazó una orden que no pertenece al conjunto legal actual."
            )
        return candidate

    def authorize_order(self, order: Any) -> NormalizedCandidate:
        payload = structured_order(order)
        key = order_key(payload)
        candidate = self.authorize_key(key)
        if str(candidate.order) != str(order):
            raise LegalOrderSourceError(
                "SafetyGate detectó misma identidad estructurada con mensaje distinto."
            )
        return candidate


def legal_order_contract() -> dict[str, Any]:
    return {
        "contractVersion": LEGAL_ORDER_CONTRACT_VERSION,
        "contractId": LEGAL_ORDER_CONTRACT_ID,
        "source": "battle.valid_orders + DoubleBattleOrder.join_orders",
        "roundTrip": "DoublesEnv.order_to_action->action_to_order strict",
        "teacherIndependentEnumeration": True,
        "teamPreview": "defer-to-existing-preview-policy",
        "onDivergence": "fail-closed-teacher-only",
    }
