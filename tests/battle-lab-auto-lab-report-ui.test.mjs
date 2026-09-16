import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const component = path.join(root, "components", "vgc", "war-room-auto-lab-v2.tsx");

test("Auto Lab report keeps the agreed visual hierarchy", () => {
  const source = fs.readFileSync(component, "utf8");

  assert.doesNotMatch(source, /Ranking de rivales problemáticos/);

  const archetypes = source.indexOf("Rendimiento por arquetipo");
  const favorable = source.indexOf("Matchups favorables");
  assert.ok(archetypes >= 0, "missing archetype section");
  assert.ok(favorable >= 0, "missing favorable matchup section");
  assert.ok(archetypes < favorable, "archetype performance must appear before matchup cards");

  assert.match(source, /function PokemonPill\(/);
  assert.match(source, /Battle pressure/);
  assert.match(source, /Pokémon rivales ligados a derrotas/);
  assert.match(source, /Core pressure/);
  assert.match(source, /Cores \/ leads rivales ligados a derrotas/);
  assert.match(source, /\[&_\[data-slot=progress-indicator\]\]:bg-amber-300/);
  assert.match(source, /\[&_\[data-slot=progress-indicator\]\]:bg-violet-300/);

  assert.match(source, /text-xl font-black text-white">Rendimiento por arquetipo/);
  assert.match(source, /text-sm font-black text-white/);
  assert.match(source, /text-\[12px\] leading-5 text-slate-400/);
});
