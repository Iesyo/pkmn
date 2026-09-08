import tournamentSnapshot from "@/data/tournament-teams-reg-m-b.json";
import {
  buildTournamentScoutingResponse,
  type TournamentScoutingResponse,
} from "@/lib/tournament-scouting";

export const dynamic = "force-dynamic";

const tournamentResponse = buildTournamentScoutingResponse(tournamentSnapshot, {
  retrievedAt: "2026-09-08T17:40:00.000Z",
});

function jsonResponse(response: TournamentScoutingResponse) {
  return Response.json(response, {
    headers: {
      "cache-control": "public, max-age=300, s-maxage=43200, stale-while-revalidate=86400",
    },
  });
}

export async function GET() {
  return jsonResponse(tournamentResponse);
}
