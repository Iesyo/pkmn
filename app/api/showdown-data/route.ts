import {
  getStoredShowdownSnapshotBytes,
  saveStoredShowdownSnapshot,
} from "@/db/showdown-snapshot";
import { buildShowdownSnapshot } from "@/lib/showdown-snapshot-builder.mjs";

export const dynamic = "force-dynamic";

function snapshotResponse(bytes: Uint8Array) {
  return new Response(new Blob([bytes]), {
    headers: {
      "cache-control": "no-store",
      "content-type": "application/gzip",
    },
  });
}

export async function GET() {
  try {
    const bytes = await getStoredShowdownSnapshotBytes();
    return bytes ? snapshotResponse(bytes) : new Response(null, { status: 404 });
  } catch (error) {
    console.error("Failed to read persisted Showdown snapshot", error);
    return new Response(null, { status: 404 });
  }
}

export async function POST() {
  try {
    const snapshot = await buildShowdownSnapshot();
    const bytes = await saveStoredShowdownSnapshot(snapshot);
    return snapshotResponse(bytes);
  } catch (error) {
    console.error("Failed to refresh Showdown snapshot", error);
    const detail = error instanceof Error ? error.message : "Error inesperado";
    return Response.json(
      { error: `No pudimos actualizar las bases desde Pokémon Showdown. ${detail}` },
      { status: 502, headers: { "cache-control": "no-store" } },
    );
  }
}
