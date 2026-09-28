import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const sqlite = new DatabaseSync(":memory:");
sqlite.exec(`
  CREATE TABLE team_versions (id TEXT PRIMARY KEY);
  CREATE TABLE pokemon_sets (team_version_id TEXT, slot INTEGER, species TEXT);
  CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT);
  CREATE TABLE matches (
    id TEXT PRIMARY KEY, team_version_id TEXT, result TEXT, opponent_name TEXT,
    opponent_paste TEXT, replay_url TEXT, origin TEXT, replay_artifact_json TEXT,
    selected_json TEXT, opponent_selected_json TEXT, opponent_picks_json TEXT DEFAULT '[]',
    lead_json TEXT, moves_used_json TEXT, rating INTEGER, notes TEXT, played_at TEXT
  );
  INSERT INTO team_versions VALUES ('ledger-team');
  INSERT INTO app_settings VALUES ('showdown_names', '["Roku"]');
`);
const team = ["Blaziken", "Indeedee-F", "Gardevoir", "Kingambit", "Rillaboom", "Basculegion"];
team.forEach((species, slot) => sqlite.prepare("INSERT INTO pokemon_sets VALUES (?, ?, ?)").run("ledger-team", slot, species));

// Adaptador de SQLite al contrato D1; las consultas y el guardado son reales.
globalThis.__ledgerImportDatabase = {
  prepare(sql) {
    const statement = sqlite.prepare(sql);
    let values = [];
    return {
      bind(...parameters) { values = parameters; return this; },
      async first() { return statement.get(...values) ?? null; },
      async all() { return { results: statement.all(...values) }; },
      async run() { return { meta: statement.run(...values) }; },
    };
  },
};
const vite = await createServer({
  appType: "custom", configFile: false, root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
  plugins: [{
    name: "ledger-test-database",
    resolveId(id) { if (id === "cloudflare:workers") return "\0ledger-test-database"; },
    load(id) { if (id === "\0ledger-test-database") return "export const env = { DB: globalThis.__ledgerImportDatabase };"; },
  }],
});
after(async () => { await vite.close(); sqlite.close(); delete globalThis.__ledgerImportDatabase; });

test("the Ledger version matches Python and keeps historical COL-102 documents readable", async () => {
  const { CHAMPIONS_RECONCILIATION_VERSION, hasCurrentReconciliation } = await vite.ssrLoadModule("/lib/showdown-replay.ts");
  const source = await readFile(new URL("../backend/pkmn_vgc/champions_replay/ledger_pipeline.py", import.meta.url), "utf8");
  assert.equal(CHAMPIONS_RECONCILIATION_VERSION, source.match(/^LEDGER_VERSION = "([^"]+)"/m)[1]);
  for (const version of [CHAMPIONS_RECONCILIATION_VERSION, "col102-r12", "col102-r11"]) {
    assert.equal(hasCurrentReconciliation({ reconciliation_version: version }), true);
  }
  for (const version of [null, "col102-r10", "champions-ledger-future"]) {
    assert.equal(hasCurrentReconciliation({ reconciliation_version: version }), false);
  }
});

test("saves the replay produced by both automata through the match API and opens its stored artifact", async (t) => {
  const script = `
import json, tempfile
from pathlib import Path
from test_champions_ledger_pipeline import trace_battle, write_trace, CONTEXT
from pkmn_vgc.champions_replay.ledger_pipeline import documents_from_trace
with tempfile.TemporaryDirectory() as directory:
    out = Path(directory)
    write_trace(out / 'ocr.trace.jsonl', trace_battle())
    job = {'id': '0123456789abcdef', 'created_at': '2026-09-28T12:00:00+00:00', 'context': CONTEXT}
    print(json.dumps(documents_from_trace(out / 'ocr.trace.jsonl', job, out)[0].to_dict()))
`;
  const canonical = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c", script], {
    cwd: root, encoding: "utf8",
    env: { ...process.env, PYTHONPATH: `${root}/backend${process.platform === "win32" ? ";" : ":"}${root}/backend/tests` },
  }));
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.equal(String(url), "http://127.0.0.1:8770/jobs/0123456789abcdef/replays/1");
    return Response.json({ replay: canonical });
  });
  const { POST } = await vite.ssrLoadModule("/app/api/matches/route.ts");
  const response = await POST(new Request("http://localhost/api/matches", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({
      teamVersionId: "ledger-team", origin: "champions", result: "loss",
      championsJobId: "0123456789abcdef", championsReplayNumber: 1,
      // El servidor debe derivarlo todo del job y descartar esta copia.
      selected: ["Inventado"], opponentPicks: ["Inventado"],
      replayArtifact: { log: "|win|Inventado" },
    }),
  }));
  const body = await response.json();
  assert.equal(response.status, 201, JSON.stringify(body));
  assert.equal(body.match.result, "win");
  assert.equal(body.match.hasReplayArtifact, true);
  assert.deepEqual(body.match.selected, ["Blaziken", "Indeedee-F"]);
  assert.deepEqual(body.match.opponentPicks, ["Garchomp", "Sneasler"]);
  const row = sqlite.prepare("SELECT * FROM matches WHERE id = ?").get(body.match.id);
  assert.deepEqual(JSON.parse(row.opponent_picks_json), ["Garchomp", "Sneasler"]);
  const stored = JSON.parse(row.replay_artifact_json);
  assert.equal(stored.log, canonical.log);
  assert.equal(stored.reconciliation_version, canonical.reconciliation_version);
  assert.equal(stored.source_battle_index, canonical.source_battle_index);
  assert.deepEqual(stored.ledger_source, canonical.ledger_source);
  const { getMatchReplayArtifact } = await vite.ssrLoadModule("/db/replay-artifacts.ts");
  assert.deepEqual(JSON.parse(JSON.stringify(await getMatchReplayArtifact(body.match.id))), stored);
});
