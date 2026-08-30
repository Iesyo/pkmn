import type { MatchRecord } from "./types";

export type MatchOrigin = "all" | "champions" | "showdown";

export function getMatchOrigin(match: Pick<MatchRecord, "replayUrl">): Exclude<MatchOrigin, "all"> {
  return match.replayUrl.trim() ? "showdown" : "champions";
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
