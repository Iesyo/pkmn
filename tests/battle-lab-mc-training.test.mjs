import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "battle_lab", "mc_training.py");
const censusScript = path.join(root, "battle_lab", "mc_census.py");
const notebook = path.join(root, "colab", "Battle_Lab_MC_Train.ipynb");

test("M-C training module parses the VGCPastes Champions header", () => {
  const program = String.raw`
import json
from battle_lab.mc_training import parse_vgcpastes_mc, pokepaste_raw_url
sample = '''Total Team Count,2\n\nTeam ID,Team Description,Pokepaste,EVs,Extracted paste?,Tournament / Event\nMC2,Two,https://pokepast.es/abcdef,Yes,Extracted,Event B\nMC1,One,https://pokepast.es/123abc,Yes,Owner's,Event A\n'''
rows = parse_vgcpastes_mc(sample)
assert [r['Team ID'] for r in rows] == ['MC2', 'MC1']
assert pokepaste_raw_url(rows[0]['Pokepaste']) == 'https://pokepast.es/abcdef/raw'
print(json.dumps(rows))
`;
  const output = execFileSync("python", ["-c", program], {
    cwd: root,
    encoding: "utf8",
  });
  const rows = JSON.parse(output);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].EVs, "Yes");
});

test("training artifacts pin M-C and keep the census separate from training", () => {
  const source = fs.readFileSync(script, "utf8");
  const censusSource = fs.readFileSync(censusScript, "utf8");
  assert.match(source, /gen9championsvgc2026regmc/);
  assert.match(source, /VGC_BENCH_COMMIT = "d79f9532947ac114dce1dda2456a590afcd375b2"/);
  assert.match(source, /official BC baseline/);
  assert.match(source, /RL M-C \[/);
  assert.match(censusSource, /discardByCause/);
  assert.match(censusSource, /archetypeCoreCoverage/);

  const parsed = JSON.parse(fs.readFileSync(notebook, "utf8"));
  const notebookText = parsed.cells.flatMap((cell) => cell.source ?? []).join("");
  assert.match(notebookText, /Colabs\/LikeNoOneEverWas\/BattleLab\/MC-Training/);
  assert.match(notebookText, /VGCPastes/);
  assert.match(notebookText, /BATTLE-LAB-MC-TRAIN-001/);
  assert.match(notebookText, /RUN_MODE = "CENSUS"/);
  assert.match(notebookText, /mc_census\.py/);
  assert.match(notebookText, /Descartes por causa/);
});

test("census helpers expose reproducible rating, winner, and team-preview metrics", () => {
  const program = String.raw`
import json
from battle_lab.mc_census import preview_species, rating_histogram, winner_role
log = """|player|p1|Alice|avatar|1500\n|player|p2|Bob|avatar|1400\n|poke|p1|Incineroar, L50\n|poke|p1|Rillaboom, L50\n|poke|p1|Flutter Mane, L50\n|poke|p1|Urshifu-Rapid-Strike, L50\n|poke|p1|Amoonguss, L50\n|poke|p1|Landorus-Therian, L50\n|win|Alice"""
assert winner_role(log) == "p1"
assert len(preview_species(log, "p1")) == 6
assert rating_histogram([999, 1200, 1600, 2001])["2000+"] == 1
print(json.dumps({"ok": True}))
`;
  const output = execFileSync("python", ["-c", program], { cwd: root, encoding: "utf8" });
  assert.equal(JSON.parse(output).ok, true);
});
