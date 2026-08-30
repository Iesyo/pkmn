import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  ssr: { external: ["@smogon/calc"] },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

test("compares the two effective Spe values and reverses the winner in Trick Room", async () => {
  const { getSpeedOrder } = await vite.ssrLoadModule("/lib/damage-calculator.ts");

  assert.equal(getSpeedOrder(378, 312, false), "left");
  assert.equal(getSpeedOrder(378, 312, true), "right");
  assert.equal(getSpeedOrder(84, 126, false), "right");
  assert.equal(getSpeedOrder(84, 126, true), "left");
  assert.equal(getSpeedOrder(126, 126, false), "tie");
  assert.equal(getSpeedOrder(126, 126, true), "tie");
});

test("Trick Room is a shared calculator field condition and starts disabled", async () => {
  const { defaultDamageField } = await vite.ssrLoadModule("/lib/damage-calculator.ts");
  const field = defaultDamageField();

  assert.equal(field.trickRoom, false);
  assert.equal(field.left.tailwind, false);
  assert.equal(field.right.tailwind, false);
});

test("the center column compares only the two current calculator Pokemon", async () => {
  const source = await readFile(new URL("../components/vgc/damage-calculator.tsx", import.meta.url), "utf8");

  assert.match(source, /function SpeedComparisonCard/);
  assert.match(source, /Orden por Speed/);
  assert.match(source, /leftName=\{left\.set\.species\}/);
  assert.match(source, /rightName=\{right\.set\.species\}/);
  assert.match(source, /leftSpeed=\{leftSpeed\}/);
  assert.match(source, /rightSpeed=\{rightSpeed\}/);
  assert.match(source, /getCurrentEffectiveSpeed\(left, format, dex, field\.left\.tailwind\)/);
  assert.match(source, /getCurrentEffectiveSpeed\(right, format, dex, field\.right\.tailwind\)/);
  assert.match(source, /label="Trick Room"/);
  assert.match(source, /menor Speed primero/);
  assert.match(source, /Speed tie/);
  assert.doesNotMatch(source, /Ataca primero|Atacar primero/);
});
