import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const wranglerCli = resolve(
  projectRoot,
  "node_modules",
  "wrangler",
  "bin",
  "wrangler.js",
);
const viteCli = resolve(projectRoot, "node_modules", "vite", "bin", "vite.js");
const wranglerState = resolve(projectRoot, ".wrangler");

await mkdir(resolve(wranglerState, "logs"), { recursive: true });

const localEnvironment = {
  ...process.env,
  WRANGLER_WRITE_LOGS: "false",
  WRANGLER_LOG_PATH: resolve(wranglerState, "logs"),
  MINIFLARE_REGISTRY_PATH: resolve(wranglerState, "registry"),
};

function run(command, args, environment, options = {}) {
  return new Promise((resolveProcess, rejectProcess) => {
    const child = spawn(command, args, {
      cwd: projectRoot,
      env: environment,
      shell: false,
      stdio: "inherit",
    });
    let interruptedSignal;

    const forwardSignal = (signal) => {
      interruptedSignal = signal;
      try {
        child.kill(signal);
      } catch {
        child.kill();
      }
    };

    const cleanup = () => {
      if (!options.forwardSignals) return;
      process.off("SIGINT", forwardSignal);
      process.off("SIGTERM", forwardSignal);
    };

    if (options.forwardSignals) {
      process.on("SIGINT", forwardSignal);
      process.on("SIGTERM", forwardSignal);
    }

    child.once("error", (error) => {
      cleanup();
      rejectProcess(error);
    });
    child.once("exit", (code, signal) => {
      cleanup();
      if (interruptedSignal) {
        resolveProcess();
        return;
      }
      if (signal) {
        rejectProcess(new Error(`El proceso termino por la senal ${signal}.`));
        return;
      }
      if (code !== 0) {
        rejectProcess(new Error(`El proceso termino con codigo ${code}.`));
        return;
      }
      resolveProcess();
    });
  });
}

console.log("[local] Preparando la base SQLite...");
await run(
  process.execPath,
  [
    wranglerCli,
    "d1",
    "migrations",
    "apply",
    "DB",
    "--local",
    "--config",
    "wrangler.local.jsonc",
    "--persist-to",
    ".wrangler/state",
  ],
  { ...localEnvironment, CI: "true" },
);

console.log("[local] Base SQLite lista. Iniciando Like No One Ever Was...");
await run(
  process.execPath,
  [viteCli, ...process.argv.slice(2)],
  localEnvironment,
  { forwardSignals: true },
);
