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
  assert.match(notebookText, /battle_lab\.mc_census/);
  assert.match(notebookText, /Descartes por causa/);
  assert.match(notebookText, /sys\.executable, "-m", "battle_lab\.mc_training"/);
  assert.match(notebookText, /sys\.executable, "-m", "battle_lab\.mc_census"/);
  assert.doesNotMatch(notebookText, /PKMN_ROOT \/ "battle_lab" \/ "mc_census\.py"/);

  const replayCell = parsed.cells.find((cell) =>
    (cell.source ?? []).join("").includes('logs_manifest_path = DATA_ROOT / "logs_manifest.json"'),
  );
  assert.ok(replayCell, "replay census cell must exist");
  const replayCellText = (replayCell.source ?? []).join("");
  assert.match(replayCellText, /^import json, os\n/);

  const injectBlock = source.match(/def inject_mc_support[\s\S]*?\n\ndef scrape_mc_logs/);
  assert.ok(injectBlock, "inject_mc_support block must exist");
  assert.match(injectBlock[0], /with working_directory\(vgc_bench_checkout\):[\s\S]*?vgc_bench\.src\.utils/);
});

test("inject_mc_support imports VGC-Bench from its own checkout and restores cwd", () => {
  const program = String.raw`
import importlib
import os
import pathlib
import sys
import tempfile
from battle_lab.mc_training import inject_mc_support

root = pathlib.Path(tempfile.mkdtemp())
caller = root / "caller"
checkout = root / "vgc-bench"
(caller).mkdir()
(checkout / "vgc_bench" / "src").mkdir(parents=True)
(checkout / "data").mkdir()
(checkout / "data" / "abilities.json").write_text("[]")
(checkout / "vgc_bench" / "src" / "utils.py").write_text(
    'import json\nwith open("data/abilities.json") as f:\n    abilities = json.load(f)\nformat_map = {}\n'
)
(checkout / "vgc_bench" / "src" / "env.py").write_text('from .utils import format_map\n')
(checkout / "vgc_bench" / "src" / "callback.py").write_text('from .utils import format_map\n')
os.chdir(caller)
inject_mc_support(checkout)
assert pathlib.Path.cwd() == caller
utils = importlib.import_module("vgc_bench.src.utils")
assert utils.abilities == []
assert utils.format_map["mc"] == "gen9championsvgc2026regmc"
print("ok")
`;
  const output = execFileSync("python", ["-c", program], { cwd: root, encoding: "utf8" });
  assert.match(output, /ok/);
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
