// Roku, revisión del cuarto corte de COL-102, 26 sep: el servidor no puede
// resolver "revisado" a partir de lo que el navegador mande en el cuerpo de
// la petición -eso es exactamente lo que permitía guardar sin `issues`
// simplemente omitiéndolas. Esto llama al mismo procesador local de
// Champions que ya usa `app/api/champions-jobs/[...path]/route.ts`
// (mismo loopback, mismo patrón de fetch) para traer el replay canónico
// del job y usarlo como fuente de verdad en el servidor, no lo que llegó
// del cliente.

const CHAMPIONS_JOBS_LOOPBACK = "http://127.0.0.1:8770";
const JOB_ID_PATTERN = /^[a-f0-9]{16}$/;

export class ChampionsJobFetchError extends Error {}

export async function fetchCanonicalChampionsReplay(jobId: string, replayNumber: number): Promise<unknown> {
  if (!JOB_ID_PATTERN.test(jobId)) {
    throw new ChampionsJobFetchError("El identificador del job de Champions no es válido.");
  }
  if (!Number.isInteger(replayNumber) || replayNumber < 1) {
    throw new ChampionsJobFetchError("El número de replay de Champions no es válido.");
  }

  let response: Response;
  try {
    response = await fetch(`${CHAMPIONS_JOBS_LOOPBACK}/jobs/${jobId}/replays/${replayNumber}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new ChampionsJobFetchError(
      "El procesador local de Champions no respondió; no pudimos verificar el replay en servidor.",
    );
  }
  if (!response.ok) {
    throw new ChampionsJobFetchError(
      `No encontramos el replay ${replayNumber} del job ${jobId} en el procesador local (status ${response.status}).`,
    );
  }

  let payload: { replay?: unknown };
  try {
    payload = (await response.json()) as { replay?: unknown };
  } catch {
    throw new ChampionsJobFetchError("El procesador local de Champions devolvió una respuesta inválida.");
  }
  if (!payload.replay) {
    throw new ChampionsJobFetchError("La respuesta del job de Champions no incluyó el replay.");
  }
  return payload.replay;
}
