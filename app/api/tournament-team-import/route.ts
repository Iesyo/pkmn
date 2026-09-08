import { parseShowdownPaste } from "@/lib/paste";
import { findTournamentScoutingTeam } from "@/lib/tournament-scouting-snapshot";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 2_048;
const MAX_PASTE_BYTES = 64 * 1_024;
const POKEPASTE_TIMEOUT_MS = 12_000;

function errorResponse(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

async function readBoundedPaste(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste es demasiado grande");
  }
  const paste = await response.text();
  if (new TextEncoder().encode(paste).byteLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste es demasiado grande");
  }
  parseShowdownPaste(paste);
  return paste.replace(/\r\n?/g, "\n").trim();
}

export async function POST(request: Request) {
  const requestLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(requestLength) && requestLength > MAX_REQUEST_BYTES) {
    return errorResponse("La solicitud es demasiado grande.", 413);
  }

  let teamId = "";
  try {
    const payload = (await request.json()) as { teamId?: unknown };
    teamId = typeof payload.teamId === "string" ? payload.teamId : "";
  } catch {
    return errorResponse("La solicitud de importación no es válida.", 400);
  }

  const selected = findTournamentScoutingTeam(teamId);
  if (!selected) return errorResponse("No encontramos ese equipo en el snapshot de torneos.", 404);

  try {
    const upstream = await fetch(`${selected.team.pokepasteUrl}/raw`, {
      headers: { accept: "text/plain" },
      redirect: "manual",
      signal: AbortSignal.timeout(POKEPASTE_TIMEOUT_MS),
    });
    if (upstream.status >= 300 && upstream.status < 400) {
      throw new Error("PokéPaste intentó redirigir la solicitud");
    }
    if (!upstream.ok) throw new Error(`PokéPaste respondió ${upstream.status}`);

    const paste = await readBoundedPaste(upstream);
    return Response.json({
      paste,
      team: {
        id: selected.team.id,
        playerName: selected.team.playerName,
        tournamentName: selected.tournamentName,
      },
    }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    console.error("Failed to import a tournament PokéPaste", { teamId, error });
    return errorResponse("No pudimos descargar ese PokéPaste. Inténtalo nuevamente.", 502);
  }
}
