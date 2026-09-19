import { getShowdownNames } from "@/db/queries";
import { apiError } from "@/lib/http";
import {
  fetchShowdownReplay,
  importShowdownReplay,
  normalizeShowdownReplayDocument,
  ReplayValidationError,
} from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { replayUrl?: string; replay?: unknown; teamSpecies?: string[] };
    const teamSpecies = (payload.teamSpecies ?? []).map((species) => species.trim()).filter(Boolean).slice(0, 6);
    if (teamSpecies.length !== 6) {
      throw new ReplayValidationError("El replay debe asociarse con una versión completa de seis Pokémon.");
    }
    if (payload.replay !== undefined && payload.replayUrl?.trim()) {
      throw new ReplayValidationError("Envía una URL de Showdown o un replay reconstruido, no ambos.");
    }

    const source = payload.replay !== undefined
      ? { replay: normalizeShowdownReplayDocument(payload.replay), replayUrl: "" }
      : await fetchShowdownReplay(payload.replayUrl ?? "").then(({ replay, urls }) => ({ replay, replayUrl: urls.replayUrl }));

    const match = importShowdownReplay(source.replay, {
      replayUrl: source.replayUrl,
      showdownNames: await getShowdownNames(),
      teamSpecies,
    });
    return Response.json({ match });
  } catch (error) {
    return apiError(error);
  }
}
