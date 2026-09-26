import { saveOpponentPicks } from "@/db/opponent-picks";
import { createMatch, type CreateMatchInput } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as CreateMatchInput;
    const match = await createMatch(payload);
    // Roku, revisión del quinto corte, 26 sep: `createMatch` ya resuelve
    // `opponentPicks` -del replay verificado en servidor cuando lo hay,
    // o del payload para el registro manual- así que esto persiste lo
    // que `match` realmente dice, no una segunda lectura del payload
    // crudo que podía no coincidir.
    const opponentPicks = await saveOpponentPicks(match.id, match.opponentPicks ?? []);
    return Response.json({ match: { ...match, opponentPicks } }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
