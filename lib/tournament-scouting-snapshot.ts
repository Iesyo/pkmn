import tournamentSnapshot from "@/data/tournament-teams-reg-m-b.json";
import { buildTournamentScoutingResponse } from "@/lib/tournament-scouting";

export const TOURNAMENT_SCOUTING_RESPONSE = buildTournamentScoutingResponse(tournamentSnapshot, {
  retrievedAt: "2026-09-08T17:40:00.000Z",
});

const tournamentTeamsById = new Map(
  TOURNAMENT_SCOUTING_RESPONSE.tournaments.flatMap((tournament) =>
    tournament.teams.map((team) => [team.id, { team, tournamentName: tournament.name }] as const),
  ),
);

export function findTournamentScoutingTeam(teamId: string) {
  return tournamentTeamsById.get(teamId) ?? null;
}
