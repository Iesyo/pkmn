export const LABMAUS_TOURNAMENTS_URL = "https://labmaus.net/tournaments";
export const TOURNAMENT_SNAPSHOT_PAGE_URL = "https://github.com/Pocolip/vs-recorder/blob/develop/frontend/src/data/tournamentTeams-regM-B.json";

const MAX_TOURNAMENTS = 250;
const MAX_TEAMS_PER_TOURNAMENT = 48;
const MAX_TEXT_LENGTH = 180;

export interface TournamentScoutingTeam {
  id: string;
  playerName: string;
  placement: number | null;
  record: string;
  pokemon: string[];
  pokepasteUrl: string;
}

export interface TournamentScoutingEvent {
  id: string;
  name: string;
  teams: TournamentScoutingTeam[];
}

export interface TournamentScoutingResponse {
  regulation: string;
  generatedAt: string;
  retrievedAt: string;
  dateRange: {
    from: string;
    to: string;
  };
  stale: boolean;
  source: {
    label: "LabMaus";
    url: typeof LABMAUS_TOURNAMENTS_URL;
  };
  snapshotSource: {
    label: "VS Recorder";
    url: typeof TOURNAMENT_SNAPSHOT_PAGE_URL;
  };
  tournaments: TournamentScoutingEvent[];
}

function recordValue(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function cleanText(value: unknown, fallback = "") {
  if (typeof value !== "string") return fallback;
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.slice(0, MAX_TEXT_LENGTH) || fallback;
}

function normalizePokemonName(value: unknown) {
  return cleanText(value, "")
    .replace(/\s*♂$/u, "-M")
    .replace(/\s*♀$/u, "-F")
    .slice(0, 64);
}

function normalizePokepasteUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 180) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || url.hostname !== "pokepast.es"
      || url.port
      || url.username
      || url.password
      || !/^\/[a-z0-9]+\/?$/i.test(url.pathname)
    ) return null;
    return `${url.origin}${url.pathname.replace(/\/$/, "")}`;
  } catch {
    return null;
  }
}

function numericPlacement(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isInteger(number) && number > 0 && number <= 100_000 ? number : null;
}

function stableHash(value: string) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function isoDate(value: unknown) {
  const text = cleanText(value, "");
  return /^\d{4}-\d{2}-\d{2}(?:T[^\s]+)?$/.test(text) && !Number.isNaN(Date.parse(text))
    ? text
    : "";
}

export function buildTournamentScoutingResponse(
  payload: unknown,
  options: { stale?: boolean; retrievedAt?: string } = {},
): TournamentScoutingResponse {
  const root = recordValue(payload);
  if (!root || !Array.isArray(root.compositions)) {
    throw new Error("El snapshot de torneos no tiene el formato esperado");
  }

  const uniqueTeams = new Map<string, TournamentScoutingTeam & { tournamentName: string }>();
  for (const compositionValue of root.compositions) {
    const composition = recordValue(compositionValue);
    if (!composition || !Array.isArray(composition.clusters)) continue;
    for (const clusterValue of composition.clusters) {
      const cluster = recordValue(clusterValue);
      if (!cluster || !Array.isArray(cluster.teams)) continue;
      for (const teamValue of cluster.teams) {
        const team = recordValue(teamValue);
        if (!team) continue;
        const pokepasteUrl = normalizePokepasteUrl(team.pokepasteUrl);
        const tournamentName = cleanText(team.tournamentName, "");
        const pokemon = Array.isArray(team.pokemonNames)
          ? team.pokemonNames.map(normalizePokemonName).filter(Boolean).slice(0, 6)
          : [];
        if (!pokepasteUrl || !tournamentName || pokemon.length !== 6 || uniqueTeams.has(pokepasteUrl)) continue;

        uniqueTeams.set(pokepasteUrl, {
          id: `team-${pokepasteUrl.split("/").at(-1)}`,
          playerName: cleanText(team.name, "Jugador sin nombre"),
          placement: numericPlacement(team.placement),
          record: cleanText(team.record, ""),
          pokemon,
          pokepasteUrl,
          tournamentName,
        });
      }
    }
  }

  const grouped = new Map<string, TournamentScoutingTeam[]>();
  for (const { tournamentName, ...team } of uniqueTeams.values()) {
    const current = grouped.get(tournamentName) ?? [];
    if (current.length < MAX_TEAMS_PER_TOURNAMENT) current.push(team);
    grouped.set(tournamentName, current);
  }

  const tournaments = [...grouped.entries()]
    .map(([name, teams]) => ({
      id: `tournament-${stableHash(name)}`,
      name,
      teams: teams.sort((left, right) => {
        if (left.placement === null && right.placement !== null) return 1;
        if (left.placement !== null && right.placement === null) return -1;
        if (left.placement !== right.placement) return (left.placement ?? 0) - (right.placement ?? 0);
        return left.playerName.localeCompare(right.playerName);
      }),
    }))
    .filter((tournament) => tournament.teams.length > 0)
    .sort((left, right) => right.teams.length - left.teams.length || left.name.localeCompare(right.name))
    .slice(0, MAX_TOURNAMENTS);

  if (!tournaments.length) throw new Error("El snapshot no contiene equipos utilizables");

  const dateRange = recordValue(root.dateRange);
  const generatedAt = isoDate(root.generatedAt);
  const from = isoDate(dateRange?.from);
  const to = isoDate(dateRange?.to);
  return {
    regulation: cleanText(root.regulation, "M-B"),
    generatedAt: generatedAt || new Date(0).toISOString(),
    retrievedAt: options.retrievedAt ?? new Date().toISOString(),
    dateRange: { from, to },
    stale: options.stale ?? false,
    source: { label: "LabMaus", url: LABMAUS_TOURNAMENTS_URL },
    snapshotSource: { label: "VS Recorder", url: TOURNAMENT_SNAPSHOT_PAGE_URL },
    tournaments,
  };
}

export function isTournamentScoutingResponse(value: unknown): value is TournamentScoutingResponse {
  const root = recordValue(value);
  return Boolean(
    root
    && typeof root.regulation === "string"
    && typeof root.generatedAt === "string"
    && typeof root.stale === "boolean"
    && Array.isArray(root.tournaments)
    && root.tournaments.every((entry) => {
      const tournament = recordValue(entry);
      return tournament
        && typeof tournament.id === "string"
        && typeof tournament.name === "string"
        && Array.isArray(tournament.teams);
    }),
  );
}
