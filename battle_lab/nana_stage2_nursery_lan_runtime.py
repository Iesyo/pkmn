"""Nana 2.3 Nursery live runtime on a trusted private LAN.

This launcher also installs the live request guard required by the Nursery
synchronization design. The guard serializes model-side Showdown requests,
suppresses duplicate rqid deliveries, and classifies benign no-human-prompt
situations as Nursery skips instead of promotion-blocking errors.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from typing import Any, Sequence

from battle_lab import nana_stage2_shadow_v21_lan_runtime as lan
from battle_lab.nana_nursery import NURSERY_MODEL_VERSION
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args
from battle_lab.nana_stage2_nursery_runtime import install_nursery_service


FORCED_SWITCH_HUMAN_GRACE_SECONDS = 0.05
FORCED_SWITCH_POLL_SECONDS = 0.005


def _request_key(battle: Any) -> str:
    request = copy.deepcopy(getattr(battle, "last_request", {}) or {})
    rqid = request.get("rqid") if isinstance(request, dict) else None
    tag = str(getattr(battle, "battle_tag", "") or "battle")
    if isinstance(rqid, int):
        return f"{tag}|rqid={rqid}"
    try:
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        encoded = repr(request)
    digest = hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{tag}|request={digest}"


def _append_skip(service: Any, *, turn: int, reason: str, details: dict[str, Any] | None = None) -> None:
    session = getattr(service, "active_session", None)
    if session is None:
        return
    payload = {
        "modelVersion": NURSERY_MODEL_VERSION,
        "turn": int(turn),
        "teacher": copy.deepcopy(getattr(service, "_nana_teacher", {}) or {}),
        "reason": reason,
        "fallback": "LIGHT",
        "promotionBlocking": False,
    }
    if details:
        payload["details"] = copy.deepcopy(details)
    try:
        service.nana.append_event(session.id, "nana_nursery_skip", payload)
    except Exception:
        pass


async def _wait_for_possible_human_forced_switch(service: Any, session: Any) -> bool:
    """Give a simultaneous human forced-switch prompt a tiny scheduling grace."""

    claimed = int(
        getattr(service, "_nana_nursery_model_generation", {}).get(session.id, 0) or 0
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FORCED_SWITCH_HUMAN_GRACE_SECONDS
    while True:
        generation = int(getattr(session, "generation", 0) or 0)
        if generation > claimed and getattr(session, "phase", "") == "waiting-choice":
            return True
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(FORCED_SWITCH_POLL_SECONDS, remaining))


def install_nursery_lan_request_guard(service_class: type) -> type:
    """Serialize/dedupe model requests before Nursery's async pre-choice barrier.

    The pinned poke-env client dispatches websocket messages as concurrent tasks.
    Once Nursery choose_move became awaitable, two handlers for the same rqid
    could otherwise both reach the same human generation and send two orders.
    This guard makes one request key the single owner of the send path.
    """

    if getattr(service_class, "_nana_nursery_lan_request_guard", False):
        return service_class

    original_ensure_ready = service_class.ensure_ready

    async def ensure_ready(self: Any) -> None:
        await original_ensure_ready(self)
        if getattr(self, "_nana_nursery_lan_player_wrapped", False):
            return
        assert self.runtime is not None
        parent_player_class = self.runtime.player_class
        service = self

        class NanaNurseryLanGuardPlayer(parent_player_class):
            _nana_nursery_lan_guard_player = True

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._nana_request_guard_lock = None
                self._nana_request_guard_loop = None
                self._nana_completed_request_keys: set[str] = set()
                self._nana_retry_request = False
                super().__init__(*args, **kwargs)

            def _guard_lock(self) -> asyncio.Lock:
                loop = asyncio.get_running_loop()
                if (
                    self._nana_request_guard_lock is None
                    or self._nana_request_guard_loop is not loop
                ):
                    self._nana_request_guard_lock = asyncio.Lock()
                    self._nana_request_guard_loop = loop
                return self._nana_request_guard_lock

            async def _handle_battle_request(
                self,
                battle: Any,
                maybe_default_order: bool = False,
            ):
                key = _request_key(battle)
                async with self._guard_lock():
                    if not maybe_default_order and key in self._nana_completed_request_keys:
                        _append_skip(
                            service,
                            turn=int(getattr(battle, "turn", 0) or 0),
                            reason="duplicate-model-request",
                            details={"requestKey": key},
                        )
                        return None

                    self._nana_retry_request = bool(maybe_default_order)
                    try:
                        result = await super()._handle_battle_request(
                            battle,
                            maybe_default_order=maybe_default_order,
                        )
                    except Exception:
                        raise
                    else:
                        if not maybe_default_order:
                            self._nana_completed_request_keys.add(key)
                        return result
                    finally:
                        self._nana_retry_request = False

            async def choose_move(self, current: Any):
                session = service.active_session
                turn = int(getattr(current, "turn", 0) or 0)
                if session is None:
                    return await super().choose_move(current)

                # [Invalid choice] retries have no fresh human decision to predict.
                # Re-evaluate canonical LIGHT immediately and keep them outside the
                # Nursery promotion-error channel.
                if self._nana_retry_request:
                    _append_skip(
                        service,
                        turn=turn,
                        reason="invalid-choice-retry-no-human-prompt",
                    )
                    return self._raw_light_choose(current)

                claimed = int(
                    service._nana_nursery_model_generation.get(session.id, 0) or 0
                )
                generation = int(getattr(session, "generation", 0) or 0)
                phase = str(getattr(session, "phase", "") or "")

                # If a human prompt already advanced and was consumed before the
                # model callback acquired the serialized path, there is no honest
                # pre-choice observation left to use. Fail closed without a 1s wait.
                if generation > claimed and phase != "waiting-choice":
                    service._nana_nursery_model_generation[session.id] = generation
                    _append_skip(
                        service,
                        turn=turn,
                        reason="human-prompt-already-consumed",
                        details={"generation": generation, "phase": phase},
                    )
                    return self._raw_light_choose(current)

                force_switch = getattr(current, "force_switch", None)
                if force_switch and any(bool(value) for value in force_switch):
                    if not await _wait_for_possible_human_forced_switch(
                        service,
                        session,
                    ):
                        _append_skip(
                            service,
                            turn=turn,
                            reason="model-only-force-switch-no-human-prompt",
                            details={
                                "generation": int(getattr(session, "generation", 0) or 0),
                                "phase": str(getattr(session, "phase", "") or ""),
                            },
                        )
                        return self._raw_light_choose(current)

                return await super().choose_move(current)

        NanaNurseryLanGuardPlayer.__name__ = "NanaNurseryLanGuardPlayer"
        self.runtime.player_class = NanaNurseryLanGuardPlayer
        self._nana_nursery_lan_player_wrapped = True

    service_class.ensure_ready = ensure_ready
    service_class._nana_nursery_lan_request_guard = True
    return service_class


def main(argv: Sequence[str] | None = None) -> int:
    lan_args, remaining = lan.parse_lan_args(argv)
    nana_args, remaining = parse_nana_args(remaining)
    service_class = install_nursery_service(profile_id=nana_args.nana_profile)
    install_nursery_lan_request_guard(service_class)

    from battle_lab import local_runtime

    lan.install_direct_lan(local_runtime)
    install_reusable_viewer(local_runtime)

    addresses = lan._candidate_lan_addresses()
    if lan_args.lan_address and lan_args.lan_address not in addresses:
        addresses.insert(0, lan_args.lan_address)

    print("", flush=True)
    print("=== Battle Lab LAN · Nana 2.3 Nursery LIVE ===", flush=True)
    print(
        "Nana ya puede ejecutar hasta 1 intervención near-LIGHT por BO1; "
        "todo lo demás cae a LIGHT.",
        flush=True,
    )
    print(
        "La segunda PC solo abre la URL Network de Vite; no ejecutes nada allí.",
        flush=True,
    )
    if addresses:
        print("IPs privadas detectadas: " + ", ".join(addresses), flush=True)
    print("API :8765 · Showdown :8766 · renderer :8767", flush=True)
    print(
        "Guard activo: un solo envío por prompt; retries/forced-switch sin prompt "
        "humano se registran como skips benignos.",
        flush=True,
    )
    print(
        "Cada intervención real queda registrada para que Nana aprenda de su "
        "propia experiencia en partidas posteriores.",
        flush=True,
    )
    print("Fallback absoluto: LIGHT. Promoción de autonomía: NO automática.", flush=True)
    print("================================================", flush=True)
    print("", flush=True)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
