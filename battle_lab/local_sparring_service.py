#!/usr/bin/env python3
"""Local loopback service for interactive Battle Lab sparring.

The browser owns the human side. Pokémon Showdown remains the battle source of
truth and the Battle Lab M-C checkpoint owns the opponent side. Only complete
team pastes are accepted; this service never fills missing competitive fields.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
import signal
import subprocess
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from battle_lab import vgc_bench_battle as battle
from battle_lab.mc_rl_light_v3 import alias_mc_runtime_catalogs
from battle_lab.mc_training import sha256_file
from battle_lab.model_release import checkpoint_identity
from battle_lab.showdown_smoke import (
    DEFAULT_FORMAT,
    DEFAULT_SHOWDOWN_REPOSITORY,
    ensure_showdown_checkout,
    install_runtime_config,
    port_is_open,
    read_showdown_commit,
    tail,
    validate_team,
)


DEFAULT_API_PORT = 8765
DEFAULT_SHOWDOWN_PORT = 8766
DEFAULT_SEED = 260913
DEFAULT_RUNTIME_ROOT = Path.home() / ".local" / "share" / "like-no-one-ever-was" / "battle-lab"
DEFAULT_CHECKPOINT = DEFAULT_RUNTIME_ROOT / "models" / "step-000196608.zip"


class OpponentRef(BaseModel):
    id: str
    label: str = ""
    source: str = ""


class StartSparringRequest(BaseModel):
    teamPaste: str = Field(min_length=1)
    opponentPaste: str = Field(min_length=1)
    opponent: OpponentRef


class TeamPreviewChoice(BaseModel):
    order: list[int] = Field(min_length=4, max_length=4)


class BattleChoice(BaseModel):
    choiceId: str = Field(min_length=1)


@dataclass
class SparringSession:
    id: str
    own_team: str
    opponent_team: str
    opponent: dict[str, str]
    created_at: float = field(default_factory=time.time)
    phase: str = "starting"
    error: str = ""
    generation: int = 0
    legal_orders: dict[str, Any] = field(default_factory=dict)
    legal_actions: list[dict[str, Any]] = field(default_factory=list)
    preview_future: asyncio.Future[list[int]] | None = None
    order_future: asyncio.Future[str] | None = None
    battle_state: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    events: list[str] = field(default_factory=lambda: ["Preparando Battle Lab local…"])
    task: asyncio.Task[None] | None = None

    def append_event(self, value: str) -> None:
        if not self.events or self.events[-1] != value:
            self.events.append(value)
        self.events = self.events[-80:]


def _split_team_blocks(raw_paste: str) -> list[list[str]]:
    normalized = raw_paste.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = []
    for raw_block in re.split(r"\n\s*\n+", normalized):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if lines:
            blocks.append(lines)
    return blocks


def strict_complete_team(raw_paste: str) -> tuple[bool, list[str]]:
    """Reject anything that would require guessing a competitive field."""

    blocks = _split_team_blocks(raw_paste)
    issues: list[str] = []
    if len(blocks) != 6:
        issues.append(f"el paste contiene {len(blocks)} Pokémon; se requieren 6")
    for index, lines in enumerate(blocks, start=1):
        first = lines[0] if lines else ""
        if " @ " not in first or not first.split(" @ ", 1)[1].strip():
            issues.append(f"slot {index}: falta objeto explícito")
        if not any(line.lower().startswith("ability:") and line.split(":", 1)[1].strip() for line in lines):
            issues.append(f"slot {index}: falta habilidad explícita")
        if not any(line.lower().startswith("level:") and line.split(":", 1)[1].strip().isdigit() for line in lines):
            issues.append(f"slot {index}: falta nivel explícito")
        if not any(line.lower().startswith("evs:") and line.split(":", 1)[1].strip() for line in lines):
            issues.append(f"slot {index}: faltan Stat Points/EVs explícitos")
        if not any(line.lower().endswith(" nature") and line[:-7].strip() for line in lines):
            issues.append(f"slot {index}: falta naturaleza explícita")
        moves = [line for line in lines if line.startswith("- ") and line[2:].strip()]
        if len(moves) != 4:
            issues.append(f"slot {index}: declara {len(moves)} movimientos; se requieren 4")
    return not issues, issues


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    name = getattr(value, "name", None)
    if name is not None:
        return str(name)
    return str(value)


def _pokemon_snapshot(mon: Any | None) -> dict[str, Any] | None:
    if mon is None:
        return None
    return {
        "species": str(getattr(mon, "species", "") or getattr(mon, "name", "")),
        "name": str(getattr(mon, "name", "") or getattr(mon, "species", "")),
        "hp": round(float(getattr(mon, "current_hp_fraction", 0.0) or 0.0) * 100, 1),
        "fainted": bool(getattr(mon, "fainted", False)),
        "status": _safe_value(getattr(mon, "status", None)),
        "item": str(getattr(mon, "item", "") or ""),
        "ability": str(getattr(mon, "ability", "") or ""),
        "active": bool(getattr(mon, "active", False)),
    }


def _team_snapshot(team: Any) -> list[dict[str, Any]]:
    values = team.values() if hasattr(team, "values") else team
    return [item for mon in values if (item := _pokemon_snapshot(mon)) is not None]


def _battle_snapshot(current: Any) -> dict[str, Any]:
    try:
        own_active = [_pokemon_snapshot(mon) for mon in current.active_pokemon]
    except Exception:
        own_active = []
    try:
        opponent_active = [_pokemon_snapshot(mon) for mon in current.opponent_active_pokemon]
    except Exception:
        opponent_active = []
    return {
        "tag": str(getattr(current, "battle_tag", "")),
        "turn": int(getattr(current, "turn", 0) or 0),
        "finished": bool(getattr(current, "finished", False)),
        "won": bool(getattr(current, "won", False)),
        "lost": bool(getattr(current, "lost", False)),
        "weather": [_safe_value(value) for value in getattr(current, "weather", {})],
        "fields": [_safe_value(value) for value in getattr(current, "fields", {})],
        "ownActive": own_active,
        "opponentActive": opponent_active,
        "ownTeam": _team_snapshot(getattr(current, "team", {})),
        "opponentTeam": _team_snapshot(getattr(current, "opponent_team", {})),
    }


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
        kind = "pass" if "pass" in raw else "other"
        value = raw
        label = "Pasar" if kind == "pass" else raw

    flags = []
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


class BattleLabLocalService:
    def __init__(
        self,
        *,
        runtime_root: Path,
        checkpoint: Path,
        device: str,
        showdown_port: int,
        seed: int,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        self.checkpoint = checkpoint.expanduser().resolve()
        self.device = device
        self.showdown_port = showdown_port
        self.seed = seed
        self.showdown_root = self.runtime_root / "pokemon-showdown"
        self.vgc_root = self.runtime_root / "vgc-bench"
        self.logs_root = self.runtime_root / "logs"
        self.replays_root = self.runtime_root / "replays"
        self.runtime: Any | None = None
        self.runtime_metadata: dict[str, Any] = {}
        self.showdown_process: subprocess.Popen[str] | None = None
        self.showdown_log_handle: Any | None = None
        self.setup_lock = asyncio.Lock()
        self.sessions: dict[str, SparringSession] = {}

    @property
    def active_session(self) -> SparringSession | None:
        for session in self.sessions.values():
            if session.phase not in {"completed", "error", "cancelled"}:
                return session
        return None

    async def ensure_ready(self) -> None:
        async with self.setup_lock:
            if self.runtime is not None and self.showdown_process is not None and self.showdown_process.poll() is None:
                return
            if not self.checkpoint.is_file():
                raise RuntimeError(
                    f"No existe el checkpoint M-C: {self.checkpoint}. "
                    "Configura --checkpoint con step-000196608.zip."
                )
            checkpoint_sha = await asyncio.to_thread(sha256_file, self.checkpoint)
            identity = checkpoint_identity(self.checkpoint, checkpoint_sha)
            if self.runtime is not None and self.runtime_metadata.get("checkpointSha256") != checkpoint_sha:
                raise RuntimeError("El checkpoint cambió: reinicia Battle Lab para cargar el modelo instalado.")
            self.logs_root.mkdir(parents=True, exist_ok=True)
            self.replays_root.mkdir(parents=True, exist_ok=True)
            showdown_commit = read_showdown_commit()
            await asyncio.to_thread(
                ensure_showdown_checkout,
                checkout=self.showdown_root,
                repository=DEFAULT_SHOWDOWN_REPOSITORY,
                commit=showdown_commit,
                logs_dir=self.logs_root,
            )
            await asyncio.to_thread(install_runtime_config, self.showdown_root)
            await asyncio.to_thread(
                battle.ensure_vgc_bench_checkout,
                checkout=self.vgc_root,
                repository=battle.VGC_BENCH_REPOSITORY,
                commit=battle.VGC_BENCH_COMMIT,
            )
            aliases = await asyncio.to_thread(alias_mc_runtime_catalogs, self.vgc_root)
            if self.runtime is None:
                runtime = await asyncio.to_thread(
                    battle.load_model_runtime,
                    checkout=self.vgc_root,
                    checkpoint=self.checkpoint,
                    requested_device=self.device,
                    seed=self.seed,
                )
                if await asyncio.to_thread(sha256_file, self.checkpoint) != checkpoint_sha:
                    raise RuntimeError("El checkpoint cambió mientras se cargaba; reinicia el runtime.")
                runtime.metadata.update({**identity, "checkpoint": str(self.checkpoint)})
                self.runtime = runtime
                self.runtime_metadata = {
                    **runtime.metadata,
                    "checkpoint": str(self.checkpoint),
                    **identity,
                    "format": DEFAULT_FORMAT,
                    "aliases": aliases,
                }
            await self._ensure_showdown_server()

    async def _ensure_showdown_server(self) -> None:
        if self.showdown_process is not None and self.showdown_process.poll() is None:
            return
        if port_is_open(self.showdown_port):
            raise RuntimeError(
                f"El puerto local {self.showdown_port} ya está ocupado; "
                "Battle Lab no terminará procesos ajenos."
            )
        log_path = self.logs_root / "local-sparring-showdown.log"
        self.showdown_log_handle = log_path.open("w", encoding="utf-8")
        self.showdown_process = subprocess.Popen(
            ["node", "pokemon-showdown", "start", str(self.showdown_port), "--no-security"],
            cwd=self.showdown_root,
            stdout=self.showdown_log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        started = time.monotonic()
        while time.monotonic() - started < 90:
            if self.showdown_process.poll() is not None:
                raise RuntimeError(f"Showdown terminó durante el arranque.\n{tail(log_path)}")
            if port_is_open(self.showdown_port):
                return
            await asyncio.sleep(0.25)
        raise TimeoutError(f"Showdown no abrió el puerto {self.showdown_port}.\n{tail(log_path)}")

    async def shutdown(self) -> None:
        for session in self.sessions.values():
            if session.task and not session.task.done():
                session.task.cancel()
        process = self.showdown_process
        if process is not None and process.poll() is None:
            with suppress(ProcessLookupError):
                process.send_signal(signal.SIGINT)
            try:
                await asyncio.to_thread(process.wait, 10)
            except Exception:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
        if self.showdown_log_handle is not None:
            self.showdown_log_handle.close()

    async def start(self, request: StartSparringRequest) -> SparringSession:
        if self.active_session is not None:
            raise HTTPException(status_code=409, detail="Ya existe un Sparring activo.")
        for label, team in (("tu Team", request.teamPaste), ("rival", request.opponentPaste)):
            complete, issues = strict_complete_team(team)
            if not complete:
                raise HTTPException(
                    status_code=422,
                    detail=f"{label} no es battle-ready: {'; '.join(issues[:8])}",
                )
        session = SparringSession(
            id=uuid.uuid4().hex[:16],
            own_team=request.teamPaste.strip(),
            opponent_team=request.opponentPaste.strip(),
            opponent=request.opponent.model_dump(),
        )
        self.sessions[session.id] = session
        session.task = asyncio.create_task(self._run_session(session))
        return session

    async def _run_session(self, session: SparringSession) -> None:
        try:
            session.append_event("Verificando checkpoint, Showdown y equipos…")
            await self.ensure_ready()
            await asyncio.to_thread(validate_team, self.showdown_root, DEFAULT_FORMAT, session.own_team)
            await asyncio.to_thread(validate_team, self.showdown_root, DEFAULT_FORMAT, session.opponent_team)

            from poke_env import AccountConfiguration, ServerConfiguration
            from poke_env.player import Player
            from poke_env.player.battle_order import DoubleBattleOrder

            class _HumanPlayer(Player):
                def __init__(self, *args: Any, sparring_session: SparringSession, **kwargs: Any):
                    self.sparring_session = sparring_session
                    super().__init__(*args, **kwargs)

                async def teampreview(self, current: Any) -> str:
                    target = self.sparring_session
                    target.phase = "team-preview"
                    target.battle_state = _battle_snapshot(current)
                    target.legal_orders.clear()
                    target.legal_actions.clear()
                    loop = asyncio.get_running_loop()
                    target.preview_future = loop.create_future()
                    target.append_event("Team Preview: elige cuatro Pokémon y su orden.")
                    selected = await target.preview_future
                    if len(selected) != 4 or len(set(selected)) != 4 or any(value < 1 or value > 6 for value in selected):
                        raise RuntimeError("Team Preview inválido.")
                    self._selected_in_teampreview = True
                    return "/team " + "".join(str(value) for value in selected)

                async def choose_move(self, current: Any):
                    target = self.sparring_session
                    target.generation += 1
                    joined = DoubleBattleOrder.join_orders(*current.valid_orders)
                    target.legal_orders = {}
                    target.legal_actions = []
                    for index, order in enumerate(joined):
                        choice_id = f"{target.generation}:{index}"
                        target.legal_orders[choice_id] = order
                        target.legal_actions.append(
                            {
                                "id": choice_id,
                                "first": _single_order_payload(order.first_order),
                                "second": _single_order_payload(order.second_order),
                            }
                        )
                    if not target.legal_actions:
                        raise RuntimeError("Showdown no produjo órdenes legales para el turno.")
                    target.battle_state = _battle_snapshot(current)
                    target.phase = "waiting-choice"
                    loop = asyncio.get_running_loop()
                    target.order_future = loop.create_future()
                    target.append_event(f"Turno {target.battle_state.get('turn', 0)}: esperando tu jugada.")
                    choice_id = await target.order_future
                    order = target.legal_orders.get(choice_id)
                    if order is None:
                        raise RuntimeError("La jugada elegida ya no pertenece al turno actual.")
                    target.phase = "resolving"
                    target.append_event("Resolviendo turno en Pokémon Showdown…")
                    return order

            suffix = hashlib.sha256(f"{time.time_ns()}-{session.id}".encode()).hexdigest()[:6]
            server_configuration = ServerConfiguration(
                f"ws://127.0.0.1:{self.showdown_port}/showdown/websocket",
                "https://play.pokemonshowdown.com/action.php?",
            )
            human = _HumanPlayer(
                sparring_session=session,
                account_configuration=AccountConfiguration(f"Human{suffix}", None),
                battle_format=DEFAULT_FORMAT,
                server_configuration=server_configuration,
                max_concurrent_battles=1,
                accept_open_team_sheet=True,
                team=session.own_team,
                save_replays=str(self.replays_root),
                log_level=logging.WARNING,
            )
            model = self.runtime.player_class(
                account_configuration=AccountConfiguration(f"BattleLab{suffix}", None),
                battle_format=DEFAULT_FORMAT,
                server_configuration=server_configuration,
                max_concurrent_battles=1,
                accept_open_team_sheet=True,
                team=session.opponent_team,
                policy=self.runtime.policy,
                deterministic=True,
                log_level=logging.WARNING,
            )

            session.append_event(f"Rival listo: {session.opponent.get('label') or session.opponent.get('id')}.")
            previous_tags = set(human.battles)
            await human.battle_against(model, n_battles=1)
            new_tags = set(human.battles) - previous_tags
            if len(new_tags) != 1:
                raise RuntimeError(f"Se esperaba una batalla y aparecieron {len(new_tags)}.")
            finished = human.battles[new_tags.pop()]
            session.battle_state = _battle_snapshot(finished)
            session.legal_orders.clear()
            session.legal_actions.clear()
            session.result = {
                "winner": "human" if finished.won else "model" if finished.lost else "tie",
                "turns": int(getattr(finished, "turn", 0) or 0),
                "battleTag": str(getattr(finished, "battle_tag", "")),
                "opponent": session.opponent,
            }
            session.phase = "completed"
            outcome = "Ganaste" if finished.won else "Battle Lab ganó" if finished.lost else "Empate"
            session.append_event(f"{outcome} · {session.result['turns']} turnos.")
        except asyncio.CancelledError:
            session.phase = "cancelled"
            session.append_event("Sparring cancelado.")
            raise
        except Exception as error:
            session.phase = "error"
            session.error = str(error)
            session.legal_orders.clear()
            session.legal_actions.clear()
            session.append_event(f"Error: {error}")

    def snapshot(self, session: SparringSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "phase": session.phase,
            "error": session.error,
            "opponent": session.opponent,
            "battle": session.battle_state,
            "actions": session.legal_actions,
            "result": session.result,
            "events": session.events,
        }

    def get_session(self, session_id: str) -> SparringSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sparring no encontrado.")
        return session

    async def submit_preview(self, session_id: str, order: list[int]) -> dict[str, Any]:
        session = self.get_session(session_id)
        future = session.preview_future
        if session.phase != "team-preview" or future is None or future.done():
            raise HTTPException(status_code=409, detail="Sparring no está esperando Team Preview.")
        if len(order) != 4 or len(set(order)) != 4 or any(value < 1 or value > 6 for value in order):
            raise HTTPException(status_code=422, detail="Elige cuatro slots distintos entre 1 y 6.")
        future.set_result(order)
        session.preview_future = None
        session.phase = "resolving"
        return self.snapshot(session)

    async def submit_choice(self, session_id: str, choice_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        future = session.order_future
        if session.phase != "waiting-choice" or future is None or future.done():
            raise HTTPException(status_code=409, detail="Sparring no está esperando una jugada.")
        if choice_id not in session.legal_orders:
            raise HTTPException(status_code=422, detail="La jugada no es legal en el estado actual.")
        future.set_result(choice_id)
        session.order_future = None
        session.phase = "resolving"
        return self.snapshot(session)


def build_app(service: BattleLabLocalService) -> FastAPI:
    app = FastAPI(title="LikeNoOneEverWas Battle Lab Local", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "ready": service.runtime is not None
            and service.showdown_process is not None
            and service.showdown_process.poll() is None,
            "checkpointExists": service.checkpoint.is_file(),
            "activeSession": service.active_session.id if service.active_session else None,
        }

    @app.get("/model-info")
    async def model_info() -> dict[str, Any]:
        try:
            await service.ensure_ready()
        except Exception as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {
            "ready": True,
            "model": service.runtime_metadata.get("modelLabel", "Battle Lab M-C"),
            **service.runtime_metadata,
        }

    @app.post("/sparring")
    async def start_sparring(request: StartSparringRequest) -> dict[str, Any]:
        session = await service.start(request)
        return service.snapshot(session)

    @app.get("/sparring/{session_id}")
    async def get_sparring(session_id: str) -> dict[str, Any]:
        return service.snapshot(service.get_session(session_id))

    @app.post("/sparring/{session_id}/team-preview")
    async def choose_team_preview(session_id: str, request: TeamPreviewChoice) -> dict[str, Any]:
        return await service.submit_preview(session_id, request.order)

    @app.post("/sparring/{session_id}/choice")
    async def choose_action(session_id: str, request: BattleChoice) -> dict[str, Any]:
        return await service.submit_choice(session_id, request.choiceId)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await service.shutdown()

    return app


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--showdown-port", type=int, default=DEFAULT_SHOWDOWN_PORT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    args.checkpoint = args.checkpoint or args.runtime_root / "models" / DEFAULT_CHECKPOINT.name
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Battle Lab local solo puede escuchar en loopback.")
    import uvicorn

    service = BattleLabLocalService(
        runtime_root=args.runtime_root,
        checkpoint=args.checkpoint,
        device=args.device,
        showdown_port=args.showdown_port,
        seed=args.seed,
    )
    uvicorn.run(build_app(service), host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
