import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const regulation = path.join(root, "lib", "champions-regulation.mjs");
const smoke = path.join(root, "battle_lab", "showdown_smoke.py");

const OFFICIAL_CHAMPIONS_LEARNSET_HOTFIX = "d3de52a17868aa22e6f5af251a68b86f573b75b1";
const STALE_M_C_PIN = "812501ede865eea397cd6e9a6f040d73ff57cc84";

test("Champions runtime pins the official Sirfetch'd Meteor Assault learnset hotfix", () => {
  const regulationSource = fs.readFileSync(regulation, "utf8");
  const smokeSource = fs.readFileSync(smoke, "utf8");

  assert.match(
    regulationSource,
    new RegExp(`CHAMPIONS_SHOWDOWN_COMMIT = "${OFFICIAL_CHAMPIONS_LEARNSET_HOTFIX}"`),
  );
  assert.doesNotMatch(regulationSource, new RegExp(STALE_M_C_PIN));
  assert.match(smokeSource, /def read_showdown_commit\(/);
  assert.match(smokeSource, /validate-team/);
});
