import {
  buildTournamentScoutingResponse,
  type TournamentScoutingResponse,
} from "@/lib/tournament-scouting";

export const TOURNAMENT_DATA_DIRECTORY_API = "https://api.github.com/repos/Pocolip/vs-recorder/contents/frontend/src/data?ref=develop";

const TOURNAMENT_BLOB_API = "https://api.github.com/repos/Pocolip/vs-recorder/git/blobs";
const TOURNAMENT_SOURCE_DIRECTORY = "frontend/src/data";
const TOURNAMENT_SOURCE_BRANCH = "develop";
const SOURCE_FILE_PATTERN = /^tournamentTeams-reg([a-z]+)-([a-z]+)\.json$/i;
const SOURCE_REVISION_PATTERN = /^[a-f0-9]{40}$/i;
const MAX_DIRECTORY_RESPONSE_BYTES = 512 * 1_024;
const MAX_BLOB_RESPONSE_BYTES = 8 * 1_024 * 1_024;
const MAX_SOURCE_BYTES = 4 * 1_024 * 1_024;
const FETCH_TIMEOUT_MS = 25_000;

type GithubDirectoryEntry = {
  name: string;
  sha: string;
  size: number;
  type: "file";
};

function recordValue(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function byteLength(value: string) {
  return new TextEncoder().encode(value).byteLength;
}

async function readBoundedJson(response: Response, maximumBytes: number, label: string) {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maximumBytes) {
    throw new Error(`${label} excede el tamaño permitido`);
  }
  const body = await response.text();
  if (byteLength(body) > maximumBytes) throw new Error(`${label} excede el tamaño permitido`);
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error(`${label} no contiene JSON válido`);
  }
}

async function fetchGithubJson(
  url: string,
  maximumBytes: number,
  label: string,
  fetcher: typeof fetch,
) {
  let response: Response;
  try {
    response = await fetcher(url, {
      headers: {
        accept: "application/vnd.github+json",
        "user-agent": "LikeNoOneEverWas/1.0",
        "x-github-api-version": "2022-11-28",
      },
      redirect: "manual",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
  } catch (error) {
    const timedOut = error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    throw new Error(timedOut ? `${label} tardó demasiado en responder` : `${label} no está disponible`);
  }
  if (response.status >= 300 && response.status < 400) {
    throw new Error(`${label} intentó redirigir la solicitud`);
  }
  if (!response.ok) throw new Error(`${label} respondió ${response.status}`);
  return readBoundedJson(response, maximumBytes, label);
}

function alphaOrdinal(value: string) {
  return [...value.toUpperCase()].reduce((total, character) => (
    total * 26 + character.charCodeAt(0) - 64
  ), 0);
}

function sourceFileRank(filename: string) {
  const match = filename.match(SOURCE_FILE_PATTERN);
  return match ? [alphaOrdinal(match[1]), alphaOrdinal(match[2])] : null;
}

function compareSourceFiles(left: GithubDirectoryEntry, right: GithubDirectoryEntry) {
  const leftRank = sourceFileRank(left.name) ?? [0, 0];
  const rightRank = sourceFileRank(right.name) ?? [0, 0];
  return leftRank[0] - rightRank[0]
    || leftRank[1] - rightRank[1]
    || left.name.localeCompare(right.name);
}

function directoryEntry(value: unknown): GithubDirectoryEntry | null {
  const entry = recordValue(value);
  if (
    !entry
    || entry.type !== "file"
    || typeof entry.name !== "string"
    || !SOURCE_FILE_PATTERN.test(entry.name)
    || typeof entry.sha !== "string"
    || !SOURCE_REVISION_PATTERN.test(entry.sha)
    || typeof entry.size !== "number"
    || !Number.isInteger(entry.size)
    || entry.size <= 0
    || entry.size > MAX_SOURCE_BYTES
  ) return null;
  return {
    name: entry.name,
    sha: entry.sha.toLowerCase(),
    size: entry.size,
    type: "file",
  };
}

function decodeGithubBlob(payload: unknown, expected: GithubDirectoryEntry) {
  const blob = recordValue(payload);
  if (
    !blob
    || blob.encoding !== "base64"
    || typeof blob.content !== "string"
    || typeof blob.sha !== "string"
    || blob.sha.toLowerCase() !== expected.sha
    || typeof blob.size !== "number"
    || blob.size !== expected.size
  ) {
    throw new Error("GitHub devolvió un archivo de torneos inesperado");
  }

  let binary: string;
  try {
    binary = atob(blob.content.replace(/\s+/g, ""));
  } catch {
    throw new Error("GitHub devolvió un archivo de torneos incompleto");
  }
  if (!binary.length || binary.length !== expected.size || binary.length > MAX_SOURCE_BYTES) {
    throw new Error("El archivo de torneos tiene un tamaño inválido");
  }

  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
  } catch {
    throw new Error("El archivo de torneos no contiene JSON válido");
  }
}

function sourcePageUrl(filename: string) {
  return `https://github.com/Pocolip/vs-recorder/blob/${TOURNAMENT_SOURCE_BRANCH}/${TOURNAMENT_SOURCE_DIRECTORY}/${filename}`;
}

export async function fetchLatestTournamentScoutingSnapshot(
  fetcher: typeof fetch = fetch,
  checkedAt = new Date().toISOString(),
): Promise<TournamentScoutingResponse> {
  const directoryPayload = await fetchGithubJson(
    TOURNAMENT_DATA_DIRECTORY_API,
    MAX_DIRECTORY_RESPONSE_BYTES,
    "El catálogo de VS Recorder",
    fetcher,
  );
  if (!Array.isArray(directoryPayload)) {
    throw new Error("VS Recorder devolvió un catálogo de archivos inesperado");
  }

  const latest = directoryPayload
    .map(directoryEntry)
    .filter((entry): entry is GithubDirectoryEntry => Boolean(entry))
    .sort(compareSourceFiles)
    .at(-1);
  if (!latest) throw new Error("VS Recorder no publicó un archivo de torneos compatible");

  const blobPayload = await fetchGithubJson(
    `${TOURNAMENT_BLOB_API}/${latest.sha}`,
    MAX_BLOB_RESPONSE_BYTES,
    `El archivo ${latest.name}`,
    fetcher,
  );
  const snapshotPayload = decodeGithubBlob(blobPayload, latest);
  return buildTournamentScoutingResponse(snapshotPayload, {
    checkedAt,
    retrievedAt: checkedAt,
    snapshotUrl: sourcePageUrl(latest.name),
    sourceFile: latest.name,
    sourceRevision: latest.sha,
    storage: "persisted",
  });
}

export function assertSafeTournamentSnapshotUpdate(
  candidate: TournamentScoutingResponse,
  previous: TournamentScoutingResponse,
) {
  if (candidate.regulation !== previous.regulation) return;
  const candidateTeams = candidate.tournaments.reduce((total, tournament) => total + tournament.teams.length, 0);
  const previousTeams = previous.tournaments.reduce((total, tournament) => total + tournament.teams.length, 0);
  if (previousTeams >= 20 && candidateTeams < Math.ceil(previousTeams * 0.5)) {
    throw new Error("La actualización perdió demasiados equipos y fue rechazada por seguridad");
  }
  if (
    !Number.isNaN(Date.parse(candidate.generatedAt))
    && !Number.isNaN(Date.parse(previous.generatedAt))
    && Date.parse(candidate.generatedAt) < Date.parse(previous.generatedAt)
  ) {
    throw new Error("La actualización es anterior al snapshot instalado y fue rechazada por seguridad");
  }
}
