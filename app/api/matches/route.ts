import { saveOpponentPicks } from "@/db/opponent-picks";
import { createMatch, type CreateMatchInput } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

type MatchPayload = CreateMatchInput & { opponentPicks?: string[] };

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as MatchPayload;
    const match = await createMatch(payload);
    const opponentPicks = await saveOpponentPicks(match.id, payload.opponentPicks ?? []);
    return Response.json({ match: { ...match, opponentPicks } }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
