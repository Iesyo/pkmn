import { createScoutingPaste, listScoutingPasteLibrary } from "@/db/scouting-pastes";
import { apiError } from "@/lib/http";
import {
  DEFAULT_SCOUTING_PASTE_PAGE_SIZE,
  normalizeScoutingPastePokemonFilters,
} from "@/lib/scouting-paste-library";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 96 * 1024;

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 && parsed <= maximum ? parsed : fallback;
}

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const pokemon = normalizeScoutingPastePokemonFilters(url.searchParams.getAll("pokemon"));
    const format = url.searchParams.get("format")?.trim() ?? "";
    const search = url.searchParams.get("search")?.trim() ?? "";
    const page = positiveInteger(url.searchParams.get("page"), 1, 10_000);
    const pageSize = positiveInteger(url.searchParams.get("pageSize"), DEFAULT_SCOUTING_PASTE_PAGE_SIZE, 48);
    return Response.json(await listScoutingPasteLibrary({ pokemon, format, search, page, pageSize }), {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const declaredLength = Number(request.headers.get("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
      return Response.json({ error: "La solicitud es demasiado grande." }, { status: 413 });
    }
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_REQUEST_BYTES) {
      return Response.json({ error: "La solicitud es demasiado grande." }, { status: 413 });
    }
    const payload = JSON.parse(raw) as {
      name?: string;
      creator?: string;
      format?: string;
      sourceUrl?: string;
      sourceLabel?: string;
      notes?: string;
      paste?: string;
    };
    const item = await createScoutingPaste(payload);
    return Response.json({ item }, { status: 201, headers: { "cache-control": "no-store" } });
  } catch (error) {
    return apiError(error);
  }
}
