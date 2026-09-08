import { saveStoredTournamentScoutingSnapshot } from "@/db/tournament-snapshot";
import type { TournamentScoutingResponse } from "@/lib/tournament-scouting";
import {
  assertSafeTournamentSnapshotUpdate,
  fetchLatestTournamentScoutingSnapshot,
} from "@/lib/tournament-scouting-refresh";
import { loadCurrentTournamentScoutingSnapshot } from "@/lib/tournament-scouting-server";

export const dynamic = "force-dynamic";

function jsonResponse(response: TournamentScoutingResponse) {
  return Response.json(response, {
    headers: {
      "cache-control": "no-store",
    },
  });
}

function teamCount(response: TournamentScoutingResponse) {
  return response.tournaments.reduce((total, tournament) => total + tournament.teams.length, 0);
}

function publicRefreshError(error: unknown) {
  if (
    error instanceof Error
    && /^(El |La |VS Recorder|GitHub)/.test(error.message)
    && error.message.length <= 240
  ) return error.message;
  return "El servidor no pudo descargar o guardar el archivo actualizado.";
}

export async function GET() {
  return jsonResponse(await loadCurrentTournamentScoutingSnapshot());
}

export async function POST() {
  const previous = await loadCurrentTournamentScoutingSnapshot();
  try {
    const candidate = await fetchLatestTournamentScoutingSnapshot();
    assertSafeTournamentSnapshotUpdate(candidate, previous);
    await saveStoredTournamentScoutingSnapshot(candidate);

    const status = candidate.archive.sourceRevision === previous.archive.sourceRevision
      ? "current" as const
      : "updated" as const;
    const teams = teamCount(candidate);
    const message = status === "current"
      ? `Ya tienes la versión más reciente: ${candidate.tournaments.length} torneos y ${teams} equipos.`
      : `Torneos actualizados: ${candidate.tournaments.length} torneos y ${teams} equipos disponibles.`;
    return Response.json({ status, message, data: candidate }, {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    console.error("Failed to refresh tournament snapshot", error);
    return Response.json({
      error: `No pudimos actualizar los torneos. Conservamos el snapshot anterior. ${publicRefreshError(error)}`,
    }, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}
