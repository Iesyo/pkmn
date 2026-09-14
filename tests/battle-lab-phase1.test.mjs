import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
const projectRoot = new URL("../", import.meta.url);

async function text(path) {
  return readFile(new URL(path, projectRoot), "utf8");
}

function parseFixture(source) {
  return source.trim().split(/\n\s*\n/).map((block) => {
    const lines = block.split("\n");
    const item = lines[0].split(" @ ")[1] ?? "";
    const moves = lines.filter((line) => line.startsWith("- "));
    const evLine = lines.find((line) => line.startsWith("EVs: ")) ?? "";
    const statPoints = [...evLine.matchAll(/(\d+)\s+(?:HP|Atk|Def|SpA|SpD|Spe)/g)].map((match) => Number(match[1]));
    return { item, moves, statPoints };
  });
}

test("keeps the Showdown commit in the regulation source of truth", async () => {
  const [regulation, runner] = await Promise.all([
    text("lib/champions-regulation.mjs"),
    text("battle_lab/showdown_smoke.py"),
  ]);

  assert.match(regulation, /CHAMPIONS_SHOWDOWN_COMMIT = "[0-9a-f]{40}"/);
  assert.match(runner, /read_showdown_commit/);
  assert.doesNotMatch(runner, /812501ede865eea397cd6e9a6f040d73ff57cc84/);
  assert.match(runner, /gen9championsvgc2026regmc/);
  assert.match(runner, /127\.0\.0\.1/);
  assert.match(runner, /exports\.repl = false/);
  assert.match(runner, /incomplete_checkout/);
  assert.match(runner, /"--depth", "1", repository/);
  assert.doesNotMatch(runner, /"--no-checkout", repository/);
  assert.match(runner, /run_checked_with_retries/);
  assert.match(runner, /run_long_command_with_retries/);
  assert.match(runner, /MINIMUM_NODE_MAJOR = 24/);
  assert.match(runner, /marker\.get\("nodeMajor"\) == node_major/);
});

test("ships two complete M-C smoke teams with legal Stat Point envelopes", async () => {
  const fixturePaths = [
    "battle_lab/fixtures/mc_team_alpha.txt",
    "battle_lab/fixtures/mc_team_beta.txt",
  ];

  for (const fixturePath of fixturePaths) {
    const team = parseFixture(await text(fixturePath));
    assert.equal(team.length, 6, fixturePath);
    assert.equal(new Set(team.map(({ item }) => item)).size, 6, fixturePath);
    for (const pokemon of team) {
      assert.equal(pokemon.moves.length, 4, fixturePath);
      assert.ok(pokemon.statPoints.every((value) => value <= 32), fixturePath);
      assert.ok(pokemon.statPoints.reduce((sum, value) => sum + value, 0) <= 66, fixturePath);
    }
  }
});

test("ships an auditable 12-team real M-C corpus", async () => {
  const manifest = JSON.parse(await text("battle_lab/corpus/champions-m-c/manifest.json"));

  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.format, "gen9championsvgc2026regmc");
  assert.equal(manifest.source.name, "VGCPastes Repository");
  assert.equal(manifest.source.gid, "2001945654");
  assert.equal(manifest.teams.length, 12);
  assert.equal(new Set(manifest.teams.map((team) => team.id)).size, 12);
  assert.ok(manifest.teams.every((team) => team.roster.length === 6));
  assert.ok(manifest.teams.every((team) => team.pokepasteUrl.startsWith("https://pokepast.es/")));

  for (const team of manifest.teams) {
    const source = await text(`battle_lab/corpus/champions-m-c/${team.file}`);
    assert.equal(parseFixture(source).length, 6, team.id);
    assert.equal(createHash("sha256").update(source).digest("hex"), team.sha256, team.id);
  }
});

test("keeps the canonical Colab launcher reproducible and free of saved output", async () => {
  const notebookPath = new URL("../colab/Battle_Lab.ipynb", import.meta.url);
  const notebook = JSON.parse(await readFile(notebookPath, "utf8"));
  const notebookSource = notebook.cells.flatMap((cell) => cell.source).join("");

  assert.equal(notebook.nbformat, 4);
  assert.ok(notebook.cells.length >= 5);
  assert.ok(notebook.cells.every((cell) => cell.cell_type !== "code" || cell.execution_count === null));
  assert.ok(notebook.cells.every((cell) => cell.cell_type !== "code" || cell.outputs.length === 0));
  assert.match(notebookSource, /battle_lab\/vgc_bench_battle\.py/);
  assert.match(notebookSource, /Pokemon VGC\/BattleLab\/results\/phase-2/);
  assert.match(notebookSource, /--battles/);
  assert.match(notebookSource, /RUN_MODE = "benchmark"/);
  assert.match(notebookSource, /BENCHMARK_BATTLES_PER_BASELINE = 500/);
  assert.match(notebookSource, /--benchmark-battles-per-baseline/);
  assert.match(notebookSource, /Elo interno/);
  assert.match(notebookSource, /--device/);
  assert.match(notebookSource, /--seed/);
  assert.match(notebookSource, /--extra-teams-dir/);
  assert.match(notebookSource, /DRIVE_TEAMS = "Pokemon VGC\/BattleLab\/teams"/);
  assert.match(notebookSource, /teams\['rotation'\]\['uniquePairings'\]/);
  assert.match(notebookSource, /packages_root = runtime_root \/ "python-packages"/);
  assert.match(notebookSource, /battle_lab_python = Path\(sys\.executable\)/);
  assert.match(notebookSource, /battle_lab_env\["PYTHONPATH"\]/);
  assert.match(notebookSource, /"--target", packages_root, "--upgrade"/);
  assert.doesNotMatch(notebookSource, /"-m", "venv"/);
  assert.match(notebookSource, /Instalar dependencias base del Battle Lab/);
  assert.match(notebookSource, /requirements-phase2\.txt/);
  assert.match(notebookSource, /"--no-deps"/);
  assert.match(notebookSource, /attempts=3/);
  assert.match(notebookSource, /stdout=subprocess\.PIPE, stderr=subprocess\.STDOUT/);
  assert.match(notebookSource, /str\(battle_lab_python\)/);
  assert.match(notebookSource, /env=battle_lab_env/);
  assert.match(notebookSource, /Node\.js y npm listos/);
  assert.match(notebookSource, /run_live\(command, cwd=pkmn_root, label="Evaluar a VGC-Bench sin piedad"/);
  assert.match(notebookSource, /NODE_VERSION = "24\.21\.0"/);
  assert.match(notebookSource, /"npm", "install", "--global", "n@latest"/);
  assert.match(notebookSource, /if node_major < 24/);
});

test("pins and verifies the merciless VGC-Bench inference path", async () => {
  const [runner, benchmark, corpus, requirements, readme, builder] = await Promise.all([
    text("battle_lab/vgc_bench_battle.py"),
    text("battle_lab/benchmarking.py"),
    text("battle_lab/team_corpus.py"),
    text("battle_lab/requirements-phase2.txt"),
    text("battle_lab/README.md"),
    text("components/vgc/team-builder.tsx"),
  ]);

  assert.match(runner, /VGC_BENCH_COMMIT = "[0-9a-f]{40}"/);
  assert.match(runner, /VGC_BENCH_CHECKPOINT_REVISION = "[0-9a-f]{40}"/);
  assert.match(runner, /VGC_BENCH_CHECKPOINT_SHA256 = \(/);
  assert.match(runner, /57f5edcab415cf6ccc1b6231923c8b66d/);
  assert.match(runner, /deterministic": True/);
  assert.match(runner, /choose_on_teampreview/);
  assert.match(runner, /EXPECTED_OBSERVATION_LENGTH = 6_936/);
  assert.match(runner, /EXPECTED_ACTION_BRANCHES = \(107, 107\)/);
  assert.match(runner, /collapse_species_aliases/);
  assert.match(runner, /build_pairing_schedule/);
  assert.match(runner, /player_a\.update_team\(pairing\.alpha\.team_text\)/);
  assert.match(runner, /"schemaVersion": 4/);
  assert.match(runner, /os\.replace\(partial, destination\)/);
  assert.match(runner, /RandomPlayer/);
  assert.match(runner, /MaxBasePowerPlayer/);
  assert.match(runner, /SimpleHeuristicsPlayer/);
  assert.match(runner, /run_baseline_benchmark/);
  assert.match(runner, /installed_poke_env_metadata/);
  assert.match(runner, /direct_url\.json/);
  assert.match(runner, /reset_battles\(\)/);
  assert.match(benchmark, /DEFAULT_BENCHMARK_BATTLES_PER_BASELINE = 500/);
  assert.match(benchmark, /build_mirrored_benchmark_schedule/);
  assert.match(benchmark, /one-virtual-draw/);
  assert.match(benchmark, /log10\(score \/ \(1 - score\)\)/);
  assert.match(corpus, /DEFAULT_CORPUS_MANIFEST/);
  assert.match(corpus, /balanced-round-robin/);
  assert.match(corpus, /team-builder-drive/);
  assert.match(builder, /Descargar \.txt/);
  assert.match(builder, /downloadShowdownPaste/);
  assert.equal(requirements.trim().split("\n").at(-1), "stable-baselines3==2.8.0");
  assert.match(readme, /500 combates contra cada baseline/);
  assert.match(readme, /self-play como modo alternativo/);
  assert.match(readme, /M-A\/M-B/);
});
