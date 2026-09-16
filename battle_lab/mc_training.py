#!/usr/bin/env python3
"""Battle Lab M-C training pipeline.

Builds a reproducible Champions M-C team corpus from VGCPastes, gathers the
same kind of OTS Showdown logs VGC-Bench uses for behavior cloning, converts
logs to trajectories, optionally behavior-clones the public VGC-Bench policy on
M-C demonstrations, and continues training with PPO self-play on M-C teams.

The upstream VGC-Bench checkout is treated as immutable source code: M-C support
is injected at runtime and all durable artifacts live under the caller-provided
work/output directories (normally Google Drive in Colab).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import pickle
import random
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


DEFAULT_FORMAT = "gen9championsvgc2026regmc"
DEFAULT_FORMAT_BO3 = "gen9championsvgc2026regmcbo3"
VGCPASTES_SPREADSHEET_ID = "1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw"
VGCPASTES_SHEET_GID = "2001945654"
VGCPASTES_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{VGCPASTES_SPREADSHEET_ID}/"
    f"export?format=csv&gid={VGCPASTES_SHEET_GID}"
)
VGC_BENCH_REPOSITORY = "https://github.com/cameronangliss/vgc-bench.git"
VGC_BENCH_COMMIT = "d79f9532947ac114dce1dda2456a590afcd375b2"
VGC_BENCH_CHECKPOINT_REVISION = "204c76741829ca0681629e41382043c385850d5c"
VGC_BENCH_CHECKPOINT_URL = (
    "https://huggingface.co/cameronangliss/vgc-bench-models/resolve/"
    f"{VGC_BENCH_CHECKPOINT_REVISION}/results/saves_bc/seed1/100.zip"
)
VGC_BENCH_CHECKPOINT_SHA256 = (
    "57f5edcab415cf6ccc1b6231923c8b66d3b1b7249b6b2562b97531471e4ca60b"
)
DEFAULT_SEED = 260913


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


def human_seconds(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "calculando"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass
class Progress:
    total: int
    label: str
    width: int = 28

    def __post_init__(self) -> None:
        self.started = time.monotonic()
        self.last_print = 0.0

    def update(self, completed: int, detail: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and completed < self.total and now - self.last_print < 0.75:
            return
        elapsed = now - self.started
        ratio = min(max(completed / self.total, 0.0), 1.0) if self.total else 1.0
        eta = elapsed / completed * (self.total - completed) if completed else None
        filled = round(self.width * ratio)
        bar = "█" * filled + "░" * (self.width - filled)
        suffix = f" · {detail}" if detail else ""
        print(
            f"{self.label} [{bar}] {completed}/{self.total} ({ratio:6.1%}) "
            f"· {human_seconds(elapsed)} · ETA {human_seconds(eta)}{suffix}",
            flush=True,
        )
        self.last_print = now


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def fetch_bytes(url: str, *, timeout: int = 60, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "pkmn-battle-lab-mc-training/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # pragma: no cover - network path
            last_error = error
            if attempt == attempts:
                break
            time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error


def fetch_text(url: str, *, timeout: int = 60, attempts: int = 3) -> str:
    return fetch_bytes(url, timeout=timeout, attempts=attempts).decode("utf-8", "replace")


def locate_header(rows: Sequence[Sequence[str]]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows):
        normalized = [str(value or "").strip() for value in row]
        if "Team ID" in normalized and "Pokepaste" in normalized and "EVs" in normalized:
            return index, normalized
    raise RuntimeError("No se encontró la fila de cabeceras de Champions M-C en VGCPastes.")


def row_mapping(headers: Sequence[str], row: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, header in enumerate(headers):
        if not header or header in result:
            continue
        result[header] = str(row[index] if index < len(row) else "").strip()
    return result


def pokepaste_raw_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.netloc.lower() not in {"pokepast.es", "www.pokepast.es"}:
        raise ValueError(f"Poképaste no reconocido: {url}")
    paste_id = parsed.path.strip("/").split("/")[0]
    if not re.fullmatch(r"[0-9a-fA-F]+", paste_id):
        raise ValueError(f"ID de Poképaste inválido: {url}")
    return f"https://pokepast.es/{paste_id}/raw"


def parse_vgcpastes_mc(csv_text: str) -> list[dict[str, str]]:
    rows = list(csv.reader(csv_text.splitlines()))
    header_index, headers = locate_header(rows)
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_row in rows[header_index + 1 :]:
        mapped = row_mapping(headers, raw_row)
        team_id = mapped.get("Team ID", "").strip().upper()
        paste = mapped.get("Pokepaste", "").strip()
        if not re.fullmatch(r"MC\d+", team_id) or not paste:
            continue
        if team_id in seen:
            continue
        seen.add(team_id)
        entries.append(mapped)
    entries.sort(key=lambda item: int(item["Team ID"][2:]), reverse=True)
    return entries


def _load_showdown_validator(project_root: Path):
    root = str(project_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    from battle_lab.showdown_smoke import validate_team  # type: ignore

    return validate_team


def sync_vgcpastes_teams(
    *,
    output_dir: Path,
    showdown_checkout: Path | None = None,
    battle_format: str = DEFAULT_FORMAT,
    minimum_teams: int = 150,
    csv_url: str = VGCPASTES_CSV_URL,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Snapshot Champions M-C teams from VGCPastes into Showdown text files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_text = fetch_text(csv_url, timeout=90)
    entries = parse_vgcpastes_mc(csv_text)
    if len(entries) < minimum_teams:
        raise RuntimeError(
            f"VGCPastes solo expuso {len(entries)} equipos M-C; se requieren al menos {minimum_teams}."
        )

    validator = None
    if showdown_checkout is not None:
        if project_root is None:
            raise ValueError("project_root es obligatorio cuando se valida con Showdown.")
        validator = _load_showdown_validator(project_root)

    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    hashes: set[str] = set()
    progress = Progress(len(entries), "Equipos VGCPastes")
    temp_dir = output_dir / ".snapshot-part"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    for index, entry in enumerate(entries, start=1):
        team_id = entry["Team ID"].upper()
        try:
            paste_url = entry["Pokepaste"]
            team_text = fetch_text(pokepaste_raw_url(paste_url), timeout=45).strip() + "\n"
            digest = sha256_text(team_text)
            if digest in hashes:
                raise RuntimeError("equipo duplicado por contenido")
            if validator is not None and showdown_checkout is not None:
                validator(showdown_checkout, battle_format, team_text)
            hashes.add(digest)
            filename = f"{team_id.lower()}.txt"
            (temp_dir / filename).write_text(team_text, encoding="utf-8")
            records.append(
                {
                    "teamId": team_id,
                    "file": filename,
                    "sha256": digest,
                    "pokepaste": paste_url,
                    "evs": entry.get("EVs"),
                    "pasteProvenance": entry.get("Extracted paste?"),
                    "replicaStatus": entry.get("Replica Status"),
                    "dateShared": entry.get("Date Shared"),
                    "event": entry.get("Tournament / Event"),
                    "rank": entry.get("Rank"),
                    "source": entry.get("Link to Source"),
                    "owner": entry.get("Owner"),
                    "description": entry.get("Team Description"),
                }
            )
        except Exception as error:  # pragma: no cover - network/runtime path
            errors.append({"teamId": team_id, "error": str(error)})
        progress.update(index, team_id, force=index == len(entries))

    if len(records) < minimum_teams:
        raise RuntimeError(
            f"Solo {len(records)} equipos M-C quedaron utilizables ({len(errors)} fallos); "
            f"se requieren al menos {minimum_teams}."
        )

    for path in output_dir.glob("mc*.txt"):
        path.unlink()
    for file in temp_dir.glob("*.txt"):
        os.replace(file, output_dir / file.name)
    shutil.rmtree(temp_dir)

    manifest = {
        "generatedAt": utc_now(),
        "source": {
            "spreadsheetId": VGCPASTES_SPREADSHEET_ID,
            "sheetGid": VGCPASTES_SHEET_GID,
            "csvUrl": csv_url,
        },
        "battleFormat": battle_format,
        "declaredRows": len(entries),
        "usableTeams": len(records),
        "errors": errors,
        "teams": records,
    }
    atomic_json(output_dir / "manifest.json", manifest)
    return manifest


def install_mc_teams(vgc_bench_checkout: Path, source_dir: Path) -> int:
    """Install the generated snapshot into the runtime-only VGC-Bench reg_mc pool."""

    source_files = sorted(source_dir.glob("mc*.txt"))
    if len(source_files) < 2:
        raise RuntimeError(f"No hay suficientes equipos en {source_dir}.")
    destination = vgc_bench_checkout / "teams" / "reg_mc"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for source in source_files:
        shutil.copy2(source, destination / source.name)
    return len(source_files)


def inject_mc_support(vgc_bench_checkout: Path) -> None:
    checkout = str(vgc_bench_checkout)
    if checkout not in sys.path:
        sys.path.insert(0, checkout)
    # VGC-Bench loads abilities/items/moves with paths relative to its checkout.
    # Always import its modules while that checkout is the current directory, then
    # restore the caller's cwd. This keeps Battle Lab callers independent of cwd.
    with working_directory(vgc_bench_checkout):
        utils = importlib.import_module("vgc_bench.src.utils")
        utils.format_map["mc"] = DEFAULT_FORMAT
        for module_name in ("vgc_bench.src.env", "vgc_bench.src.callback"):
            module = importlib.import_module(module_name)
            module.format_map["mc"] = DEFAULT_FORMAT


def scrape_mc_logs(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    max_logs_per_format: int,
    num_workers: int = 16,
    read_increment: int = 20_000,
    include_bo3: bool = True,
    max_passes: int = 50,
) -> dict[str, Any]:
    """Use VGC-Bench's own OTS filters to gather M-C Showdown replays."""

    inject_mc_support(vgc_bench_checkout)
    scraper = importlib.import_module("vgc_bench.scrape_logs")
    formats = [DEFAULT_FORMAT] + ([DEFAULT_FORMAT_BO3] if include_bo3 else [])
    logs_dir = data_root / "battle_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"generatedAt": utc_now(), "formats": {}}
    progress = Progress(len(formats), "Replays humanos M-C")

    with working_directory(data_root):
        for format_index, battle_format in enumerate(formats, start=1):
            passes = 0
            done = False
            while not done and passes < max_passes:
                passes += 1
                path = Path("battle_logs") / f"logs_{battle_format}.json"
                before = len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0
                done = scraper.scrape_logs(
                    num_workers,
                    read_increment,
                    battle_format,
                    max_logs=max_logs_per_format,
                )
                after = len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0
                print(
                    f"{battle_format}: pasada {passes} · {after} logs OTS válidos "
                    f"(+{max(0, after - before)})",
                    flush=True,
                )
                if after >= max_logs_per_format:
                    done = True
            path = Path("battle_logs") / f"logs_{battle_format}.json"
            count = len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0
            summary["formats"][battle_format] = {"logs": count, "passes": passes}
            progress.update(format_index, f"{battle_format}: {count}", force=True)

    summary["totalLogs"] = sum(item["logs"] for item in summary["formats"].values())
    atomic_json(data_root / "logs_manifest.json", summary)
    return summary


def build_mc_trajectories(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    num_workers: int = 4,
    min_rating: int | None = None,
    only_winner: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """Convert the collected M-C OTS logs using VGC-Bench's official parser."""

    inject_mc_support(vgc_bench_checkout)
    converter = importlib.import_module("vgc_bench.logs2trajs")
    traj_dir = data_root / "trajs"
    if traj_dir.exists():
        shutil.rmtree(traj_dir)
    traj_dir.mkdir(parents=True)

    log_files = sorted((data_root / "battle_logs").glob("logs_*.json"))
    log_count = sum(len(json.loads(path.read_text(encoding="utf-8"))) for path in log_files)
    if log_count == 0:
        raise RuntimeError("No hay battle logs M-C para convertir.")
    print(
        f"Convirtiendo {log_count} logs M-C. VGC-Bench imprimirá el avance por bloque; "
        "el tiempo depende de CPU y del número de workers.",
        flush=True,
    )
    started = time.monotonic()
    with working_directory(data_root):
        converter.main(num_workers, min_rating, only_winner, strict)
    files = sorted(traj_dir.glob("*.pkl"))
    transitions = 0
    for path in files:
        with path.open("rb") as handle:
            trajectory = pickle.load(handle)
        transitions += len(trajectory.acts)
    summary = {
        "generatedAt": utc_now(),
        "logs": log_count,
        "trajectories": len(files),
        "transitions": transitions,
        "minRating": min_rating,
        "onlyWinner": only_winner,
        "seconds": round(time.monotonic() - started, 3),
    }
    atomic_json(data_root / "trajs_manifest.json", summary)
    return summary


def download_baseline(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and sha256_file(destination) == VGC_BENCH_CHECKPOINT_SHA256:
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    data = fetch_bytes(VGC_BENCH_CHECKPOINT_URL, timeout=180, attempts=3)
    partial.write_bytes(data)
    digest = sha256_file(partial)
    if digest != VGC_BENCH_CHECKPOINT_SHA256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 inválido para baseline VGC-Bench: {digest}")
    os.replace(partial, destination)
    return destination


def _resolve_device(requested: str) -> str:
    import torch

    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("Se pidió CUDA, pero PyTorch no detecta GPU.")
    return requested


def _mc_eval_players(vgc_bench_checkout: Path, port: int, seed: int, team_count: int):
    from poke_env.environment import SingleAgentWrapper
    from poke_env.player import RandomPlayer, SimpleHeuristicsPlayer
    from poke_env.ps_client import ServerConfiguration

    inject_mc_support(vgc_bench_checkout)
    env_module = importlib.import_module("vgc_bench.src.env")
    teams_module = importlib.import_module("vgc_bench.src.teams")
    ShowdownEnv = env_module.ShowdownEnv
    RandomTeamBuilder = teams_module.RandomTeamBuilder
    env = ShowdownEnv(
        battle_format=DEFAULT_FORMAT,
        log_level=40,
        accept_open_team_sheet=True,
        start_listening=False,
        choose_on_teampreview=True,
        team=RandomTeamBuilder(seed, team_count, "mc"),
    )
    opponent = RandomPlayer(start_listening=False)
    single_agent_env = SingleAgentWrapper(env, opponent)
    config = ServerConfiguration(
        f"ws://localhost:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )
    return single_agent_env, config, SimpleHeuristicsPlayer, RandomTeamBuilder


def fine_tune_bc(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    output_root: Path,
    port: int,
    device: str,
    seed: int,
    epochs: int,
    div_frac: float,
    eval_battles: int,
    team_count: int,
    initial_checkpoint: Path | None = None,
    initial_sha256: str | None = None,
    num_workers: int | None = None,
) -> dict[str, Any]:
    """Behavior-clone the public BC policy further on real M-C trajectories."""

    if not 0 < div_frac <= 1:
        raise ValueError("div_frac debe estar en (0, 1].")
    inject_mc_support(vgc_bench_checkout)
    from imitation.algorithms.bc import BC
    from imitation.data.types import DictObs, Trajectory
    from imitation.util.logger import configure
    from stable_baselines3 import PPO
    from torch.utils.data import DataLoader, Dataset
    import numpy as np

    policy_module = importlib.import_module("vgc_bench.src.policy")
    callback_module = importlib.import_module("vgc_bench.src.callback")
    utils_module = importlib.import_module("vgc_bench.src.utils")
    players_module = importlib.import_module("vgc_bench.src.policy_player")
    MaskedActorCriticPolicy = policy_module.MaskedActorCriticPolicy
    Callback = callback_module.Callback
    BatchPolicyPlayer = players_module.BatchPolicyPlayer
    act_len = utils_module.act_len
    set_global_seed = utils_module.set_global_seed

    class MCTrajectoryDataset(Dataset):
        def __init__(self, directory: Path):
            self.files = sorted(directory.glob("*.pkl"))
            if not self.files:
                raise RuntimeError(f"No hay trayectorias en {directory}.")

        def __len__(self):
            return len(self.files)

        def __getitem__(self, idx):
            with self.files[idx].open("rb") as handle:
                traj = pickle.load(handle)
            obs = traj.obs
            n_steps = obs.shape[0]
            action_mask = np.ones((n_steps, 2 * act_len), dtype=np.float32)
            action_mask[:, 47:87] = 0
            action_mask[:, act_len + 47 : act_len + 87] = 0
            dict_obs = DictObs({"observation": obs, "action_mask": action_mask})
            return Trajectory(obs=dict_obs, acts=traj.acts, infos=traj.infos, terminal=traj.terminal)

    set_global_seed(seed)
    device = _resolve_device(device)
    baseline = initial_checkpoint or download_baseline(output_root / "baseline" / "vgc-bench-ma-mb-100.zip")
    if initial_checkpoint is not None and (
        not initial_sha256 or sha256_file(baseline) != initial_sha256
    ):
        raise RuntimeError("SHA-256 inválido para el checkpoint inicial BC.")
    dataset = MCTrajectoryDataset(data_root / "trajs")
    div_count = max(1, round(1 / div_frac))
    batch_trajectories = max(1, len(dataset) // div_count)
    loader_workers = max(0, min(4, (os.cpu_count() or 1) if num_workers is None else num_workers))
    dataloader = DataLoader(
        dataset,
        batch_size=batch_trajectories,
        shuffle=True,
        num_workers=loader_workers,
        persistent_workers=loader_workers > 0,
        collate_fn=lambda batch: batch,
    )

    with working_directory(vgc_bench_checkout):
        single_env, server_config, SimpleHeuristicsPlayer, RandomTeamBuilder = _mc_eval_players(
            vgc_bench_checkout, port, seed, team_count
        )
        checkpoint_dir = output_root / "bc" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        completed_epoch = 0
        starting_checkpoint = baseline
        if initial_checkpoint is not None:
            for epoch in range(1, epochs + 1):
                path = checkpoint_dir / f"epoch-{epoch:03d}.zip"
                digest = path.with_suffix(".sha256")
                optimizer_path = checkpoint_dir / f"epoch-{epoch:03d}.optimizer.pt"
                optimizer_digest = optimizer_path.with_suffix(".sha256")
                if (path.is_file() and digest.is_file() and digest.read_text().strip() == sha256_file(path)
                    and optimizer_path.is_file() and optimizer_digest.is_file()
                    and optimizer_digest.read_text().strip() == sha256_file(optimizer_path)):
                    completed_epoch, starting_checkpoint = epoch, path
                else:
                    break
        model = PPO.load(str(starting_checkpoint), env=single_env, device=device)
        if hasattr(model.policy, "actor_grad"):
            model.policy.actor_grad = True
        if not isinstance(model.policy, MaskedActorCriticPolicy):
            raise RuntimeError("El baseline no cargó MaskedActorCriticPolicy.")
        log_dir = output_root / "bc" / "logs"
        checkpoint_dir = output_root / "bc" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        bc = BC(
            observation_space=model.observation_space,
            action_space=model.action_space,
            rng=np.random.default_rng(seed),
            policy=model.policy,
            batch_size=1024,
            device=device,
            custom_logger=configure(str(log_dir), ["tensorboard"]),
        )
        eval_agent = BatchPolicyPlayer(
            policy=model.policy,
            server_configuration=server_config,
            battle_format=DEFAULT_FORMAT,
            log_level=40,
            max_concurrent_battles=max(1, min(10, eval_battles)),
            accept_open_team_sheet=True,
            team=RandomTeamBuilder(seed, team_count, "mc"),
        ) if eval_battles > 0 else None
        eval_opponent = SimpleHeuristicsPlayer(
            server_configuration=server_config,
            battle_format=DEFAULT_FORMAT,
            log_level=40,
            max_concurrent_battles=max(1, min(10, eval_battles)),
            accept_open_team_sheet=True,
            team=RandomTeamBuilder(seed, team_count, "mc"),
        ) if eval_battles > 0 else None

        progress = Progress(epochs, "BC M-C")
        history: list[dict[str, Any]] = []
        optimizer_path = checkpoint_dir / f"epoch-{completed_epoch:03d}.optimizer.pt"
        if completed_epoch and optimizer_path.exists():
            import torch
            bc.optimizer.load_state_dict(torch.load(optimizer_path, map_location=device, weights_only=True))
        for epoch in range(completed_epoch + 1, epochs + 1):
            if initial_checkpoint is not None:
                import torch
                torch.manual_seed(seed + epoch)
            for demonstrations in dataloader:
                bc.set_demonstrations(demonstrations)
                bc.train(n_epochs=1)
            win_rates = Callback.compare(eval_agent, eval_opponent, eval_battles) if eval_battles > 0 else None
            checkpoint = checkpoint_dir / f"epoch-{epoch:03d}.zip"
            partial = checkpoint.with_name(checkpoint.stem + ".part.zip")
            model.save(partial)
            partial.replace(checkpoint)
            if initial_checkpoint is not None:
                optimizer_path = checkpoint_dir / f"epoch-{epoch:03d}.optimizer.pt"
                partial_optimizer = optimizer_path.with_suffix(".part.pt")
                torch.save(bc.optimizer.state_dict(), partial_optimizer)
                partial_optimizer.replace(optimizer_path)
                optimizer_path.with_suffix(".sha256").write_text(sha256_file(optimizer_path) + "\n")
            checkpoint.with_suffix(".sha256").write_text(sha256_file(checkpoint) + "\n")
            history.append({"epoch": epoch, "heuristicWinRates": win_rates, "checkpoint": str(checkpoint)})
            progress.update(epoch, f"heuristic={win_rates}", force=True)
        single_env.close()

    final_checkpoint = checkpoint_dir / f"epoch-{epochs:03d}.zip"
    summary = {
        "generatedAt": utc_now(),
        "baseline": str(baseline),
        "baselineSha256": sha256_file(baseline),
        "finalCheckpoint": str(final_checkpoint),
        "finalCheckpointSha256": sha256_file(final_checkpoint),
        "epochs": epochs,
        "trajectories": len(dataset),
        "device": device,
        "history": history,
    }
    atomic_json(output_root / "bc" / "summary.json", summary)
    return summary


def _ensure_results_symlink(vgc_bench_checkout: Path, output_root: Path, suffix: str) -> Path:
    link = vgc_bench_checkout / f"results_{suffix}"
    target = output_root / "rl"
    target.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        if link.resolve() != target.resolve():
            link.unlink()
    elif link.exists():
        raise RuntimeError(f"{link} existe y no es symlink; no se reemplazará.")
    if not link.exists():
        link.symlink_to(target, target_is_directory=True)
    return link


def seed_rl_with_checkpoint(
    *,
    vgc_bench_checkout: Path,
    output_root: Path,
    suffix: str,
    seed: int,
    team_count: int,
    checkpoint: Path | None,
) -> Path | None:
    if checkpoint is None:
        return None
    if not checkpoint.is_file():
        raise RuntimeError(f"Checkpoint BC M-C inexistente: {checkpoint}")
    _ensure_results_symlink(vgc_bench_checkout, output_root, suffix)
    save_dir = (
        output_root
        / "rl"
        / "saves_bc_sp_xm"
        / "reg_mc"
        / f"{team_count}_teams"
        / f"seed{seed}"
    )
    save_dir.mkdir(parents=True, exist_ok=True)
    destination = save_dir / "100.zip"
    shutil.copy2(checkpoint, destination)
    return destination


def run_rl_self_play(
    *,
    vgc_bench_checkout: Path,
    output_root: Path,
    port: int,
    device: str,
    seed: int,
    team_count: int,
    num_envs: int,
    num_eval_workers: int,
    total_steps: int,
    initial_checkpoint: Path | None,
    suffix: str = "mc",
) -> dict[str, Any]:
    """Continue VGC-Bench PPO self-play on M-C, starting from BC knowledge."""

    if total_steps <= 98_304:
        print(
            "⚠️ VGC-Bench congela el actor durante los primeros 98,304 pasos cuando parte de BC; "
            "usa >98,304 para que la política también aprenda decisiones M-C.",
            flush=True,
        )
    inject_mc_support(vgc_bench_checkout)
    _ensure_results_symlink(vgc_bench_checkout, output_root, suffix)
    seeded_path = seed_rl_with_checkpoint(
        vgc_bench_checkout=vgc_bench_checkout,
        output_root=output_root,
        suffix=suffix,
        seed=seed,
        team_count=team_count,
        checkpoint=initial_checkpoint,
    )

    train_module = importlib.import_module("vgc_bench.train")
    callback_module = importlib.import_module("vgc_bench.src.callback")
    utils_module = importlib.import_module("vgc_bench.src.utils")
    OfficialCallback = callback_module.Callback
    LearningStyle = utils_module.LearningStyle
    set_global_seed = utils_module.set_global_seed

    class VisibleProgressCallback(OfficialCallback):
        def _on_training_start(self):
            super()._on_training_start()
            self._battle_lab_started = time.monotonic()
            self._battle_lab_last = -1

        def _on_step(self) -> bool:
            keep_going = super()._on_step()
            current = int(self.model.num_timesteps)
            bucket = current // max(1, total_steps // 100)
            if bucket != self._battle_lab_last or current >= total_steps:
                self._battle_lab_last = bucket
                elapsed = time.monotonic() - self._battle_lab_started
                ratio = min(current / total_steps, 1.0)
                eta = elapsed / current * max(total_steps - current, 0) if current else None
                width = 28
                filled = round(width * ratio)
                bar = "█" * filled + "░" * (width - filled)
                print(
                    f"RL M-C [{bar}] {current:,}/{total_steps:,} ({ratio:6.1%}) "
                    f"· {human_seconds(elapsed)} · ETA {human_seconds(eta)}",
                    flush=True,
                )
            return keep_going

    train_module.Callback = VisibleProgressCallback
    callback_module.format_map["mc"] = DEFAULT_FORMAT
    set_global_seed(seed)
    device = _resolve_device(device)
    started = time.monotonic()
    with working_directory(vgc_bench_checkout):
        train_module.train(
            "mc",
            seed,
            team_count,
            num_envs,
            num_eval_workers,
            40,
            port,
            device,
            LearningStyle.PURE_SELF_PLAY,
            True,
            False,
            True,
            None,
            None,
            suffix,
            total_steps,
            True,
        )

    save_dir = (
        output_root
        / "rl"
        / "saves_bc_sp_xm"
        / "reg_mc"
        / f"{team_count}_teams"
        / f"seed{seed}"
    )
    checkpoints = sorted(
        (path for path in save_dir.glob("*.zip") if path.stem.lstrip("-").isdigit()),
        key=lambda path: int(path.stem),
    )
    if not checkpoints:
        raise RuntimeError(f"RL terminó sin checkpoints en {save_dir}.")
    final = checkpoints[-1]
    summary = {
        "generatedAt": utc_now(),
        "device": device,
        "seed": seed,
        "teamCount": team_count,
        "totalSteps": total_steps,
        "numEnvs": num_envs,
        "initialCheckpoint": str(seeded_path) if seeded_path else "official BC baseline (downloaded by VGC-Bench)",
        "finalCheckpoint": str(final),
        "finalCheckpointSha256": sha256_file(final),
        "seconds": round(time.monotonic() - started, 3),
    }
    atomic_json(output_root / "rl" / "summary.json", summary)
    return summary


def census(*, team_dir: Path, data_root: Path, output_root: Path) -> dict[str, Any]:
    manifest_path = team_dir / "manifest.json"
    team_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    logs_manifest_path = data_root / "logs_manifest.json"
    logs_manifest = json.loads(logs_manifest_path.read_text(encoding="utf-8")) if logs_manifest_path.exists() else {}
    traj_manifest_path = data_root / "trajs_manifest.json"
    traj_manifest = json.loads(traj_manifest_path.read_text(encoding="utf-8")) if traj_manifest_path.exists() else {}
    summary = {
        "generatedAt": utc_now(),
        "teams": {
            "usable": team_manifest.get("usableTeams", len(list(team_dir.glob("mc*.txt")))),
            "sourceRows": team_manifest.get("declaredRows"),
            "source": "VGCPastes Repository / Champions M-C",
        },
        "humanLogs": logs_manifest.get("totalLogs", 0),
        "trajectories": traj_manifest.get("trajectories", 0),
        "transitions": traj_manifest.get("transitions", 0),
        "officialPublicMCDataset": False,
        "strategy": "BC real M-C if enough OTS logs, then PPO self-play on full VGCPastes M-C team corpus; otherwise PPO self-play starts from the official M-A/M-B BC baseline.",
    }
    atomic_json(output_root / "census.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battle Lab Champions M-C training pipeline")
    parser.add_argument("--vgc-bench", type=Path, required=True, help="Pinned VGC-Bench checkout")
    parser.add_argument("--project-root", type=Path, required=True, help="pkmn checkout root")
    parser.add_argument("--data-root", type=Path, required=True, help="Durable data root")
    parser.add_argument("--output-root", type=Path, required=True, help="Durable checkpoint/result root")
    parser.add_argument("--team-dir", type=Path, required=True, help="Durable M-C team snapshot")
    parser.add_argument("--showdown", type=Path, help="Pinned Showdown checkout for legality validation")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync-teams")
    sync.add_argument("--minimum-teams", type=int, default=150)
    sync.add_argument("--skip-showdown-validation", action="store_true")

    scrape = sub.add_parser("scrape-logs")
    scrape.add_argument("--max-logs-per-format", type=int, default=20_000)
    scrape.add_argument("--num-workers", type=int, default=16)
    scrape.add_argument("--read-increment", type=int, default=20_000)
    scrape.add_argument("--no-bo3", action="store_true")

    traj = sub.add_parser("build-trajectories")
    traj.add_argument("--num-workers", type=int, default=4)
    traj.add_argument("--min-rating", type=int, default=None)
    traj.add_argument("--only-winner", action="store_true")
    traj.add_argument("--strict", action="store_true")

    bc = sub.add_parser("bc")
    bc.add_argument("--epochs", type=int, default=5)
    bc.add_argument("--div-frac", type=float, default=0.1)
    bc.add_argument("--eval-battles", type=int, default=50)

    rl = sub.add_parser("rl")
    rl.add_argument("--total-steps", type=int, default=196_608)
    rl.add_argument("--num-envs", type=int, default=2)
    rl.add_argument("--num-eval-workers", type=int, default=4)
    rl.add_argument("--initial-checkpoint", type=Path, default=None)

    sub.add_parser("census")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync-teams":
        showdown = None if args.skip_showdown_validation else args.showdown
        if showdown is None and not args.skip_showdown_validation:
            raise RuntimeError("--showdown es obligatorio salvo --skip-showdown-validation.")
        result = sync_vgcpastes_teams(
            output_dir=args.team_dir,
            showdown_checkout=showdown,
            minimum_teams=args.minimum_teams,
            project_root=args.project_root,
        )
    elif args.command == "scrape-logs":
        result = scrape_mc_logs(
            vgc_bench_checkout=args.vgc_bench,
            data_root=args.data_root,
            max_logs_per_format=args.max_logs_per_format,
            num_workers=args.num_workers,
            read_increment=args.read_increment,
            include_bo3=not args.no_bo3,
        )
    elif args.command == "build-trajectories":
        result = build_mc_trajectories(
            vgc_bench_checkout=args.vgc_bench,
            data_root=args.data_root,
            num_workers=args.num_workers,
            min_rating=args.min_rating,
            only_winner=args.only_winner,
            strict=args.strict,
        )
    elif args.command == "bc":
        team_count = install_mc_teams(args.vgc_bench, args.team_dir)
        result = fine_tune_bc(
            vgc_bench_checkout=args.vgc_bench,
            data_root=args.data_root,
            output_root=args.output_root,
            port=args.port,
            device=args.device,
            seed=args.seed,
            epochs=args.epochs,
            div_frac=args.div_frac,
            eval_battles=args.eval_battles,
            team_count=team_count,
        )
    elif args.command == "rl":
        team_count = install_mc_teams(args.vgc_bench, args.team_dir)
        result = run_rl_self_play(
            vgc_bench_checkout=args.vgc_bench,
            output_root=args.output_root,
            port=args.port,
            device=args.device,
            seed=args.seed,
            team_count=team_count,
            num_envs=args.num_envs,
            num_eval_workers=args.num_eval_workers,
            total_steps=args.total_steps,
            initial_checkpoint=args.initial_checkpoint,
        )
    elif args.command == "census":
        result = census(team_dir=args.team_dir, data_root=args.data_root, output_root=args.output_root)
    else:  # pragma: no cover
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
