import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_team_split.py");
const notebook = path.join(root, "colab", "Battle_Lab_MC_Train.ipynb");

test("M-C team split keeps species signatures isolated from holdout", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "battle-lab-split-"));
  const source = path.join(tmp, "source");
  const output = path.join(tmp, "output");
  fs.mkdirSync(source, { recursive: true });

  const teamText = (species) =>
    species
      .map(
        (name) =>
          `${name} @ Leftovers\nAbility: Pressure\nLevel: 50\nEVs: 4 HP / 252 Atk / 252 Spe\nJolly Nature\n- Protect\n- Tackle`,
      )
      .join("\n\n") + "\n";

  const shared = ["Pikachu", "Charizard", "Blastoise", "Venusaur", "Snorlax", "Gengar"];
  for (let i = 1; i <= 20; i += 1) {
    const species = [...shared];
    if (i > 4) species[0] = `Raichu-${i}`;
    fs.writeFileSync(path.join(source, `mc${i}.txt`), teamText(species));
  }

  execFileSync(
    "python",
    [
      script,
      "--source-dir",
      source,
      "--output-root",
      output,
      "--holdout-fraction",
      "0.20",
      "--seed",
      "42",
    ],
    { cwd: root, encoding: "utf8" },
  );

  const manifest = JSON.parse(fs.readFileSync(path.join(output, "split_manifest.json"), "utf8"));
  assert.equal(manifest.sourceTeams, 20);
  assert.equal(manifest.signatureLeakage, 0);
  assert.ok(manifest.trainTeams > manifest.holdoutTeams);
  assert.equal(manifest.trainTeams + manifest.holdoutTeams, 20);
});

test("M-C training notebook uses train-only corpus and preserves holdout", () => {
  const parsed = JSON.parse(fs.readFileSync(notebook, "utf8"));
  const text = parsed.cells.flatMap((cell) => cell.source ?? []).join("");
  assert.match(text, /battle_lab\.mc_team_split/);
  assert.match(text, /TRAIN_BASE = build_mc_base\(TRAIN_TEAM_DIR\)/);
  assert.match(text, /mc_train\(\s*"bc"/);
  assert.match(text, /mc_train\(\*rl_args\)/);
  assert.match(text, /HOLDOUT_TEAM_DIR/);
  assert.match(text, /signatureLeakage/);
});
