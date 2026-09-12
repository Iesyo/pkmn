import {
  buildVgcPastesCsvUrl,
  getVgcPastesFormat,
  parseVgcPastesTeams,
  type VgcPastesFormat,
  type VgcPastesTeam,
} from "@/lib/vgcpastes-scouting";

const SOURCE_TIMEOUT_MS = 12_000;
const MAX_CSV_BYTES = 4 * 1024 * 1024;
const CACHE_TTL_MS = 5 * 60 * 1_000;

type FormatCacheEntry = {
  format: VgcPastesFormat;
  teams: VgcPastesTeam[];
  fetchedAt: string;
  expiresAt: number;
};

const cache = new Map<string, FormatCacheEntry>();

async function readBoundedCsv(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_CSV_BYTES) {
    throw new Error("VGCPastes devolvió un archivo demasiado grande");
  }
  const csv = await response.text();
  if (new TextEncoder().encode(csv).byteLength > MAX_CSV_BYTES) {
    throw new Error("VGCPastes devolvió un archivo demasiado grande");
  }
  return csv;
}

export function clearVgcPastesScoutingCache() {
  cache.clear();
}

export async function loadVgcPastesFormat(
  formatId: string,
  options: {
    force?: boolean;
    now?: number;
    fetcher?: typeof fetch;
  } = {},
) {
  const format = getVgcPastesFormat(formatId);
  if (!format) throw new Error("Formato de VGCPastes no soportado");

  const now = options.now ?? Date.now();
  const cached = cache.get(format.id);
  if (!options.force && cached && cached.expiresAt > now) return cached;

  const fetcher = options.fetcher ?? fetch;
  const response = await fetcher(buildVgcPastesCsvUrl(format), {
    headers: { accept: "text/csv,text/plain;q=0.9,*/*;q=0.1" },
    redirect: "manual",
    signal: AbortSignal.timeout(SOURCE_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`VGCPastes respondió ${response.status}`);

  const teams = parseVgcPastesTeams(await readBoundedCsv(response));
  const entry: FormatCacheEntry = {
    format,
    teams,
    fetchedAt: new Date(now).toISOString(),
    expiresAt: now + CACHE_TTL_MS,
  };
  cache.set(format.id, entry);
  return entry;
}
