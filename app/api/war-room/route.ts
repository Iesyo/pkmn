import { listScoutingPasteDetails } from "@/db/scouting-pastes";
import { loadVgcPastesFormat } from "@/lib/vgcpastes-scouting-server";
import { loadCurrentTournamentScoutingSnapshot } from "@/lib/tournament-scouting-server";
import {
  WAR_ROOM_FORMAT_ID,
  buildWarRoomCorpusResponse,
} from "@/lib/war-room";
import { WAR_ROOM_HISTORICAL_REGULATIONS } from "@/lib/war-room-regulations";

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
    return errorResponse("War Room trabaja contra la regulación vigente Champions M-C.", 400);
  }

  try {
    const force = url.searchParams.get("refresh") === "1";
    const [source, savedPastes, tournamentSnapshot, historicalSources] = await Promise.all([
      loadVgcPastesFormat(formatId, {
        force,
      }),
      listScoutingPasteDetails().catch((error) => {
        console.warn("War Room is continuing without the private Scouting library", error);
        return [];
      }),
      loadCurrentTournamentScoutingSnapshot().catch((error) => {
        console.warn("War Room is continuing without tournament pastes", error);
        return null;
      }),
      Promise.all(WAR_ROOM_HISTORICAL_REGULATIONS.map(async (regulation) => {
        try {
          return await loadVgcPastesFormat(regulation.formatId, { force });
        } catch (error) {
          console.warn(`War Room is continuing without historical ${regulation.formatLabel}`, error);
          return null;
        }
      })).then((entries) => entries.filter((entry): entry is NonNullable<typeof entry> => Boolean(entry))),
    ]);
    return Response.json(
      buildWarRoomCorpusResponse(source.format, source.teams, source.fetchedAt, savedPastes, tournamentSnapshot, historicalSources),
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
