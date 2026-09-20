import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

async function source(relativePath) {
  return readFile(new URL(relativePath, `file://${root}/`), "utf8");
}

test("uploads Champions videos in resumable chunks and processes every battle", async () => {
  const [component, jobs, api] = await Promise.all([
    source("components/vgc/champions-video-upload.tsx"),
    source("backend/pkmn_vgc/champions_jobs.py"),
    source("backend/pkmn_vgc/champions_jobs_api.py"),
  ]);

  assert.match(component, /CHUNK_BYTES = 8 \* 1024 \* 1024/);
  assert.match(component, /job\.status === "uploading"/);
  assert.match(component, /job\.uploadedBytes/);
  assert.match(component, /maxBattles: 0/);
  assert.match(component, /ocrWorkers/);
  assert.match(component, /Workers OCR/);
  assert.match(component, /Revisar partida/);
  assert.match(component, /Reintentar análisis/);
  assert.match(component, /Reanalizar vídeo/);
  assert.match(component, /Avisos del análisis/);
  assert.match(jobs, /ThreadPoolExecutor\(max_workers=1/);
  assert.match(jobs, /ocr_workers=ocr_workers/);
  assert.match(jobs, /reset_battle_state|ReplayCapturePipeline/);
  assert.match(jobs, /replay-\{index:03d\}/);
  assert.match(api, /@app\.put\("\/jobs\/\{job_id\}\/chunks"\)/);
  assert.match(api, /@app\.get\("\/jobs\/\{job_id\}\/replays\/\{replay_number\}"\)/);
  assert.match(api, /@app\.post\("\/jobs\/\{job_id\}\/retry"\)/);
});

test("keeps the Python processor behind a strict same-origin loopback proxy", async () => {
  const proxy = await source("app/api/champions-jobs/[...path]/route.ts");
  const match = proxy.match(/const ALLOWED_PATH = (\/\^.*\$\/[a-z]*);/);
  assert.ok(match?.[1]);
  const allowed = Function(`"use strict"; return (${match[1]});`)();

  for (const value of [
    "health",
    "jobs",
    "jobs/0123456789abcdef",
    "jobs/0123456789abcdef/chunks",
    "jobs/0123456789abcdef/retry",
    "jobs/0123456789abcdef/replays/1",
    "jobs/0123456789abcdef/replays/12",
  ]) assert.equal(allowed.test(value), true, `${value} debería estar permitido`);

  for (const value of [
    "jobs/not-a-job",
    "jobs/0123456789abcdef/source.mp4",
    "jobs/0123456789abcdef/replays/0",
    "jobs/0123456789abcdef/replays/1/extra",
    "admin",
  ]) assert.equal(allowed.test(value), false, `${value} no debería estar permitido`);

  assert.match(proxy, /127\.0\.0\.1:8770/);
  assert.match(proxy, /await request\.arrayBuffer\(\)/);
});

test("starts the local Champions queue with the web development runtime", async () => {
  const developmentScript = await source("scripts/dev.mjs");

  assert.match(developmentScript, /\.venv-champions/);
  assert.match(developmentScript, /pkmn_vgc\.champions_jobs_api:app/);
  assert.match(developmentScript, /"8770"/);
  assert.match(developmentScript, /championsService\.kill\(\)/);
});
