import { getStoredTournamentScoutingSnapshot } from "@/db/tournament-snapshot";
import { TOURNAMENT_SCOUTING_RESPONSE } from "@/lib/tournament-scouting-snapshot";

export async function loadCurrentTournamentScoutingSnapshot() {
  try {
    return await getStoredTournamentScoutingSnapshot() ?? TOURNAMENT_SCOUTING_RESPONSE;
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Error inesperado";
    console.warn(`Using bundled tournament snapshot because D1 is unavailable: ${detail}`);
    return { ...TOURNAMENT_SCOUTING_RESPONSE, stale: true };
  }
}
