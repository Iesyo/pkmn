import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON ?? "python3";

test("Nana 2 shadow v1 remains import-compatible only for shared proxy", () => {
  const script = String.raw`
from battle_lab.nana_stage2_shadow_runtime import response_utility
assert callable(response_utility)
`;
  execFileSync(python, ["-c", script], { cwd: root, encoding: "utf8" });
});

test("Nana 2 shadow v1 direct runtime is retired", () => {
  assert.throws(
    () => execFileSync(
      python,
      ["-m", "battle_lab.nana_stage2_shadow_runtime", "--nana-profile", "ies"],
      { cwd: root, encoding: "utf8", stdio: "pipe" },
    ),
  );
});
