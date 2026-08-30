import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function source(path) {
  return readFileSync(path, "utf8");
}

test("Teams folders are persisted without touching immutable versions", () => {
  const migration = source("drizzle/0006_team_folders.sql");
  const schema = source("db/schema.ts");
  const folderQueries = source("db/team-folders.ts");

  assert.match(migration, /CREATE TABLE `team_folders`/);
  assert.match(migration, /ALTER TABLE `teams` ADD `folder_id`/);
  assert.match(migration, /ON DELETE SET NULL/);
  assert.match(schema, /export const teamFolders = sqliteTable/);
  assert.match(schema, /folderId: text\("folder_id"\)/);
  assert.match(folderQueries, /UPDATE teams SET folder_id = NULL WHERE folder_id = \?/);
  assert.match(folderQueries, /UPDATE teams SET folder_id = \? WHERE id = \?/);
  assert.doesNotMatch(migration, /ALTER TABLE `team_versions`/);
});

test("Teams API returns folders and supports moving a complete team", () => {
  const teamsRoute = source("app/api/teams/route.ts");
  const moveRoute = source("app/api/teams/[id]/route.ts");
  const foldersRoute = source("app/api/team-folders/route.ts");

  assert.match(teamsRoute, /folders,/);
  assert.match(teamsRoute, /folderId: folderAssignments\[team\.id\] \?\? null/);
  assert.match(moveRoute, /moveTeamToFolder/);
  assert.match(moveRoute, /folderId/);
  assert.match(foldersRoute, /createTeamFolder/);
});

test("Teams UI offers folders, move menu and drag-and-drop fallback", () => {
  const dashboard = source("app/vgc-dashboard.tsx");
  const card = source("components/vgc/library-card.tsx");
  const folders = source("components/vgc/team-folders.tsx");
  const pokemonData = source("lib/pokemon-data.ts");

  assert.match(dashboard, /CreateFolderDialog/);
  assert.match(dashboard, /TeamFolderSection/);
  assert.match(dashboard, /Sin carpeta|folder=\{null\}/);
  assert.match(card, /draggable/);
  assert.match(card, /Mover a/);
  assert.match(card, /TEAM_DRAG_MIME/);
  assert.match(card, /import \{ getSpriteUrl \} from "@\/lib\/pokemon-data"/);
  assert.match(card, /src=\{getSpriteUrl\(pokemon\.species\)\}/);
  assert.doesNotMatch(card, /getPokemonSpriteUrl/);
  assert.match(pokemonData, /export function getSpriteUrl\(species: string\)/);
  assert.match(folders, /onDropTeam/);
  assert.match(folders, /Eliminar carpeta/);
  assert.match(folders, /Volverán automáticamente a Sin carpeta/);
});

test("folder disclosure and drag hover stay stable across child transitions and remounts", () => {
  const folders = source("components/vgc/team-folders.tsx");

  assert.match(folders, /const folderDisclosureState = new Map<string, boolean>\(\)/);
  assert.match(folders, /useState\(\(\) => folderDisclosureState\.get\(folderKey\) \?\? true\)/);
  assert.match(folders, /folderDisclosureState\.set\(folderKey, next\)/);
  assert.match(folders, /const dragDepth = useRef\(0\)/);
  assert.match(folders, /onDragEnter=\{handleDragEnter\}/);
  assert.match(folders, /dragDepth\.current \+= 1/);
  assert.match(folders, /dragDepth\.current = Math\.max\(0, dragDepth\.current - 1\)/);
  assert.doesNotMatch(folders, /relatedTarget/);
  assert.match(folders, /aria-expanded=\{open\}/);
  assert.match(folders, /hidden=\{!open\}/);
  assert.doesNotMatch(folders, /\{open \? \(/);
});
