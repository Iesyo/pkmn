"""Install War Room Auto Lab routes on the existing Battle Lab loopback service.

The installer is deliberately additive: Sparring/Nana keep their current service
and ports, while Auto Lab obtains the frozen LIGHT player class from the loaded
runtime and never routes gauntlet battles through Nana's personalized wrappers.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from battle_lab import local_sparring_service as local
from battle_lab import vgc_bench_battle as battle
from battle_lab.auto_lab import AutoLabTeam, run_auto_lab_gauntlet
from battle_lab.showdown_smoke import DEFAULT_FORMAT, validate_team


MAX_VARIANTS = 6
MAX_OPPONENTS = 12
DEFAULT_BATTLES_PER_OPPONENT = 2
MAX_BATTLES_PER_OPPONENT = 20


class AutoLabTeamPayload(BaseModel):
    id: str = Field(min_length=1, max_length=96)
    label: str = Field(min_length=1, max_length=160)
    teamPaste: str = Field(min_length=1)


class StartAutoLabRequest(BaseModel):
    baseline: AutoLabTeamPayload
    variants: list[AutoLabTeamPayload] = Field(default_factory=list, max_length=MAX_VARIANTS)
    opponents: list[AutoLabTeamPayload] = Field(min_length=1, max_length=MAX_OPPONENTS)
    battlesPerOpponent: int = Field(
        default=DEFAULT_BATTLES_PER_OPPONENT,
        ge=2,
        le=MAX_BATTLES_PER_OPPONENT,
    )


@dataclass
class AutoLabJob:
    id: str
    request: StartAutoLabRequest
    created_at: float = field(default_factory=time.time)
    phase: str = "queued"
    error: str = ""
    completed_battles: int = 0
    total_battles: int = 0
    current_candidate_id: str = ""
    current_candidate_label: str = ""
    current_opponent_id: str = ""
    result: dict[str, Any] | None = None
    events: list[str] = field(default_factory=lambda: ["Auto Lab en cola…"])
    task: asyncio.Task[None] | None = None

    def append_event(self, value: str) -> None:
        if not self.events or self.events[-1] != value:
            self.events.append(value)
        self.events = self.events[-60:]


def _payload_team(value: AutoLabTeamPayload) -> AutoLabTeam:
    return AutoLabTeam(id=value.id, label=value.label, team_text=value.teamPaste)


def _frozen_light_runtime(runtime: Any) -> battle.ModelRuntime:
    """Strip Nana wrappers while preserving the exact loaded LIGHT policy."""

    player_class = runtime.player_class
    light_class = next(
        (candidate for candidate in player_class.__mro__ if candidate.__name__ == "BattleLabPolicyPlayer"),
        None,
    )
    if light_class is None:
        if player_class.__name__ == "BattleLabPolicyPlayer":
            light_class = player_class
        else:
            raise RuntimeError(
                "No se encontró BattleLabPolicyPlayer en el runtime cargado; Auto Lab no usará una capa adaptativa por accidente."
            )
    metadata = dict(getattr(runtime, "metadata", {}) or {})
    metadata.update({"autoLabPolicy": "LIGHT M-C", "adaptiveLayer": False})
    return battle.ModelRuntime(
        policy=runtime.policy,
        player_class=light_class,
        torch=runtime.torch,
        metadata=metadata,
    )


def install_auto_lab_service() -> type:
    """Patch the existing service class/app exactly once."""

    service_class = local.BattleLabLocalService
    if getattr(service_class, "_auto_lab_installed", False):
        return service_class

    original_init = service_class.__init__
    original_start = service_class.start
    original_shutdown = service_class.shutdown
    original_build_app = local.build_app

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.auto_lab_jobs: dict[str, AutoLabJob] = {}

    @property
    def active_auto_lab(self: Any) -> AutoLabJob | None:
        for job in self.auto_lab_jobs.values():
            if job.phase not in {"completed", "error", "cancelled"}:
                return job
        return None

    async def start(self: Any, request: Any):
        if self.active_auto_lab is not None:
            raise HTTPException(status_code=409, detail="Auto Lab está ejecutando un Gauntlet; espera a que termine antes de iniciar Sparring.")
        return await original_start(self, request)

    async def shutdown(self: Any) -> None:
        for job in self.auto_lab_jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
        await original_shutdown(self)

    def auto_lab_snapshot(self: Any, job: AutoLabJob) -> dict[str, Any]:
        elapsed = max(0.0, time.time() - job.created_at)
        progress = (job.completed_battles / job.total_battles) if job.total_battles else 0.0
        eta = (elapsed / progress - elapsed) if progress > 0 and job.phase == "running" else None
        return {
            "id": job.id,
            "phase": job.phase,
            "error": job.error,
            "completedBattles": job.completed_battles,
            "totalBattles": job.total_battles,
            "progress": round(progress, 6),
            "elapsedSeconds": round(elapsed, 1),
            "etaSeconds": round(eta, 1) if eta is not None else None,
            "currentCandidateId": job.current_candidate_id or None,
            "currentCandidateLabel": job.current_candidate_label or None,
            "currentOpponentId": job.current_opponent_id or None,
            "events": list(job.events),
            "result": job.result,
        }

    def get_auto_lab_job(self: Any, job_id: str) -> AutoLabJob:
        job = self.auto_lab_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Gauntlet Auto Lab no encontrado.")
        return job

    async def start_auto_lab(self: Any, request: StartAutoLabRequest) -> AutoLabJob:
        if self.active_session is not None:
            raise HTTPException(status_code=409, detail="Hay un Sparring activo; termínalo antes de ejecutar Auto Lab.")
        if self.active_auto_lab is not None:
            raise HTTPException(status_code=409, detail="Ya existe un Gauntlet Auto Lab activo.")
        if request.battlesPerOpponent % 2:
            raise HTTPException(status_code=422, detail="battlesPerOpponent debe ser par para equilibrar ambos lados.")

        payloads = [request.baseline, *request.variants, *request.opponents]
        ids = [item.id for item in payloads]
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=422, detail="Baseline, variantes y rivales necesitan IDs únicos.")

        for item in payloads:
            complete, issues = local.strict_complete_team(item.teamPaste)
            if not complete:
                raise HTTPException(
                    status_code=422,
                    detail=f"{item.label} no es battle-ready: {'; '.join(issues[:8])}",
                )

        job = AutoLabJob(id=uuid.uuid4().hex[:16], request=request)
        job.total_battles = (1 + len(request.variants)) * len(request.opponents) * request.battlesPerOpponent
        self.auto_lab_jobs[job.id] = job
        job.task = asyncio.create_task(self._run_auto_lab(job))
        return job

    async def _run_auto_lab(self: Any, job: AutoLabJob) -> None:
        try:
            job.phase = "preparing"
            job.append_event("Verificando LIGHT M-C, Showdown y equipos…")
            await self.ensure_ready()
            assert self.runtime is not None

            for item in [job.request.baseline, *job.request.variants, *job.request.opponents]:
                await asyncio.to_thread(validate_team, self.showdown_root, DEFAULT_FORMAT, item.teamPaste)

            light_runtime = _frozen_light_runtime(self.runtime)
            job.phase = "running"
            job.append_event(
                f"Gauntlet listo: baseline + {len(job.request.variants)} sets candidatos × {len(job.request.opponents)} rivales."
            )

            async def progress(payload: dict[str, Any]) -> None:
                job.completed_battles = int(payload.get("completedBattles", job.completed_battles) or 0)
                job.current_candidate_id = str(payload.get("candidateId") or job.current_candidate_id)
                job.current_candidate_label = str(payload.get("candidateLabel") or job.current_candidate_label)
                job.current_opponent_id = str(payload.get("opponentId") or "")
                if job.current_opponent_id:
                    job.append_event(
                        f"{job.current_candidate_label}: {job.current_opponent_id} · {job.completed_battles}/{job.total_battles}"
                    )

            replay_root = self.replays_root / "auto-lab" / job.id
            replay_root.mkdir(parents=True, exist_ok=True)
            result = await run_auto_lab_gauntlet(
                runtime=light_runtime,
                port=self.showdown_port,
                battle_format=DEFAULT_FORMAT,
                baseline=_payload_team(job.request.baseline),
                variants=[_payload_team(item) for item in job.request.variants],
                opponents=[_payload_team(item) for item in job.request.opponents],
                battles_per_opponent=job.request.battlesPerOpponent,
                timeout=300.0,
                replay_root=replay_root,
                progress=progress,
            )
            job.completed_battles = job.total_battles
            job.result = result
            job.phase = "completed"
            best = result.get("bestVariantId")
            if best:
                job.append_event(f"Auditoría terminada · mejor set candidato: {best}.")
            elif job.request.variants:
                job.append_event("Auditoría terminada · ningún set candidato superó el baseline.")
            else:
                job.append_event("Auditoría del baseline terminada · no había sets alternativos que comparar.")
        except asyncio.CancelledError:
            job.phase = "cancelled"
            job.append_event("Gauntlet cancelado.")
            raise
        except Exception as error:
            job.phase = "error"
            job.error = str(error)
            job.append_event(f"Auto Lab falló: {error}")

    service_class.__init__ = __init__
    service_class.active_auto_lab = active_auto_lab
    service_class.start = start
    service_class.shutdown = shutdown
    service_class.auto_lab_snapshot = auto_lab_snapshot
    service_class.get_auto_lab_job = get_auto_lab_job
    service_class.start_auto_lab = start_auto_lab
    service_class._run_auto_lab = _run_auto_lab
    service_class._auto_lab_installed = True

    def build_app(service: Any):
        app = original_build_app(service)

        @app.post("/auto-lab")
        async def start_auto_lab_route(request: StartAutoLabRequest) -> dict[str, Any]:
            job = await service.start_auto_lab(request)
            return service.auto_lab_snapshot(job)

        @app.get("/auto-lab/{job_id}")
        async def get_auto_lab_route(job_id: str) -> dict[str, Any]:
            return service.auto_lab_snapshot(service.get_auto_lab_job(job_id))

        return app

    local.build_app = build_app
    return service_class
