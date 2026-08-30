import { saveOpponentPicks } from "@/db/opponent-picks";
import { createMatch, type CreateMatchInput } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

type MatchPayload = CreateMatchInput & { opponentPicks?: string[] };

function opponentPicksFromPayload(payload: MatchPayload) {
  if (payload.opponentPicks !== undefined) return payload.opponentPicks;
  if (!payload.replayUrl && payload.opponentSelected?.length === 4) return payload.opponentSelected;
  return [];
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as MatchPayload;
    const match = await createMatch(payload);
    const opponentPicks = await saveOpponentPicks(match.id, opponentPicksFromPayload(payload));
    return Response.json({ match: { ...match, opponentPicks } }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
