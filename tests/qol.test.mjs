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

test("keeps the Team Builder mounted between sections and removes the global Add Team action", async () => {
  const dashboard = await source("app/vgc-dashboard.tsx");

  assert.match(dashboard, /<TabsContent value="builder" forceMount className="mt-0 outline-none">/);
  assert.equal((dashboard.match(/<AddTeamDialog onCreated=\{handleTeamCreated\} \/>/g) ?? []).length, 1);
  assert.match(dashboard, /<ShowdownNamesDialog names=\{showdownNames\} onSaved=\{setShowdownNames\} \/><\/div>/);
});

test("removes demo teams from every runtime team surface and separates team from version selection", async () => {
  const [dashboard, selector] = await Promise.all([
    source("app/vgc-dashboard.tsx"),
    source("components/vgc/team-selector.tsx"),
  ]);
  const compareSection = dashboard.match(/<TabsContent value="compare"[\s\S]*?<TabsContent value="library"/)?.[0] ?? "";
  const librarySection = dashboard.match(/<TabsContent value="library"[\s\S]*?<TabsContent value="builder"/)?.[0] ?? "";

  assert.doesNotMatch(dashboard, /DEMO_GROUPS/);
  assert.doesNotMatch(dashboard, /DEFAULT_LEFT_VERSION_ID/);
  assert.doesNotMatch(dashboard, /@\/lib\/demo-data/);
  assert.doesNotMatch(dashboard, /Modo muestra/);
  assert.doesNotMatch(dashboard, /libraryVersion\.demo/);
  assert.match(dashboard, /type ConnectionState = "checking" \| "ready" \| "error"/);
  assert.match(compareSection, /groups=\{storedGroups\}/);
  assert.match(librarySection, /\{storedGroups\.length\} equipos/);
  assert.match(librarySection, /storedGroups\.map\(\(team\)/);
  assert.match(dashboard, /<TeamBuilder key=\{builderVersionId\} groups=\{storedGroups\}/);
  assert.match(dashboard, /No hay Teams guardados/);
  assert.match(selector, />Equipo<\/span>/);
  assert.match(selector, />Versión<\/span>/);
  assert.match(selector, /handleTeamChange/);
  assert.match(selector, /team\?\.versions\[0\]\?\.id/);
});

test("pins replay entry state and persisted matches to the exact team version", async () => {
  const [panel, dialogs, queries] = await Promise.all([
    source("components/vgc/team-panel.tsx"),
    source("components/vgc/team-dialogs.tsx"),
    source("db/queries.ts"),
  ]);

  assert.match(panel, /<MatchHistory key=\{version\.id\} version=\{version\}/);
  assert.match(dialogs, /teamVersionId:\s*version\.id/);
  assert.match(queries, /SELECT id FROM team_versions WHERE id = \?/);
  assert.match(queries, /INSERT INTO matches \(id, team_version_id,/);
  assert.match(queries, /match\.id,\s*\n\s*input\.teamVersionId,/);
  assert.match(queries, /match\.team_version_id === version\.id/);
});
