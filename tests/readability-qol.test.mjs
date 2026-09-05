import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const damageSourceUrl = new URL("../components/vgc/damage-calculator.tsx", import.meta.url);
const builderSourceUrl = new URL("../components/vgc/team-builder.tsx", import.meta.url);

test("keeps damage and move details readable", async () => {
  const source = await readFile(damageSourceUrl, "utf8");

  assert.match(source, /line-clamp-3 text-\[10px\] leading-4 text-slate-400/);
  assert.match(source, /font-mono text-\[9px\] leading-4 text-slate-500/);
  assert.match(source, /font-mono text-\[10px\] leading-4 text-cyan-100\/85/);
  assert.match(source, /line-clamp-1 text-\[10px\] leading-4 text-slate-400/);
});

test("leaves breathing room below the fourth move in Team Builder cards", async () => {
  const source = await readFile(builderSourceUrl, "utf8");

  assert.match(source, /relative h-56 min-w-0 overflow-hidden rounded-2xl border/);
  assert.match(source, /mt-1\.5 space-y-0\.5 pb-2/);
});
