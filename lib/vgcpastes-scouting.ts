export const VGCPASTES_SPREADSHEET_ID = "1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw";
export const VGCPASTES_WORKBOOK_URL = `https://docs.google.com/spreadsheets/d/${VGCPASTES_SPREADSHEET_ID}/htmlview`;
export const VGCPASTES_GRID_RANGE = "A:AS";

export const VGCPASTES_FORMATS = [
  { id: "champions-m-c", label: "Champions M-C", gid: "2001945654" },
  { id: "champions-m-b", label: "Champions M-B", gid: "1458357160" },
  { id: "champions-m-a", label: "Champions M-A", gid: "791705272" },
  { id: "sv-regulation-i", label: "SV Regulation I", gid: "972834435" },
] as const;

export const DEFAULT_VGCPASTES_FORMAT_ID = VGCPASTES_FORMATS[0].id;
export const DEFAULT_VGCPASTES_PAGE_SIZE = 24;
export const VGCPASTES_PAGE_SIZES = [12, 24, 48] as const;
export const MAX_VGCPASTES_POKEMON_FILTERS = 3;

const MAX_TEXT_LENGTH = 240;
const MAX_POKEMON_NAME_LENGTH = 64;
const TEAM_ID_COLUMN = 44; // AS
const POKEPASTE_COLUMN = 24; // Y
const EVS_COLUMN = 25; // Z
const REPLICA_CODE_COLUMN = 28; // AC
const DATE_SHARED_COLUMN = 29; // AD
const TOURNAMENT_COLUMN = 30; // AE
const RANK_COLUMN = 31; // AF
const SOURCE_URL_COLUMN = 32; // AG
const OWNER_COLUMN = 35; // AJ
const POKEMON_START_COLUMN = 37; // AL
const POKEMON_END_COLUMN = 42; // AQ

export type VgcPastesFormat = (typeof VGCPASTES_FORMATS)[number];

export interface VgcPastesTeam {
  id: string;
  description: string;
  playerName: string;
  owner: string;
  pokepasteUrl: string;
  hasEvs: boolean;
  replicaCode: string;
  dateShared: string;
  tournament: string;
  rank: string;
  sourceUrl: string;
  pokemon: string[];
}

export interface VgcPastesScoutingResponse {
  source: {
    label: "VGCPastes Repository";
    url: string;
  };
  fetchedAt: string;
  format: VgcPastesFormat;
  formats: VgcPastesFormat[];
  pokemonOptions: string[];
  query: {
    pokemon: string[];
    page: number;
    pageSize: number;
  };
  pagination: {
    page: number;
    pageSize: number;
    totalItems: number;
    totalPages: number;
    totalAvailable: number;
  };
  teams: VgcPastesTeam[];
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
    .slice(0, MAX_POKEMON_NAME_LENGTH);
}

export function normalizePokemonSearch(value: unknown) {
  return cleanText(value, "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .slice(0, MAX_POKEMON_NAME_LENGTH);
}

export function normalizeVgcPastesPokemonFilters(values: unknown): string[] {
  const candidates = Array.isArray(values) ? values : [values];
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const value of candidates) {
    const pokemon = cleanText(value, "").slice(0, MAX_POKEMON_NAME_LENGTH);
    const key = normalizePokemonSearch(pokemon);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    normalized.push(pokemon);
    if (normalized.length >= MAX_VGCPASTES_POKEMON_FILTERS) break;
  }

  return normalized;
}

function normalizePokepasteUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 180) return "";
  try {
    const url = new URL(value.trim());
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

function normalizeExternalUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 500) return "";
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" || url.username || url.password) return "";
    return url.toString();
  } catch {
    return "";
  }
}

function normalizeReplicaCode(value: unknown) {
  const code = cleanText(value, "");
  if (!code || /^none$/i.test(code) || code === "-") return "";
  return code.slice(0, 40);
}

export function parseCsvRows(csv: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < csv.length; index += 1) {
    const character = csv[index];
    if (quoted) {
      if (character === '"') {
        if (csv[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += character;
      }
      continue;
    }

    if (character === '"') {
      quoted = true;
    } else if (character === ",") {
      row.push(cell);
      cell = "";
    } else if (character === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (character !== "\r") {
      cell += character;
    }
  }

  if (cell.length > 0 || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

export function parseVgcPastesTeams(csv: string): VgcPastesTeam[] {
  const rows = parseCsvRows(csv);
  const headerIndex = rows.findIndex((row) => cleanText(row[0], "") === "Team ID" && cleanText(row[POKEPASTE_COLUMN], "") === "Pokepaste");
  if (headerIndex < 0) throw new Error("VGCPastes cambió el encabezado esperado");

  const teams: VgcPastesTeam[] = [];
  const seen = new Set<string>();
  for (const row of rows.slice(headerIndex + 1)) {
    const id = cleanText(row[TEAM_ID_COLUMN] || row[0], "");
    if (!id || seen.has(id)) continue;

    const pokemon = row
      .slice(POKEMON_START_COLUMN, POKEMON_END_COLUMN + 1)
      .map(normalizePokemonName)
      .filter(Boolean);
    if (pokemon.length !== 6) continue;

    const owner = cleanText(row[OWNER_COLUMN], "");
    const fullName = cleanText(row[3], "");
    seen.add(id);
    teams.push({
      id,
      description: cleanText(row[1], ""),
      playerName: fullName || owner || "Jugador sin nombre",
      owner,
      pokepasteUrl: normalizePokepasteUrl(row[POKEPASTE_COLUMN]),
      hasEvs: /^(yes|sí|si)$/i.test(cleanText(row[EVS_COLUMN], "")),
      replicaCode: normalizeReplicaCode(row[REPLICA_CODE_COLUMN]),
      dateShared: cleanText(row[DATE_SHARED_COLUMN], ""),
      tournament: cleanText(row[TOURNAMENT_COLUMN], ""),
      rank: cleanText(row[RANK_COLUMN], ""),
      sourceUrl: normalizeExternalUrl(row[SOURCE_URL_COLUMN]),
      pokemon,
    });
  }

  if (!teams.length) throw new Error("VGCPastes no contiene equipos utilizables para este formato");
  return teams;
}

export function getVgcPastesFormat(formatId: string | null | undefined): VgcPastesFormat | null {
  const normalized = cleanText(formatId, DEFAULT_VGCPASTES_FORMAT_ID).toLowerCase();
  return VGCPASTES_FORMATS.find((format) => format.id === normalized) ?? null;
}

export function buildVgcPastesSheetUrl(format: VgcPastesFormat) {
  return `${VGCPASTES_WORKBOOK_URL}?gid=${encodeURIComponent(format.gid)}#gid=${encodeURIComponent(format.gid)}`;
}

export function buildVgcPastesCsvUrl(format: VgcPastesFormat) {
  const query = new URLSearchParams({
    tqx: "out:csv",
    gid: format.gid,
    headers: "3",
    range: VGCPASTES_GRID_RANGE,
  });
  return `https://docs.google.com/spreadsheets/d/${VGCPASTES_SPREADSHEET_ID}/gviz/tq?${query.toString()}`;
}

function safePositiveInteger(value: unknown, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function buildVgcPastesScoutingResponse(
  format: VgcPastesFormat,
  teams: VgcPastesTeam[],
  options: {
    fetchedAt?: string;
    pokemon?: string | string[];
    page?: number;
    pageSize?: number;
  } = {},
): VgcPastesScoutingResponse {
  const pokemon = normalizeVgcPastesPokemonFilters(options.pokemon ?? []);
  const pokemonKeys = pokemon.map(normalizePokemonSearch);
  const requestedPageSize = safePositiveInteger(options.pageSize, DEFAULT_VGCPASTES_PAGE_SIZE);
  const pageSize = VGCPASTES_PAGE_SIZES.includes(requestedPageSize as (typeof VGCPASTES_PAGE_SIZES)[number])
    ? requestedPageSize
    : DEFAULT_VGCPASTES_PAGE_SIZE;
  const filtered = pokemonKeys.length
    ? teams.filter((team) => {
      const teamPokemon = new Set(team.pokemon.map(normalizePokemonSearch));
      return pokemonKeys.every((key) => teamPokemon.has(key));
    })
    : teams;
  const totalPages = Math.ceil(filtered.length / pageSize);
  const requestedPage = safePositiveInteger(options.page, 1);
  const page = totalPages > 0 ? Math.min(requestedPage, totalPages) : 1;
  const offset = (page - 1) * pageSize;
  const pokemonOptions = [...new Set(teams.flatMap((team) => team.pokemon))]
    .sort((left, right) => left.localeCompare(right));

  return {
    source: { label: "VGCPastes Repository", url: buildVgcPastesSheetUrl(format) },
    fetchedAt: options.fetchedAt ?? new Date().toISOString(),
    format,
    formats: [...VGCPASTES_FORMATS],
    pokemonOptions,
    query: { pokemon, page, pageSize },
    pagination: {
      page,
      pageSize,
      totalItems: filtered.length,
      totalPages,
      totalAvailable: teams.length,
    },
    teams: filtered.slice(offset, offset + pageSize),
  };
}

function recordValue(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function isKnownFormat(value: unknown) {
  const format = recordValue(value);
  return Boolean(
    format
    && typeof format.id === "string"
    && typeof format.label === "string"
    && typeof format.gid === "string"
    && VGCPASTES_FORMATS.some((entry) => entry.id === format.id && entry.label === format.label && entry.gid === format.gid),
  );
}

function isValidPokemonFilterList(value: unknown) {
  if (!Array.isArray(value) || value.length > MAX_VGCPASTES_POKEMON_FILTERS) return false;
  if (!value.every((entry) => typeof entry === "string" && entry.length > 0 && entry.length <= MAX_POKEMON_NAME_LENGTH)) return false;
  const keys = value.map(normalizePokemonSearch);
  return keys.every(Boolean) && new Set(keys).size === keys.length;
}

export function isVgcPastesScoutingResponse(value: unknown): value is VgcPastesScoutingResponse {
  const root = recordValue(value);
  const source = recordValue(root?.source);
  const format = recordValue(root?.format);
  const pagination = recordValue(root?.pagination);
  const query = recordValue(root?.query);
  const expectedSourceUrl = format && typeof format.id === "string"
    ? VGCPASTES_FORMATS.find((entry) => entry.id === format.id)
    : null;
  return Boolean(
    root
    && source?.label === "VGCPastes Repository"
    && expectedSourceUrl
    && source.url === buildVgcPastesSheetUrl(expectedSourceUrl)
    && typeof root.fetchedAt === "string"
    && !Number.isNaN(Date.parse(root.fetchedAt))
    && isKnownFormat(format)
    && Array.isArray(root.formats)
    && root.formats.length === VGCPASTES_FORMATS.length
    && root.formats.every(isKnownFormat)
    && Array.isArray(root.pokemonOptions)
    && root.pokemonOptions.every((entry) => typeof entry === "string" && entry.length > 0 && entry.length <= MAX_POKEMON_NAME_LENGTH)
    && query
    && isValidPokemonFilterList(query.pokemon)
    && typeof query.page === "number"
    && typeof query.pageSize === "number"
    && VGCPASTES_PAGE_SIZES.includes(query.pageSize as (typeof VGCPASTES_PAGE_SIZES)[number])
    && pagination
    && typeof pagination.page === "number"
    && typeof pagination.pageSize === "number"
    && pagination.pageSize === query.pageSize
    && typeof pagination.totalItems === "number"
    && typeof pagination.totalPages === "number"
    && typeof pagination.totalAvailable === "number"
    && Array.isArray(root.teams)
    && root.teams.length <= pagination.pageSize
    && root.teams.every((entry) => {
      const team = recordValue(entry);
      return team
        && typeof team.id === "string"
        && typeof team.description === "string"
        && typeof team.playerName === "string"
        && typeof team.owner === "string"
        && typeof team.pokepasteUrl === "string"
        && typeof team.hasEvs === "boolean"
        && typeof team.replicaCode === "string"
        && typeof team.dateShared === "string"
        && typeof team.tournament === "string"
        && typeof team.rank === "string"
        && typeof team.sourceUrl === "string"
        && Array.isArray(team.pokemon)
        && team.pokemon.length === 6
        && team.pokemon.every((species) => typeof species === "string" && species.length > 0 && species.length <= MAX_POKEMON_NAME_LENGTH);
    }),
  );
}
