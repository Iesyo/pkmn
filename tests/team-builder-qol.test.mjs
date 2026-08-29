import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const builderSourceUrl = new URL("../components/vgc/team-builder.tsx", import.meta.url);

test("renders six summary cards with per-slot removal", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.match(source, /relative h-52 min-w-0 overflow-hidden rounded-2xl border/);
  assert.ok(source.includes('pokemon.item || "Sin objeto"'));
  assert.ok(source.includes('pokemon.ability || "Sin habilidad"'));
  assert.ok(source.includes('pokemon.moves.map((move, moveIndex) =>'));
  assert.ok(source.includes('aria-label={`Quitar ${pokemon.species} del Team`}'));
  assert.ok(source.includes('function clearSlot(index: number)'));
  assert.ok(source.includes('emptyPokemon(set.slot)'));
  assert.ok(source.includes('onClear={() => clearSlot(index)}'));
});

test("resetting a slot also resets its calculator session", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.ok(source.includes('const [slotRevisions, setSlotRevisions]'));
  assert.ok(source.includes('`${selected.id}:${format}:${slotRevisions[selectedSlot]}`'));
  assert.match(source, /!key\.startsWith\(`\$\{slotId\}:`\)/);
  assert.match(source, /currentIndex === index \? revision \+ 1 : revision/);
});
