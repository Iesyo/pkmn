from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .champions_jobs import ChampionsJobManager


class CreateJobBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    sizeBytes: int = Field(gt=0)
    teamVersionId: str = Field(min_length=1, max_length=128)
    context: dict[str, Any]
    sampleFps: float = Field(default=2.0, ge=0.25, le=10)
    maxBattles: int = Field(default=0, ge=0)


jobs = ChampionsJobManager(
    Path(os.getenv("PKMN_CHAMPIONS_JOBS_PATH", "data/champions-jobs")),
    max_upload_bytes=int(os.getenv("PKMN_CHAMPIONS_MAX_UPLOAD_BYTES", str(20 * 1024**3))),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.resume_pending()
    yield


app = FastAPI(
    title="Pokémon Champions video queue",
    version="0.1.0",
    description="Cola local para recibir vídeos y reconstruir replays de Pokémon Showdown.",
    lifespan=lifespan,
)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=500, detail=str(error) or "No pudimos completar la operación.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", status_code=201)
def create_job(body: CreateJobBody) -> dict[str, object]:
    try:
        return {
            "job": jobs.create_job(
                filename=body.filename,
                size_bytes=body.sizeBytes,
                team_version_id=body.teamVersionId,
                context=body.context,
                sample_fps=body.sampleFps,
                max_battles=body.maxBattles,
            )
        }
    except Exception as error:
        raise _http_error(error) from error


@app.get("/jobs")
def list_jobs(team_version_id: str | None = None) -> dict[str, object]:
    return {"jobs": jobs.list_jobs(team_version_id=team_version_id)}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    try:
        return {"job": jobs.get_job(job_id)}
    except Exception as error:
        raise _http_error(error) from error


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict[str, object]:
    try:
        return {"job": jobs.retry_job(job_id)}
    except Exception as error:
        raise _http_error(error) from error


@app.put("/jobs/{job_id}/chunks")
async def append_chunk(
    job_id: str,
    request: Request,
    offset: int = Query(ge=0),
) -> dict[str, object]:
    try:
        return {"job": jobs.append_chunk(job_id, offset=offset, data=await request.body())}
    except Exception as error:
        raise _http_error(error) from error


@app.get("/jobs/{job_id}/replays/{replay_number}")
def get_replay(job_id: str, replay_number: int) -> dict[str, object]:
    try:
        return {"replay": jobs.replay_document(job_id, replay_number)}
    except Exception as error:
        raise _http_error(error) from error
