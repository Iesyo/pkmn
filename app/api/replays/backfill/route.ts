import {
  getShowdownNames,
  listMoveUsageBackfillCandidates,
  saveBackfilledMoveUsage,
} from "@/db/queries";
import { apiError } from "@/lib/http";
import { fetchShowdownReplay, importShowdownReplay } from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

export async function POST() {
  try {
    const [candidates, showdownNames] = await Promise.all([
      listMoveUsageBackfillCandidates(),
      getShowdownNames(),
    ]);
    const imported = await Promise.allSettled(
      candidates.map(async (candidate) => {
        const { urls, replay } = await fetchShowdownReplay(candidate.replayUrl);
        const match = importShowdownReplay(replay, {
          replayUrl: urls.replayUrl,
          showdownNames,
          teamSpecies: candidate.teamSpecies,
        });
        return { matchId: candidate.matchId, movesUsed: match.movesUsed };
      }),
    );

    let updated = 0;
    for (const result of imported) {
      if (result.status !== "fulfilled") continue;
      await saveBackfilledMoveUsage(result.value.matchId, result.value.movesUsed);
      updated += 1;
    }
    return Response.json({ updated, inspected: candidates.length });
  } catch (error) {
    return apiError(error);
  }
}
