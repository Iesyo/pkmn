import tournamentSnapshot from "@/data/tournament-teams-reg-m-b.json";
import {
  BUNDLED_TOURNAMENT_SOURCE_FILE,
  BUNDLED_TOURNAMENT_SOURCE_REVISION,
  buildTournamentScoutingResponse,
} from "@/lib/tournament-scouting";

export const TOURNAMENT_SCOUTING_RESPONSE = buildTournamentScoutingResponse(tournamentSnapshot, {
  retrievedAt: "2026-09-08T17:40:00.000Z",
  checkedAt: "2026-09-08T17:40:00.000Z",
  sourceFile: BUNDLED_TOURNAMENT_SOURCE_FILE,
  sourceRevision: BUNDLED_TOURNAMENT_SOURCE_REVISION,
  storage: "bundled",
});
