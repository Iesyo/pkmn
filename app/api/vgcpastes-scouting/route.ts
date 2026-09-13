import {
  buildVgcPastesScoutingResponse,
  DEFAULT_VGCPASTES_FORMAT_ID,
  DEFAULT_VGCPASTES_PAGE_SIZE,
  getVgcPastesFormat,
} from "@/lib/vgcpastes-scouting";
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

  const pokemon = (url.searchParams.get("pokemon") ?? "").trim().slice(0, 64);
  const page = positiveInteger(url.searchParams.get("page"), 1, 10_000);
  const pageSize = positiveInteger(url.searchParams.get("pageSize"), DEFAULT_VGCPASTES_PAGE_SIZE, 48);
  const refresh = url.searchParams.get("refresh") === "1";

  try {
    const source = await loadVgcPastesFormat(formatId, { force: refresh });
    return Response.json(buildVgcPastesScoutingResponse(source.format, source.teams, {
      fetchedAt: source.fetchedAt,
      pokemon,
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
