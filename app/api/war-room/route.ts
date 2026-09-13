import { listScoutingPasteDetails } from "@/db/scouting-pastes";
import { loadVgcPastesFormat } from "@/lib/vgcpastes-scouting-server";
import {
  WAR_ROOM_FORMAT_ID,
  buildWarRoomCorpusResponse,
} from "@/lib/war-room";

export const dynamic = "force-dynamic";

function errorResponse(error: string, status: number) {
  return Response.json({ error }, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const formatId = url.searchParams.get("format") || WAR_ROOM_FORMAT_ID;
  if (formatId !== WAR_ROOM_FORMAT_ID) {
    return errorResponse("War Room v1 trabaja contra la regulación vigente Champions M-C.", 400);
  }

  try {
    const [source, savedPastes] = await Promise.all([
      loadVgcPastesFormat(formatId, {
        force: url.searchParams.get("refresh") === "1",
      }),
      listScoutingPasteDetails().catch((error) => {
        console.warn("War Room is continuing without the private Scouting library", error);
        return [];
      }),
    ]);
    return Response.json(
      buildWarRoomCorpusResponse(source.format, source.teams, source.fetchedAt, savedPastes),
      {
        headers: {
          "cache-control": "no-store",
        },
      },
    );
  } catch (error) {
    console.error("Failed to load War Room corpus", error);
    return errorResponse("No pudimos cargar el corpus competitivo de War Room.", 502);
  }
}
