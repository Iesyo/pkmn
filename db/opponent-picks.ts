import type { TeamGroup } from "@/lib/types";
import { getDatabase } from "./raw";

interface OpponentPicksRow {
  id: string;
  opponent_picks_json: string;
}

interface OpponentPicksCandidateRow {
  match_id: string;
  replay_url: string;
  species: string;
  slot: number;
}

export interface OpponentPicksBackfillCandidate {
  matchId: string;
  replayUrl: string;
  teamSpecies: string[];
}

function normalizeOpponentPicks(values: string[]) {
  return [...new Set(values.map((species) => species.trim()).filter(Boolean))].slice(0, 4);
}

function parseOpponentPicks(value: string) {
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed)
      ? normalizeOpponentPicks(parsed.filter((entry): entry is string => typeof entry === "string"))
      : [];
  } catch {
    return [];
  }
}

export async function enrichTeamsWithOpponentPicks(teams: TeamGroup[]): Promise<TeamGroup[]> {
  if (!teams.some((team) => team.versions.some((version) => version.matches.length))) return teams;

  const db = await getDatabase();
  const result = await db
    .prepare("SELECT id, opponent_picks_json FROM matches")
    .all<OpponentPicksRow>();
  const picksByMatch = new Map(result.results.map((row) => [row.id, parseOpponentPicks(row.opponent_picks_json)]));

  return teams.map((team) => ({
    ...team,
    versions: team.versions.map((version) => ({
      ...version,
      matches: version.matches.map((match) => ({
        ...match,
        opponentPicks: picksByMatch.get(match.id) ?? match.opponentPicks ?? [],
      })),
    })),
  }));
}

export async function saveOpponentPicks(matchId: string, values: string[]) {
  const picks = normalizeOpponentPicks(values);
  const db = await getDatabase();
  await db
    .prepare("UPDATE matches SET opponent_picks_json = ? WHERE id = ?")
    .bind(JSON.stringify(picks), matchId)
    .run();
  return picks;
}

export async function saveBackfilledOpponentPicks(matchId: string, values: string[]) {
  const picks = normalizeOpponentPicks(values);
  if (!picks.length) return false;
  const db = await getDatabase();
  const result = await db
    .prepare("UPDATE matches SET opponent_picks_json = ? WHERE id = ? AND opponent_picks_json = '[]'")
    .bind(JSON.stringify(picks), matchId)
    .run();
  return Boolean(result.meta.changes);
}

export async function listOpponentPicksBackfillCandidates(limit = 12): Promise<OpponentPicksBackfillCandidate[]> {
  const db = await getDatabase();
  const safeLimit = Math.min(50, Math.max(1, Math.trunc(limit)));
  const rows = await db
    .prepare(
      `SELECT m.id AS match_id, m.replay_url, p.species, p.slot
       FROM matches m
       JOIN pokemon_sets p ON p.team_version_id = m.team_version_id
       WHERE m.id IN (
         SELECT id FROM matches
         WHERE opponent_picks_json = '[]' AND replay_url <> ''
         ORDER BY played_at DESC
         LIMIT ?
       )
       ORDER BY m.played_at DESC, p.slot ASC`,
    )
    .bind(safeLimit)
    .all<OpponentPicksCandidateRow>();

  const candidates = new Map<string, OpponentPicksBackfillCandidate>();
  for (const row of rows.results) {
    const current = candidates.get(row.match_id) ?? {
      matchId: row.match_id,
      replayUrl: row.replay_url,
      teamSpecies: [],
    };
    current.teamSpecies.push(row.species);
    candidates.set(row.match_id, current);
  }
  return [...candidates.values()].map((candidate) => ({
    ...candidate,
    teamSpecies: candidate.teamSpecies.slice(0, 6),
  }));
}
