import type { MatchRecord, MatchSource } from "./types";

export type MatchOrigin = "all" | MatchSource;

type MatchOriginFields = Pick<MatchRecord, "replayUrl"> & Partial<Pick<MatchRecord, "origin">>;
type MatchReplayFields = Pick<MatchRecord, "id" | "replayUrl"> & Partial<Pick<MatchRecord, "hasReplayArtifact">>;

export function getMatchOrigin(match: MatchOriginFields): MatchSource {
  if (match.origin === "champions" || match.origin === "showdown") return match.origin;
  return match.replayUrl.trim() ? "showdown" : "champions";
}

export function getMatchReplayHref(match: MatchReplayFields) {
  if (match.replayUrl.trim()) return match.replayUrl;
  return match.hasReplayArtifact ? `/api/matches/${encodeURIComponent(match.id)}/replay` : "";
}

export function filterMatchesByOrigin(matches: MatchRecord[], origin: MatchOrigin) {
  if (origin === "all") return matches;
  return matches.filter((match) => getMatchOrigin(match) === origin);
}

export function countMatchesByOrigin(matches: MatchRecord[]) {
  return matches.reduce(
    (counts, match) => {
      counts[getMatchOrigin(match)] += 1;
      return counts;
    },
    { champions: 0, showdown: 0 },
  );
}
