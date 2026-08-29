import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const backendUrl = new URL("../db/pokemon-library.ts", import.meta.url);
const schemaUrl = new URL("../db/schema.ts", import.meta.url);
const migrationUrl = new URL("../drizzle/0005_pokemon_library.sql", import.meta.url);
const dialogUrl = new URL("../components/vgc/pokemon-library-dialog.tsx", import.meta.url);
const builderUrl = new URL("../components/vgc/team-builder.tsx", import.meta.url);

test("deduplicates reusable Pokemon sets by canonical competitive content", async () => {
  const source = await readFile(backendUrl, "utf8");

  assert.ok(source.includes("function canonicalSignature"));
  assert.ok(source.includes("species: toId(row.species)"));
  assert.ok(source.includes("stats: EV_STATS.map"));
  assert.ok(source.includes("moves: moveIds"));
  assert.ok(source.includes(".sort();"));
  assert.ok(source.includes("set_hash = ?"));
  assert.ok(source.includes("MAX(version_number) AS max_version"));
  assert.ok(source.includes("ON CONFLICT(team_version_id, slot)"));
});

test("keeps immutable Team snapshots separate from canonical library versions", async () => {
  const schema = await readFile(schemaUrl, "utf8");
  const migration = await readFile(migrationUrl, "utf8");

  assert.ok(schema.includes('"pokemon_sets"'));
  assert.ok(schema.includes('"pokemon_library_entries"'));
  assert.ok(schema.includes('"pokemon_library_versions"'));
  assert.ok(schema.includes('"pokemon_library_usages"'));
  assert.ok(migration.includes("pokemon_library_versions_entry_hash_idx"));
  assert.ok(migration.includes("pokemon_library_usages_team_slot_idx"));
});

test("My Pokemon dialog filters by current format and loads one stored version", async () => {
  const source = await readFile(dialogUrl, "utf8");

  assert.ok(source.includes("My Pokémon"));
  assert.ok(source.includes("/api/pokemon-library?format="));
  assert.ok(source.includes("encodeURIComponent(format)"));
  assert.ok(source.includes("onLoad(version.set"));
  assert.ok(source.includes("Las configuraciones idénticas comparten versión"));
});

test("Team Builder loads a library version into only the active slot", async () => {
  const source = await readFile(builderUrl, "utf8");

  assert.ok(source.includes('import { PokemonLibraryDialog } from "./pokemon-library-dialog"'));
  assert.ok(source.includes("function loadPokemonFromLibrary"));
  assert.ok(source.includes("index === selectedSlot ? hydrated : set"));
  assert.ok(source.includes("!key.startsWith(`${slot.id}:`)"));
  assert.ok(source.includes("index === selectedSlot ? revision + 1 : revision"));
  assert.ok(source.includes("<PokemonLibraryDialog format={format} onLoad={loadPokemonFromLibrary} />"));
});
