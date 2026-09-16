import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const bridge = path.join(root, "components", "clipboard-fallback.tsx");
const layout = path.join(root, "app", "layout.tsx");

test("LAN clipboard fallback covers non-secure private-network origins", () => {
  const source = fs.readFileSync(bridge, "utf8");
  assert.match(source, /window\.isSecureContext && navigator\.clipboard\?\.writeText/);
  assert.match(source, /document\.execCommand\("copy"\)/);
  assert.match(source, /Object\.defineProperty\(navigator, "clipboard"/);
  assert.match(source, /window\.prompt\("Copia manualmente el paste de la variante:"/);
});

test("root layout installs clipboard fallback before interactive controls", () => {
  const source = fs.readFileSync(layout, "utf8");
  assert.match(source, /import \{ ClipboardFallback \} from "@\/components\/clipboard-fallback"/);
  assert.match(source, /<ClipboardFallback \/>/);
});
