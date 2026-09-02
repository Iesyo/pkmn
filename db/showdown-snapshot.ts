import type { ShowdownSnapshot } from "@/lib/showdown-data";
import { getDatabase } from "./raw";

const SNAPSHOT_KEY = "showdown_snapshot_gzip_base64_v1";
const MAX_D1_VALUE_BYTES = 1_900_000;
const BASE64_CHUNK_SIZE = 0x8000;

function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK_SIZE) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK_SIZE));
  }
  return btoa(binary);
}

function base64ToBytes(value: string) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function gzipSnapshot(snapshot: ShowdownSnapshot) {
  const stream = new Blob([`${JSON.stringify(snapshot)}\n`])
    .stream()
    .pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function getStoredShowdownSnapshotBytes() {
  const db = await getDatabase();
  const row = await db
    .prepare("SELECT value FROM app_settings WHERE key = ?")
    .bind(SNAPSHOT_KEY)
    .first<{ value: string }>();
  if (!row?.value) return null;
  try {
    return base64ToBytes(row.value);
  } catch (error) {
    console.warn("Ignoring invalid persisted Showdown snapshot", error);
    return null;
  }
}

export async function saveStoredShowdownSnapshot(snapshot: ShowdownSnapshot) {
  const bytes = await gzipSnapshot(snapshot);
  const encoded = bytesToBase64(bytes);
  if (new TextEncoder().encode(encoded).byteLength > MAX_D1_VALUE_BYTES) {
    throw new Error("El snapshot actualizado de Showdown excede el tamaño seguro para D1.");
  }

  const db = await getDatabase();
  await db
    .prepare(
      `INSERT INTO app_settings (key, value, updated_at)
       VALUES (?, ?, CURRENT_TIMESTAMP)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP`,
    )
    .bind(SNAPSHOT_KEY, encoded)
    .run();
  return bytes;
}
