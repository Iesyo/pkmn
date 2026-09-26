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
};

// Roku, revisión del tercer corte, 26 sep: "Guardar partida" mostraba las
// incidencias como texto, pero nada -ni el cliente ni el servidor- impedía
// persistir un replay con una incidencia `severity: "blocking"`. Prueba de
// extremo a extremo real (no sólo un patrón de texto): createMatch() valida
// esto ANTES de tocar la base de datos, así que corre igual sin D1.
test("createMatch rejects a champions replay with a blocking issue before touching the database", async () => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");

  await assert.rejects(
    () =>
      createMatch({
        ...baseInput,
        replayArtifact: {
          log: "|start\n|win|IesYo",
          issues: [{ severity: "blocking", message: "La selección del rival contiene 2/4 Pokémon." }],
        },
      }),
    (error) => {
      assert.ok(error instanceof DomainError, `expected DomainError, got ${error}`);
      assert.match(error.message, /bloqueantes/);
      return true;
    },
  );
});

test("createMatch does not reject on warning-only issues at the validation stage", async () => {
  const { createMatch } = await vite.ssrLoadModule("/db/queries.ts");

  // Sin D1 disponible en este harness, esto sigue de largo hasta
  // getDatabase() y falla ahí -lo que prueba que NINGUNA de las
  // validaciones tempranas (incluida la de incidencias) lo frenó antes.
  await assert.rejects(
    () =>
      createMatch({
        ...baseInput,
        replayArtifact: {
          log: "|start\n|win|IesYo",
          issues: [{ severity: "warning", message: "Aviso menor, no bloqueante." }],
        },
      }),
    (error) => {
      assert.doesNotMatch(String(error.message ?? error), /bloqueantes/);
      return true;
    },
  );
});
