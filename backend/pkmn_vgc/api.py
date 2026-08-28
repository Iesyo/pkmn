from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .parser import PasteValidationError
from .repository import Repository

repository = Repository(Path(os.getenv("PKMN_DB_PATH", "data/pkmn.db")))


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    yield


app = FastAPI(
    title="Like No One Ever Was API",
    version="0.1.0",
    description="API personal para teams, versiones y partidas de Pokémon VGC.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TeamBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    paste: str = Field(min_length=20)
    format: str = "gen9"
    mechanics: list[str] = Field(default_factory=lambda: ["tera"])


class VersionBody(BaseModel):
    paste: str = Field(min_length=20)
    format: str | None = None
    mechanics: list[str] | None = None


class MatchBody(BaseModel):
    team_version_id: str
    result: Literal["win", "loss"]
    opponent_name: str = "Rival"
    opponent_paste: str = ""
    replay_url: str = ""
    selected: list[str] = Field(default_factory=list, max_length=4)
    opponent_selected: list[str] = Field(default_factory=list, max_length=6)
    lead: list[str] = Field(default_factory=list, max_length=2)
    rating: int | None = None
    notes: str = ""
    played_at: str | None = None


class SettingsBody(BaseModel):
    showdown_names: list[str] = Field(min_length=1, max_length=10)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, (ValueError, PasteValidationError)):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail="No pudimos completar la operación.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/teams")
def list_teams() -> dict[str, object]:
    return {"teams": repository.list_teams()}


@app.get("/api/settings")
def get_settings() -> dict[str, object]:
    return {"showdown_names": repository.get_showdown_names()}


@app.put("/api/settings")
def update_settings(body: SettingsBody) -> dict[str, object]:
    try:
        return {"showdown_names": repository.save_showdown_names(body.showdown_names)}
    except Exception as error:
        raise _http_error(error) from error


@app.post("/api/teams", status_code=201)
def create_team(body: TeamBody) -> dict[str, object]:
    try:
        return {"version": repository.create_team(body.name, body.paste, format=body.format, mechanics=body.mechanics)}
    except Exception as error:
        raise _http_error(error) from error


@app.post("/api/teams/{team_id}/versions", status_code=201)
def create_version(team_id: str, body: VersionBody) -> dict[str, object]:
    try:
        return {"version": repository.create_version(team_id, body.paste, format=body.format, mechanics=body.mechanics)}
    except Exception as error:
        raise _http_error(error) from error


@app.post("/api/matches", status_code=201)
def create_match(body: MatchBody) -> dict[str, object]:
    try:
        return {
            "match": repository.add_match(
                body.team_version_id,
                body.result,
                opponent_name=body.opponent_name,
                opponent_paste=body.opponent_paste,
                replay_url=body.replay_url,
                selected=body.selected,
                opponent_selected=body.opponent_selected,
                lead=body.lead,
                rating=body.rating,
                notes=body.notes,
                played_at=body.played_at,
            )
        }
    except Exception as error:
        raise _http_error(error) from error
