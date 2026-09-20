import { spawn } from "node:child_process";
import { access, mkdir } from "node:fs/promises";
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

async function championsServiceReady() {
  try {
    const response = await fetch("http://127.0.0.1:8770/health", {
      signal: AbortSignal.timeout(500),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function firstExistingPath(paths) {
  for (const path of paths) {
    try {
      await access(path);
      return path;
    } catch {
      // Try the next platform-specific virtual environment path.
    }
  }
  return null;
}

async function startChampionsService() {
  if (await championsServiceReady()) {
    console.log("[local] Procesador Champions ya disponible en 127.0.0.1:8770.");
    return null;
  }

  const python = await firstExistingPath([
    resolve(projectRoot, ".venv-champions", "Scripts", "python.exe"),
    resolve(projectRoot, ".venv-champions", "bin", "python"),
  ]);
  if (!python) {
    console.warn("[local] No encontramos .venv-champions; la web iniciará sin carga de vídeos Champions.");
    return null;
  }

  console.log("[local] Iniciando la cola local de vídeos Champions...");
  const child = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "pkmn_vgc.champions_jobs_api:app",
      "--app-dir",
      "backend",
      "--host",
      "127.0.0.1",
      "--port",
      "8770",
    ],
    {
      cwd: projectRoot,
      env: { ...localEnvironment, PYTHONUNBUFFERED: "1" },
      shell: false,
      stdio: "inherit",
    },
  );
  let exited = false;
  child.once("exit", () => {
    exited = true;
  });
  for (let attempt = 0; attempt < 40 && !exited; attempt += 1) {
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    if (await championsServiceReady()) return child;
  }
  if (!exited) child.kill();
  console.warn("[local] La cola Champions no pudo iniciar; revisa la instalación de backend[champions].");
  return null;
}

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
const championsService = await startChampionsService();
try {
  await run(
    process.execPath,
    [viteCli, ...process.argv.slice(2)],
    localEnvironment,
    { forwardSignals: true },
  );
} finally {
  if (championsService && championsService.exitCode === null) championsService.kill();
}
