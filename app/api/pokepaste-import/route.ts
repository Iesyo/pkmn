import { parseShowdownPaste } from "@/lib/paste";

export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 2_048;
const MAX_PASTE_BYTES = 64 * 1_024;
const POKEPASTE_TIMEOUT_MS = 12_000;

function errorResponse(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

function normalizePokepasteUrl(value: unknown) {
  if (typeof value !== "string" || value.length > 180) return null;
  try {
    const url = new URL(value.trim());
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
  parseShowdownPaste(normalized);
  return normalized;
}

export async function POST(request: Request) {
  const requestLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(requestLength) && requestLength > MAX_REQUEST_BYTES) {
    return errorResponse("La solicitud es demasiado grande.", 413);
  }

  let pokepasteUrl: string | null = null;
  try {
    const payload = (await request.json()) as { url?: unknown };
    pokepasteUrl = normalizePokepasteUrl(payload.url);
  } catch {
    return errorResponse("La solicitud de importación no es válida.", 400);
  }

  if (!pokepasteUrl) {
    return errorResponse("Usa una URL válida de https://pokepast.es/…", 400);
  }

  try {
    const upstream = await fetch(`${pokepasteUrl}/raw`, {
      headers: { accept: "text/plain" },
      redirect: "manual",
      signal: AbortSignal.timeout(POKEPASTE_TIMEOUT_MS),
    });

    if (upstream.status >= 300 && upstream.status < 400) {
      throw new Error("PokéPaste intentó redirigir la solicitud");
    }
    if (!upstream.ok) throw new Error(`PokéPaste respondió ${upstream.status}`);

    const paste = await readBoundedPaste(upstream);
    return Response.json({ paste }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    console.error("Failed to import PokéPaste URL", { pokepasteUrl, error });
    return errorResponse("No pudimos descargar o leer ese PokéPaste.", 502);
  }
}
