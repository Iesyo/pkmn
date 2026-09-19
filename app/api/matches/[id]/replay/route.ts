import { getMatchReplayArtifact } from "@/db/replay-artifacts";
import { apiError } from "@/lib/http";
import { renderShowdownReplayHtml } from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

const REPLAY_HEADERS = {
  "cache-control": "private, no-store",
  "content-disposition": 'inline; filename="champions-replay.html"',
  "content-security-policy": "default-src 'none'; script-src https://play.pokemonshowdown.com; style-src 'unsafe-inline' https://play.pokemonshowdown.com; img-src data: https://play.pokemonshowdown.com; media-src https://play.pokemonshowdown.com; font-src https://play.pokemonshowdown.com; connect-src https://play.pokemonshowdown.com; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox allow-scripts allow-popups",
  "content-type": "text/html; charset=utf-8",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
} as const;

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    const replay = await getMatchReplayArtifact(id);
    return new Response(renderShowdownReplayHtml(replay, `champions-${id}`), {
      headers: REPLAY_HEADERS,
    });
  } catch (error) {
    return apiError(error);
  }
}
