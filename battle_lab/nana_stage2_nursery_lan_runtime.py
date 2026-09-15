"""Nana 2.3 Nursery LIVE runtime on the supported trusted-LAN path.

Live Nursery is intentionally supported through this launcher while the generic
non-LAN entrypoint remains diagnostic-only. The guard serializes model-side
Showdown requests, suppresses duplicate rqid deliveries, and classifies benign
no-human-prompt situations as Nursery skips instead of promotion-blocking
errors.
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
from battle_lab.nana_stage2_nursery_runtime import (
    PRECHOICE_TIMEOUT_SECONDS,
    install_nursery_service,
)


FORCED_SWITCH_HUMAN_GRACE_SECONDS = 0.05


def _request_key(battle: Any) -> str:
    request = copy.deepcopy(getattr(battle, "last_request", {}) or {})
    rqid = request.get("rqid") if isinstance(request, dict) else None
    tag = str(getattr(battle, "battle_tag", "") or "battle")
    if isinstance(rqid, int):
        return f"{tag}|rqid={rqid}"
    try:
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception:
        encoded = repr(request)
    digest = hashlib.sha256(
        encoded.encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    return f"{tag}|request={digest}"


def _append_skip(
    service: Any,
    *,
    turn: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
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


async def _wait_for_human_prompt(
    service: Any,
    session: Any,
    *,
    turn: int,
    timeout: float,
) -> bool:
    """Wait for the exact fresh human prompt required by the base Nursery barrier.

    ``asyncio.sleep(0.005)`` is commonly rounded up to roughly one Windows timer
    tick. A cooperative ``sleep(0)`` yields to the peer websocket task directly,
    so the nominal grace window remains meaningful on the ROG as well as Linux.

    The guard deliberately mirrors every base-barrier readiness condition before
    delegating: fresh generation, waiting-choice phase, matching turn and legal
    actions present. This keeps the guard -> base-barrier handoff atomic and
    prevents a transient cross-websocket turn skew from becoming a blocking
    ``nana_nursery_error``.
    """

    claimed = int(
        getattr(service, "_nana_nursery_model_generation", {}).get(
            session.id,
            0,
        )
        or 0
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    while True:
        generation = int(getattr(session, "generation", 0) or 0)
        state_turn = int(
            (getattr(session, "battle_state", {}) or {}).get("turn", 0) or 0
        )
        if (
            generation > claimed
            and getattr(session, "phase", "") == "waiting-choice"
            and state_turn == int(turn)
            and bool(getattr(session, "legal_actions", None))
        ):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0)


async def _wait_for_possible_human_forced_switch(
    service: Any,
    session: Any,
    *,
    turn: int,
) -> bool:
    return await _wait_for_human_prompt(
        service,
        session,
        turn=turn,
        timeout=FORCED_SWITCH_HUMAN_GRACE_SECONDS,
    )


def install_nursery_lan_request_guard(service_class: type) -> type:
    """Serialize/dedupe model requests before Nursery's async pre-choice barrier.

    The pinned poke-env client dispatches websocket messages as concurrent tasks.
    Nursery's normal-turn choice path may yield while it waits for a human
    pre-choice prompt, so two handlers could otherwise race on one generation.
    Holding one per-player lock around the complete request handler restores
    single-owner semantics; rqid dedupe is a second guard against duplicate normal
    deliveries.

    VGC-Bench's pinned Team Preview is different: it calls ``choose_move``
    synchronously and explicitly rejects Awaitables. The LAN wrapper therefore
    keeps the public ``choose_move`` method synchronous for preview, returning raw
    LIGHT immediately, while normal turns return the coroutine produced by
    ``_choose_move_live``. poke-env already supports either a direct BattleOrder or
    an Awaitable on normal battle requests.
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
                async with self._guard_lock():
                    key = _request_key(battle)
                    if (
                        not maybe_default_order
                        and key in self._nana_completed_request_keys
                    ):
                        _append_skip(
                            service,
                            turn=int(getattr(battle, "turn", 0) or 0),
                            reason="duplicate-model-request",
                            details={"requestKey": key},
                        )
                        return None

                    self._nana_retry_request = bool(maybe_default_order)
                    was_waiting = bool(getattr(battle, "_wait", False))
                    try:
                        result = await super()._handle_battle_request(
                            battle,
                            maybe_default_order=maybe_default_order,
                        )
                    except Exception:
                        raise
                    else:
                        # Do not mark a request as answered when poke-env exited
                        # early on battle._wait without sending any order.
                        if not maybe_default_order and not was_waiting:
                            self._nana_completed_request_keys.add(key)
                        return result
                    finally:
                        self._nana_retry_request = False

            def choose_move(self, current: Any):
                # VGC-Bench Team Preview calls self.choose_move synchronously and
                # asserts the result is not Awaitable. Do not route preview through
                # Nursery's async pre-choice machinery; frozen LIGHT already owns
                # the canonical preview policy.
                if getattr(current, "teampreview", False):
                    return self._raw_light_choose(current)
                return self._choose_move_live(current)

            async def _choose_move_live(self, current: Any):
                session = service.active_session
                turn = int(getattr(current, "turn", 0) or 0)
                if session is None:
                    return await super().choose_move(current)

                # [Invalid choice] retries have no fresh human decision to predict.
                if self._nana_retry_request:
                    _append_skip(
                        service,
                        turn=turn,
                        reason="invalid-choice-retry-no-human-prompt",
                    )
                    return self._raw_light_choose(current)

                claimed = int(
                    service._nana_nursery_model_generation.get(session.id, 0)
                    or 0
                )
                generation = int(getattr(session, "generation", 0) or 0)
                phase = str(getattr(session, "phase", "") or "")

                # A newer prompt that is no longer waiting-choice has already been
                # consumed; it cannot honestly be used as a pre-choice sample.
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
                        turn=turn,
                    ):
                        _append_skip(
                            service,
                            turn=turn,
                            reason="model-only-force-switch-no-human-prompt",
                            details={
                                "generation": int(
                                    getattr(session, "generation", 0) or 0
                                ),
                                "phase": str(
                                    getattr(session, "phase", "") or ""
                                ),
                            },
                        )
                        return self._raw_light_choose(current)
                else:
                    # Close the residual timeout path here instead of letting the
                    # base barrier emit a promotion-blocking nursery_error when the
                    # human UI disappears, lags on another websocket turn, or never
                    # publishes a complete prompt.
                    state_turn = int(
                        (getattr(session, "battle_state", {}) or {}).get(
                            "turn", 0
                        )
                        or 0
                    )
                    prompt_ready = (
                        generation > claimed
                        and phase == "waiting-choice"
                        and state_turn == turn
                        and bool(getattr(session, "legal_actions", None))
                    )
                    if not prompt_ready and not await _wait_for_human_prompt(
                        service,
                        session,
                        turn=turn,
                        timeout=PRECHOICE_TIMEOUT_SECONDS,
                    ):
                        _append_skip(
                            service,
                            turn=turn,
                            reason="prechoice-sync-timeout",
                            details={
                                "generation": int(
                                    getattr(session, "generation", 0) or 0
                                ),
                                "phase": str(
                                    getattr(session, "phase", "") or ""
                                ),
                                "stateTurn": int(
                                    (getattr(session, "battle_state", {}) or {}).get(
                                        "turn", 0
                                    )
                                    or 0
                                ),
                                "hasLegalActions": bool(
                                    getattr(session, "legal_actions", None)
                                ),
                            },
                        )
                        return self._raw_light_choose(current)

                # A fresh human prompt is now known to exist. The base Nursery
                # barrier should resolve immediately and compute the actual
                # nursery-model-prechoice prediction before the human submit path.
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
        "Este launcher LAN es el único entrypoint autorizado para Nursery LIVE; "
        "el runtime base queda solo para diagnóstico hasta generalizar el guard.",
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
        "Guard activo: un solo envío por prompt; retries/forced-switch/timeouts "
        "sin prompt humano se registran como skips benignos.",
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
