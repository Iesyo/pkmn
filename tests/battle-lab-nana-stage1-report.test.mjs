import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const reportModule = path.join(root, "battle_lab", "nana_stage1_report.py");
const python = process.env.PYTHON ?? "python3";

function makeAction(id) {
  return {
    id,
    first: { kind: "move", value: `move-${id}-a`, target: 1, flags: [] },
    second: { kind: "move", value: `move-${id}-b`, target: 2, flags: [] },
  };
}

function fixture() {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "nana-stage1-report-"));
  const sessions = path.join(runtime, "nana", "profiles", "ies", "sessions");
  fs.mkdirSync(sessions, { recursive: true });
  const sessionId = "report-session";
  const legal = [makeAction("a"), makeAction("b"), makeAction("c"), makeAction("d")];
  const rows = [
    { generation: 1, ready: false, history: 6, context: 1, rank: 1, probability: 0.25, chosen: "a" },
    { generation: 2, ready: false, history: 7, context: 2, rank: 4, probability: 0.25, chosen: "d" },
    { generation: 3, ready: true, history: 24, context: 6, rank: 1, probability: 0.5, chosen: "a" },
    { generation: 4, ready: true, history: 25, context: 7, rank: 2, probability: 0.4, chosen: "b" },
  ];
  const events = [];
  for (const row of rows) {
    events.push({
      schemaVersion: 1,
      timestamp: `2026-09-15T03:00:0${row.generation}Z`,
      profileId: "ies",
      sessionId,
      type: "human_prediction",
      payload: {
        generation: row.generation,
        turn: row.generation,
        historySamples: row.history,
        contextSamples: row.context,
        ready: row.ready,
        confidence: row.ready ? 0.5 : 0.05,
        confidenceBand: row.ready ? "medium" : "cold-start",
        candidateCount: 4,
        top: [],
      },
    });
    events.push({
      schemaVersion: 1,
      timestamp: `2026-09-15T03:00:1${row.generation}Z`,
      profileId: "ies",
      sessionId,
      type: "human_choice_observed",
      payload: {
        generation: row.generation,
        turn: row.generation,
        legalActions: legal,
        action: legal.find((candidate) => candidate.id === row.chosen),
        prediction: {
          rank: row.rank,
          chosenProbability: row.probability,
          confidence: row.ready ? 0.5 : 0.05,
          confidenceBand: row.ready ? "medium" : "cold-start",
          historySamples: row.history,
          contextSamples: row.context,
        },
      },
    });
  }
  fs.writeFileSync(
    path.join(sessions, `${sessionId}.jsonl`),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
  );
  return runtime;
}

test("Nana 1 report separates ready observations and compares them to uniform baseline", () => {
  execFileSync(python, ["-m", "py_compile", reportModule], { cwd: root, encoding: "utf8" });
  const runtime = fixture();
  const output = execFileSync(
    python,
    [reportModule, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  const report = JSON.parse(output);
  assert.equal(report.decisions, 4);
  assert.equal(report.readyDecisions, 2);
  assert.equal(report.notReadyDecisions, 2);
  assert.equal(report.ready.top1Accuracy, 0.5);
  assert.equal(report.ready.uniformTop1, 0.25);
  assert.equal(report.ready.top1LiftPp, 25);
  assert.equal(report.ready.top3Accuracy, 1);
  assert.equal(report.ready.uniformTop3, 0.75);
  assert.equal(report.ready.top3LiftPp, 25);
  assert.ok(report.ready.meanChosenProbability > report.ready.uniformChosenProbability);
  assert.ok(report.ready.logLossGain > 0);
});
