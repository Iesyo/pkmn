import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const reportModule = path.join(root, "battle_lab", "nana_stage1_calibration_report.py");
const python = process.env.PYTHON ?? "python3";

function action(id) {
  return {
    id,
    first: { kind: "move", value: `a-${id}`, target: 1, flags: [] },
    second: { kind: "move", value: `b-${id}`, target: 2, flags: [] },
  };
}

function writeSession(sessions, sessionId, { marked, probability, rank }) {
  const legal = [action("a"), action("b"), action("c"), action("d")];
  const events = [];
  if (marked) {
    events.push({
      schemaVersion: 1,
      timestamp: "2026-09-15T04:00:00Z",
      profileId: "ies",
      sessionId,
      type: "nana_predictor_version",
      payload: { modelVersion: "contextual-bayes-v2" },
    });
  }
  events.push({
    schemaVersion: 1,
    timestamp: "2026-09-15T04:00:01Z",
    profileId: "ies",
    sessionId,
    type: "human_prediction",
    payload: {
      generation: 1,
      historySamples: 30,
      contextSamples: 8,
      ready: true,
      confidence: 0.2,
      confidenceBand: "low",
      candidateCount: 4,
      top: [],
    },
  });
  events.push({
    schemaVersion: 1,
    timestamp: "2026-09-15T04:00:02Z",
    profileId: "ies",
    sessionId,
    type: "human_choice_observed",
    payload: {
      generation: 1,
      legalActions: legal,
      action: legal[0],
      prediction: {
        rank,
        chosenProbability: probability,
        confidence: 0.2,
        confidenceBand: "low",
        historySamples: 30,
        contextSamples: 8,
      },
    },
  });
  fs.writeFileSync(
    path.join(sessions, `${sessionId}.jsonl`),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
  );
}

test("calibration report excludes old Nana 1 sessions without v2 marker", () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "nana-stage1-calibration-report-"));
  const sessions = path.join(runtime, "nana", "profiles", "ies", "sessions");
  fs.mkdirSync(sessions, { recursive: true });
  writeSession(sessions, "old-v1", { marked: false, probability: 0.01, rank: 4 });
  writeSession(sessions, "new-v2", { marked: true, probability: 0.5, rank: 1 });

  const output = execFileSync(
    python,
    [reportModule, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  const report = JSON.parse(output);
  assert.equal(report.modelVersion, "contextual-bayes-v2");
  assert.equal(report.markedSessions, 1);
  assert.equal(report.decisions, 1);
  assert.equal(report.readyDecisions, 1);
  assert.equal(report.ready.top1Accuracy, 1);
  assert.ok(report.ready.logLossGain > 0);
  assert.equal(report.gate, "collecting");
});
