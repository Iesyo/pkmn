import {
  buildOpponentMetaPresets,
  type OpponentMetaResponse,
} from "@/lib/opponent-meta-presets";
import { CHAMPIONS_REGULATION } from "@/lib/champions-regulation.mjs";

export const dynamic = "force-dynamic";

const UPSTREAM_BASE_URL = "https://championsbattledata.com/api/battle/Doubles";
const SOURCE_URL = "https://championsbattledata.com/";
const FRESH_CACHE_MS = 6 * 60 * 60 * 1_000;
const STALE_CACHE_MS = 7 * 24 * 60 * 60 * 1_000;
const MAX_RESPONSE_BYTES = 512 * 1_024;
const UPSTREAM_TIMEOUT_MS = 10_000;

type CacheEntry = {
  response: OpponentMetaResponse;
  freshUntil: number;
  staleUntil: number;
};

const responseCache = new Map<string, CacheEntry>();

function jsonResponse(response: OpponentMetaResponse, status = 200) {
  return Response.json(response, {
    status,
    headers: {
      "cache-control": "public, max-age=300, s-maxage=21600, stale-while-revalidate=86400",
    },
  });
}

function speciesId(rawSpecies: string) {
  const normalized = rawSpecies.toLowerCase().replace(/[^a-z0-9]+/g, "");
  return normalized.length > 0 && normalized.length <= 80 ? normalized : null;
}

async function readBoundedJson(response: Response) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) {
    throw new Error("La respuesta de Battle Data es demasiado grande");
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_RESPONSE_BYTES) {
    throw new Error("La respuesta de Battle Data es demasiado grande");
  }
  return JSON.parse(body) as unknown;
}

async function fetchMeta(id: string): Promise<OpponentMetaResponse> {
  const upstream = await fetch(`${UPSTREAM_BASE_URL}/${encodeURIComponent(id)}`, {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
  if (upstream.status === 404) {
    return {
      pokemon: id,
      format: "Doubles",
      regulation: CHAMPIONS_REGULATION,
      season: "Current",
      retrievedAt: new Date().toISOString(),
      stale: false,
      methodology: "marginal-frequency-composite",
      source: {
        label: "Pokémon Champions Battle Data",
        url: SOURCE_URL,
      },
      presets: [],
    };
  }
  if (!upstream.ok) throw new Error(`Battle Data respondió ${upstream.status}`);

  const payload = await readBoundedJson(upstream);
  const root = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload as Record<string, unknown>
    : {};
  const pokemon = typeof root.pokemon === "string" ? root.pokemon.trim() : "";
  const season = typeof root.season === "string" ? root.season.trim() : "Current";
  return {
    pokemon: pokemon || id,
    format: "Doubles",
    regulation: CHAMPIONS_REGULATION,
    season: season || "Current",
    retrievedAt: new Date().toISOString(),
    stale: false,
    methodology: "marginal-frequency-composite",
    source: {
      label: "Pokémon Champions Battle Data",
      url: SOURCE_URL,
    },
    presets: buildOpponentMetaPresets(payload),
  };
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ species: string }> },
) {
  const { species } = await context.params;
  const id = speciesId(species);
  if (!id) {
    return Response.json(
      { error: "Pokémon no válido." },
      { status: 400, headers: { "cache-control": "no-store" } },
    );
  }

  const now = Date.now();
  const cacheId = `${CHAMPIONS_REGULATION}:${id}`;
  const cached = responseCache.get(cacheId);
  if (cached && cached.freshUntil > now) return jsonResponse(cached.response);

  try {
    const response = await fetchMeta(id);
    responseCache.set(cacheId, {
      response,
      freshUntil: now + FRESH_CACHE_MS,
      staleUntil: now + STALE_CACHE_MS,
    });
    return jsonResponse(response);
  } catch (error) {
    if (cached && cached.staleUntil > now) {
      return jsonResponse({ ...cached.response, stale: true });
    }
    console.error("Failed to load Pokémon Champions opponent meta", error);
    return Response.json(
      { error: "No pudimos cargar el meta del rival. Puedes configurar el set manualmente." },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }
}
