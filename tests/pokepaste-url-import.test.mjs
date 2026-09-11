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
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const paste = ["Charizard", "Garchomp", "Whimsicott", "Floette", "Incineroar", "Basculegion-M"]
  .map((species) => `${species} @ Leftovers\nAbility: Pressure\nLevel: 50\n- Protect\n- Tackle\n- Growl\n- Substitute`)
  .join("\n\n");

test("downloads a safe PokéPaste URL through /raw and returns the existing parser format", async () => {
  const { POST } = await vite.ssrLoadModule("/app/api/pokepaste-import/route.ts");
  const originalFetch = globalThis.fetch;
  const requested = [];
  globalThis.fetch = async (input, init) => {
    requested.push({ url: String(input), redirect: init?.redirect });
    return new Response(paste, { headers: { "content-type": "text/plain" } });
  };

  try {
    const response = await POST(new Request("http://localhost/api/pokepaste-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: "https://pokepast.es/f6455b416b2e9f80" }),
    }));
    const payload = await response.json();

    assert.equal(response.status, 200);
    assert.equal(payload.paste, paste);
    assert.deepEqual(requested, [{
      url: "https://pokepast.es/f6455b416b2e9f80/raw",
      redirect: "manual",
    }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects arbitrary URLs before making a network request", async () => {
  const { POST } = await vite.ssrLoadModule("/app/api/pokepaste-import/route.ts");
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(paste);
  };

  try {
    const response = await POST(new Request("http://localhost/api/pokepaste-import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: "https://example.com/team" }),
    }));
    assert.equal(response.status, 400);
    assert.equal(calls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Team Builder resolves PokéPaste URLs through the safe import endpoint", async () => {
  const source = await readFile(new URL("../components/vgc/team-builder.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/pokepaste-import/);
  assert.match(source, /PokéPaste importado/);
  assert.match(source, /URL de PokéPaste/);
});
