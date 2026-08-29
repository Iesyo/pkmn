import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

async function source(relativePath) {
  return readFile(new URL(relativePath, `file://${root}/`), "utf8");
}

test("keeps replay deletion and the library version label usable", async () => {
  const [history, dashboard, route, action] = await Promise.all([
    source("components/vgc/match-history.tsx"),
    source("app/vgc-dashboard.tsx"),
    source("app/api/matches/[id]/route.ts"),
    source("db/match-actions.ts"),
  ]);

  assert.match(history, /Eliminar partida/);
  assert.match(history, /method:\s*"DELETE"/);
  assert.match(route, /await deleteMatch\(id\)/);
  assert.match(action, /DELETE FROM scouting_analyses WHERE match_id = \?/);
  assert.match(action, /DELETE FROM matches WHERE id = \?/);
  assert.match(dashboard, /SelectTrigger className="min-w-44[^\"]*sm:min-w-48"/);
  assert.match(dashboard, /versiones inmutables/);
});
