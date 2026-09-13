import assert from "node:assert/strict";
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

test("keeps the Colab launcher reproducible and free of saved output", async () => {
  const notebookPath = new URL("../colab/Battle_Lab_Phase_1.ipynb", import.meta.url);
  const notebook = JSON.parse(await readFile(notebookPath, "utf8"));
  const notebookSource = notebook.cells.flatMap((cell) => cell.source).join("");

  assert.equal(notebook.nbformat, 4);
  assert.ok(notebook.cells.length >= 5);
  assert.ok(notebook.cells.every((cell) => cell.cell_type !== "code" || cell.execution_count === null));
  assert.ok(notebook.cells.every((cell) => cell.cell_type !== "code" || cell.outputs.length === 0));
  assert.match(notebookSource, /battle_lab\/showdown_smoke\.py/);
  assert.match(notebookSource, /Pokemon VGC\/BattleLab\/results\/phase-1/);
  assert.match(notebookSource, /--battles/);
  assert.match(notebookSource, /packages_root = runtime_root \/ "python-packages"/);
  assert.match(notebookSource, /battle_lab_python = Path\(sys\.executable\)/);
  assert.match(notebookSource, /battle_lab_env\["PYTHONPATH"\]/);
  assert.match(notebookSource, /"--target", packages_root, "--upgrade"/);
  assert.doesNotMatch(notebookSource, /"-m", "venv"/);
  assert.match(notebookSource, /Instalar dependencias del Battle Lab/);
  assert.match(notebookSource, /attempts=3/);
  assert.match(notebookSource, /stdout=subprocess\.PIPE, stderr=subprocess\.STDOUT/);
  assert.match(notebookSource, /str\(battle_lab_python\)/);
  assert.match(notebookSource, /env=battle_lab_env/);
});
