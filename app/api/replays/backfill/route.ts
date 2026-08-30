import {
  listOpponentPicksBackfillCandidates,
  saveBackfilledOpponentPicks,
} from "@/db/opponent-picks";
import {
  getShowdownNames,
  listMoveUsageBackfillCandidates,
  saveBackfilledMoveUsage,
} from "@/db/queries";
import { apiError } from "@/lib/http";
import { fetchShowdownReplay, importShowdownReplay } from "@/lib/showdown-replay";

export const dynamic = "force-dynamic";

type ReplayBackfillCandidate = {
  matchId: string;
  replayUrl: string;
  teamSpecies: string[];
  needsMoves: boolean;
  needsOpponentPicks: boolean;
};

export async function POST() {
  try {
    const [moveCandidates, opponentPickCandidates, showdownNames] = await Promise.all([
      listMoveUsageBackfillCandidates(),
      listOpponentPicksBackfillCandidates(),
      getShowdownNames(),
    ]);
    const candidates = new Map<string, ReplayBackfillCandidate>();
    for (const candidate of moveCandidates) {
      candidates.set(candidate.matchId, { ...candidate, needsMoves: true, needsOpponentPicks: false });
    }
    for (const candidate of opponentPickCandidates) {
      const current = candidates.get(candidate.matchId);
      candidates.set(candidate.matchId, current
        ? { ...current, needsOpponentPicks: true }
        : { ...candidate, needsMoves: false, needsOpponentPicks: true });
    }

    const imported = await Promise.allSettled(
      [...candidates.values()].map(async (candidate) => {
        const { urls, replay } = await fetchShowdownReplay(candidate.replayUrl);
        const match = importShowdownReplay(replay, {
          replayUrl: urls.replayUrl,
          showdownNames,
          teamSpecies: candidate.teamSpecies,
        });
        return {
          ...candidate,
          movesUsed: match.movesUsed,
          opponentPicks: match.opponentPicks,
        };
      }),
    );

    let updated = 0;
    for (const result of imported) {
      if (result.status !== "fulfilled") continue;
      let changed = false;
      if (result.value.needsMoves) {
        await saveBackfilledMoveUsage(result.value.matchId, result.value.movesUsed);
        changed = true;
      }
      if (result.value.needsOpponentPicks) {
        changed = await saveBackfilledOpponentPicks(result.value.matchId, result.value.opponentPicks) || changed;
      }
      if (changed) updated += 1;
    }
    return Response.json({ updated, inspected: candidates.size });
  } catch (error) {
    return apiError(error);
  }
}
