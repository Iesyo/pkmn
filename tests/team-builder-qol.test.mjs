import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const builderSourceUrl = new URL("../components/vgc/team-builder.tsx", import.meta.url);

test("renders six summary cards with per-slot removal", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.match(source, /relative h-56 min-w-0 overflow-hidden rounded-2xl border/);
  assert.ok(source.includes('pokemon.item || "Sin objeto"'));
  assert.ok(source.includes('pokemon.nature || "Sin naturaleza"'));
  assert.ok(source.includes('pokemon.ability || "Sin habilidad"'));
  assert.ok(source.includes('pokemon.moves.map((move, moveIndex) =>'));
  assert.match(source, /text-amber-200\/90/);
  assert.match(source, /text-fuchsia-300\/85/);
  assert.match(source, /width=\{72\} height=\{72\}/);
  assert.match(source, /size-16 object-contain/);
  assert.ok(source.includes('aria-label={`Quitar ${pokemon.species} del Team`}'));
  assert.ok(source.includes('function clearSlot(index: number)'));
  assert.ok(source.includes('emptyPokemon(set.slot)'));
  assert.ok(source.includes('onClear={() => clearSlot(index)}'));

  const nameIndex = source.indexOf('{pokemon.species}</p>');
  const itemIndex = source.indexOf('{pokemon.item || "Sin objeto"}</p>');
  const typesIndex = source.indexOf('pokemon.types.map((type) =>');
  const natureIndex = source.indexOf('{pokemon.nature || "Sin naturaleza"}</p>');
  assert.ok(nameIndex >= 0 && nameIndex < itemIndex && itemIndex < typesIndex && typesIndex < natureIndex);
});

test("resetting a slot also resets its calculator session", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.ok(source.includes('const [slotRevisions, setSlotRevisions]'));
  assert.ok(source.includes('`${selected.id}:${format}:${slotRevisions[selectedSlot]}`'));
  assert.match(source, /!key\.startsWith\(`\$\{slotId\}:`\)/);
  assert.match(source, /currentIndex === index \? revision \+ 1 : revision/);
});

test("keeps import and export dialogs fixed while the paste scrolls internally", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.match(source, /DialogContent className="grid h-\[88vh\] max-h-\[40rem\] grid-rows-\[auto_minmax\(0,1fr\)_auto\] overflow-hidden/);
  assert.match(source, /field-sizing-fixed h-full min-h-0 resize-none overflow-y-auto/);
  assert.match(source, /grid grid-cols-2 gap-2 border-t border-white\/8 pt-4/);
  assert.ok(source.includes("<DialogClose asChild>"));
});
