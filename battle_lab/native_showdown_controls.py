"""Native Pokémon Showdown controls bridge for Battle Lab Sparring.

The official classic client remains the UI owner for team preview, moves,
targets, switches and mechanics. The actual human websocket participant remains
the loopback poke-env player so Nana can keep its existing observation hooks.
We expose poke-env's raw Showdown request to the browser and translate the
client's canonical numeric `/choose` command back to the exact valid order for
that request.

Modern Showdown team-preview requests include ``maxChosenTeamSize`` (for VGC,
usually 4). The pinned classic client predates that request field, so the viewer
bridge seeds its native ``battle.teamPreviewCount`` from the request before
rendering controls. The classic client still serializes the full reordered team
(`/team 123456`); Battle Lab validates that full permutation and consumes only
the first ``maxChosenTeamSize`` entries. Missing picks are never invented.

When the active service is Nana-enabled, the model has a stable Showdown
identity: username ``Nana`` and avatar ``3``. The avatar is explicitly re-applied
and awaited after login but before battle creation, avoiding the poke-env login
race where ``logged_in`` can unblock a challenge just before `/avatar 3` is sent.
The short-lived poke-env clients are explicitly disconnected at the end of every
session so that the fixed name can be reused safely in the next BO1.

This module is installed on top of whichever BattleLabLocalService is current
(base Sparring, Nana 0/1, or Nana 2 shadow), so existing submit_preview and
submit_choice overrides remain the single persistence path.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import time
from contextlib import suppress
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from battle_lab import local_sparring_service as sparring


NANA_DISPLAY_NAME = "Nana"
NANA_AVATAR = "3"


class NativeShowdownChoice(BaseModel):
    command: str = Field(min_length=1, max_length=512)


async def _pin_nana_avatar(player: Any, avatar: str = NANA_AVATAR) -> None:
    """Serialize Nana's avatar change before any battle room can be created.

    The pinned poke-env fork already requests the configured avatar during login,
    but its ``logged_in`` event is set by the concurrent ``updateuser`` handler
    before ``log_in`` finishes awaiting ``change_avatar``. A caller waiting only
    on login can therefore start a challenge first. Awaiting a second idempotent
    change here establishes an explicit websocket ordering barrier: `/avatar 3`
    is sent before the later challenge that creates the battle room.
    """

    client = getattr(player, "ps_client", None)
    change_avatar = getattr(client, "change_avatar", None)
    if not callable(change_avatar):
        raise RuntimeError("poke-env no expone change_avatar para fijar a Nana.")
    await change_avatar(avatar)


def _room_tag_for_battle(player: Any, current_battle: Any) -> str:
    """Return the authoritative Showdown room id for a tracked battle.

    War Room cannot mount the classic renderer until it knows the room hash.
    Normally poke-env exposes it as ``battle_tag``, but the player's ``battles``
    mapping is the canonical routing table and gives us a safe fallback if a
    transient/custom battle object does not surface that public property yet.
    """

    direct = str(
        getattr(current_battle, "battle_tag", "")
        or getattr(current_battle, "_battle_tag", "")
        or ""
    ).strip()
    if direct:
        return direct

    battles = getattr(player, "battles", None)
    if isinstance(battles, dict):
        for room_tag, tracked in battles.items():
            if tracked is current_battle and room_tag:
                return str(room_tag).strip()
    return ""


def _snapshot_with_room_tag(session: Any, player: Any, current_battle: Any) -> dict[str, Any]:
    """Keep the room id stable from room creation through every UI snapshot."""

    snapshot = sparring._battle_snapshot(current_battle)
    room_tag = _room_tag_for_battle(player, current_battle)
    if not room_tag:
        room_tag = str(getattr(session, "room_tag", "") or snapshot.get("tag") or "").strip()
    if room_tag:
        session.room_tag = room_tag
        snapshot["tag"] = room_tag
    return snapshot


def _to_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _normalize_native_command(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\|\d+\s*$", "", text)
    if text.startswith("/"):
        text = text[1:]
    text = re.sub(r"\s*,\s*", ",", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _request_move_slot(request: dict[str, Any], position: int, move_id: str) -> int | None:
    active = request.get("active")
    if not isinstance(active, list) or position >= len(active) or not isinstance(active[position], dict):
        return None
    moves = active[position].get("moves")
    if not isinstance(moves, list):
        return None
    wanted = _to_id(move_id)
    for index, move in enumerate(moves, start=1):
        if not isinstance(move, dict):
            continue
        candidate = _to_id(move.get("id") or move.get("move"))
        if candidate == wanted:
            return index
    return None


def _request_switch_slot(request: dict[str, Any], subject: Any) -> int | None:
    side = request.get("side")
    pokemon = side.get("pokemon") if isinstance(side, dict) else None
    if not isinstance(pokemon, list):
        return None
    wanted = {
        _to_id(getattr(subject, "name", "")),
        _to_id(getattr(subject, "species", "")),
    }
    wanted.discard("")
    for index, entry in enumerate(pokemon, start=1):
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("ident") or "")
        ident_name = ident.split(":", 1)[-1].strip()
        details_species = str(entry.get("details") or "").split(",", 1)[0].strip()
        aliases = {_to_id(ident_name), _to_id(details_species)}
        if wanted & aliases:
            return index
    return None


def _native_single_choice(order: Any, *, position: int, request: dict[str, Any]) -> str | None:
    subject = getattr(order, "order", None)
    cls_name = type(subject).__name__.lower()
    if cls_name == "move":
        slot = _request_move_slot(request, position, getattr(subject, "id", ""))
        if slot is None:
            return None
        choice = f"move {slot}"
        if bool(getattr(order, "mega", False)):
            choice += " mega"
        elif bool(getattr(order, "z_move", False)):
            choice += " zmove"
        elif bool(getattr(order, "dynamax", False)):
            choice += " dynamax"
        elif bool(getattr(order, "terastallize", False)):
            choice += " terastallize"
        target = int(getattr(order, "move_target", 0) or 0)
        if target:
            choice += f" {target}"
        return choice
    if cls_name == "pokemon":
        slot = _request_switch_slot(request, subject)
        return f"switch {slot}" if slot is not None else None
    raw = str(subject or "").lower()
    if "pass" in raw:
        return "pass"
    return None


def _native_double_choice(order: Any, request: dict[str, Any]) -> str | None:
    first = _native_single_choice(order.first_order, position=0, request=request)
    second = _native_single_choice(order.second_order, position=1, request=request)
    if first is None or second is None:
        return None
    return _normalize_native_command(f"choose {first},{second}")


def _team_preview_shape(request: dict[str, Any] | None) -> tuple[int, int]:
    request = request if isinstance(request, dict) else {}
    side = request.get("side")
    pokemon = side.get("pokemon") if isinstance(side, dict) else None
    team_size = len(pokemon) if isinstance(pokemon, list) else 6
    if team_size <= 0:
        team_size = 6
    try:
        chosen = int(request.get("maxChosenTeamSize") or 0)
    except (TypeError, ValueError):
        chosen = 0
    if chosen <= 0 or chosen > team_size:
        chosen = team_size
    return chosen, team_size


def _team_preview_order(
    command: str,
    *,
    request: dict[str, Any] | None = None,
) -> list[int] | None:
    normalized = _normalize_native_command(command)
    if not normalized.startswith("team "):
        return None
    payload = normalized[5:].strip()
    values = (
        [int(value) for value in re.findall(r"\d+", payload)]
        if "," in payload
        else [int(char) for char in payload if char.isdigit()]
    )
    expected_count, team_size = _team_preview_shape(request)

    if len(values) == expected_count:
        selected = values
    elif (
        len(values) == team_size
        and len(set(values)) == team_size
        and set(values) == set(range(1, team_size + 1))
    ):
        # The classic BattleRoom reorders the full party and serializes all slots.
        # In pick-N formats, the first N entries are the actual brought Pokémon.
        selected = values[:expected_count]
    else:
        return None

    if (
        len(selected) != expected_count
        or len(set(selected)) != expected_count
        or any(value < 1 or value > team_size for value in selected)
    ):
        return None
    return selected


def install_native_showdown_controls() -> type:
    """Install native-control support above the currently active service class."""

    current = sparring.BattleLabLocalService
    if getattr(current, "_battle_lab_native_showdown_controls", False):
        return current

    parent_class = current
    original_build_app = sparring.build_app

    class NativeShowdownControlsService(parent_class):
        _battle_lab_native_showdown_controls = True

        @staticmethod
        def _native_reset(session: Any) -> None:
            session.native_request = None
            session.native_choices = {}

        async def _run_session(self, session: Any) -> None:
            """Run the normal poke-env battle while exposing its private request."""

            human: Any | None = None
            model: Any | None = None
            try:
                session.append_event("Verificando checkpoint, Showdown y equipos…")
                await self.ensure_ready()
                await asyncio.to_thread(
                    sparring.validate_team,
                    self.showdown_root,
                    sparring.DEFAULT_FORMAT,
                    session.own_team,
                )
                await asyncio.to_thread(
                    sparring.validate_team,
                    self.showdown_root,
                    sparring.DEFAULT_FORMAT,
                    session.opponent_team,
                )

                from poke_env import AccountConfiguration, ServerConfiguration
                from poke_env.player import Player
                from poke_env.player.battle_order import DoubleBattleOrder

                class _HumanPlayer(Player):
                    def __init__(self, *args: Any, sparring_session: Any, **kwargs: Any):
                        self.sparring_session = sparring_session
                        super().__init__(*args, **kwargs)

                    async def _create_battle(self, split_message: list[str]):
                        current_battle = await super()._create_battle(split_message)
                        target = self.sparring_session
                        room_tag = _room_tag_for_battle(self, current_battle)
                        if room_tag:
                            target.room_tag = room_tag
                            if isinstance(getattr(target, "battle_state", None), dict):
                                target.battle_state["tag"] = room_tag
                        return current_battle

                    async def teampreview(self, current_battle: Any) -> str:
                        target = self.sparring_session
                        target.phase = "team-preview"
                        target.battle_state = _snapshot_with_room_tag(target, self, current_battle)
                        target.legal_orders.clear()
                        target.legal_actions.clear()
                        target.native_choices = {}
                        target.native_request = copy.deepcopy(
                            getattr(current_battle, "last_request", {}) or {}
                        )
                        loop = asyncio.get_running_loop()
                        target.preview_future = loop.create_future()
                        target.append_event(
                            "Team Preview: usa los controles nativos de Pokémon Showdown."
                        )
                        selected = await target.preview_future
                        expected_count, team_size = _team_preview_shape(target.native_request)
                        if (
                            len(selected) != expected_count
                            or len(set(selected)) != expected_count
                            or any(value < 1 or value > team_size for value in selected)
                        ):
                            raise RuntimeError("Team Preview inválido.")
                        self._selected_in_teampreview = True
                        return "/team " + "".join(str(value) for value in selected)

                    async def choose_move(self, current_battle: Any):
                        target = self.sparring_session
                        target.generation += 1
                        joined = DoubleBattleOrder.join_orders(*current_battle.valid_orders)
                        request = copy.deepcopy(
                            getattr(current_battle, "last_request", {}) or {}
                        )
                        target.legal_orders = {}
                        target.legal_actions = []
                        target.native_choices = {}
                        target.native_request = request
                        missing_native: list[str] = []
                        for index, order in enumerate(joined):
                            choice_id = f"{target.generation}:{index}"
                            target.legal_orders[choice_id] = order
                            target.legal_actions.append(
                                {
                                    "id": choice_id,
                                    "first": sparring._single_order_payload(order.first_order),
                                    "second": sparring._single_order_payload(order.second_order),
                                }
                            )
                            native = _native_double_choice(order, request)
                            if native is None:
                                missing_native.append(choice_id)
                            else:
                                target.native_choices[native] = choice_id
                        if not target.legal_actions:
                            raise RuntimeError(
                                "Showdown no produjo órdenes legales para el turno."
                            )
                        if missing_native:
                            raise RuntimeError(
                                "El puente nativo de Showdown no pudo representar "
                                f"{len(missing_native)}/{len(target.legal_actions)} órdenes legales."
                            )
                        target.battle_state = _snapshot_with_room_tag(target, self, current_battle)
                        target.phase = "waiting-choice"
                        loop = asyncio.get_running_loop()
                        target.order_future = loop.create_future()
                        target.append_event(
                            f"Turno {target.battle_state.get('turn', 0)}: esperando una jugada desde la UI nativa de Showdown."
                        )
                        choice_id = await target.order_future
                        order = target.legal_orders.get(choice_id)
                        if order is None:
                            raise RuntimeError(
                                "La jugada elegida ya no pertenece al turno actual."
                            )
                        target.phase = "resolving"
                        target.append_event("Resolviendo turno en Pokémon Showdown…")
                        return order

                suffix = hashlib.sha256(
                    f"{time.time_ns()}-{session.id}".encode()
                ).hexdigest()[:6]
                server_configuration = ServerConfiguration(
                    f"ws://127.0.0.1:{self.showdown_port}/showdown/websocket",
                    "https://play.pokemonshowdown.com/action.php?",
                )
                human = _HumanPlayer(
                    sparring_session=session,
                    account_configuration=AccountConfiguration(f"Human{suffix}", None),
                    battle_format=sparring.DEFAULT_FORMAT,
                    server_configuration=server_configuration,
                    max_concurrent_battles=1,
                    accept_open_team_sheet=True,
                    team=session.own_team,
                    save_replays=str(self.replays_root),
                    log_level=logging.WARNING,
                )
                assert self.runtime is not None
                nana_enabled = hasattr(self, "nana")
                model_name = NANA_DISPLAY_NAME if nana_enabled else f"BattleLab{suffix}"
                model_avatar = NANA_AVATAR if nana_enabled else None
                model = self.runtime.player_class(
                    account_configuration=AccountConfiguration(model_name, None),
                    avatar=model_avatar,
                    battle_format=sparring.DEFAULT_FORMAT,
                    server_configuration=server_configuration,
                    max_concurrent_battles=1,
                    accept_open_team_sheet=True,
                    team=session.opponent_team,
                    policy=self.runtime.policy,
                    deterministic=True,
                    log_level=logging.WARNING,
                )

                if nana_enabled:
                    await _pin_nana_avatar(model, NANA_AVATAR)

                session.append_event(
                    f"Rival listo: {model_name}."
                )
                previous_tags = set(human.battles)
                await human.battle_against(model, n_battles=1)
                new_tags = set(human.battles) - previous_tags
                if len(new_tags) != 1:
                    raise RuntimeError(
                        f"Se esperaba una batalla y aparecieron {len(new_tags)}."
                    )
                finished = human.battles[new_tags.pop()]
                session.battle_state = _snapshot_with_room_tag(session, human, finished)
                session.legal_orders.clear()
                session.legal_actions.clear()
                self._native_reset(session)
                session.result = {
                    "winner": (
                        "human"
                        if finished.won
                        else "model" if finished.lost else "tie"
                    ),
                    "turns": int(getattr(finished, "turn", 0) or 0),
                    "battleTag": _room_tag_for_battle(human, finished),
                    "opponent": session.opponent,
                    "modelDisplayName": model_name,
                    "modelAvatar": model_avatar,
                }
                session.phase = "completed"
                outcome = (
                    "Ganaste"
                    if finished.won
                    else "Battle Lab ganó" if finished.lost else "Empate"
                )
                session.append_event(
                    f"{outcome} · {session.result['turns']} turnos."
                )
            except asyncio.CancelledError:
                session.phase = "cancelled"
                self._native_reset(session)
                session.append_event("Sparring cancelado.")
                raise
            except Exception as error:
                session.phase = "error"
                session.error = str(error)
                session.legal_orders.clear()
                session.legal_actions.clear()
                self._native_reset(session)
                session.append_event(f"Error: {error}")
            finally:
                # poke-env clients are long-lived by default. Close both sockets so the
                # stable Nana username can be reused by the next local BO1.
                for player in (human, model):
                    if player is None:
                        continue
                    with suppress(Exception):
                        await player.ps_client.stop_listening()

        def snapshot(self, session: Any) -> dict[str, Any]:
            data = super().snapshot(session)
            battle = data.get("battle")
            if isinstance(battle, dict) and not battle.get("tag"):
                room_tag = str(getattr(session, "room_tag", "") or "").strip()
                if room_tag:
                    battle["tag"] = room_tag
            data["generation"] = int(getattr(session, "generation", 0) or 0)
            data["nativeRequest"] = copy.deepcopy(
                getattr(session, "native_request", None)
            )
            # War Room's legacy custom pickers key off the original phase names.
            # Expose virtual phases so only the native Showdown controls are shown;
            # the internal session phase remains unchanged for all service/Nana logic.
            if session.phase == "team-preview":
                data["phase"] = "native-team-preview"
            elif session.phase == "waiting-choice":
                data["phase"] = "native-waiting-choice"
            return data

        async def submit_native_choice(
            self, session_id: str, command: str
        ) -> dict[str, Any]:
            session = self.get_session(session_id)
            if session.phase == "team-preview":
                order = _team_preview_order(
                    command,
                    request=getattr(session, "native_request", None),
                )
                if order is None:
                    raise HTTPException(
                        status_code=422,
                        detail="Team Preview nativo inválido.",
                    )
                # Dynamic dispatch intentionally preserves Nana's recorder hook.
                return await self.submit_preview(session_id, order)

            if session.phase != "waiting-choice":
                raise HTTPException(
                    status_code=409,
                    detail="Sparring no está esperando una decisión nativa.",
                )
            normalized = _normalize_native_command(command)
            native_choices = getattr(session, "native_choices", {})
            choice_id = native_choices.get(normalized)
            if not choice_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "La decisión de Showdown no coincide con valid_orders del turno "
                        f"actual: {normalized}"
                    ),
                )
            # Dynamic dispatch intentionally preserves Nana 0/1/2 observation.
            return await self.submit_choice(session_id, choice_id)

    def build_app(service: Any):
        app = original_build_app(service)

        @app.post("/sparring/{session_id}/native-choice")
        async def choose_native_action(
            session_id: str, request: NativeShowdownChoice
        ) -> dict[str, Any]:
            return await service.submit_native_choice(session_id, request.command)

        return app

    sparring.BattleLabLocalService = NativeShowdownControlsService
    sparring.build_app = build_app
    return NativeShowdownControlsService
