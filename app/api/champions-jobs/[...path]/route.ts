export const dynamic = "force-dynamic";

const CHAMPIONS_JOBS_LOOPBACK = "http://127.0.0.1:8770";
const ALLOWED_PATH = /^(?:health|jobs(?:\/[a-f0-9]{16}(?:\/chunks|\/retry|\/protect|\/cleanup|\/diagnostics|\/replays\/[1-9][0-9]*)?)?)$/;

async function forward(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const relativePath = path.join("/");
  if (!ALLOWED_PATH.test(relativePath)) {
    return Response.json({ detail: "Ruta de la cola Champions no permitida." }, { status: 404 });
  }

  const sourceUrl = new URL(request.url);
  const upstreamUrl = new URL(`${CHAMPIONS_JOBS_LOOPBACK}/${relativePath}`);
  upstreamUrl.search = sourceUrl.search;
  const method = request.method.toUpperCase();
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  let body: BodyInit | undefined;
  if (method === "PUT") body = await request.arrayBuffer();
  else if (!["GET", "HEAD", "DELETE"].includes(method)) body = await request.text();

  try {
    const upstream = await fetch(upstreamUrl, {
      method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(method === "PUT" ? 120_000 : 30_000),
    });
    const responseHeaders: Record<string, string> = {
      "content-type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8",
      "cache-control": "no-store",
    };
    const contentDisposition = upstream.headers.get("content-disposition");
    if (contentDisposition) responseHeaders["content-disposition"] = contentDisposition;
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Champions video queue loopback proxy failed", { relativePath, error });
    return Response.json(
      {
        detail: "El procesador local de Champions no responde en la ROG. Reinicia la aplicación para levantarlo.",
      },
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

export async function PUT(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context);
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  return forward(request, context);
}
