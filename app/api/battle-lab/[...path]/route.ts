export const dynamic = "force-dynamic";

const BATTLE_LAB_LOOPBACK = "http://127.0.0.1:8765";
const ALLOWED_PATH = /^(?:health|model-info|sparring(?:\/[a-f0-9]{16}(?:\/(?:team-preview|choice))?)?)$/;

async function forward(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const relativePath = path.join("/");
  if (!ALLOWED_PATH.test(relativePath)) {
    return Response.json({ detail: "Ruta de Battle Lab no permitida." }, { status: 404 });
  }

  const method = request.method.toUpperCase();
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  try {
    const upstream = await fetch(`${BATTLE_LAB_LOOPBACK}/${relativePath}`, {
      method,
      headers,
      body: method === "GET" || method === "HEAD" ? undefined : await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(relativePath === "model-info" ? 300_000 : 30_000),
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    console.error("Battle Lab loopback proxy failed", { relativePath, error });
    return Response.json(
      { detail: "Battle Lab local no responde en 127.0.0.1:8765." },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context);
}
