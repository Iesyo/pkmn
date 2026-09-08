import type { TournamentScoutingResponse } from "@/lib/tournament-scouting";
import { TOURNAMENT_SCOUTING_RESPONSE } from "@/lib/tournament-scouting-snapshot";

export const dynamic = "force-dynamic";

function jsonResponse(response: TournamentScoutingResponse) {
  return Response.json(response, {
    headers: {
      "cache-control": "public, max-age=300, s-maxage=43200, stale-while-revalidate=86400",
    },
  });
}

export async function GET() {
  return jsonResponse(TOURNAMENT_SCOUTING_RESPONSE);
}
