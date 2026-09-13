import { toId } from "./pokemon-data";
import type { PokemonSet } from "./types";
import { getVgcPastesScoutingSpeciesIdentity } from "./vgcpastes-scouting-search";

export const WAR_ROOM_PASTE_EVIDENCE_SCHEMA_VERSION = 1;
export const MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES = 36;

export type WarRoomPasteSource = "tournament" | "vgcpastes" | "scouting-library";
export type WarRoomPasteSourceTier = "tournament" | "curated" | "collection";

export interface WarRoomPasteEvidenceCandidate {
  id: string;
  source: WarRoomPasteSource;
  savedPasteId: string;
  playerName: string;
  tournament: string;
  rank: string;
  pokepasteUrl: string;
  pokemon: string[];
}

export interface WarRoomPasteEvidenceTeam extends WarRoomPasteEvidenceCandidate {
  sourceTier: WarRoomPasteSourceTier;
  sourceLabel: string;
  sourceUrl: string;
  quality: number;
  sets: PokemonSet[];
}

export interface WarRoomPasteEvidenceResponse {
  schemaVersion: typeof WAR_ROOM_PASTE_EVIDENCE_SCHEMA_VERSION;
  regulation: string;
  fetchedAt: string;
  requested: number;
  loaded: number;
  failed: number;
  teams: WarRoomPasteEvidenceTeam[];
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function pasteEvidenceSpeciesKey(value: string) {
  return toId(getVgcPastesScoutingSpeciesIdentity(value) || value);
}

function safePokepasteUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 180) return "";
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || url.hostname !== "pokepast.es"
      || url.port
      || url.username
      || url.password
      || !/^\/[a-z0-9]+\/?$/i.test(url.pathname)
    ) return "";
    return `${url.origin}${url.pathname.replace(/\/$/, "")}`;
  } catch {
    return "";
  }
}

function cleanText(value: unknown, maximum = 180) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, maximum) : "";
}

function isPasteSource(value: unknown): value is WarRoomPasteSource {
  return value === "tournament" || value === "vgcpastes" || value === "scouting-library";
}

function normalizeCandidate(value: unknown): WarRoomPasteEvidenceCandidate | null {
  const candidate = recordValue(value);
  if (!candidate || !isPasteSource(candidate.source)) return null;
  const id = cleanText(candidate.id, 120);
  const savedPasteId = cleanText(candidate.savedPasteId, 120);
  const pokepasteUrl = safePokepasteUrl(candidate.pokepasteUrl);
  const pokemon = Array.isArray(candidate.pokemon)
    ? candidate.pokemon.map((species) => cleanText(species, 64)).filter(Boolean).slice(0, 6)
    : [];
  if (!id || pokemon.length !== 6 || (!savedPasteId && !pokepasteUrl)) return null;
  if (candidate.source === "scouting-library" && !savedPasteId) return null;
  if (candidate.source !== "scouting-library" && !pokepasteUrl) return null;
  if (new Set(pokemon.map(pasteEvidenceSpeciesKey)).size !== 6) return null;
  return {
    id,
    source: candidate.source,
    savedPasteId: candidate.source === "scouting-library" ? savedPasteId : "",
    playerName: cleanText(candidate.playerName, 180),
    tournament: cleanText(candidate.tournament, 180),
    rank: cleanText(candidate.rank, 80),
    pokepasteUrl,
    pokemon,
  };
}

export function normalizeWarRoomPasteEvidenceCandidates(value: unknown) {
  if (!Array.isArray(value) || value.length > MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES) return null;
  const candidates: WarRoomPasteEvidenceCandidate[] = [];
  const seen = new Set<string>();
  for (const entry of value) {
    const candidate = normalizeCandidate(entry);
    if (!candidate) return null;
    const key = candidate.savedPasteId
      ? `saved:${candidate.savedPasteId}`
      : `url:${candidate.pokepasteUrl}`;
    if (seen.has(key)) continue;
    seen.add(key);
    candidates.push(candidate);
  }
  return candidates;
}

function numericRank(value: string) {
  const match = value.match(/\d+/);
  const rank = match ? Number(match[0]) : Number.NaN;
  return Number.isInteger(rank) && rank > 0 ? rank : null;
}

function sourceScore(candidate: WarRoomPasteEvidenceCandidate) {
  if (candidate.source === "tournament") return 36;
  if (candidate.source === "vgcpastes") {
    return candidate.tournament && candidate.tournament !== "-" ? 28 : 22;
  }
  const label = `${candidate.tournament} ${candidate.rank}`.toLowerCase();
  if (/tournament|torneo|labmaus|vgcpastes/.test(label)) return 24;
  return 18;
}

function candidateIdentity(candidate: WarRoomPasteEvidenceCandidate) {
  if (candidate.savedPasteId) return `saved:${candidate.savedPasteId}`;
  if (candidate.pokepasteUrl) return `url:${candidate.pokepasteUrl}`;
  return `roster:${candidate.pokemon.map(pasteEvidenceSpeciesKey).sort().join("|")}`;
}

/**
 * Selects a small, diverse paste batch before any upstream paste is fetched.
 * Exact core overlap dominates source prestige; tournament and curated sources
 * break ties, and every current/recommended species gets a chance to contribute.
 */
export function selectWarRoomPasteEvidenceCandidates(
  currentSpecies: readonly string[],
  lockedSpecies: readonly string[],
  targetSpecies: readonly string[],
  corpus: readonly WarRoomPasteEvidenceCandidate[],
  limit = MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES,
) {
  const boundedLimit = Math.max(1, Math.min(MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES, Math.floor(limit)));
  const current = new Set(currentSpecies.map(pasteEvidenceSpeciesKey).filter(Boolean));
  const locked = new Set(lockedSpecies.map(pasteEvidenceSpeciesKey).filter(Boolean));
  const targets = new Set(targetSpecies.map(pasteEvidenceSpeciesKey).filter(Boolean));
  const scored = corpus
    .filter((candidate) => candidate.savedPasteId || safePokepasteUrl(candidate.pokepasteUrl))
    .map((candidate) => {
      const keys = new Set(candidate.pokemon.map(pasteEvidenceSpeciesKey).filter(Boolean));
      const currentMatches = [...current].filter((key) => keys.has(key)).length;
      const lockedMatches = [...locked].filter((key) => keys.has(key)).length;
      const targetMatches = [...targets].filter((key) => keys.has(key)).length;
      const rank = numericRank(candidate.rank);
      const placementBonus = rank === null ? 0 : Math.max(0, 14 - Math.min(rank, 14));
      return {
        candidate,
        keys,
        score: lockedMatches * 220 + currentMatches * 75 + targetMatches * 45 + sourceScore(candidate) + placementBonus,
      };
    })
    .filter((entry) => entry.score > sourceScore(entry.candidate))
    .sort((left, right) => right.score - left.score || left.candidate.id.localeCompare(right.candidate.id));

  const selected: WarRoomPasteEvidenceCandidate[] = [];
  const selectedKeys = new Set<string>();
  const add = (entry: (typeof scored)[number] | undefined) => {
    if (!entry || selected.length >= boundedLimit) return;
    const key = candidateIdentity(entry.candidate);
    if (selectedKeys.has(key)) return;
    selectedKeys.add(key);
    selected.push(entry.candidate);
  };

  // Preserve representation for replacement candidates and every member of
  // the current team instead of letting one popular archetype consume the batch.
  for (const species of [...targets, ...current]) {
    let added = 0;
    for (const entry of scored) {
      if (!entry.keys.has(species)) continue;
      const before = selected.length;
      add(entry);
      if (selected.length > before) added += 1;
      if (added >= 3) break;
    }
  }
  for (const entry of scored) add(entry);
  return selected;
}

function isPokemonSet(value: unknown): value is PokemonSet {
  const set = recordValue(value);
  return Boolean(
    set
    && typeof set.id === "string"
    && typeof set.slot === "number"
    && typeof set.species === "string"
    && typeof set.item === "string"
    && typeof set.ability === "string"
    && typeof set.evs === "string"
    && typeof set.nature === "string"
    && Array.isArray(set.moves)
    && set.moves.length >= 1
    && set.moves.length <= 4
    && set.moves.every((moveValue) => {
      const move = recordValue(moveValue);
      return move && typeof move.name === "string";
    }),
  );
}

export function isWarRoomPasteEvidenceResponse(value: unknown): value is WarRoomPasteEvidenceResponse {
  const root = recordValue(value);
  if (
    !root
    || root.schemaVersion !== WAR_ROOM_PASTE_EVIDENCE_SCHEMA_VERSION
    || typeof root.regulation !== "string"
    || typeof root.fetchedAt !== "string"
    || Number.isNaN(Date.parse(root.fetchedAt))
    || typeof root.requested !== "number"
    || !Number.isInteger(root.requested)
    || root.requested < 0
    || typeof root.loaded !== "number"
    || !Number.isInteger(root.loaded)
    || root.loaded < 0
    || typeof root.failed !== "number"
    || !Number.isInteger(root.failed)
    || root.failed < 0
    || !Array.isArray(root.teams)
    || root.loaded !== root.teams.length
    || root.requested !== root.loaded + root.failed
  ) return false;
  return root.teams.every((value) => {
    const team = recordValue(value);
    return Boolean(
      normalizeCandidate(team)
      && (team?.sourceTier === "tournament" || team?.sourceTier === "curated" || team?.sourceTier === "collection")
      && typeof team?.sourceLabel === "string"
      && typeof team?.sourceUrl === "string"
      && typeof team?.quality === "number"
      && Number.isFinite(team.quality)
      && team.quality >= 0
      && team.quality <= 100
      && Array.isArray(team?.sets)
      && team.sets.length === 6
      && team.sets.every(isPokemonSet),
    );
  });
}
