import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const backendUrl = new URL("../db/pokemon-library.ts", import.meta.url);
const schemaUrl = new URL("../db/schema.ts", import.meta.url);
const migrationUrl = new URL("../drizzle/0005_pokemon_library.sql", import.meta.url);
const selectorUrl = new URL("../components/vgc/pokemon-library-dialog.tsx", import.meta.url);
const calculatorUrl = new URL("../components/vgc/damage-calculator.tsx", import.meta.url);

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

test("saved sets are exposed as a contextual version selector", async () => {
  const source = await readFile(selectorUrl, "utf8");

  assert.ok(source.includes("PokemonLibraryVersionSelect"));
  assert.ok(source.includes("/api/pokemon-library?format="));
  assert.ok(source.includes("encodeURIComponent(format)"));
  assert.ok(source.includes("toId(candidate.species) === toId(species)"));
  assert.ok(source.includes("<Label>Set</Label>"));
  assert.ok(source.includes('"Elegir versión"'));
  assert.ok(source.includes('"Sin versiones"'));
  assert.ok(source.includes("onLoad(version.set"));
  assert.ok(source.includes('className="grid min-w-0 gap-2"'));
  assert.ok(source.includes('className="w-full min-w-0 border-violet-300/15'));
  assert.ok(!source.includes("DialogTrigger"));
  assert.ok(!source.includes("<Button"));
});

test("both calculator panels expose saved sets in a balanced Pokemon/Set row", async () => {
  const source = await readFile(calculatorUrl, "utf8");

  assert.ok(source.includes('import { PokemonLibraryVersionSelect } from "./pokemon-library-dialog"'));
  assert.ok(source.includes("function chooseLibraryVersion"));
  assert.ok(source.includes("id: set.id"));
  assert.ok(source.includes("slot: set.slot"));
  assert.ok(source.includes('className="grid gap-3 sm:grid-cols-2"'));
  assert.ok(source.includes('className="grid min-w-0 gap-2"'));
  assert.ok(source.includes('className="w-full min-w-0 border-white/10 bg-white/4"'));
  assert.ok(source.includes("<PokemonLibraryVersionSelect species={set.species} format={format}"));
  assert.ok(!source.includes('side === "left" ? <PokemonLibraryVersionSelect'));
  assert.ok(source.includes("chooseLibraryVersion(librarySet)"));
});
