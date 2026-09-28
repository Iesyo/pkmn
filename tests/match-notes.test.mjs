import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const sqlite = new DatabaseSync(":memory:");
sqlite.exec(`
  CREATE TABLE matches (id TEXT PRIMARY KEY, notes TEXT NOT NULL, replay_artifact_json TEXT, result TEXT);
  INSERT INTO matches VALUES ('saved-replay', 'Nota inicial', '{"log":"|start"}', 'win');
`);
globalThis.__matchNotesDatabase = {
  prepare(sql) {
    const statement = sqlite.prepare(sql);
    let values = [];
    return {
      bind(...parameters) { values = parameters; return this; },
      async first() { return statement.get(...values) ?? null; },
      async run() { return { meta: statement.run(...values) }; },
    };
  },
};

const vite = await createServer({
  appType: "custom", configFile: false, root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
  plugins: [{
    name: "match-notes-test-database",
    resolveId(id) { if (id === "cloudflare:workers") return "\0match-notes-test-database"; },
    load(id) { if (id === "\0match-notes-test-database") return "export const env = { DB: globalThis.__matchNotesDatabase };"; },
  }],
});
after(async () => { await vite.close(); sqlite.close(); delete globalThis.__matchNotesDatabase; });

test("PATCH edits saved replay notes without changing the replay or result, and can clear them", async () => {
  const { PATCH } = await vite.ssrLoadModule("/app/api/matches/[id]/route.ts");
  const context = { params: Promise.resolve({ id: "saved-replay" }) };
  const request = (notes) => new Request("http://localhost/api/matches/saved-replay", {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ notes }),
  });

  const response = await PATCH(request("  Aprendí a jugar contra Trick Room  "), context);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { notes: "Aprendí a jugar contra Trick Room" });
  assert.deepEqual({ ...sqlite.prepare("SELECT notes, replay_artifact_json, result FROM matches WHERE id = ?").get("saved-replay") }, {
    notes: "Aprendí a jugar contra Trick Room", replay_artifact_json: '{"log":"|start"}', result: "win",
  });

  assert.equal((await PATCH(request(""), context)).status, 200);
  assert.equal(sqlite.prepare("SELECT notes FROM matches WHERE id = ?").get("saved-replay").notes, "");
});

test("PATCH rejects unknown matches and invalid notes without changing saved data", async () => {
  const { PATCH } = await vite.ssrLoadModule("/app/api/matches/[id]/route.ts");
  const request = (notes) => new Request("http://localhost/api/matches/saved-replay", {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ notes }),
  });
  const context = (id) => ({ params: Promise.resolve({ id }) });
  const before = sqlite.prepare("SELECT notes FROM matches WHERE id = ?").get("saved-replay").notes;

  assert.equal((await PATCH(request("hello"), context("missing"))).status, 404);
  assert.equal((await PATCH(request(42), context("saved-replay"))).status, 400);
  assert.equal((await PATCH(request("x".repeat(5001)), context("saved-replay"))).status, 400);
  assert.equal(sqlite.prepare("SELECT notes FROM matches WHERE id = ?").get("saved-replay").notes, before);
});

test("new matches enforce the same note length before writing to the database", async () => {
  const { createMatch, DomainError } = await vite.ssrLoadModule("/db/queries.ts");
  await assert.rejects(
    () => createMatch({ teamVersionId: "unused", result: "win", notes: "x".repeat(5001) }),
    (error) => error instanceof DomainError && /5000 caracteres/.test(error.message),
  );
});
