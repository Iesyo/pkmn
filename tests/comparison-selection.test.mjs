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

const groups = [
  { id: "team-a", versions: [{ id: "a-v7", teamId: "team-a" }, { id: "a-v6", teamId: "team-a" }] },
  { id: "team-b", versions: [{ id: "b-v5", teamId: "team-b" }, { id: "b-v4", teamId: "team-b" }] },
];

test("restores both exact team versions after a new session", async () => {
  const { COMPARISON_SELECTION_STORAGE_KEY, readComparisonSelection, writeComparisonSelection, resolveComparedVersions } = await vite.ssrLoadModule("/lib/comparison-selection.ts");
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  writeComparisonSelection(storage, { leftVersionId: "a-v6", rightVersionId: "b-v4" });

  assert.ok(values.has(COMPARISON_SELECTION_STORAGE_KEY));
  const restored = readComparisonSelection(storage);

  assert.deepEqual(restored, { leftVersionId: "a-v6", rightVersionId: "b-v4" });
  const comparison = resolveComparedVersions(groups, restored.leftVersionId, restored.rightVersionId);
  assert.equal(comparison.left.id, "a-v6");
  assert.equal(comparison.right.id, "b-v4");
});

test("replaces a deleted version without changing the other side", async () => {
  const { resolveComparedVersions } = await vite.ssrLoadModule("/lib/comparison-selection.ts");
  const changed = [groups[0], { ...groups[1], versions: [{ id: "b-v5", teamId: "team-b" }] }];
  const comparison = resolveComparedVersions(changed, "a-v6", "b-v4");

  assert.equal(comparison.left.id, "a-v6");
  assert.equal(comparison.right.id, "b-v5");
});

test("ignores malformed browser preferences and handles teams without versions", async () => {
  const { parseComparisonSelection, readComparisonSelection, writeComparisonSelection, resolveComparedVersions } = await vite.ssrLoadModule("/lib/comparison-selection.ts");
  assert.equal(parseComparisonSelection("{broken"), null);
  assert.equal(parseComparisonSelection('{"leftVersionId":"a-v7"}'), null);
  assert.equal(readComparisonSelection({ getItem: () => { throw new Error("blocked"); } }), null);
  assert.doesNotThrow(() => writeComparisonSelection({ setItem: () => { throw new Error("full"); } }, { leftVersionId: "a-v7", rightVersionId: "b-v5" }));

  const comparison = resolveComparedVersions([{ id: "empty", versions: [] }, ...groups], "", "");
  assert.equal(comparison.left.id, "a-v7");
  assert.equal(comparison.right.id, "b-v5");
});
