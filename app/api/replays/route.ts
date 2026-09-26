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
    const payload = (await request.json()) as {
      replayUrl?: string;
      replay?: unknown;
      teamSpecies?: string[];
      championsJobId?: string;
      championsReplayNumber?: number;
    };
    const teamSpecies = (payload.teamSpecies ?? []).map((species) => species.trim()).filter(Boolean).slice(0, 6);
    if (teamSpecies.length !== 6) {
      throw new ReplayValidationError("El replay debe asociarse con una versión completa de seis Pokémon.");
    }
    if (payload.replay !== undefined && payload.replayUrl?.trim()) {
      throw new ReplayValidationError("Envía una URL de Showdown o un replay reconstruido, no ambos.");
    }

    const reconstructed = payload.replay !== undefined;
    const source = reconstructed
      ? { replay: normalizeShowdownReplayDocument(payload.replay), replayUrl: "" }
      : await fetchShowdownReplay(payload.replayUrl ?? "").then(({ replay, urls }) => ({ replay, replayUrl: urls.replayUrl }));

    const match = importShowdownReplay(source.replay, {
      replayUrl: source.replayUrl,
      showdownNames: await getShowdownNames(),
      teamSpecies,
      origin: reconstructed ? "champions" : "showdown",
      replayArtifact: reconstructed ? source.replay : null,
      // Roku, revisión del cuarto corte, 26 sep: acompaña al replay para
      // que "Guardar partida" pueda mandarlos a `createMatch`, que vuelve
      // a buscar el replay canónico en el job en vez de confiar en éste.
      championsJobId: reconstructed ? payload.championsJobId : undefined,
      championsReplayNumber: reconstructed ? payload.championsReplayNumber : undefined,
    });
    return Response.json({ match });
  } catch (error) {
    return apiError(error);
  }
}
