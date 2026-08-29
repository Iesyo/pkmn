import { getShowdownNames } from "@/db/queries";
import { apiError } from "@/lib/http";
import {
  importShowdownReplay,
  normalizeShowdownReplayUrl,
  ReplayValidationError,
  type ShowdownReplayDocument,
} from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

const MAX_REPLAY_BYTES = 5_000_000;

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { replayUrl?: string; teamSpecies?: string[] };
    const teamSpecies = (payload.teamSpecies ?? []).map((species) => species.trim()).filter(Boolean).slice(0, 6);
    if (teamSpecies.length !== 6) {
      throw new ReplayValidationError("El replay debe asociarse con una versión completa de seis Pokémon.");
    }

    const urls = normalizeShowdownReplayUrl(payload.replayUrl ?? "");
    let response: Response;
    try {
      response = await fetch(urls.jsonUrl, {
        headers: { accept: "application/json" },
        redirect: "manual",
        signal: AbortSignal.timeout(12_000),
      });
    } catch (error) {
      const timedOut = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
      throw new ReplayValidationError(
        timedOut
          ? "Showdown tardó demasiado en responder. Intenta nuevamente."
          : "No pudimos conectar con Showdown en este momento.",
        timedOut ? 504 : 502,
      );
    }
    if (response.status >= 300 && response.status < 400) {
      throw new ReplayValidationError("Showdown intentó redirigir el replay a una ubicación no permitida.", 502);
    }
    if (response.status === 404) {
      throw new ReplayValidationError("Showdown no encontró ese replay o ya no está disponible.", 404);
    }
    if (!response.ok) {
      throw new ReplayValidationError("Showdown no pudo entregar el replay en este momento.", 502);
    }

    const contentLength = Number(response.headers.get("content-length"));
    if (Number.isFinite(contentLength) && contentLength > MAX_REPLAY_BYTES) {
      throw new ReplayValidationError("El replay excede el tamaño permitido.", 413);
    }
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > MAX_REPLAY_BYTES) {
      throw new ReplayValidationError("El replay excede el tamaño permitido.", 413);
    }

    let replay: ShowdownReplayDocument;
    try {
      replay = JSON.parse(body) as ShowdownReplayDocument;
    } catch {
      throw new ReplayValidationError("Showdown devolvió un replay ilegible.", 502);
    }
    if (Array.isArray(replay.log)) replay.log = replay.log.join("\n");

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
