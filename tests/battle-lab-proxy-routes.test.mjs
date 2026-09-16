import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const routePath = path.join(root, "app", "api", "battle-lab", "[...path]", "route.ts");

function allowedPathRegex() {
  const source = fs.readFileSync(routePath, "utf8");
  const match = source.match(/const ALLOWED_PATH = (\/\^.*\$\/[a-z]*);/);
  assert.ok(match?.[1], "No se encontró ALLOWED_PATH en el proxy de Battle Lab.");
  // The matched source is a repository-owned regex literal, not user input.
  return Function(`"use strict"; return (${match[1]});`)();
}

test("Battle Lab proxy allows the explicit Auto Lab preflight without opening arbitrary paths", () => {
  const allowed = allowedPathRegex();

  for (const value of [
    "health",
    "model-info",
    "sparring",
    "sparring/0123456789abcdef",
    "sparring/0123456789abcdef/team-preview",
    "sparring/0123456789abcdef/choice",
    "auto-lab",
    "auto-lab/validate",
    "auto-lab/0123456789abcdef",
  ]) {
    assert.equal(allowed.test(value), true, `${value} debería estar permitido`);
  }

  for (const value of [
    "auto-lab/not-a-job",
    "auto-lab/validate/extra",
    "auto-lab/0123456789abcdef/extra",
    "admin",
  ]) {
    assert.equal(allowed.test(value), false, `${value} no debería estar permitido`);
  }
});
