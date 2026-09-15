import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import url from "node:url";

const root = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), "..");
const audit = path.join(root, "battle_lab", "nana_audit.py");
const python = process.env.PYTHON ?? "python3";

function fixture({ broken = false } = {}) {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "nana-audit-"));
  const profile = path.join(runtime, "nana", "profiles", "ies");
  const sessions = path.join(profile, "sessions");
  fs.mkdirSync(sessions, { recursive: true });
  const sessionId = "abc123def4567890";
  const battleTag = "battle-gen9championsvgc-123";
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
    value: 0.125,
    branches: [
      {
        slot: 1,
        selectedIndex: 7,
        selectedLabel: canonical.labels[0],
        scores: [
          { index: 7, probability: 0.7, selected: true },
          { index: 8, probability: 0.3, selected: false },
        ],
      },
      {
        slot: 2,
        selectedIndex: 8,
        selectedLabel: canonical.labels[1],
        scores: [
          { index: 8, probability: 0.8, selected: true },
          { index: 9, probability: 0.2, selected: false },
        ],
      },
    ],
  };
  const humanAction = {
    id: "1:0",
    first: { kind: "move", value: "protect", target: 0, flags: [] },
    second: { kind: "move", value: "thunderbolt", target: 1, flags: [] },
  };
  const events = [
    {
      schemaVersion: 1,
      timestamp: "2026-09-15T02:00:00Z",
      profileId: "ies",
      sessionId,
      type: "session_start",
      payload: {
        opponent: { id: "team-1" },
        context: { mode: "Nana 0 observational" },
      },
    },
    {
      schemaVersion: 1,
      timestamp: "2026-09-15T02:00:01Z",
      profileId: "ies",
      sessionId,
      type: "team_preview",
      payload: { side: "human", order: [1, 2, 3, 4], state: {} },
    },
    {
      schemaVersion: 1,
      timestamp: "2026-09-15T02:00:02Z",
      profileId: "ies",
      sessionId,
      type: "team_preview",
      payload: { side: "model", order: [4, 3, 2, 1], state: {} },
    },
    {
      schemaVersion: 1,
      timestamp: "2026-09-15T02:00:03Z",
      profileId: "ies",
      sessionId,
      type: "turn_choice",
      payload: {
        turn: 1,
        state: { turn: 1 },
        legalActions: [humanAction],
        humanAction,
        modelAction: broken ? { indices: [0, 0] } : canonical,
        light,
      },
    },
    {
      schemaVersion: 1,
      timestamp: "2026-09-15T02:00:04Z",
      profileId: "ies",
      sessionId,
      type: "session_end",
      payload: {
        result: { winner: "human", turns: 1, battleTag },
        finalState: { finished: true, turn: 1, tag: battleTag },
      },
    },
  ];
  fs.writeFileSync(
    path.join(sessions, `${sessionId}.jsonl`),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
  );
  fs.writeFileSync(
    path.join(profile, "habits.json"),
    JSON.stringify({
      schemaVersion: 1,
      profileId: "ies",
      sessions: 1,
      completedSessions: 1,
      results: { wins: 1, losses: 0, ties: 0 },
      turnChoices: 1,
    }),
  );
  return runtime;
}

test("Nana audit passes a coherent completed Nana 0 session", () => {
  execFileSync(python, ["-m", "py_compile", audit], { cwd: root, encoding: "utf8" });
  const runtime = fixture();
  const output = execFileSync(
    python,
    [audit, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  const report = JSON.parse(output);
  assert.equal(report.pass, true);
  assert.equal(report.turnChoices, 1);
  assert.equal(report.lightDecisionEvents, 1);
  assert.equal(report.winner, "human");
});

test("Nana audit fails when recorded model action diverges from LIGHT canonical action", () => {
  const runtime = fixture({ broken: true });
  const result = spawnSync(
    python,
    [audit, "--runtime-root", runtime, "--nana-profile", "ies", "--json"],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(result.status, 1);
  const report = JSON.parse(result.stdout);
  assert.equal(report.pass, false);
  assert.ok(report.issues.some((issue) => issue.includes("modelAction no coincide")));
});
