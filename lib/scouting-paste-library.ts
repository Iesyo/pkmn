import { getVgcPastesScoutingSpeciesIdentity } from "@/lib/vgcpastes-scouting-search";
import { normalizePokemonSearch } from "@/lib/vgcpastes-scouting";

export const MAX_SCOUTING_PASTE_POKEMON_FILTERS = 3;
export const DEFAULT_SCOUTING_PASTE_PAGE_SIZE = 24;
export const SCOUTING_PASTE_PAGE_SIZES = [12, 24, 48] as const;

const MAX_TEXT = 160;

export interface ScoutingPasteSummary {
  id: string;
  name: string;
  creator: string;
  format: string;
  sourceUrl: string;
  sourceLabel: string;
  notes: string;
  createdAt: string;
  updatedAt: string;
  pokemon: string[];
}

export interface ScoutingPasteDetail extends ScoutingPasteSummary {
  versionId: string;
  paste: string;
}

export interface ScoutingPasteLibraryResponse {
  formats: string[];
  pokemonOptions: string[];
  query: {
    pokemon: string[];
    format: string;
    search: string;
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
  items: ScoutingPasteSummary[];
}

function cleanText(value: unknown, max = MAX_TEXT) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

function searchKey(value: unknown) {
  return cleanText(value, 500)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

export function scoutingSpeciesIdentity(value: unknown) {
  return getVgcPastesScoutingSpeciesIdentity(cleanText(value, 80));
}

export function normalizeScoutingPastePokemonFilters(values: unknown): string[] {
  const candidates = Array.isArray(values) ? values : [values];
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of candidates) {
    const species = scoutingSpeciesIdentity(value);
    const key = normalizePokemonSearch(species);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(species);
    if (result.length >= MAX_SCOUTING_PASTE_POKEMON_FILTERS) break;
  }
  return result;
}

function positiveInteger(value: unknown, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export function buildScoutingPasteLibraryResponse(
  records: ScoutingPasteSummary[],
  options: {
    pokemon?: string | string[];
    format?: string;
    search?: string;
    page?: number;
    pageSize?: number;
  } = {},
): ScoutingPasteLibraryResponse {
  const pokemon = normalizeScoutingPastePokemonFilters(options.pokemon ?? []);
  const pokemonKeys = pokemon.map(normalizePokemonSearch);
  const format = cleanText(options.format, 80);
  const formatKey = searchKey(format);
  const search = cleanText(options.search, 100);
  const queryKey = searchKey(search);
  const requestedPageSize = positiveInteger(options.pageSize, DEFAULT_SCOUTING_PASTE_PAGE_SIZE);
  const pageSize = SCOUTING_PASTE_PAGE_SIZES.includes(requestedPageSize as (typeof SCOUTING_PASTE_PAGE_SIZES)[number])
    ? requestedPageSize
    : DEFAULT_SCOUTING_PASTE_PAGE_SIZE;

  const filtered = records.filter((record) => {
    if (formatKey && searchKey(record.format) !== formatKey) return false;
    if (pokemonKeys.length) {
      const teamKeys = new Set(record.pokemon.map((species) => normalizePokemonSearch(scoutingSpeciesIdentity(species))));
      if (!pokemonKeys.every((key) => teamKeys.has(key))) return false;
    }
    if (queryKey) {
      const haystack = searchKey(`${record.name} ${record.creator} ${record.sourceLabel} ${record.notes}`);
      if (!haystack.includes(queryKey)) return false;
    }
    return true;
  });

  const totalPages = Math.ceil(filtered.length / pageSize);
  const requestedPage = positiveInteger(options.page, 1);
  const page = totalPages > 0 ? Math.min(requestedPage, totalPages) : 1;
  const offset = (page - 1) * pageSize;
  const formats = [...new Set(records.map((record) => cleanText(record.format, 80)).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));
  const pokemonOptions = [...new Set(records.flatMap((record) => record.pokemon.map(scoutingSpeciesIdentity)).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right));

  return {
    formats,
    pokemonOptions,
    query: { pokemon, format, search, page, pageSize },
    pagination: {
      page,
      pageSize,
      totalItems: filtered.length,
      totalPages,
      totalAvailable: records.length,
    },
    items: filtered.slice(offset, offset + pageSize),
  };
}

function isSummary(value: unknown): value is ScoutingPasteSummary {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === "string"
    && typeof item.name === "string"
    && typeof item.creator === "string"
    && typeof item.format === "string"
    && typeof item.sourceUrl === "string"
    && typeof item.sourceLabel === "string"
    && typeof item.notes === "string"
    && typeof item.createdAt === "string"
    && typeof item.updatedAt === "string"
    && Array.isArray(item.pokemon)
    && item.pokemon.every((species) => typeof species === "string");
}

export function isScoutingPasteLibraryResponse(value: unknown): value is ScoutingPasteLibraryResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const root = value as Record<string, unknown>;
  const query = root.query as Record<string, unknown> | undefined;
  const pagination = root.pagination as Record<string, unknown> | undefined;
  return Array.isArray(root.formats)
    && root.formats.every((entry) => typeof entry === "string")
    && Array.isArray(root.pokemonOptions)
    && root.pokemonOptions.every((entry) => typeof entry === "string")
    && Boolean(query)
    && Array.isArray(query?.pokemon)
    && typeof query?.format === "string"
    && typeof query?.search === "string"
    && typeof query?.page === "number"
    && typeof query?.pageSize === "number"
    && Boolean(pagination)
    && typeof pagination?.page === "number"
    && typeof pagination?.pageSize === "number"
    && typeof pagination?.totalItems === "number"
    && typeof pagination?.totalPages === "number"
    && typeof pagination?.totalAvailable === "number"
    && Array.isArray(root.items)
    && root.items.every(isSummary);
}
