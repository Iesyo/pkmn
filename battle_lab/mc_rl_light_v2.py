#!/usr/bin/env python3
"""Battle Lab M-C LIGHT v2: visible, resumable PPO/self-play.

This runner starts from the public VGC-Bench BC checkpoint, skips the blocking
inline benchmark, extends VGC-Bench's immutable categorical catalogs at runtime
for M-C-only values while preserving every existing ID, and persists progress
plus checkpoints so Colab runs can resume safely.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from battle_lab.mc_training import (
    _resolve_device,
    atomic_json,
    download_baseline,
    human_seconds,
    inject_mc_support,
    install_mc_teams,
    sha256_file,
    working_directory,
)

ACTOR_UNFREEZE_STEP = 98_304
DEFAULT_TOTAL_STEPS = 196_608
DEFAULT_CHECKPOINT_EVERY = 24_576

# Append-only extensions. Never insert/sort: the public BC checkpoint learned
# the existing integer IDs, so changing their positions would invalidate it.
MC_ABILITY_EXTENSIONS = ("auraguard",)
MC_ITEM_EXTENSIONS = (
    "absolitez",
    "baxcalibrite",
    "garchompitez",
    "golisopite",
    "lucarionitez",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_step(path: Path) -> int | None:
    match = re.fullmatch(r"step-(\d+)\.zip", path.name)
    return int(match.group(1)) if match else None


def latest_checkpoint(checkpoint_dir: Path) -> tuple[int, Path] | None:
    candidates: list[tuple[int, Path]] = []
    for path in checkpoint_dir.glob("step-*.zip"):
        step = checkpoint_step(path)
        if step is not None:
            candidates.append((step, path))
    return max(candidates, key=lambda item: item[0]) if candidates else None


def _eta(elapsed: float, completed_delta: int, remaining: int) -> float | None:
    if completed_delta <= 0:
        return None
    return elapsed / completed_delta * max(remaining, 0)


def _append_missing(values: list[str], extensions: Sequence[str]) -> list[str]:
    added: list[str] = []
    for value in extensions:
        if value not in values:
            values.append(value)
            added.append(value)
    return added


def extend_mc_runtime_catalogs(vgc_bench_checkout: Path) -> dict[str, list[str]]:
    """Append M-C-only IDs without changing any existing VGC-Bench ID."""

    checkout = str(vgc_bench_checkout)
    import sys

    if checkout not in sys.path:
        sys.path.insert(0, checkout)
    with working_directory(vgc_bench_checkout):
        utils = importlib.import_module("vgc_bench.src.utils")
        abilities_before = tuple(utils.abilities)
        items_before = tuple(utils.items)
        added_abilities = _append_missing(utils.abilities, MC_ABILITY_EXTENSIONS)
        added_items = _append_missing(utils.items, MC_ITEM_EXTENSIONS)
        if tuple(utils.abilities[: len(abilities_before)]) != abilities_before:
            raise RuntimeError("Se alteraron IDs históricos de abilities; se aborta para proteger el checkpoint.")
        if tuple(utils.items[: len(items_before)]) != items_before:
            raise RuntimeError("Se alteraron IDs históricos de items; se aborta para proteger el checkpoint.")
    return {"abilities": added_abilities, "items": added_items}


def run_light(
    *,
    vgc_bench_checkout: Path,
    output_root: Path,
    team_dir: Path,
    port: int,
    device: str,
    seed: int,
    total_steps: int,
    num_envs: int,
    checkpoint_every: int,
    initial_checkpoint: Path | None = None,
    initial_sha256: str | None = None,
    actor_unfreeze_step: int = ACTOR_UNFREEZE_STEP,
) -> dict[str, Any]:
    if actor_unfreeze_step < 0 or total_steps <= actor_unfreeze_step:
        raise ValueError(
            f"LIGHT requiere >{actor_unfreeze_step:,} pasos para que el actor también aprenda."
        )
    if checkpoint_every <= 0:
        raise ValueError("checkpoint_every debe ser > 0.")

    print("\n🐉 Battle Lab LIGHT M-C v2", flush=True)
    print("Fase 1/4 · preparando corpus y compatibilidad M-C...", flush=True)
    catalog_added = extend_mc_runtime_catalogs(vgc_bench_checkout)
    inject_mc_support(vgc_bench_checkout)
    team_count = install_mc_teams(vgc_bench_checkout, team_dir)
    print(f"✅ {team_count} equipos M-C de train instalados", flush=True)
    if catalog_added["abilities"] or catalog_added["items"]:
        print(
            "✅ Catálogo M-C extendido sin mover IDs históricos: "
            f"abilities +{catalog_added['abilities']} · items +{catalog_added['items']}",
            flush=True,
        )
    else:
        print("✅ Catálogo M-C ya compatible", flush=True)

    print("Fase 2/4 · creando entorno PPO/self-play local...", flush=True)
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback

    env_module = importlib.import_module("vgc_bench.src.env")
    policy_module = importlib.import_module("vgc_bench.src.policy")
    utils_module = importlib.import_module("vgc_bench.src.utils")
    ShowdownEnv = env_module.ShowdownEnv
    MaskedActorCriticPolicy = policy_module.MaskedActorCriticPolicy
    LearningStyle = utils_module.LearningStyle
    set_global_seed = utils_module.set_global_seed

    set_global_seed(seed)
    device = _resolve_device(device)
    run_root = output_root / "rl" / "light" / f"seed{seed}"
    checkpoint_dir = run_root / "checkpoints"
    tensorboard_dir = run_root / "tensorboard"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tensorboard_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_root / "status.json"
    summary_path = run_root / "summary.json"
    latest_text = output_root / "latest_run.txt"

    def write_status(**values: Any) -> None:
        payload = {
            "generatedAt": utc_now(),
            "runner": "mc_rl_light_v2",
            "mode": "LIGHT",
            "seed": seed,
            "teamCount": team_count,
            "totalSteps": total_steps,
            "numEnvs": num_envs,
            "catalogExtensions": catalog_added,
            **values,
        }
        atomic_json(status_path, payload)

    baseline = initial_checkpoint or download_baseline(output_root / "baseline" / "vgc-bench-ma-mb-100.zip")
    if initial_checkpoint is not None:
        if not initial_sha256 or sha256_file(baseline) != initial_sha256:
            raise RuntimeError("SHA-256 inválido para el checkpoint inicial explícito.")
        identity = {"initialSha256": initial_sha256, "totalSteps": total_steps,
                    "seed": seed, "numEnvs": num_envs, "actorUnfreezeStep": actor_unfreeze_step,
                    "teams": {p.name: sha256_file(p) for p in sorted(team_dir.glob("mc*.txt"))}}
        identity_path = run_root / "resume_identity.json"
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise RuntimeError("El contrato PPO cambió: usa otra ejecución para estos parámetros/datos.")
        atomic_json(identity_path, identity)
    resume = latest_checkpoint(checkpoint_dir)
    if resume and initial_checkpoint is not None:
        complete = [
            (checkpoint_step(p), p) for p in checkpoint_dir.glob("step-*.zip")
            if checkpoint_step(p) is not None and p.with_suffix(".sha256").is_file()
            and p.with_suffix(".sha256").read_text().strip() == sha256_file(p)
        ]
        resume = max(complete, default=None, key=lambda item: item[0])
    start_step = resume[0] if resume else 0
    resume_path = resume[1] if resume else baseline

    print(
        "Fase 3/4 · "
        + (f"reanudando desde checkpoint {start_step:,}" if resume else
           "cargando checkpoint inicial verificado" if initial_checkpoint else "cargando baseline BC M-A/M-B"),
        flush=True,
    )
    print(f"Dispositivo: {device} · envs: {num_envs} · objetivo: {total_steps:,}", flush=True)
    write_status(
        state="initializing",
        currentStep=start_step,
        progress=start_step / total_steps,
        checkpoint=str(resume_path),
        device=device,
    )

    env = None
    ppo = None
    try:
        with working_directory(vgc_bench_checkout):
            env = ShowdownEnv.create_env(
                "mc",
                seed,
                team_count,
                num_envs,
                40,
                port,
                LearningStyle.PURE_SELF_PLAY,
                False,
                True,
                None,
            )
            ppo = PPO(
                MaskedActorCriticPolicy,
                env,
                learning_rate=1e-5,
                n_steps=3072 // (2 * num_envs),
                batch_size=512,
                gamma=1,
                tensorboard_log=str(tensorboard_dir),
                policy_kwargs={"d_model": 256, "choose_on_teampreview": True},
                device=device,
            )
            ppo.set_parameters(str(resume_path), device=ppo.device)
            ppo.num_timesteps = start_step

            if start_step >= total_steps:
                final_path = resume_path
                result = {
                    "generatedAt": utc_now(),
                    "state": "completed",
                    "device": device,
                    "seed": seed,
                    "teamCount": team_count,
                    "totalSteps": total_steps,
                    "numEnvs": num_envs,
                    "startStep": start_step,
                    "finalStep": start_step,
                    "finalCheckpoint": str(final_path),
                    "finalCheckpointSha256": sha256_file(final_path),
                    "seconds": 0.0,
                }
                atomic_json(summary_path, result)
                write_status(**result)
                return result

            import torch
            policy_device = next(ppo.policy.parameters()).device
            parameter_count = sum(p.numel() for p in ppo.policy.parameters())
            if policy_device.type == "cuda":
                torch.cuda.reset_peak_memory_stats(policy_device)

            def runtime_stats() -> dict[str, Any]:
                stats = {"policyDevice": str(policy_device), "policyParameters": parameter_count,
                         "simulationEnvs": num_envs, "vectorEnvs": int(env.num_envs),
                         "sessionStartStep": start_step, "memoryScope": "current_training_process"}
                if policy_device.type == "cuda":
                    stats.update(gpuName=torch.cuda.get_device_name(policy_device),
                                 cudaAllocatedMiB=round(torch.cuda.memory_allocated(policy_device) / 2**20, 1),
                                 cudaPeakAllocatedMiB=round(torch.cuda.max_memory_allocated(policy_device) / 2**20, 1),
                                 cudaPeakReservedMiB=round(torch.cuda.max_memory_reserved(policy_device) / 2**20, 1))
                return stats

            print("Runtime PPO: " + json.dumps(runtime_stats(), ensure_ascii=False), flush=True)

            class VisibleLightCallback(BaseCallback):
                def __init__(self) -> None:
                    super().__init__()
                    self.started = 0.0
                    self.last_bucket = -1
                    self.next_checkpoint = ((start_step // checkpoint_every) + 1) * checkpoint_every
                    self.last_checkpoint: Path | None = resume_path if resume else None

                def _on_training_start(self) -> None:
                    self.started = time.monotonic()
                    print("Fase 4/4 · PPO/self-play iniciado ✅", flush=True)
                    print(
                        f"RL M-C [{'█' * round(28 * start_step / total_steps)}"
                        f"{'░' * (28 - round(28 * start_step / total_steps))}] "
                        f"{start_step:,}/{total_steps:,} ({start_step / total_steps:6.1%}) · ETA calculando",
                        flush=True,
                    )
                    write_status(
                        state="training",
                        currentStep=start_step,
                        progress=start_step / total_steps,
                        elapsedSeconds=0,
                        etaSeconds=None,
                        checkpoint=str(self.last_checkpoint) if self.last_checkpoint else str(baseline),
                        device=device,
                        runtime=runtime_stats(),
                    )

                def _on_rollout_start(self) -> None:
                    current = int(self.model.num_timesteps)
                    # This hook follows the preceding PPO update. Saving inside
                    # _on_step would discard that rollout's pending update on resume.
                    while current >= self.next_checkpoint and self.next_checkpoint < total_steps:
                        self._save_checkpoint(current)
                        self.next_checkpoint += checkpoint_every
                    progress = min(current / total_steps, 1.0)
                    self.model.ent_coef = max(0.02, 0.05 * (0.001 / 0.05) ** progress)
                    self.model.logger.record("train/ent_coef", self.model.ent_coef)
                    if hasattr(self.model.policy, "actor_grad"):
                        self.model.policy.actor_grad = current >= actor_unfreeze_step

                def _save_checkpoint(self, current: int) -> Path:
                    path = checkpoint_dir / f"step-{current:09d}.zip"
                    partial = path.with_name(path.stem + ".part.zip")
                    self.model.save(partial)
                    partial.replace(path)
                    path.with_suffix(".sha256").write_text(sha256_file(path) + "\n")
                    self.last_checkpoint = path
                    print(f"💾 checkpoint {current:,}: {path.name}", flush=True)
                    return path

                def _on_step(self) -> bool:
                    current = int(self.model.num_timesteps)
                    bucket_size = max(1, total_steps // 100)
                    bucket = current // bucket_size
                    if bucket != self.last_bucket or current >= total_steps:
                        self.last_bucket = bucket
                        elapsed = time.monotonic() - self.started
                        delta = max(current - start_step, 0)
                        remaining = max(total_steps - current, 0)
                        eta = _eta(elapsed, delta, remaining)
                        ratio = min(max(current / total_steps, 0.0), 1.0)
                        filled = round(28 * ratio)
                        bar = "█" * filled + "░" * (28 - filled)
                        actor = "activo" if current >= actor_unfreeze_step else "congelado"
                        print(
                            f"RL M-C [{bar}] {current:,}/{total_steps:,} ({ratio:6.1%}) "
                            f"· {human_seconds(elapsed)} · ETA {human_seconds(eta)} · actor {actor}",
                            flush=True,
                        )
                        write_status(
                            state="training",
                            currentStep=current,
                            progress=ratio,
                            elapsedSeconds=round(elapsed, 3),
                            etaSeconds=round(eta, 3) if eta is not None and math.isfinite(eta) else None,
                            actorGrad=current >= actor_unfreeze_step,
                            checkpoint=str(self.last_checkpoint) if self.last_checkpoint else str(baseline),
                            device=device,
                            runtime=runtime_stats(),
                        )
                    return True

                def _on_training_end(self) -> None:
                    current = int(self.model.num_timesteps)
                    self._save_checkpoint(current)
                    elapsed = time.monotonic() - self.started
                    print(
                        f"✅ PPO LIGHT terminado: {current:,} pasos en {human_seconds(elapsed)}",
                        flush=True,
                    )

            callback = VisibleLightCallback()
            started = time.monotonic()
            remaining_steps = total_steps - start_step
            ppo.learn(
                remaining_steps,
                callback=callback,
                tb_log_name="mc-light-v2",
                reset_num_timesteps=False,
            )
            elapsed = time.monotonic() - started
            final_step = int(ppo.num_timesteps)
            final = checkpoint_dir / f"step-{final_step:09d}.zip"
            if not final.exists():
                ppo.save(final)

            result = {
                "generatedAt": utc_now(),
                "state": "completed",
                "device": device,
                "seed": seed,
                "teamCount": team_count,
                "totalSteps": total_steps,
                "numEnvs": num_envs,
                "startStep": start_step,
                "finalStep": final_step,
                "baseline": str(baseline),
                "resumedFrom": str(resume[1]) if resume else None,
                "finalCheckpoint": str(final),
                "finalCheckpointSha256": sha256_file(final),
                "seconds": round(elapsed, 3),
                "runtime": runtime_stats(),
            }
            print("Runtime PPO final: " + json.dumps(result["runtime"], ensure_ascii=False), flush=True)
            atomic_json(summary_path, result)
            write_status(**result)
            latest_text.write_text(
                "\n".join(
                    [
                        "Battle Lab M-C — LIGHT training",
                        "",
                        "Estado: COMPLETADO",
                        f"Pasos: {final_step:,}/{total_steps:,}",
                        f"Equipos train: {team_count}",
                        f"Checkpoint final: {final}",
                        f"SHA256: {result['finalCheckpointSha256']}",
                        f"Segundos: {result['seconds']}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return result
    except BaseException as error:
        current = int(getattr(ppo, "num_timesteps", start_step)) if ppo is not None else start_step
        tb = traceback.format_exc()
        write_status(
            state="failed",
            currentStep=current,
            progress=min(current / total_steps, 1.0),
            error=f"{type(error).__name__}: {error}",
            traceback=tb[-12000:],
            device=device,
        )
        print("\n❌ Battle Lab LIGHT falló; traceback interno:", flush=True)
        print(tb, flush=True)
        print(f"📄 Estado persistido: {status_path}", flush=True)
        raise
    finally:
        if env is not None:
            env.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battle Lab visible LIGHT v2 PPO/self-play M-C")
    parser.add_argument("--vgc-bench", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--team-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--seed", type=int, default=260913)
    parser.add_argument("--total-steps", type=int, default=DEFAULT_TOTAL_STEPS)
    parser.add_argument("--num-envs", type=int, default=2)
    parser.add_argument("--checkpoint-every", type=int, default=DEFAULT_CHECKPOINT_EVERY)
    parser.add_argument("--initial-checkpoint", type=Path)
    parser.add_argument("--initial-sha256")
    parser.add_argument("--actor-unfreeze-step", type=int, default=ACTOR_UNFREEZE_STEP)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_light(
        vgc_bench_checkout=args.vgc_bench,
        output_root=args.output_root,
        team_dir=args.team_dir,
        port=args.port,
        device=args.device,
        seed=args.seed,
        total_steps=args.total_steps,
        num_envs=args.num_envs,
        checkpoint_every=args.checkpoint_every,
        initial_checkpoint=args.initial_checkpoint,
        initial_sha256=args.initial_sha256,
        actor_unfreeze_step=args.actor_unfreeze_step,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
