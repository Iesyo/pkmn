import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("model promotion verifies bytes, preserves rollback and checks live activation", () => {
  execFileSync(process.env.PYTHON ?? "python3", ["-m", "unittest", "discover", "-s", "tests", "-p", "model_release_checks.py", "-v"],
    { cwd: root, encoding: "utf8", timeout: 30000 });
});
