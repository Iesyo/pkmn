import {
  DEFAULT_VGCPASTES_FORMAT_ID,
  DEFAULT_VGCPASTES_PAGE_SIZE,
  getVgcPastesFormat,
  normalizeVgcPastesTextFilter,
} from "@/lib/vgcpastes-scouting";
import {
  buildVgcPastesScoutingSearchResponse,
  normalizeVgcPastesScoutingSpeciesFilters,
} from "@/lib/vgcpastes-scouting-search";
import { loadVgcPastesFormat } from "@/lib/vgcpastes-scouting-server";

export const dynamic = "force-dynamic";

function errorResponse(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 && parsed <= maximum ? parsed : fallback;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const formatId = url.searchParams.get("format") || DEFAULT_VGCPASTES_FORMAT_ID;
  if (!getVgcPastesFormat(formatId)) {
    return errorResponse("Ese formato no está disponible en VGCPastes.", 400);
  }

  const pokemon = normalizeVgcPastesScoutingSpeciesFilters(url.searchParams.getAll("pokemon"));
  const player = normalizeVgcPastesTextFilter(url.searchParams.get("player"));
  const event = normalizeVgcPastesTextFilter(url.searchParams.get("event"));
  const rank = normalizeVgcPastesTextFilter(url.searchParams.get("rank"));
  const date = normalizeVgcPastesTextFilter(url.searchParams.get("date"));
  const hasEvs = url.searchParams.get("hasEvs") === "1";
  const hasPaste = url.searchParams.get("hasPaste") === "1";
  const hasReplica = url.searchParams.get("hasReplica") === "1";
  const page = positiveInteger(url.searchParams.get("page"), 1, 10_000);
  const pageSize = positiveInteger(url.searchParams.get("pageSize"), DEFAULT_VGCPASTES_PAGE_SIZE, 48);
  const refresh = url.searchParams.get("refresh") === "1";

  try {
    const source = await loadVgcPastesFormat(formatId, { force: refresh });
    return Response.json(buildVgcPastesScoutingSearchResponse(source.format, source.teams, {
      fetchedAt: source.fetchedAt,
      pokemon,
      player,
      event,
      rank,
      date,
      hasEvs,
      hasPaste,
      hasReplica,
      page,
      pageSize,
    }), {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    console.error("Failed to load VGCPastes scouting source", { formatId, error });
    return errorResponse("No pudimos cargar VGCPastes ahora. Inténtalo nuevamente.", 502);
  }
}
