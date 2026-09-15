import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const audit = path.join(root, "battle_lab", "nana_stage1_audit.py");
const python = process.env.PYTHON ?? "python3";

function fixture({ reversed = false } = {}) {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "nana-stage1-audit-"));
  const profile = path.join(runtime, "nana", "profiles", "ies");
  const sessions = path.join(profile, "sessions");
  fs.mkdirSync(sessions, { recursive: true });
  const sessionId = "stage1abc1234567";
  const battleTag = "battle-gen9championsvgc-456";
  const canonical = {
    indices: [7, 8],
    labels: ["move 1 target -2", "move 1 target -1"],
    order: "mock-order",
  };
  const light = {
    waiting: false,
    selectionRule: "sequential-greedy",
    branch2ConditionedOnFirst: true,
    canonicalAction: canonical,
    value: 0.25,
    branches: [
      { slot: 1, selectedIndex: 7, scores: [{ index: 7, probability: 1, selected: true }] },
      { slot: 2, selectedIndex: 8, scores: [{ index: 8, probability: 1, selected: true }] },
    ],
  };
  const action = {
    id: "1:0",
    first: { kind: "move", value: "protect", target: 0, flags: [] },
    second: { kind: "move", value: "thunderbolt", target: 1, flags: [] },
  };
  const signature = "move|protect|0|//move|thunderbolt|1|";
  const prediction = {
    schemaVersion: 1,
    timestamp: "2026-09-15T03:00:03Z",
    profileId: "ies",
    sessionId,
    type: "human_prediction",
    payload: {
      generation: 1,
      turn: 1,
      source: "snapshot",
      historySamples: 6,
      contextSamples: 2,
      ready: false,
      confidence: 0.05,
      confidenceBand: "cold-start",
      entropy: 0.9,
      candidateCount: 1,
      top: [{ id: "1:0", signature, probability: 1, first: action.first, second: action.second }],
    },
  };
  const observed = {
    schemaVersion: 1,
    timestamp: "2026-09-15T03:00:04Z",
    profileId: "ies",
    sessionId,
    type: "human_choice_observed",
    payload: {
      generation: 1,
      turn: 1,
      state: { turn: 1 },
      legalActions: [action],
      action,
      actionSignature: signature,
      prediction: {
        rank: 1,
        chosenProbability: 1,
        top1Hit: true,
        top3Hit: true,
        confidence: 0.05,
        confidenceBand: "cold-start",
        historySamples: 6,
        contextSamples: 2,
      },
    },
  };
  const events = [
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:00Z", profileId: "ies", sessionId, type: "session_start", payload: { opponent: { id: "team-1" }, context: { mode: "Nana 0 observational" } } },
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:01Z", profileId: "ies", sessionId, type: "nana_stage", payload: { stage: 1, mode: "Nana 1 predictor observational", influence: 0.0 } },
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:02Z", profileId: "ies", sessionId, type: "team_preview", payload: { side: "human", order: [1,2,3,4], state: {} } },
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:02.5Z", profileId: "ies", sessionId, type: "team_preview", payload: { side: "model", order: [4,3,2,1], state: {} } },
    ...(reversed ? [observed, prediction] : [prediction, observed]),
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:05Z", profileId: "ies", sessionId, type: "turn_choice", payload: { turn: 1, state: { turn: 1 }, legalActions: [action], humanAction: action, modelAction: canonical, light } },
    { schemaVersion: 1, timestamp: "2026-09-15T03:00:06Z", profileId: "ies", sessionId, type: "session_end", payload: { result: { winner: "human", turns: 1, battleTag }, finalState: { finished: true, turn: 1, tag: battleTag } } },
  ];
  fs.writeFileSync(path.join(sessions, `${sessionId}.jsonl`), events.map((event) => JSON.stringify(event)).join("\n") + "\n");
  fs.writeFileSync(path.join(profile, "habits.json"), JSON.stringify({ schemaVersion: 1, profileId: "ies", sessions: 1, completedSessions: 1, results: { wins: 1, losses: 0, ties: 0 }, turnChoices: 1 }));
  fs.writeFileSync(path.join(profile, "predictor.json"), JSON.stringify({ schemaVersion: 1, profileId: "ies", stage: 1, historySamples: 7, metrics: { evaluated: 1, top1Hits: 1, top3Hits: 1 } }));
  return runtime;
}

test("Nana 1 audit passes a pre-choice observational prediction", () => {
  const runtime = fixture();
  const output = execFileSync(
    python,
    [audit, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  const report = JSON.parse(output);
  assert.equal(report.pass, true);
  assert.equal(report.predictions, 1);
  assert.equal(report.observations, 1);
  assert.equal(report.top1Hits, 1);
});

test("Nana 1 audit fails when the prediction is recorded after observation", () => {
  const runtime = fixture({ reversed: true });
  const result = spawnSync(
    python,
    [audit, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(result.status, 1);
  const report = JSON.parse(result.stdout);
  assert.equal(report.pass, false);
  assert.ok(report.issues.some((issue) => issue.includes("predicción no precede")));
});
