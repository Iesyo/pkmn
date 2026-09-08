export const LABMAUS_TOURNAMENTS_URL = "https://labmaus.net/tournaments";
export const TOURNAMENT_SNAPSHOT_PAGE_URL = "https://github.com/Pocolip/vs-recorder/blob/develop/frontend/src/data/tournamentTeams-regM-B.json";
export const BUNDLED_TOURNAMENT_SOURCE_FILE = "tournamentTeams-regM-B.json";
export const BUNDLED_TOURNAMENT_SOURCE_REVISION = "fd61c8396b25945794728548eb8444579f90bedf";

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

export interface TournamentTeamBuilderImport {
  paste: string;
  suggestedName: string;
  sourceLabel: string;
  estimates?: {
    nature: number;
    statPoints: number;
  };
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
    url: string;
  };
  archive: {
    sourceFile: string;
    sourceRevision: string;
    storage: "bundled" | "persisted";
    checkedAt: string;
  };
  tournaments: TournamentScoutingEvent[];
}

export interface TournamentScoutingRefreshResponse {
  status: "updated" | "current";
  message: string;
  data: TournamentScoutingResponse;
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
  options: {
    stale?: boolean;
    retrievedAt?: string;
    snapshotUrl?: string;
    sourceFile?: string;
    sourceRevision?: string;
    storage?: "bundled" | "persisted";
    checkedAt?: string;
  } = {},
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
  const retrievedAt = options.retrievedAt ?? new Date().toISOString();
  return {
    regulation: cleanText(root.regulation, "M-B"),
    generatedAt: generatedAt || new Date(0).toISOString(),
    retrievedAt,
    dateRange: { from, to },
    stale: options.stale ?? false,
    source: { label: "LabMaus", url: LABMAUS_TOURNAMENTS_URL },
    snapshotSource: { label: "VS Recorder", url: options.snapshotUrl ?? TOURNAMENT_SNAPSHOT_PAGE_URL },
    archive: {
      sourceFile: cleanText(options.sourceFile, BUNDLED_TOURNAMENT_SOURCE_FILE),
      sourceRevision: cleanText(options.sourceRevision, BUNDLED_TOURNAMENT_SOURCE_REVISION),
      storage: options.storage ?? "bundled",
      checkedAt: options.checkedAt ?? retrievedAt,
    },
    tournaments,
  };
}

export function isTournamentScoutingResponse(value: unknown): value is TournamentScoutingResponse {
  const root = recordValue(value);
  const archive = recordValue(root?.archive);
  const dateRange = recordValue(root?.dateRange);
  const source = recordValue(root?.source);
  const snapshotSource = recordValue(root?.snapshotSource);
  return Boolean(
    root
    && typeof root.regulation === "string" && root.regulation.length <= 32
    && typeof root.generatedAt === "string" && !Number.isNaN(Date.parse(root.generatedAt))
    && typeof root.retrievedAt === "string" && !Number.isNaN(Date.parse(root.retrievedAt))
    && typeof root.stale === "boolean"
    && dateRange
    && typeof dateRange.from === "string"
    && typeof dateRange.to === "string"
    && source
    && source.label === "LabMaus"
    && source.url === LABMAUS_TOURNAMENTS_URL
    && snapshotSource
    && snapshotSource.label === "VS Recorder"
    && typeof snapshotSource.url === "string"
    && snapshotSource.url.startsWith("https://github.com/Pocolip/vs-recorder/blob/")
    && archive
    && typeof archive.sourceFile === "string" && /^tournamentTeams-reg[a-z]+-[a-z]+\.json$/i.test(archive.sourceFile)
    && typeof archive.sourceRevision === "string" && /^[a-f0-9]{40}$/i.test(archive.sourceRevision)
    && (archive.storage === "bundled" || archive.storage === "persisted")
    && typeof archive.checkedAt === "string" && !Number.isNaN(Date.parse(archive.checkedAt))
    && Array.isArray(root.tournaments) && root.tournaments.length > 0 && root.tournaments.length <= MAX_TOURNAMENTS
    && root.tournaments.every((entry) => {
      const tournament = recordValue(entry);
      return tournament
        && typeof tournament.id === "string"
        && typeof tournament.name === "string" && tournament.name.length > 0 && tournament.name.length <= MAX_TEXT_LENGTH
        && Array.isArray(tournament.teams) && tournament.teams.length > 0 && tournament.teams.length <= MAX_TEAMS_PER_TOURNAMENT
        && tournament.teams.every((teamValue) => {
          const team = recordValue(teamValue);
          return team
            && typeof team.id === "string"
            && typeof team.playerName === "string"
            && (team.placement === null || numericPlacement(team.placement) === team.placement)
            && typeof team.record === "string"
            && Array.isArray(team.pokemon)
            && team.pokemon.length === 6
            && team.pokemon.every((species) => typeof species === "string" && species.length > 0 && species.length <= 64)
            && typeof team.pokepasteUrl === "string"
            && normalizePokepasteUrl(team.pokepasteUrl) === team.pokepasteUrl;
        });
    }),
  );
}

export function findTournamentScoutingTeam(response: TournamentScoutingResponse, teamId: string) {
  for (const tournament of response.tournaments) {
    const team = tournament.teams.find((entry) => entry.id === teamId);
    if (team) return { team, tournamentName: tournament.name };
  }
  return null;
}

export function isTournamentScoutingRefreshResponse(value: unknown): value is TournamentScoutingRefreshResponse {
  const root = recordValue(value);
  return Boolean(
    root
    && (root.status === "updated" || root.status === "current")
    && typeof root.message === "string"
    && isTournamentScoutingResponse(root.data),
  );
}
