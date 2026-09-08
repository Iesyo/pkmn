import { buildMostUsedOpponentMetaEstimate, type OpponentMetaEstimate } from "@/lib/opponent-meta-presets";
import { parseShowdownPaste } from "@/lib/paste";
import { DEFAULT_BATTLE_MECHANICS, serializeShowdownPaste } from "@/lib/team-builder";
import { findTournamentScoutingTeam } from "@/lib/tournament-scouting-snapshot";
import type { PokemonSet } from "@/lib/types";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 2_048;
const MAX_PASTE_BYTES = 64 * 1_024;
const MAX_BATTLE_DATA_BYTES = 512 * 1_024;
const POKEPASTE_TIMEOUT_MS = 12_000;
const BATTLE_DATA_TIMEOUT_MS = 10_000;
const BATTLE_DATA_CACHE_MS = 6 * 60 * 60 * 1_000;
const BATTLE_DATA_BASE_URL = "https://championsbattledata.com/api/battle/Doubles";

type EstimateCacheEntry = {
  value: OpponentMetaEstimate | null;
  expiresAt: number;
};

const estimateCache = new Map<string, EstimateCacheEntry>();

function errorResponse(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

async function readBoundedPaste(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste es demasiado grande");
  }
  const paste = await response.text();
  if (new TextEncoder().encode(paste).byteLength > MAX_PASTE_BYTES) {
    throw new Error("El PokéPaste es demasiado grande");
  }
  const normalized = paste.replace(/\r\n?/g, "\n").trim();
  return { paste: normalized, pokemon: parseShowdownPaste(normalized) };
}

function speciesId(species: string) {
  const normalized = species.toLowerCase().replace(/[^a-z0-9]+/g, "");
  return normalized.length > 0 && normalized.length <= 80 ? normalized : null;
}

async function readBoundedJson(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BATTLE_DATA_BYTES) {
    throw new Error("La respuesta de Battle Data es demasiado grande");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_BATTLE_DATA_BYTES) {
    throw new Error("La respuesta de Battle Data es demasiado grande");
  }
  return JSON.parse(body) as unknown;
}

async function loadMostUsedEstimate(species: string) {
  const id = speciesId(species);
  if (!id) return null;

  const now = Date.now();
  const cached = estimateCache.get(id);
  if (cached && cached.expiresAt > now) return cached.value;

  try {
    const response = await fetch(`${BATTLE_DATA_BASE_URL}/${encodeURIComponent(id)}`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(BATTLE_DATA_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`Battle Data respondió ${response.status}`);
    const value = buildMostUsedOpponentMetaEstimate(await readBoundedJson(response));
    estimateCache.set(id, { value, expiresAt: now + BATTLE_DATA_CACHE_MS });
    return value;
  } catch (error) {
    console.warn("Failed to estimate missing tournament set data", { species, error });
    return null;
  }
}

async function fillMissingMetaData(pokemon: PokemonSet[]) {
  const estimates = await Promise.all(pokemon.map((set) => {
    if (set.nature && set.evs) return Promise.resolve(null);
    return loadMostUsedEstimate(set.species);
  }));
  let nature = 0;
  let statPoints = 0;
  const enriched = pokemon.map((set, index) => {
    const estimate = estimates[index];
    if (!estimate) return set;
    const nextNature = set.nature || estimate.nature;
    const nextEvs = set.evs || estimate.evs;
    if (!set.nature && nextNature) nature += 1;
    if (!set.evs && nextEvs) statPoints += 1;
    return { ...set, nature: nextNature, evs: nextEvs };
  });
  return { pokemon: enriched, estimates: { nature, statPoints } };
}

export async function POST(request: Request) {
  const requestLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(requestLength) && requestLength > MAX_REQUEST_BYTES) {
    return errorResponse("La solicitud es demasiado grande.", 413);
  }

  let teamId = "";
  try {
    const payload = (await request.json()) as { teamId?: unknown };
    teamId = typeof payload.teamId === "string" ? payload.teamId : "";
  } catch {
    return errorResponse("La solicitud de importación no es válida.", 400);
  }

  const selected = findTournamentScoutingTeam(teamId);
  if (!selected) return errorResponse("No encontramos ese equipo en el snapshot de torneos.", 404);

  try {
    const upstream = await fetch(`${selected.team.pokepasteUrl}/raw`, {
      headers: { accept: "text/plain" },
      redirect: "manual",
      signal: AbortSignal.timeout(POKEPASTE_TIMEOUT_MS),
    });
    if (upstream.status >= 300 && upstream.status < 400) {
      throw new Error("PokéPaste intentó redirigir la solicitud");
    }
    if (!upstream.ok) throw new Error(`PokéPaste respondió ${upstream.status}`);

    const imported = await readBoundedPaste(upstream);
    const enriched = await fillMissingMetaData(imported.pokemon);
    const estimatedTotal = enriched.estimates.nature + enriched.estimates.statPoints;
    const paste = estimatedTotal > 0
      ? serializeShowdownPaste(enriched.pokemon, [...DEFAULT_BATTLE_MECHANICS])
      : imported.paste;
    return Response.json({
      paste,
      estimates: enriched.estimates,
      team: {
        id: selected.team.id,
        playerName: selected.team.playerName,
        tournamentName: selected.tournamentName,
      },
    }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    console.error("Failed to import a tournament PokéPaste", { teamId, error });
    return errorResponse("No pudimos descargar ese PokéPaste. Inténtalo nuevamente.", 502);
  }
}
