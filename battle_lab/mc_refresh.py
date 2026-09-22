"""One resumable Colab cycle: current data -> candidate -> canonical production comparison.

Based on the validated 40_017 v3 execution pattern and the existing Battle Lab
LIGHT v3/holdout runners. Every expensive phase runs in a fresh Python process.
"""
from __future__ import annotations

import argparse
import csv
import fcntl
import importlib
import importlib.metadata
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from battle_lab.mc_refresh_data import (
    filter_training_logs, read_json, refresh_replays, resolve_workers,
    snapshot_split, sync_teams,
)
from battle_lab.mc_training import (
    DEFAULT_FORMAT, VGC_BENCH_COMMIT, VGC_BENCH_REPOSITORY, atomic_json,
    human_seconds, sha256_file, sha256_text, utc_now,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_SEED = 260913
LIGHT_SHA256 = "fa8687d08feeb169f4eb4f4a078b65971346e2ef5b0ca0ff899e811721075759"
PROFILES = {
    "CENSUS": {"steps": 0, "bcEpochs": 0},
    "LIGHT": {"steps": 196_608, "bcEpochs": 3},
    "NORMAL": {"steps": 786_432, "bcEpochs": 5},
    "HARD": {"steps": 3_145_728, "bcEpochs": 10},
}
STAGES = ("prepare", "teams", "replays", "split", "trajectories", "bc", "rl", "evaluate")
LABELS = {"prepare": "Preparar motores fijados", "teams": "Actualizar pastes",
          "replays": "Actualizar partidas humanas", "split": "Aislar entrenamiento y evaluación",
          "trajectories": "Convertir demostraciones humanas", "bc": "Aprender de humanos",
          "rl": "PPO/self-play", "evaluate": "Evaluar el candidato"}


def bc_data_gate(trajectories: int, transitions: int, minimum_transitions: int = 10000) -> dict:
    if minimum_transitions not in (9500, 10000):
        raise ValueError("El mínimo BC debe ser 9500 (piloto) o 10000 (estándar).")
    return {"bcEligible": trajectories >= 1000 and transitions >= minimum_transitions,
            "bcMinimumTrajectories": 1000, "bcMinimumTransitions": minimum_transitions,
            "bcDataPolicy": "pilot_9500" if minimum_transitions == 9500 else "standard_10000"}


def git_sha(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def runtime_versions(device: str = "cpu") -> dict:
    # The notebook's structural tests use only the standard library. Import the
    # actual training stack before spending time collecting data; Colab can have
    # unrelated preinstalled extras with conflicting pip requirements.
    for module in ("stable_baselines3", "imitation.algorithms.bc", "supersuit",
                   "poke_env.environment", "poke_env.ps_client", "nashpy", "huggingface_hub"):
        importlib.import_module(module)
    import numpy as np
    import torch
    # Exercise the NumPy bridge and the selected CPU/GPU without training.
    probe = torch.from_numpy(np.ones(1, dtype=np.float32)).to(device)
    if probe.sum().item() != 1:
        raise RuntimeError("Falló la comprobación mínima de PyTorch.")
    versions = {"python": sys.version.split()[0],
                "node": subprocess.check_output(["node", "--version"], text=True).strip()}
    for package in ("torch", "numpy", "stable-baselines3", "imitation", "poke-env", "supersuit"):
        versions[package] = importlib.metadata.version(package)
    print("✅ Preflight de entrenamiento: imports y PyTorch OK · " + device, flush=True)
    return versions


def ensure_champion(root: Path) -> dict:
    """Resolve the single canonical production model from its registry."""
    registry = root / "Refresh" / "champion.json"
    champion = read_json(registry)
    if champion is None:
        raise RuntimeError("Falta Refresh/champion.json; no existe un productivo canónico verificable.")
    if champion.get("format") != DEFAULT_FORMAT:
        raise RuntimeError("El champion pertenece a otra regulación.")
    if not Path(champion["checkpoint"]).is_file():
        raise RuntimeError("Falta el checkpoint productivo canónico: " + champion["checkpoint"])
    if sha256_file(Path(champion["checkpoint"])) != champion["sha256"]:
        raise RuntimeError("El checkpoint productivo no coincide con su SHA-256.")
    return champion


def production_reference(root: Path) -> dict:
    """Return the single promoted champion as the canonical production policy.

    Historical checkpoints may remain on disk for reproducibility, but normal
    training and direct evaluation must never silently benchmark against them.
    """
    champion = ensure_champion(root)
    return {**champion, "source": "champion-registry", "role": "production",
            "liveDeploymentVerified": False}


def config_identity(config: dict) -> str:
    # Resource telemetry changes between Colab sessions. It is not an experiment parameter.
    stable = {k: v for k, v in config.items() if k != "workers"}
    return sha256_text(json.dumps(stable, sort_keys=True, ensure_ascii=False))


def select_run(root: Path, config: dict, action: str = "auto", run_id: str = "") -> Path:
    active = read_json(root / "Refresh" / "active_run.json", {})
    requested = run_id or (active.get("runId", "") if action != "new" else "")
    if requested:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", requested):
            raise ValueError("Invalid RUN_ID")
        path = root / "Refresh" / "runs" / requested
        previous = read_json(path / "config.json")
        state = read_json(path / "status.json", {})
        if previous and (state.get("state") not in {"completed", "census_completed"} or run_id):
            if config_identity(previous) != config_identity(config):
                raise RuntimeError("La ejecución pendiente tiene otro código, champion o configuración. "
                                   "Reanuda con sus parámetros o elige RUN_ACTION='new'.")
            # Recompute safe capacity on this Colab VM without changing the experiment.
            atomic_json(path / "config.json", {**previous, "workers": config.get("workers", {})})
            return path
        if action == "resume":
            raise RuntimeError("No hay una ejecución incompleta compatible para reanudar.")
    if action == "resume":
        raise RuntimeError("No hay ejecución que reanudar.")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / "Refresh" / "runs" / name
    path.mkdir(parents=True, exist_ok=False)
    atomic_json(path / "config.json", config)
    atomic_json(root / "Refresh" / "active_run.json", {"runId": name, "codeSha": config["codeSha"]})
    return path


def artifact_result(path: Path, *, files=(), **extra) -> dict:
    return {"artifact": str(path), "artifactSha256": sha256_file(path),
            "fileHashes": {str(p): sha256_file(p) for p in sorted(files)}, **extra}


def check_artifact(result: dict) -> None:
    if "artifact" in result and sha256_file(Path(result["artifact"])) != result["artifactSha256"]:
        raise RuntimeError("Un artefacto de una fase terminada fue modificado.")
    for path, digest in result.get("fileHashes", {}).items():
        if sha256_file(Path(path)) != digest:
            raise RuntimeError("Los datos congelados fueron modificados: " + path)


def perform_stage(stage: str, config: dict, run: Path) -> dict:
    from battle_lab import mc_training as training
    from battle_lab.mc_rl_light_v3 import alias_mc_runtime_catalogs
    from battle_lab.showdown_smoke import (
        DEFAULT_SHOWDOWN_REPOSITORY, ensure_showdown_checkout, install_runtime_config,
        read_showdown_commit, running_showdown, validate_team,
    )
    root = Path(config["root"])
    refresh = root / "Refresh"
    runtime = Path(config["runtimeRoot"])
    vgc, showdown = runtime / "vgc-bench", runtime / "pokemon-showdown"
    legacy_split = root / "data" / "team-splits" / f"seed-{LEGACY_SEED}"
    registry = refresh / "partitions.json"
    workers = config["workers"]["workers"]
    dependencies = {"split": ("teams", "replays"), "trajectories": ("split",),
                    "bc": ("split", "trajectories"), "rl": ("split", "bc"), "evaluate": ("rl",)}
    for dependency in dependencies.get(stage, ()):
        check_artifact(read_json(run / "phases" / (dependency + ".json")))
    if stage == "prepare":
        from battle_lab.vgc_bench_battle import ensure_vgc_bench_checkout
        if read_showdown_commit() != config["showdownSha"] or git_sha(PROJECT_ROOT) != config["codeSha"]:
            raise RuntimeError("El código cambió después de congelar la ejecución.")
        ensure_showdown_checkout(checkout=showdown, repository=DEFAULT_SHOWDOWN_REPOSITORY,
                                 commit=config["showdownSha"], logs_dir=run / "logs")
        install_runtime_config(showdown)
        ensure_vgc_bench_checkout(checkout=vgc, repository=VGC_BENCH_REPOSITORY, commit=VGC_BENCH_COMMIT)
        return {"showdown": git_sha(showdown), "vgcBench": git_sha(vgc)}
    if stage == "teams":
        result = sync_teams(output=run / "teams", cache=refresh / "cache" / "pastes", workers=workers,
                            validate=lambda text: validate_team(showdown, DEFAULT_FORMAT, text),
                            extra_dir=refresh / "extra-teams")
        return artifact_result(run / "teams" / "manifest.json", usableTeams=result["usableTeams"],
                               files=(run / "teams").glob("mc*.txt"),
                               rejectedTeams=len(result["errors"]))
    alias_mc_runtime_catalogs(vgc)
    training.inject_mc_support(vgc)
    if stage == "replays":
        cache = refresh / "cache" / "replays"
        result = refresh_replays(cache=cache, legacy_data=root / "data", workers=workers,
                                  max_pages=config["replayPages"])
        shutil.copytree(cache / "battle_logs", run / "raw_logs" / "battle_logs", dirs_exist_ok=True)
        atomic_json(run / "raw_logs" / "manifest.json", result)
        return artifact_result(run / "raw_logs" / "manifest.json",
                               files=(run / "raw_logs" / "battle_logs").glob("*.json"), **result)
    if stage == "split":
        split = snapshot_split(teams=run / "teams", output=run / "split", registry=registry,
                                legacy_split=legacy_split, seed=LEGACY_SEED)
        filtered = filter_training_logs(cache=run / "raw_logs", output=run / "human" / "battle_logs",
                                          registry=registry, min_rating=1200)
        # Freeze lineage after assigning every demonstration's signatures too.
        shutil.copy2(registry, run / "partitions.json")
        return artifact_result(run / "split" / "split_manifest.json", trainTeams=split["trainTeams"],
                               files=[*(run / "split").rglob("*.txt"),
                                      *(run / "human").rglob("*.json"), run / "partitions.json"],
                               holdoutTeams=split["holdoutTeams"], freshHoldoutTeams=split["freshHoldoutTeams"],
                               eligibleLogs=filtered["eligibleLogs"], filter=filtered)
    if stage == "trajectories":
        filtered = read_json(run / "human" / "log_filter_manifest.json")
        if filtered["eligibleLogs"] == 0:
            result = {"trajectories": 0, "transitions": 0, "state": "no_eligible_logs"}
            atomic_json(run / "human" / "trajs_manifest.json", result)
        else:
            import functools
            import multiprocessing
            converter = importlib.import_module("vgc_bench.logs2trajs")
            # Colab is Linux. Preserve the M-C catalog aliases in parser workers.
            converter.ProcessPoolExecutor = functools.partial(converter.ProcessPoolExecutor,
                                                                mp_context=multiprocessing.get_context("fork"))
            result = training.build_mc_trajectories(vgc_bench_checkout=vgc, data_root=run / "human",
                                                     num_workers=workers, min_rating=1200, only_winner=True)
        return artifact_result(run / "human" / "trajs_manifest.json", **result,
                               files=(run / "human" / "trajs").glob("*.pkl"),
                               **bc_data_gate(result["trajectories"], result["transitions"],
                                              config.get("bcMinTransitions", 10000)))
    if stage == "bc":
        human = read_json(run / "phases" / "trajectories.json")
        if not human["bcEligible"]:
            minimum = human.get("bcMinimumTransitions", 10000)
            return {"state": "skipped", "reason": f"Insufficient eligible human demonstrations (1000 trajectories/{minimum} transitions).",
                    **config["champion"]}
        split = read_json(run / "split" / "split_manifest.json")
        training.install_mc_teams(vgc, run / "split" / "train")
        result = training.fine_tune_bc(
            vgc_bench_checkout=vgc, data_root=run / "human", output_root=run / "training",
            port=config["port"], device=config["device"], seed=config["seed"],
            epochs=config["profile"]["bcEpochs"], div_frac=.1, eval_battles=0, team_count=split["trainTeams"],
            num_workers=workers,
            initial_checkpoint=Path(config["champion"]["checkpoint"]), initial_sha256=config["champion"]["sha256"])
        return artifact_result(Path(result["finalCheckpoint"]), state="completed",
                               checkpoint=result["finalCheckpoint"], sha256=result["finalCheckpointSha256"],
                               epochs=result["epochs"], trainingHistory=result["history"],
                               dataPolicy=human.get("bcDataPolicy", "standard_10000"))
    if stage == "rl":
        from battle_lab import mc_rl_light_v2 as runner
        runner.extend_mc_runtime_catalogs = alias_mc_runtime_catalogs
        initial = read_json(run / "phases" / "bc.json")
        with running_showdown(showdown, config["port"], run / "logs" / "training-showdown.log"):
            result = runner.run_light(
                vgc_bench_checkout=vgc, output_root=run / "training", team_dir=run / "split" / "train",
                port=config["port"], device=config["device"], seed=config["seed"],
                total_steps=config["profile"]["steps"], num_envs=config["numEnvs"], checkpoint_every=24576,
                initial_checkpoint=Path(initial["checkpoint"]), initial_sha256=initial["sha256"],
                actor_unfreeze_step=98304 if initial["state"] == "completed" else 0)
        return artifact_result(Path(result["finalCheckpoint"]), state="completed",
                               checkpoint=result["finalCheckpoint"], sha256=result["finalCheckpointSha256"],
                               steps=result["finalStep"], runtime=result.get("runtime", {}))
    if stage == "evaluate":
        from battle_lab.mc_refresh_eval import evaluate, evaluate_direct
        candidate = read_json(run / "phases" / "rl.json")
        direct = config.get("evaluationProtocol") == "direct-v1"
        reference = {"production": config["production"], "runtime_versions": config["runtimeVersions"]} if direct else {"champion": config["champion"]}
        result = (evaluate_direct if direct else evaluate)(vgc_root=vgc, showdown=showdown, run=run, **reference,
                           candidate=candidate, battles=config["battles"], seed=config["seed"],
                           port=config["port"], device=config["device"], battle_format=DEFAULT_FORMAT,
                           code_sha=config["codeSha"], showdown_sha=config["showdownSha"])
        return artifact_result(Path(result["comparisonFile"]), **result)
    raise ValueError(stage)


def start_phase_timing(status: dict, stage: str) -> dict:
    """Keep observed work from every attempt, including Colab interruptions."""
    attempts = status.setdefault("phaseAttempts", [])
    for previous in attempts:
        if previous["state"] == "running":
            previous["state"] = "interrupted"
    attempt = {"stage": stage, "startedAt": utc_now(), "elapsedSeconds": 0.0,
               "state": "running", "measurementComplete": False}
    attempts.append(attempt)
    return attempt


def timing_summary(status: dict) -> dict:
    attempts = status.get("phaseAttempts", [])
    if not attempts:
        return {"available": False, "note": "Esta corrida no registró tiempos por intento."}
    phases = {}
    for attempt in attempts:
        phases[attempt["stage"]] = phases.get(attempt["stage"], 0.0) + attempt["elapsedSeconds"]
    phases = {stage: round(seconds, 2) for stage, seconds in phases.items()}
    first = attempts[0]["startedAt"]
    last = attempts[-1].get("finishedAt") or attempts[-1].get("heartbeatAt") or attempts[-1]["startedAt"]
    wall = max(0.0, (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds())
    return {"available": True, "startedAt": first, "lastObservedAt": last,
            "observedActiveSeconds": round(sum(phases.values()), 2),
            "observedTrainingSeconds": round(sum(phases.get(stage, 0) for stage in ("bc", "rl")), 2),
            "observedEvaluationSeconds": phases.get("evaluate", 0.0),
            "wallSeconds": round(wall, 2), "phaseSeconds": phases,
            "attempts": len(attempts),
            "measurementComplete": all(attempt.get("measurementComplete", False) for attempt in attempts),
            "scope": "Fases ejecutadas de este ciclo; excluye montaje de Drive e instalación inicial del notebook. "
                     "Activo suma los intentos registrados; transcurrido incluye pausas entre sesiones. "
                     "Una desconexión abrupta puede perder el tiempo posterior al último heartbeat."}


def run_child(stage: str, run: Path, status: dict, phase_seconds: dict,
              *, worker_config: Path | None = None, status_root: Path | None = None) -> None:
    status_root = status_root or run
    log_path = status_root / "logs" / (stage + ".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", "-m", "battle_lab.mc_refresh", "--worker-stage", stage, "--worker-run", str(run)]
    if worker_config is not None:
        command += ["--worker-config", str(worker_config)]
    proc = subprocess.Popen(command, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, start_new_session=True)
    lines = queue.Queue()
    def pump():
        for line in proc.stdout:
            lines.put(line)
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    started = last_output = time.monotonic()
    last_heartbeat = 0.0
    attempt = start_phase_timing(status, stage)
    succeeded = False
    try:
        atomic_json(status_root / "status.json", status)
        with log_path.open("a", encoding="utf-8") as log:
            while proc.poll() is None or reader.is_alive() or not lines.empty():
                try:
                    line = lines.get(timeout=.2)
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                    last_output = time.monotonic()
                except queue.Empty:
                    pass
                elapsed = time.monotonic() - started
                if time.monotonic() - last_heartbeat >= 5:
                    estimate = phase_seconds.get(stage)
                    eta = max(0, estimate - elapsed) if estimate and estimate > elapsed else None
                    done = len(status["completedStages"])
                    pct = done / status["totalStages"]
                    print(f"[{('█' * round(24*pct)).ljust(24, '░')}] fase {STAGES.index(stage)+1}/{status['totalStages']} · "
                          f"{LABELS[stage]} · {human_seconds(elapsed)} · ETA fase {human_seconds(eta)}", flush=True)
                    status.update(phase=stage, heartbeatAt=utc_now(), phaseElapsedSeconds=round(elapsed, 1),
                                  phaseEtaSeconds=eta, state="running")
                    attempt.update(elapsedSeconds=round(elapsed, 2), heartbeatAt=status["heartbeatAt"])
                    atomic_json(status_root / "status.json", status)
                    last_heartbeat = time.monotonic()
                if proc.poll() is None and time.monotonic() - last_output > 1800:
                    raise RuntimeError(f"{stage}: 30 minutos sin salida del proceso; se conserva el checkpoint.")
        if proc.returncode:
            raise RuntimeError(f"La fase {stage} terminó con código {proc.returncode}; consulta {log_path}")
        succeeded = True
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        reader.join(timeout=2)
        attempt.update(elapsedSeconds=round(time.monotonic() - started, 2), finishedAt=utc_now(),
                       state="completed" if succeeded else "failed", measurementComplete=True)
        atomic_json(status_root / "status.json", status)
    phase_seconds[stage] = attempt["elapsedSeconds"]


def write_report(run: Path, config: dict, status: dict, *, report_root: Path | None = None) -> dict:
    destination = report_root or run
    phases = {p.stem: read_json(p) for p in (run / "phases").glob("*.json")}
    if report_root:
        phases["evaluate"] = read_json(report_root / "phases" / "evaluate.json")
    evaluation = phases.get("evaluate", {})
    comparison = evaluation.get("comparison")
    verdict = {"pass": "MEJORA_OBSERVADA", "mixed": "RESULTADO_MIXTO", "fail": "SIN_MEJORA"}.get(
        (comparison or {}).get("verdict"), "CENSO_COMPLETADO")
    previous = read_json(Path(config["root"]) / "Refresh" / "latest_result.json", {})
    direct = (comparison or {}).get("protocol") == "direct-v1"
    report = {"schemaVersion": 1, "generatedAt": utc_now(), "runId": run.name,
              "state": status["state"], "verdict": verdict, "config": config, "phases": phases,
              "timing": timing_summary(status),
              "previousRunId": previous.get("runId"), "championChanged": False,
              "note": "El gate heredado detecta mejora observada. No prueba significancia estadística ni nivel humano. El champion y la ROG no se reemplazan automáticamente."}
    if direct:
        report["note"] = "MEJORA_OBSERVADA significa score >50% directamente contra la referencia productiva fijada. Base y Simple Heuristics se informan por separado. Es descriptivo, sin confirmar significancia estadística ni nivel humano. No cambia champion/ROG/Nana. La referencia documentada no verifica el runtime vivo de la ROG."
    atomic_json(destination / "report.json", report)
    lines = ["Battle Lab M-C — actualización, entrenamiento y evaluación", "",
             f"Ejecución: {run.name}", f"Resultado: {verdict}", f"Modo: {config['mode']}",
             f"Champion: {config['champion']['id']} ({config['champion']['sha256']})",
             f"Pastes completos: {phases['teams']['usableTeams']}",
             f"Partidas OTS: {phases['replays']['totalLogs']} (+{phases['replays']['addedLogs']} nuevas)",
             f"Partidas aptas para conversión: {phases['split']['eligibleLogs']}",
             f"Equipos train / holdout: {phases['split']['trainTeams']} / {phases['split']['holdoutTeams']}",
             f"Equipos holdout recién reservados: {phases['split']['freshHoldoutTeams']}",
             f"Trayectorias / transiciones elegibles: {phases['trajectories']['trajectories']} / {phases['trajectories']['transitions']}",
             f"BC-MC: {phases.get('bc', {}).get('state', 'pendiente' if phases['trajectories']['bcEligible'] else 'datos insuficientes')}",
             f"Mínimo BC: 1000 trayectorias / {config.get('bcMinTransitions', 10000)} transiciones"]
    timing = report["timing"]
    if timing["available"]:
        lines += ["", f"Tiempo activo registrado del ciclo: {human_seconds(timing['observedActiveSeconds'])}",
                  f"Aprendizaje BC + PPO registrado: {human_seconds(timing['observedTrainingSeconds'])}",
                  f"Evaluación registrada: {human_seconds(timing['observedEvaluationSeconds'])}",
                  f"Tiempo transcurrido, incluidas pausas: {human_seconds(timing['wallSeconds'])}"]
        for stage, seconds in timing["phaseSeconds"].items():
            lines.append(f"  {LABELS.get(stage, stage)}: {human_seconds(seconds)}")
        lines.append(timing["scope"])
        if not timing["measurementComplete"]:
            lines.append("Medición parcial: al menos un intento perdió su cierre; se conserva solo el tiempo observado.")
    for fmt, scrape in phases["replays"]["formats"].items():
        lines.append(f"Fuente {fmt}: {scrape['stopReason']} · {scrape['pages']} páginas · +{scrape['added']} partidas")
    lines.append("Filtro humano: " + json.dumps(phases["split"]["filter"]["counts"], ensure_ascii=False))
    if "numEnvs" in config:
        lines.append(f"Entrenamiento: {config['device']} · {config['numEnvs']} entornos; evaluación: una batalla simultánea")
    runtime = phases.get("rl", {}).get("runtime", {})
    if runtime:
        lines.append("Runtime PPO medido: " + json.dumps(runtime, ensure_ascii=False))
    if config.get("evaluationRecovery"):
        lines.append("Código del entrenamiento conservado: " + config["evaluationRecovery"]["trainingCodeSha"])
    if comparison and direct:
        lines += [f"Batallas directas: {evaluation['battles']}",
                  f"Modelo productivo de referencia: {config['production']['sha256']}",
                  f"Holdout: {evaluation['holdoutView']['holdoutTeams']} equipos únicos", ""]
        shutil.copyfile(Path(evaluation["comparisonFile"]).with_suffix(".csv"), destination / "comparison.csv")
        for opponent, record in comparison["opponents"].items():
            lines.append(f"Candidato vs {opponent}: {record['wins']} victorias / {record['losses']} derrotas / {record['ties']} empates · score {record['scorePercent']:.2f}%")
            lines.append("Replays: " + record["replays"])
        lines += ["", f"Candidato: {phases['rl']['checkpoint']}", f"SHA256 candidato: {phases['rl']['sha256']}"]
    elif comparison:
        view = evaluation.get("holdoutView", {})
        if view:
            lines.append(f"Holdout evaluado: {view['holdoutTeams']} equipos únicos de {view['sourceHoldoutTeams']} archivos reservados")
        lines += [f"Batallas de evaluación: {evaluation['battles']}",
                  f"Score global champion: {comparison['publicOverallScorePercent']:.2f}%",
                  f"Score global candidato: {comparison['lightOverallScorePercent']:.2f}%",
                  f"Delta global: {comparison['overallDeltaPercentagePoints']:+.2f} puntos porcentuales", ""]
        with (destination / "comparison.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["control", "champion_score_pct", "candidate_score_pct", "delta_pp"])
            for control, values in comparison["controls"].items():
                writer.writerow([control, values["publicScorePercent"], values["lightScorePercent"], values["deltaPercentagePoints"]])
                lines.append(f"{values['label']}: {values['publicScorePercent']:.2f}% → {values['lightScorePercent']:.2f}% ({values['deltaPercentagePoints']:+.2f} pp)")
        lines += ["", "Holdout recién reservado (no usado en entrenamientos M-C anteriores de esta línea):"]
        for name, fresh in evaluation["freshHoldout"].items():
            score = "sin muestra" if fresh["scorePercent"] is None else f"{fresh['scorePercent']:.2f}%"
            lines.append(f"{name}: {fresh['games']} batallas, {fresh['teamsTested']} equipos, score {score}")
        lines += ["", f"Candidato: {phases['rl']['checkpoint']}", f"SHA256 candidato: {phases['rl']['sha256']}"]
    lines += ["", report["note"], "", f"Informe completo: {destination / 'report.json'}",
              f"Código: {config['codeSha']}", f"Showdown: {config['showdownSha']}"]
    text = "\n".join(lines) + "\n"
    (destination / "report.txt").write_text(text, encoding="utf-8")
    refresh = Path(config["root"]) / "Refresh"
    prefix = "latest_direct" if report_root else "latest"
    atomic_json(refresh / (prefix + "_result.json"), report)
    (refresh / (prefix + "_run.txt")).write_text(text, encoding="utf-8")
    print(text, flush=True)
    return report


def recovery_config(root: Path, run_id: str, *, code_sha: str, versions: dict,
                    direct: bool = False) -> tuple[Path, dict]:
    """Validate only the immutable inputs an evaluation actually consumes.

    Training-only payloads (raw logs, trajectories and BC intermediates) can be
    thousands of small files on mounted Drive. Re-hashing them cannot change an
    evaluation result once the final checkpoint and frozen split are fixed, so
    recovery checks their phase metadata but does not re-read those payloads.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid recovery RUN_ID")
    run = root / "Refresh" / "runs" / run_id
    original = read_json(run / "config.json")
    if not original or Path(original["root"]).resolve() != root.resolve():
        raise RuntimeError("Falta la configuración original de esta corrida.")
    if original.get("mode") == "CENSUS":
        raise RuntimeError("Un censo sin entrenamiento no puede recuperar evaluación.")
    if versions != original["runtimeVersions"]:
        raise RuntimeError("El runtime cambió; restaura las versiones de config.json antes de evaluar.")

    phases = {}
    for stage in STAGES[:-1]:
        phase = read_json(run / "phases" / (stage + ".json"))
        if not phase:
            raise RuntimeError("Falta una fase anterior completa: " + stage)
        phases[stage] = phase

    candidate = phases["rl"]
    if candidate.get("state") != "completed" or candidate.get("steps", -1) < original["profile"]["steps"]:
        raise RuntimeError("El entrenamiento no está completo; no se puede saltar a evaluación.")

    # The evaluation consumes the frozen team split, not raw replays/trajectories.
    # Verify the split manifest itself here; evaluation_corpus() revalidates every
    # train/holdout team exactly once immediately before playing battles.
    split = phases["split"]
    split_manifest = run / "split" / "split_manifest.json"
    if split.get("artifact") != str(split_manifest) or not split_manifest.is_file():
        raise RuntimeError("Falta el manifiesto congelado de train/holdout.")
    if sha256_file(split_manifest) != split.get("artifactSha256"):
        raise RuntimeError("El manifiesto congelado de train/holdout cambió.")

    # Verify the final candidate once. A direct evaluation resolves and verifies
    # the current canonical production separately; a legacy recovery still needs
    # the original champion because it is one of its two evaluated policies.
    check_artifact(candidate)
    if not direct:
        champion = original["champion"]
        if sha256_file(Path(champion["checkpoint"])) != champion["sha256"]:
            raise RuntimeError("El champion original cambió antes de recuperar evaluación.")

    validation = {
        "mode": "evaluation-inputs-only",
        "phaseMetadataChecked": list(STAGES[:-1]),
        "verified": ["split_manifest", "candidate_checkpoint"]
                    + ([] if direct else ["original_champion_checkpoint"]),
        "deferredToEvaluation": ["train_holdout_team_hashes", "production_checkpoint"] if direct
                                else ["train_holdout_team_hashes"],
        "skippedTrainingPayloads": ["teams", "replays", "trajectories", "bc"],
    }
    print("⚡ Validación ligera: checkpoint + manifiesto; no se releen trayectorias/logs de entrenamiento.", flush=True)
    config = {**original, "codeSha": code_sha,
              "evaluationRecovery": {"trainingCodeSha": original["codeSha"],
                                     "sourceConfigSha256": sha256_file(run / "config.json"),
                                     "candidateSha256": candidate["sha256"],
                                     "scope": ["prepare", "evaluate"],
                                     "validation": validation}}
    return run, config


def recover_evaluation(root: Path, run_id: str = "", *, direct: bool = False,
                       battles: int = 500) -> None:
    run_id = run_id or read_json(root / "Refresh" / "active_run.json", {}).get("runId", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("No hay una corrida válida que recuperar.")
    original = read_json(root / "Refresh" / "runs" / run_id / "config.json")
    if not original:
        raise RuntimeError("No existe config.json para la corrida seleccionada.")
    run, config = recovery_config(root, run_id, code_sha=git_sha(PROJECT_ROOT),
                                  versions=runtime_versions(original["device"]), direct=direct)
    if direct:
        if battles < 2 or battles % 2:
            raise ValueError("Las batallas por rival deben ser pares y al menos 2")
        config.update(evaluationProtocol="direct-v1", battles=battles,
                      production=production_reference(root))
        config["evaluationRecovery"]["directOnly"] = True
    config_path = run / "recovery" / ("direct_config.json" if direct else "evaluation_config.json")
    atomic_json(config_path, config)
    destination = run / "direct_evaluation" if direct else run
    status = read_json(destination / "status.json", {})
    status.update(completedStages=list(STAGES[:-1]), totalStages=len(STAGES), runId=run_id,
                  state="running", recovery=config["evaluationRecovery"])
    status.pop("error", None)
    status.pop("finishedAt", None)
    durations = {}
    print("♻️ Recuperación: se conservan datos y checkpoint; solo se prepara el motor y se evalúa.", flush=True)
    try:
        for stage in ("prepare", "evaluate"):
            kwargs = {"status_root": destination} if direct else {}
            run_child(stage, run, status, durations, worker_config=config_path, **kwargs)
        status.update(state="completed", completedStages=list(STAGES), finishedAt=utc_now())
        if direct:
            write_report(run, config, status, report_root=destination)
        else:
            write_report(run, config, status)
        atomic_json(destination / "status.json", status)
    except BaseException as error:
        status.update(state="failed", error=f"{type(error).__name__}: {error}", failedAt=utc_now())
        atomic_json(destination / "status.json", status)
        raise


def promote(root: Path, run_id: str, *, direct: bool = False) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID")
    run = root / "Refresh" / "runs" / run_id
    report_path = run / "direct_evaluation" / "report.json" if direct else run / "report.json"
    report = read_json(report_path)
    if not report or report.get("state") != "completed" or report.get("verdict") != "MEJORA_OBSERVADA":
        raise RuntimeError("Solo puede seleccionarse un candidato con benchmark completo y mejora observada.")
    old = ensure_champion(root)
    reference_role = "production" if direct else "champion"
    reference = report["config"].get(reference_role, {})
    if old["sha256"] != reference.get("sha256"):
        changed = "modelo productivo" if direct else "champion"
        raise RuntimeError(f"El {changed} cambió desde esta comparación; el candidato debe reevaluarse.")
    candidate = report["phases"]["rl"]
    check_artifact(candidate)
    check_artifact(report["phases"]["evaluate"])
    champion = {"id": "MC-" + run_id, "format": DEFAULT_FORMAT, "checkpoint": candidate["checkpoint"],
                "sha256": candidate["sha256"], "parentSha256": old["sha256"], "sourceRun": run_id,
                "selectedAt": utc_now(), "selection": "explicit-after-observed-pass"}
    atomic_json(run / "promotion.json", {"previous": old, "champion": champion,
                                         "benchmarkReport": str(report_path), "benchmarkSha256": sha256_file(report_path)})
    atomic_json(root / "Refresh" / "champion.json", champion)
    print("Champion seleccionado para próximas corridas de este Colab. La ROG/Nana no se modificó.", flush=True)
    return champion


def main(argv=None) -> int:
    # Let context managers stop their own Showdown session on Colab interruption.
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Colab pipeline interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--runtime-root", type=Path, default=Path("/content/battle-lab-refresh-runtime"))
    parser.add_argument("--mode", choices=PROFILES, default="LIGHT")
    parser.add_argument("--run-action", choices=("auto", "new", "resume"), default="auto")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--seed", type=int, default=260916)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--num-envs", type=int, default=2)
    parser.add_argument("--bc-min-transitions", type=int, choices=(9500, 10000), default=10000)
    parser.add_argument("--battles", type=int, default=500)
    parser.add_argument("--replay-pages", type=int, default=100)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--promote-run", default="")
    parser.add_argument("--promote-direct-run", default="")
    parser.add_argument("--recover-evaluation", action="store_true")
    parser.add_argument("--direct-evaluation", action="store_true")
    parser.add_argument("--worker-stage", choices=STAGES)
    parser.add_argument("--worker-run", type=Path)
    parser.add_argument("--worker-config", type=Path)
    args = parser.parse_args(argv)
    if args.worker_stage:
        if args.worker_config:
            direct = args.worker_config.name == "direct_config.json"
            expected = args.worker_run / "recovery" / ("direct_config.json" if direct else "evaluation_config.json")
            if args.worker_config.resolve() != expected.resolve() or args.worker_stage not in ("prepare", "evaluate"):
                raise RuntimeError("La recuperación solo permite preparar y evaluar.")
            config = read_json(expected)
            if sha256_file(args.worker_run / "config.json") != config["evaluationRecovery"]["sourceConfigSha256"]:
                raise RuntimeError("La configuración original cambió durante la recuperación.")
        else:
            config = read_json(args.worker_run / "config.json")
        if git_sha(PROJECT_ROOT) != config["codeSha"]:
            raise RuntimeError("Worker is running a different code snapshot")
        result = perform_stage(args.worker_stage, config, args.worker_run)
        phase_dir = args.worker_run / "phases"
        if args.worker_config:
            if config["evaluationRecovery"].get("directOnly"):
                phase_dir = args.worker_run / "direct_evaluation" / "phases"
            elif args.worker_stage == "prepare":
                phase_dir = args.worker_run / "recovery" / "phases"
        atomic_json(phase_dir / (args.worker_stage + ".json"), result)
        return 0
    if args.root is None:
        parser.error("--root is required")
    root = args.root.resolve()
    refresh = root / "Refresh"
    refresh.mkdir(parents=True, exist_ok=True)
    with (refresh / "pipeline.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Ya hay una corrida escribiendo en esta línea de modelos.") from None
        if args.promote_run:
            promote(root, args.promote_run)
            return 0
        if args.promote_direct_run:
            promote(root, args.promote_direct_run, direct=True)
            return 0
        if args.recover_evaluation:
            recover_evaluation(root, args.run_id)
            return 0
        if args.direct_evaluation:
            recover_evaluation(root, args.run_id, direct=True, battles=args.battles)
            return 0
        if args.battles < 2 or args.battles % 2 or args.replay_pages < 1 or args.num_envs not in (1, 2, 4):
            parser.error("battles must be even >=2; replay-pages >=1; num-envs one of 1,2,4")
        from battle_lab.showdown_smoke import read_showdown_commit
        champion = ensure_champion(root)
        device = args.device
        if device == "auto":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        config = {"schemaVersion": 1, "root": str(root), "mode": args.mode, "profile": PROFILES[args.mode],
                  "runtimeRoot": str(args.runtime_root.resolve()), "champion": champion,
                  "codeSha": git_sha(PROJECT_ROOT), "showdownSha": read_showdown_commit(),
                  "runtimeVersions": runtime_versions(device),
                  "vgcBenchSha": VGC_BENCH_COMMIT, "workers": resolve_workers(args.workers),
                  "numEnvs": args.num_envs, "battles": args.battles, "replayPages": args.replay_pages,
                  "bcMinTransitions": args.bc_min_transitions,
                  "evaluationProtocol": "direct-v1",
                  "production": production_reference(root),
                  "seed": args.seed, "device": device, "port": args.port}
        if config["production"]["sha256"] != champion["sha256"]:
            raise RuntimeError("La referencia productiva debe coincidir con el champion canónico vigente.")
        run = select_run(root, config, args.run_action, args.run_id)
        stages = STAGES[:5] if args.mode == "CENSUS" else STAGES
        status = read_json(run / "status.json", {"completedStages": [], "totalStages": len(stages), "runId": run.name})
        duration_path = refresh / f"phase_durations-{args.mode}-{args.num_envs}-{device}.json"
        durations = read_json(duration_path, {})
        try:
            for stage in stages:
                done = run / "phases" / (stage + ".json")
                if done.exists() and stage != "prepare":
                    check_artifact(read_json(done))
                    print("♻️ Fase conservada: " + LABELS[stage], flush=True)
                else:
                    status["phase"] = stage
                    run_child(stage, run, status, durations)
                if stage not in status["completedStages"]:
                    status["completedStages"].append(stage)
                atomic_json(run / "status.json", status)
                atomic_json(duration_path, durations)
            status.update(state="census_completed" if args.mode == "CENSUS" else "completed", finishedAt=utc_now())
            write_report(run, config, status)
            atomic_json(run / "status.json", status)
        except BaseException as error:
            status.update(state="failed", error=f"{type(error).__name__}: {error}", failedAt=utc_now())
            atomic_json(run / "status.json", status)
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
