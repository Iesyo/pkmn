import { getShowdownNames } from "@/db/queries";
import { apiError } from "@/lib/http";
import {
  fetchShowdownReplay,
  importShowdownReplay,
  ReplayValidationError,
} from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { replayUrl?: string; teamSpecies?: string[] };
    const teamSpecies = (payload.teamSpecies ?? []).map((species) => species.trim()).filter(Boolean).slice(0, 6);
    if (teamSpecies.length !== 6) {
      throw new ReplayValidationError("El replay debe asociarse con una versión completa de seis Pokémon.");
    }

    const { urls, replay } = await fetchShowdownReplay(payload.replayUrl ?? "");

    const match = importShowdownReplay(replay, {
      replayUrl: urls.replayUrl,
      showdownNames: await getShowdownNames(),
      teamSpecies,
    });
    return Response.json({ match });
  } catch (error) {
    return apiError(error);
  }
}
