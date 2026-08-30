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

test("keeps demo teams out of the comparator and separates team from version selection", async () => {
  const [dashboard, selector] = await Promise.all([
    source("app/vgc-dashboard.tsx"),
    source("components/vgc/team-selector.tsx"),
  ]);
  const compareSection = dashboard.match(/<TabsContent value="compare"[\s\S]*?<TabsContent value="library"/)?.[0] ?? "";

  assert.match(dashboard, /const comparisonVersions = useMemo\(\(\) => storedGroups\.flatMap/);
  assert.match(compareSection, /groups=\{storedGroups\}/);
  assert.doesNotMatch(compareSection, /DEMO_GROUPS/);
  assert.doesNotMatch(compareSection, /versions=\{versions\}/);
  assert.match(selector, />Equipo<\/span>/);
  assert.match(selector, />Versión<\/span>/);
  assert.match(selector, /handleTeamChange/);
  assert.match(selector, /team\?\.versions\[0\]\?\.id/);
});
