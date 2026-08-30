import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const serviceUrl = new URL("../deploy/pkmn.service", import.meta.url);

test("keeps the production runtime read-only except for Vite config scratch", async () => {
  const service = await readFile(serviceUrl, "utf8");

  assert.match(service, /User=pkmn/);
  assert.match(service, /Group=pkmn/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/rm -rf -- \/opt\/pkmn-runtime\/node_modules\/\.vite-temp/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/install -d -o root -g pkmn -m 0770 \/opt\/pkmn-runtime\/node_modules\/\.vite-temp/);
  assert.match(service, /ExecStart=\/opt\/pkmn-runtime\/deploy\/start-vps\.sh/);
  assert.doesNotMatch(service, /chmod\s+-R\s+[^\n]*g\+w[^\n]*\/opt\/pkmn-runtime/);
});
