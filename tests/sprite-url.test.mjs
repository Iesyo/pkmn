import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

test("maps Showdown Mega species names to the sprite catalog convention", async () => {
  const { getSpriteUrl } = await vite.ssrLoadModule("/lib/pokemon-data.ts");

  assert.equal(
    getSpriteUrl("Venusaur-Mega"),
    "https://play.pokemonshowdown.com/sprites/gen5/venusaur-mega.png",
  );
  assert.equal(
    getSpriteUrl("Charizard-Mega-X"),
    "https://play.pokemonshowdown.com/sprites/gen5/charizard-megax.png",
  );
  assert.equal(
    getSpriteUrl("Charizard-Mega-Y"),
    "https://play.pokemonshowdown.com/sprites/gen5/charizard-megay.png",
  );
  assert.equal(
    getSpriteUrl("Rayquaza-Mega"),
    "https://play.pokemonshowdown.com/sprites/gen5/rayquaza-mega.png",
  );
});

test("supports Legends ZA Mega naming quirks without broken sprite URLs", async () => {
  const { getSpriteUrl } = await vite.ssrLoadModule("/lib/pokemon-data.ts");

  assert.equal(
    getSpriteUrl("Meowstic-M-Mega"),
    "https://play.pokemonshowdown.com/sprites/gen5/meowstic-mmega.png",
  );
  assert.equal(
    getSpriteUrl("Meowstic-F-Mega"),
    "https://play.pokemonshowdown.com/sprites/gen5/meowstic-fmega.png",
  );
  assert.equal(
    getSpriteUrl("Malamar-Mega"),
    "https://play.pokemonshowdown.com/sprites/ani/malamar-mega.gif",
  );
  assert.equal(
    getSpriteUrl("Raichu-Mega-X"),
    "https://play.pokemonshowdown.com/sprites/ani/raichu-megax.gif",
  );
  assert.equal(
    getSpriteUrl("Raichu-Mega-Y"),
    "https://play.pokemonshowdown.com/sprites/ani/raichu-megay.gif",
  );
});

test("falls back to the base art while Showdown has no standard Mega-Z sprite", async () => {
  const { getSpriteUrl } = await vite.ssrLoadModule("/lib/pokemon-data.ts");

  assert.equal(
    getSpriteUrl("Absol-Mega-Z"),
    "https://play.pokemonshowdown.com/sprites/gen5/absol.png",
  );
  assert.equal(
    getSpriteUrl("Lucario-Mega-Z"),
    "https://play.pokemonshowdown.com/sprites/gen5/lucario.png",
  );
  assert.equal(
    getSpriteUrl("Garchomp-Mega-Z"),
    "https://play.pokemonshowdown.com/sprites/gen5/garchomp.png",
  );
});

test("keeps existing non-Mega sprite aliases intact", async () => {
  const { getSpriteUrl } = await vite.ssrLoadModule("/lib/pokemon-data.ts");

  assert.equal(
    getSpriteUrl("Landorus-Therian"),
    "https://play.pokemonshowdown.com/sprites/gen5/landorus-therian.png",
  );
  assert.equal(
    getSpriteUrl("Flutter Mane"),
    "https://play.pokemonshowdown.com/sprites/gen5/fluttermane.png",
  );
});
