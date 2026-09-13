import { listScoutingPasteDetails } from "@/db/scouting-pastes";
import { CHAMPIONS_REGULATION } from "@/lib/champions-regulation.mjs";
import { parseShowdownPaste } from "@/lib/paste";
import {
  MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES,
  WAR_ROOM_PASTE_EVIDENCE_SCHEMA_VERSION,
  normalizeWarRoomPasteEvidenceCandidates,
  pasteEvidenceSpeciesKey,
  type WarRoomPasteEvidenceCandidate,
  type WarRoomPasteEvidenceTeam,
  type WarRoomPasteSourceTier,
} from "@/lib/war-room-paste-evidence";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_PASTE_BYTES = 64 * 1024;
const PASTE_TIMEOUT_MS = 12_000;
const PASTE_CACHE_MS = 6 * 60 * 60 * 1_000;
const MAX_CACHE_ENTRIES = 128;
const FETCH_CONCURRENCY = 6;

type PasteCacheEntry = {
  paste: string;
  expiresAt: number;
};

const pasteCache = new Map<string, PasteCacheEntry>();

function errorResponse(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

async function readBoundedText(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste supera el límite seguro");
  }
  const paste = (await response.text()).replace(/\r\n?/g, "\n").trim();
  if (new TextEncoder().encode(paste).byteLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste supera el límite seguro");
  }
  return paste;
}

function trimPasteCache(now: number) {
  for (const [url, entry] of pasteCache) {
    if (entry.expiresAt <= now) pasteCache.delete(url);
  }
  while (pasteCache.size >= MAX_CACHE_ENTRIES) {
    const oldest = pasteCache.keys().next().value;
    if (typeof oldest !== "string") break;
    pasteCache.delete(oldest);
  }
}

async function fetchPaste(url: string) {
  const now = Date.now();
  const cached = pasteCache.get(url);
  if (cached && cached.expiresAt > now) return cached.paste;
  const response = await fetch(`${url}/raw`, {
    headers: { accept: "text/plain" },
    redirect: "manual",
    signal: AbortSignal.timeout(PASTE_TIMEOUT_MS),
  });
  if (response.status >= 300 && response.status < 400) {
    throw new Error("PokéPaste intentó redirigir la solicitud");
  }
  if (!response.ok) throw new Error(`PokéPaste respondió ${response.status}`);
  const paste = await readBoundedText(response);
  trimPasteCache(now);
  pasteCache.set(url, { paste, expiresAt: now + PASTE_CACHE_MS });
  return paste;
}

function normalizedRoster(values: readonly string[]) {
  return values.map(pasteEvidenceSpeciesKey).filter(Boolean).sort().join("|");
}

function numericRank(rank: string) {
  const match = rank.match(/\d+/);
  const parsed = match ? Number(match[0]) : Number.NaN;
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function sourceTier(candidate: WarRoomPasteEvidenceCandidate, sourceText: string): WarRoomPasteSourceTier {
  if (
    candidate.source === "tournament"
    || (candidate.source === "vgcpastes" && Boolean(candidate.rank) && Boolean(candidate.tournament) && candidate.tournament !== "-")
    || /(?:^|\b)(?:tournament|torneo|labmaus)(?:\b|$)/i.test(sourceText)
  ) {
    return "tournament";
  }
  if (candidate.source === "vgcpastes" || /vgc\s*pastes?/i.test(sourceText)) return "curated";
  return "collection";
}

function evidenceQuality(tier: WarRoomPasteSourceTier, rank: string) {
  const base = tier === "tournament" ? 90 : tier === "curated" ? 78 : 65;
  const placement = numericRank(rank);
  const bonus = placement === null ? 0 : Math.max(0, 11 - Math.min(placement, 11));
  return Math.min(100, base + bonus);
}

function safeSourceUrl(value: string | null | undefined) {
  if (!value || value.length > 500) return "";
  try {
    const url = new URL(value);
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) return "";
    return url.toString();
  } catch {
    return "";
  }
}

async function mapConcurrent<T, R>(items: readonly T[], concurrency: number, task: (item: T) => Promise<R>) {
  const results: Array<PromiseSettledResult<R> | undefined> = Array.from({ length: items.length });
  let cursor = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      try {
        results[index] = { status: "fulfilled", value: await task(items[index]) };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  });
  await Promise.all(workers);
  return results.filter((entry): entry is PromiseSettledResult<R> => Boolean(entry));
}

function pasteSignature(team: WarRoomPasteEvidenceTeam) {
  return team.sets.map((set) => [
    pasteEvidenceSpeciesKey(set.species),
    set.item.toLowerCase(),
    set.ability.toLowerCase(),
    set.nature.toLowerCase(),
    set.evs.toLowerCase(),
    ...set.moves.map((move) => move.name.toLowerCase()).sort(),
  ].join("|")).sort().join("::");
}

export async function POST(request: Request) {
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
    return errorResponse("La solicitud de evidencia es demasiado grande.", 413);
  }

  let candidates: WarRoomPasteEvidenceCandidate[] | null = null;
  try {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_REQUEST_BYTES) {
      return errorResponse("La solicitud de evidencia es demasiado grande.", 413);
    }
    const payload = JSON.parse(raw) as { candidates?: unknown };
    candidates = normalizeWarRoomPasteEvidenceCandidates(payload.candidates);
  } catch {
    return errorResponse("La solicitud de evidencia no es válida.", 400);
  }
  if (!candidates || !candidates.length) {
    return errorResponse(`Envía entre 1 y ${MAX_WAR_ROOM_PASTE_EVIDENCE_CANDIDATES} referencias de paste válidas.`, 400);
  }

  const savedIds = new Set(candidates.map((candidate) => candidate.savedPasteId).filter(Boolean));
  const savedPastes = savedIds.size
    ? await listScoutingPasteDetails().catch((error) => {
      console.warn("War Room paste evidence is continuing without saved pastes", error);
      return [];
    })
    : [];
  const savedById = new Map(savedPastes.filter((paste) => savedIds.has(paste.id)).map((paste) => [paste.id, paste]));

  const settled = await mapConcurrent(candidates, FETCH_CONCURRENCY, async (candidate): Promise<WarRoomPasteEvidenceTeam> => {
    const saved = candidate.savedPasteId ? savedById.get(candidate.savedPasteId) : null;
    if (candidate.savedPasteId && !saved) throw new Error("El paste guardado ya no existe");
    const paste = saved?.paste ?? await fetchPaste(candidate.pokepasteUrl);
    const sets = parseShowdownPaste(paste);
    if (normalizedRoster(sets.map((set) => set.species)) !== normalizedRoster(candidate.pokemon)) {
      throw new Error("La plantilla publicada ya no coincide con el roster indexado");
    }
    const sourceText = `${saved?.sourceLabel ?? ""} ${saved?.name ?? ""} ${candidate.tournament}`;
    const tier = sourceTier(candidate, sourceText);
    const rank = candidate.rank;
    return {
      ...candidate,
      sourceTier: tier,
      sourceLabel: saved?.sourceLabel || (candidate.source === "vgcpastes" && tier === "tournament" ? "VGCPastes · torneo" : tier === "tournament" ? "Paste de torneo" : tier === "curated" ? "VGCPastes" : "Mis pastes"),
      sourceUrl: safeSourceUrl(saved?.sourceUrl) || candidate.pokepasteUrl,
      quality: evidenceQuality(tier, rank),
      sets,
    };
  });

  const bySignature = new Map<string, WarRoomPasteEvidenceTeam>();
  for (const result of settled) {
    if (result.status !== "fulfilled") continue;
    const signature = pasteSignature(result.value);
    const current = bySignature.get(signature);
    if (!current || result.value.quality > current.quality) bySignature.set(signature, result.value);
  }
  const teams = [...bySignature.values()].sort((left, right) => right.quality - left.quality || left.id.localeCompare(right.id));
  const failed = candidates.length - teams.length;

  return Response.json({
    schemaVersion: WAR_ROOM_PASTE_EVIDENCE_SCHEMA_VERSION,
    regulation: CHAMPIONS_REGULATION,
    fetchedAt: new Date().toISOString(),
    requested: candidates.length,
    loaded: teams.length,
    failed,
    teams,
  }, { headers: { "cache-control": "no-store" } });
}
