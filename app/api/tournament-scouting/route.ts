import {
  buildTournamentScoutingResponse,
  type TournamentScoutingResponse,
} from "@/lib/tournament-scouting";

export const dynamic = "force-dynamic";

const SNAPSHOT_URL = "https://raw.githubusercontent.com/Pocolip/vs-recorder/develop/frontend/src/data/tournamentTeams-regM-B.json";
const FRESH_CACHE_MS = 12 * 60 * 60 * 1_000;
const STALE_CACHE_MS = 7 * 24 * 60 * 60 * 1_000;
const MAX_RESPONSE_BYTES = 2 * 1_024 * 1_024;
const UPSTREAM_TIMEOUT_MS = 12_000;

let responseCache: {
  response: TournamentScoutingResponse;
  freshUntil: number;
  staleUntil: number;
} | null = null;

function jsonResponse(response: TournamentScoutingResponse) {
  return Response.json(response, {
    headers: {
      "cache-control": "public, max-age=300, s-maxage=43200, stale-while-revalidate=86400",
    },
  });
}

async function readBoundedJson(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) {
    throw new Error("El snapshot de torneos es demasiado grande");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_RESPONSE_BYTES) {
    throw new Error("El snapshot de torneos es demasiado grande");
  }
  return JSON.parse(body) as unknown;
}

async function fetchTournamentSnapshot() {
  const upstream = await fetch(SNAPSHOT_URL, {
    headers: { accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
  if (!upstream.ok) throw new Error(`El snapshot de torneos respondió ${upstream.status}`);
  const retrievedAt = new Date().toISOString();
  return buildTournamentScoutingResponse(await readBoundedJson(upstream), { retrievedAt });
}

export async function GET() {
  const now = Date.now();
  if (responseCache && responseCache.freshUntil > now) return jsonResponse(responseCache.response);

  try {
    const response = await fetchTournamentSnapshot();
    responseCache = {
      response,
      freshUntil: now + FRESH_CACHE_MS,
      staleUntil: now + STALE_CACHE_MS,
    };
    return jsonResponse(response);
  } catch (error) {
    if (responseCache && responseCache.staleUntil > now) {
      return jsonResponse({ ...responseCache.response, stale: true });
    }
    console.error("Failed to load the public LabMaus tournament snapshot", error);
    return Response.json(
      { error: "No pudimos cargar los torneos ahora. Inténtalo nuevamente en unos minutos." },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }
}
