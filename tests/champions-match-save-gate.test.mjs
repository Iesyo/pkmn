import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const baseInput = {
  teamVersionId: "does-not-matter-for-this-check",
  result: "win",
  origin: "champions",
  championsJobId: "0123456789abcdef",
  championsReplayNumber: 1,
};

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function mockChampionsJobsLoopback(t, respond) {
  t.mock.method(globalThis, "fetch", async (url, init) => {
    const href = typeof url === "string" ? url : url.toString();
    if (!href.startsWith("http://127.0.0.1:8770/")) {
      throw new Error(`unexpected fetch in test: ${href}`);
    }
    return respond(href, init);
  });
}

// Roku, revisión del cuarto corte, 26 sep: el servidor sólo miraba el JSON
// que mandaba el navegador -`normalizeReplayIssues` da `[]` si el arreglo
// falta, así que una petición con el mismo log y sin `issues` evitaba la
// compuerta por completo. createMatch() ahora vuelve a buscar el replay
// canónico él mismo (championsJobId + championsReplayNumber, verificables
// en servidor) y usa ESE documento -nunca el que mandó el cliente- tanto
// para la compuerta como para lo que se persiste.

test("createMatch fetches the canonical replay from the job and rejects on its blocking issue, ignoring the client's copy", async (t) => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");
  mockChampionsJobsLoopback(t, (href) => {
    assert.equal(href, "http://127.0.0.1:8770/jobs/0123456789abcdef/replays/1");
    return jsonResponse({
      replay: {
        log: "|start\n|win|IesYo",
        issues: [{ severity: "blocking", message: "0 imposible sin faint confirmado." }],
      },
    });
  });

  await assert.rejects(
    () =>
      createMatch({
        ...baseInput,
        // El cliente manda una copia "limpia", sin issues -no debe importar:
        // el servidor descarta esto y usa lo que él mismo buscó en el job.
        replayArtifact: { log: "|start\n|win|IesYo", issues: [] },
      }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /bloqueantes/);
      return true;
    },
  );
});

test("createMatch requires championsJobId and championsReplayNumber for a champions-origin match", async () => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");

  await assert.rejects(
    () => createMatch({ teamVersionId: "x", result: "win", origin: "champions" }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /no puede verificar/);
      return true;
    },
  );
});

test("createMatch blocks the save when the canonical job replay can't be fetched", async (t) => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");
  mockChampionsJobsLoopback(t, () => jsonResponse({ detail: "not found" }, 404));

  await assert.rejects(
    () => createMatch({ ...baseInput, replayArtifact: { log: "|start\n|win|IesYo" } }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /No encontramos/);
      return true;
    },
  );
});

test("createMatch blocks the save when the local Champions processor is unreachable", async (t) => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");
  t.mock.method(globalThis, "fetch", async () => {
    throw new Error("ECONNREFUSED");
  });

  await assert.rejects(
    () => createMatch({ ...baseInput, replayArtifact: { log: "|start\n|win|IesYo" } }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /no respondió/);
      return true;
    },
  );
});

test("createMatch proceeds past validation when the canonical replay has no blocking issues", async (t) => {
  const { createMatch } = await vite.ssrLoadModule("/db/queries.ts");
  mockChampionsJobsLoopback(t, () =>
    jsonResponse({ replay: { log: "|start\n|win|IesYo", issues: [{ severity: "warning", message: "Aviso menor." }] } }),
  );

  // Sin D1 disponible en este harness: pasar la compuerta llega hasta
  // getDatabase(), que sí falla -eso prueba que nada de lo anterior
  // (incluida la incidencia sólo de tipo warning) lo frenó antes.
  await assert.rejects(
    () => createMatch({ ...baseInput, replayArtifact: { log: "|start\n|win|IesYo" } }),
    (error) => {
      assert.doesNotMatch(String(error?.message ?? error), /bloqueantes|no puede verificar|No encontramos|no respondió/);
      return true;
    },
  );
});

test("createMatch rejects a replayArtifact sent for a non-champions origin", async () => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");

  await assert.rejects(
    () =>
      createMatch({
        teamVersionId: "x",
        result: "win",
        origin: "showdown",
        replayUrl: "https://replay.pokemonshowdown.com/gen9vgc2026regmc-1",
        replayArtifact: { log: "|start\n|win|IesYo" },
      }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /sólo puede guardarse con origen Champions/);
      return true;
    },
  );
});
