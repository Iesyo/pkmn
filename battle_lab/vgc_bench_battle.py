#!/usr/bin/env python3
"""Run merciless VGC-Bench evaluation on the private Battle Lab server.

This Phase 2 runner provisions a pinned VGC-Bench source checkout and its
latest public behavior-cloning checkpoint, validates an auditable Champions
M-C team corpus, and either runs deterministic self-play or a mirrored benchmark
against the three official poke-env baselines. Results and replay artifacts are
written as an atomic JSON and ZIP pair.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import shutil
import sys
import time
import urllib.request
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, MutableMapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from battle_lab.showdown_smoke import (  # noqa: E402
    DEFAULT_FORMAT,
    DEFAULT_RUNTIME_ROOT,
    DEFAULT_SHOWDOWN_REPOSITORY,
    MINIMUM_NODE_MAJOR,
    Progress,
    atomic_json,
    battle_summary,
    ensure_showdown_checkout,
    environment_snapshot,
    git_revision,
    install_runtime_config,
    installed_node_version,
    read_showdown_commit,
    run_checked,
    run_checked_with_retries,
    running_showdown,
    tail,
    utc_now,
    validate_team,
    verify_illegal_team_is_rejected,
)
from battle_lab.benchmarking import (  # noqa: E402
    BASELINE_BY_ID,
    BASELINE_SPECS,
    DEFAULT_BENCHMARK_BATTLES_PER_BASELINE,
    BenchmarkBattlePlan,
    benchmark_schedule_statistics,
    build_mirrored_benchmark_schedule,
    summarize_vgc_bench_record,
)
from battle_lab.team_corpus import (  # noqa: E402
    DEFAULT_CORPUS_MANIFEST,
    TeamCorpus,
    TeamPairing,
    TeamRecord,
    add_extra_teams,
    build_pairing_schedule,
    explicit_pair,
    load_bundled_corpus,
    pairing_statistics,
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
VGC_BENCH_CHECKPOINT_BYTES = 11_919_042
EXPECTED_OBSERVATION_LENGTH = 6_936
EXPECTED_ACTION_BRANCHES = (107, 107)
DEFAULT_SEED = 260_913
POKE_ENV_REQUIREMENTS = PROJECT_ROOT / "battle_lab" / "requirements-phase1.txt"


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_poke_env_metadata() -> dict[str, str]:
    """Verify that the installed doubles baselines come from the pinned fork."""

    from importlib.metadata import PackageNotFoundError, distribution

    requirement = next(
        (
            line.strip()
            for line in POKE_ENV_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("poke_env @ git+")
        ),
        "",
    )
    if "@" not in requirement:
        raise RuntimeError("requirements-phase1.txt no fija el fork de poke-env.")
    direct_reference = requirement.split("git+", 1)[1]
    repository, expected_commit = direct_reference.rsplit("@", 1)
    if len(expected_commit) != 40 or any(
        character not in "0123456789abcdef" for character in expected_commit
    ):
        raise RuntimeError("El commit fijado de poke-env no es un SHA Git válido.")
    try:
        package = distribution("poke-env")
    except PackageNotFoundError as error:
        raise RuntimeError(
            "Falta el fork fijado de poke-env; instala requirements-phase1.txt."
        ) from error
    direct_url = json.loads(package.read_text("direct_url.json") or "{}")
    actual_commit = direct_url.get("vcs_info", {}).get("commit_id")
    if actual_commit != expected_commit:
        raise RuntimeError(
            f"poke-env está en {actual_commit or 'una revisión desconocida'}; "
            f"Battle Lab requiere {expected_commit}."
        )
    return {
        "repository": repository,
        "commit": expected_commit,
        "version": package.version,
    }


def ensure_vgc_bench_checkout(
    *, checkout: Path, repository: str, commit: str
) -> dict[str, Any]:
    """Create or refresh the dedicated, submodule-free VGC-Bench checkout."""

    checkout.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    cloned = False
    if not (checkout / ".git").is_dir():
        if checkout.exists() and any(checkout.iterdir()):
            raise RuntimeError(
                f"La ruta de VGC-Bench existe pero no es un checkout Git: {checkout}"
            )
        run_checked_with_retries(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--depth",
                "1",
                repository,
                str(checkout),
            ],
            cwd=checkout.parent,
            timeout=240,
        )
        cloned = True

    incomplete = not (checkout / "pyproject.toml").is_file() or not (
        checkout / "data" / "moves.json"
    ).is_file()
    if not incomplete:
        dirty = run_checked(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout
        ).stdout.strip()
        if dirty:
            raise RuntimeError(
                "El checkout dedicado de VGC-Bench contiene cambios rastreados; "
                "usa una ruta de runtime limpia."
            )

    run_checked_with_retries(
        ["git", "fetch", "--depth", "1", "origin", commit],
        cwd=checkout,
        timeout=240,
    )
    if git_revision(checkout) != commit or incomplete:
        command = ["git", "checkout", "--detach"]
        if incomplete:
            command.append("--force")
        command.append(commit)
        run_checked(command, cwd=checkout, timeout=60)

    required = [
        checkout / "vgc_bench" / "src" / "policy.py",
        checkout / "vgc_bench" / "src" / "policy_player.py",
        checkout / "data" / "abilities.json",
        checkout / "data" / "items.json",
        checkout / "data" / "moves.json",
    ]
    missing = [
        str(path.relative_to(checkout)) for path in required if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "El checkout fijado de VGC-Bench está incompleto: " + ", ".join(missing)
        )
    actual = git_revision(checkout)
    if actual != commit:
        raise RuntimeError(f"VGC-Bench quedó en {actual}; se esperaba {commit}.")
    return {
        "cloned": cloned,
        "checkoutSeconds": round(time.monotonic() - started, 3),
    }


def verify_vgc_bench_checkout(checkout: Path, commit: str) -> None:
    actual = git_revision(checkout)
    if actual != commit:
        raise RuntimeError(
            f"--skip-setup requiere VGC-Bench en {commit}; "
            f"el checkout está en {actual}."
        )
    for relative in (
        "vgc_bench/src/policy.py",
        "vgc_bench/src/policy_player.py",
        "data/abilities.json",
        "data/items.json",
        "data/moves.json",
    ):
        if not (checkout / relative).is_file():
            raise RuntimeError(f"--skip-setup no encontró {relative} en VGC-Bench.")


def download_checkpoint(
    *, destination: Path, url: str, expected_sha256: str, attempts: int = 3
) -> dict[str, Any]:
    """Download the pinned checkpoint atomically and verify its exact bytes."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(destination) == expected_sha256:
        return {
            "cached": True,
            "bytes": destination.stat().st_size,
            "downloadSeconds": 0.0,
        }

    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        with suppress(FileNotFoundError):
            partial.unlink()
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "pkmn-battle-lab/phase-2"}
            )
            digest = hashlib.sha256()
            received = 0
            with urllib.request.urlopen(request, timeout=120) as response, partial.open(
                "wb"
            ) as output:
                total = int(response.headers.get("Content-Length") or 0)
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    denominator = total or VGC_BENCH_CHECKPOINT_BYTES
                    ratio = min(received / denominator, 1.0)
                    print(
                        f"\rCheckpoint VGC-Bench [{ratio:>6.1%}] "
                        f"{received / 1_000_000:.1f} MB",
                        end="",
                        flush=True,
                    )
            print()
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise RuntimeError(
                    "SHA-256 inválido para el checkpoint de VGC-Bench: "
                    f"{actual_sha256}; se esperaba {expected_sha256}."
                )
            os.replace(partial, destination)
            return {
                "cached": False,
                "bytes": received,
                "downloadSeconds": round(time.monotonic() - started, 3),
            }
        except Exception as error:
            last_error = error
            with suppress(FileNotFoundError):
                partial.unlink()
            if attempt == attempts:
                break
            wait_seconds = 5 * attempt
            print(
                f"⚠️ Descarga del checkpoint falló; reintento "
                f"{attempt + 1}/{attempts} en {wait_seconds}s.",
                flush=True,
            )
            time.sleep(wait_seconds)
    assert last_error is not None
    raise RuntimeError(
        f"No fue posible obtener el checkpoint fijado tras {attempts} intentos."
    ) from last_error


def collapse_species_aliases(
    team: MutableMapping[str, Any], *, side: str
) -> int:
    """Remove OTS aliases that describe the same species twice.

    The VGC-Bench poke-env fork can add a seventh entry for canonical form names
    (for example ``Indeedee`` + ``Indeedee-F``). Champions uses Species Clause,
    so a mapping larger than six is valid only when it collapses to six unique
    species. Keeping the first object preserves Showdown's team-slot indices.
    """

    if len(team) <= 6:
        return 0
    collapsed: dict[str, Any] = {}
    first_by_species: dict[str, Any] = {}
    for identifier, pokemon in team.items():
        species = str(getattr(pokemon, "species", "") or "").lower()
        species_key = species or f"__unknown__:{identifier}"
        if species_key not in first_by_species:
            first_by_species[species_key] = pokemon
            collapsed[identifier] = pokemon
            continue
        first = first_by_species[species_key]
        if getattr(pokemon, "selected_in_teampreview", False):
            setattr(first, "_selected_in_teampreview", True)

    if len(collapsed) != 6:
        summary = [
            str(getattr(pokemon, "species", identifier))
            for identifier, pokemon in team.items()
        ]
        raise RuntimeError(
            f"VGC-Bench esperaba 6 especies en {side}, pero recibió "
            f"{len(team)} entradas que colapsan a {len(collapsed)}: {summary}"
        )
    removed = len(team) - len(collapsed)
    team.clear()
    team.update(collapsed)
    return removed


def normalize_battle_aliases(battle: Any) -> int:
    removed = collapse_species_aliases(battle.team, side="equipo propio")
    opponent_mapping = getattr(battle, "_opponent_team", None)
    if opponent_mapping:
        removed += collapse_species_aliases(
            opponent_mapping, side="equipo oponente"
        )
    return removed


@dataclass
class ModelRuntime:
    policy: Any
    player_class: type
    torch: Any
    metadata: dict[str, Any]


def load_model_runtime(
    *, checkout: Path, checkpoint: Path, requested_device: str, seed: int
) -> ModelRuntime:
    """Import pinned VGC-Bench code and load the exact policy checkpoint."""

    try:
        import torch
        from stable_baselines3 import PPO
    except ImportError as error:
        raise RuntimeError(
            "Faltan PyTorch o stable-baselines3. Instala "
            "battle_lab/requirements-phase1.txt y requirements-phase2.txt."
        ) from error

    checkout_text = str(checkout)
    if checkout_text not in sys.path:
        sys.path.insert(0, checkout_text)
    try:
        with working_directory(checkout):
            from vgc_bench.src.policy import MaskedActorCriticPolicy
            from vgc_bench.src.policy_player import PolicyPlayer
            from vgc_bench.src.utils import set_global_seed
    except (ImportError, FileNotFoundError) as error:
        raise RuntimeError(
            f"No fue posible importar el checkout fijado de VGC-Bench en {checkout}."
        ) from error

    if requested_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(
            "Se pidió --device cuda, pero PyTorch no detecta GPU. Usa un runtime "
            "Colab con acelerador GPU o selecciona auto/cpu."
        )
    device = (
        "cuda"
        if requested_device == "auto" and torch.cuda.is_available()
        else requested_device
    )
    if device == "auto":
        device = "cpu"

    set_global_seed(seed)
    started = time.monotonic()
    model = PPO.load(str(checkpoint), device=device)
    policy = model.policy
    if not isinstance(policy, MaskedActorCriticPolicy):
        raise RuntimeError(
            f"El checkpoint cargó {type(policy).__name__}, no MaskedActorCriticPolicy."
        )
    if not policy.choose_on_teampreview:
        raise RuntimeError(
            "El checkpoint no controla Team Preview; no es el modelo esperado."
        )
    policy.set_training_mode(False)

    observation_space = model.observation_space["observation"]
    observation_shape = tuple(int(value) for value in observation_space.shape)
    action_branches = tuple(int(value) for value in model.action_space.nvec)
    if observation_shape != (EXPECTED_OBSERVATION_LENGTH,):
        raise RuntimeError(
            f"El modelo espera observaciones {observation_shape}; Battle Lab requiere "
            f"({EXPECTED_OBSERVATION_LENGTH},)."
        )
    if action_branches != EXPECTED_ACTION_BRANCHES:
        raise RuntimeError(
            f"El modelo usa ramas de acción {action_branches}; se esperaban "
            f"{EXPECTED_ACTION_BRANCHES}."
        )

    class BattleLabPolicyPlayer(PolicyPlayer):
        """Pinned VGC-Bench player with the poke-env OTS alias repair."""

        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, **kwargs)
            self.aliases_removed = 0

        def embed_battle(
            self, battle: Any, fake_rating: int | None = None
        ) -> Any:
            self.aliases_removed += normalize_battle_aliases(battle)
            observation = PolicyPlayer.embed_battle(battle, fake_rating)
            if observation.shape != (EXPECTED_OBSERVATION_LENGTH,):
                raise RuntimeError(
                    "La observación de VGC-Bench tiene forma inesperada: "
                    f"{observation.shape}; equipos propio/oponente: "
                    f"{len(battle.team)}/{len(battle.opponent_team)}."
                )
            return observation

    BattleLabPolicyPlayer.__name__ = "BattleLabPolicyPlayer"
    metadata = {
        "device": str(policy.device),
        "torchVersion": torch.__version__,
        "stableBaselinesVersion": __import__("stable_baselines3").__version__,
        "observationShape": list(observation_shape),
        "actionBranches": list(action_branches),
        "parameters": sum(parameter.numel() for parameter in policy.parameters()),
        "chooseOnTeamPreview": bool(policy.choose_on_teampreview),
        "deterministic": True,
        "loadSeconds": round(time.monotonic() - started, 3),
        "seed": seed,
    }
    return ModelRuntime(policy, BattleLabPolicyPlayer, torch, metadata)


async def run_vgc_bench_battles(
    *,
    runtime: ModelRuntime,
    port: int,
    battle_format: str,
    schedule: Sequence[TeamPairing],
    timeout: float,
    replay_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    from poke_env import AccountConfiguration, ServerConfiguration

    if not schedule:
        raise ValueError("La agenda de combates no puede estar vacía.")
    count = len(schedule)
    suffix = hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:6]
    server_configuration = ServerConfiguration(
        f"ws://127.0.0.1:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )
    common = {
        "battle_format": battle_format,
        "server_configuration": server_configuration,
        "max_concurrent_battles": 1,
        "accept_open_team_sheet": True,
        "log_level": logging.WARNING,
        "policy": runtime.policy,
        "deterministic": True,
    }
    player_a = runtime.player_class(
        account_configuration=AccountConfiguration(f"VGCAlpha{suffix}", None),
        team=schedule[0].alpha.team_text,
        save_replays=str(replay_dir),
        **common,
    )
    player_b = runtime.player_class(
        account_configuration=AccountConfiguration(f"VGCBeta{suffix}", None),
        team=schedule[0].beta.team_text,
        **common,
    )

    summaries: list[dict[str, Any]] = []
    progress = Progress(count, "VGC-Bench sin piedad")
    try:
        for index, pairing in enumerate(schedule):
            player_a.update_team(pairing.alpha.team_text)
            player_b.update_team(pairing.beta.team_text)
            previous_tags = set(player_a.battles)
            started = time.monotonic()
            await asyncio.wait_for(
                player_a.battle_against(player_b, n_battles=1), timeout=timeout
            )
            new_tags = set(player_a.battles) - previous_tags
            if len(new_tags) != 1:
                raise RuntimeError(
                    f"Se esperaba una batalla nueva y se recibieron {len(new_tags)}: "
                    f"{sorted(new_tags)}"
                )
            battle = player_a.battles[new_tags.pop()]
            if not battle.finished:
                raise RuntimeError(f"La batalla {battle.battle_tag} no terminó.")
            summary = battle_summary(
                battle,
                time.monotonic() - started,
                player_a.username,
                player_b.username,
            )
            summary["winnerSide"] = (
                "alpha"
                if summary["winner"] == player_a.username
                else "beta"
                if summary["winner"] == player_b.username
                else "tie"
            )
            summary["pairing"] = {
                "id": pairing.canonical_id,
                "alphaTeamId": pairing.alpha.id,
                "betaTeamId": pairing.beta.id,
            }
            summaries.append(summary)
            progress.advance(
                f"Batalla {index + 1}: {pairing.alpha.id} vs {pairing.beta.id}"
            )
            player_a.reset_battles()
            player_b.reset_battles()
    finally:
        with suppress(Exception):
            await player_a.ps_client.stop_listening()
        with suppress(Exception):
            await player_b.ps_client.stop_listening()

    wins = {
        "alpha": sum(item["winner"] == player_a.username for item in summaries),
        "beta": sum(item["winner"] == player_b.username for item in summaries),
        "ties": sum(item["winner"] == "tie" for item in summaries),
    }
    aliases = {
        "alpha": int(player_a.aliases_removed),
        "beta": int(player_b.aliases_removed),
    }
    return summaries, wins, aliases


async def run_baseline_benchmark(
    *,
    runtime: ModelRuntime,
    port: int,
    battle_format: str,
    schedule: Sequence[BenchmarkBattlePlan],
    timeout: float,
    replay_dir: Path,
    seed: int,
) -> dict[str, Any]:
    """Evaluate VGC-Bench against every baseline on one mirrored schedule."""

    from poke_env import AccountConfiguration, ServerConfiguration
    from poke_env.player import (
        MaxBasePowerPlayer,
        RandomPlayer,
        SimpleHeuristicsPlayer,
    )

    if not schedule:
        raise ValueError("La agenda de benchmark no puede estar vacía.")
    baseline_classes = {
        "random": RandomPlayer,
        "max-base-power": MaxBasePowerPlayer,
        "simple-heuristics": SimpleHeuristicsPlayer,
    }
    if set(baseline_classes) != set(BASELINE_BY_ID):
        raise RuntimeError("El catálogo de baselines y sus clases no coincide.")

    server_configuration = ServerConfiguration(
        f"ws://127.0.0.1:{port}/showdown/websocket",
        "https://play.pokemonshowdown.com/action.php?",
    )
    common = {
        "battle_format": battle_format,
        "server_configuration": server_configuration,
        "max_concurrent_battles": 1,
        "accept_open_team_sheet": True,
        "log_level": logging.WARNING,
    }
    progress = Progress(
        len(schedule) * len(BASELINE_SPECS), "Benchmark VGC-Bench"
    )
    all_summaries: list[dict[str, Any]] = []
    opponents: dict[str, dict[str, Any]] = {}

    for baseline_index, spec in enumerate(BASELINE_SPECS):
        # Baselines use Python's global RNG for preview, targets and some switches.
        # Resetting it reproduces their choices for an equivalent battle state;
        # Showdown's own damage RNG remains intentionally independent.
        random.seed(seed)
        suffix = hashlib.sha256(
            f"{time.time_ns()}:{baseline_index}:{spec.id}".encode()
        ).hexdigest()[:6]
        first_plan = schedule[0]
        vgc_first_team = (
            first_plan.pairing.alpha
            if first_plan.vgc_bench_side == "alpha"
            else first_plan.pairing.beta
        )
        baseline_first_team = (
            first_plan.pairing.beta
            if first_plan.vgc_bench_side == "alpha"
            else first_plan.pairing.alpha
        )
        opponent_replays = replay_dir / spec.id
        opponent_replays.mkdir(parents=True, exist_ok=True)
        vgc_player = runtime.player_class(
            account_configuration=AccountConfiguration(
                f"VGC{baseline_index}{suffix}", None
            ),
            team=vgc_first_team.team_text,
            save_replays=str(opponent_replays),
            policy=runtime.policy,
            deterministic=True,
            **common,
        )
        baseline_player = baseline_classes[spec.id](
            account_configuration=AccountConfiguration(
                f"BL{baseline_index}{suffix}", None
            ),
            team=baseline_first_team.team_text,
            **common,
        )
        summaries: list[dict[str, Any]] = []
        opponent_started = time.monotonic()
        try:
            for battle_index, plan in enumerate(schedule):
                pairing = plan.pairing
                if plan.vgc_bench_side == "alpha":
                    alpha_player = vgc_player
                    beta_player = baseline_player
                else:
                    alpha_player = baseline_player
                    beta_player = vgc_player
                alpha_player.update_team(pairing.alpha.team_text)
                beta_player.update_team(pairing.beta.team_text)
                previous_tags = set(alpha_player.battles)
                started = time.monotonic()
                await asyncio.wait_for(
                    alpha_player.battle_against(beta_player, n_battles=1),
                    timeout=timeout,
                )
                new_tags = set(alpha_player.battles) - previous_tags
                if len(new_tags) != 1:
                    raise RuntimeError(
                        "Se esperaba una batalla nueva contra "
                        f"{spec.label} y se recibieron {len(new_tags)}: "
                        f"{sorted(new_tags)}"
                    )
                battle = alpha_player.battles[new_tags.pop()]
                if not battle.finished:
                    raise RuntimeError(f"La batalla {battle.battle_tag} no terminó.")
                summary = battle_summary(
                    battle,
                    time.monotonic() - started,
                    alpha_player.username,
                    beta_player.username,
                )
                summary["winnerSide"] = (
                    "alpha"
                    if summary["winner"] == alpha_player.username
                    else "beta"
                    if summary["winner"] == beta_player.username
                    else "tie"
                )
                summary["winnerAgent"] = (
                    "vgcBench"
                    if summary["winner"] == vgc_player.username
                    else "baseline"
                    if summary["winner"] == baseline_player.username
                    else "tie"
                )
                summary["pairing"] = {
                    "id": pairing.canonical_id,
                    "alphaTeamId": pairing.alpha.id,
                    "betaTeamId": pairing.beta.id,
                }
                summary["benchmark"] = {
                    "baselineId": spec.id,
                    "scheduleIndex": battle_index,
                    "vgcBenchSide": plan.vgc_bench_side,
                }
                summaries.append(summary)
                all_summaries.append(summary)
                progress.advance(
                    f"{spec.label} {battle_index + 1}/{len(schedule)} · "
                    f"{pairing.alpha.id} vs {pairing.beta.id}"
                )
                # Battle objects are large. Replays and compact summaries are already
                # saved, so bound memory during 1,500-battle Colab runs.
                alpha_player.reset_battles()
                beta_player.reset_battles()
        finally:
            with suppress(Exception):
                await vgc_player.ps_client.stop_listening()
            with suppress(Exception):
                await baseline_player.ps_client.stop_listening()

        duration = time.monotonic() - opponent_started
        wins = sum(item["winnerAgent"] == "vgcBench" for item in summaries)
        losses = sum(item["winnerAgent"] == "baseline" for item in summaries)
        ties = sum(item["winnerAgent"] == "tie" for item in summaries)
        report = summarize_vgc_bench_record(wins=wins, losses=losses, ties=ties)
        report.update(spec.result_metadata())
        report.update(
            {
                "teamPreview": "random-poke-env-default",
                "durationSeconds": round(duration, 3),
                "battlesPerMinute": round(len(summaries) / duration * 60, 3),
                "aliasesRemoved": int(vgc_player.aliases_removed),
            }
        )
        opponents[spec.id] = report

    overall = summarize_vgc_bench_record(
        wins=sum(report["wins"] for report in opponents.values()),
        losses=sum(report["losses"] for report in opponents.values()),
        ties=sum(report["ties"] for report in opponents.values()),
    )
    return {
        "items": all_summaries,
        "opponents": opponents,
        "overall": overall,
        "aliasesRemoved": {
            baseline_id: report["aliasesRemoved"]
            for baseline_id, report in opponents.items()
        },
    }


def validate_team_corpus(
    *, showdown_checkout: Path, battle_format: str, corpus: TeamCorpus
) -> tuple[list[TeamRecord], dict[str, str], list[dict[str, str]]]:
    """Validate every team and skip only invalid user-provided additions."""

    valid: list[TeamRecord] = []
    messages: dict[str, str] = {}
    ignored = list(corpus.ignored)
    for team in corpus.teams:
        try:
            messages[team.id] = validate_team(
                showdown_checkout, battle_format, team.team_text
            )
            valid.append(team)
        except RuntimeError as error:
            if team.origin != "team-builder-drive":
                raise RuntimeError(
                    f"El equipo versionado {team.id} dejó de ser válido en "
                    f"{battle_format}.\n{error}"
                ) from error
            ignored.append(
                {
                    "path": str(team.path.resolve()),
                    "reason": f"Showdown lo rechazó: {error}"[:2_000],
                }
            )
    if len(valid) < 2:
        raise RuntimeError(
            "Battle Lab necesita al menos dos equipos distintos y válidos."
        )
    return valid, messages, ignored


def results_by_team(battles: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    for battle in battles:
        pairing = battle["pairing"]
        alpha_id = pairing["alphaTeamId"]
        beta_id = pairing["betaTeamId"]
        alpha = results.setdefault(
            alpha_id,
            {"battles": 0, "wins": 0, "losses": 0, "ties": 0},
        )
        beta = results.setdefault(
            beta_id,
            {"battles": 0, "wins": 0, "losses": 0, "ties": 0},
        )
        alpha["battles"] += 1
        beta["battles"] += 1
        if battle["winnerSide"] == "alpha":
            alpha["wins"] += 1
            beta["losses"] += 1
        elif battle["winnerSide"] == "beta":
            beta["wins"] += 1
            alpha["losses"] += 1
        else:
            alpha["ties"] += 1
            beta["ties"] += 1
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--showdown-checkout", type=Path, default=None)
    parser.add_argument("--showdown-repository", default=DEFAULT_SHOWDOWN_REPOSITORY)
    parser.add_argument("--showdown-commit", default=None)
    parser.add_argument("--vgc-bench-checkout", type=Path, default=None)
    parser.add_argument("--vgc-bench-repository", default=VGC_BENCH_REPOSITORY)
    parser.add_argument("--vgc-bench-commit", default=VGC_BENCH_COMMIT)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--checkpoint-url", default=VGC_BENCH_CHECKPOINT_URL)
    parser.add_argument(
        "--checkpoint-sha256", default=VGC_BENCH_CHECKPOINT_SHA256
    )
    parser.add_argument("--skip-setup", action="store_true")
    parser.add_argument("--format", default=DEFAULT_FORMAT)
    parser.add_argument(
        "--corpus-manifest", type=Path, default=DEFAULT_CORPUS_MANIFEST
    )
    parser.add_argument(
        "--extra-teams-dir",
        type=Path,
        action="append",
        default=[],
        help="Directorio adicional con un equipo Showdown por archivo .txt/.team.",
    )
    parser.add_argument(
        "--team-a",
        type=Path,
        default=None,
        help="Equipo Alpha explícito; requiere --team-b y desactiva la rotación.",
    )
    parser.add_argument(
        "--team-b",
        type=Path,
        default=None,
        help="Equipo Beta explícito; requiere --team-a y desactiva la rotación.",
    )
    parser.add_argument(
        "--mode",
        choices=("self-play", "benchmark"),
        default="self-play",
        help="Self-play neuronal o benchmark espejado contra los tres baselines.",
    )
    parser.add_argument("--battles", type=int, default=20)
    parser.add_argument(
        "--benchmark-battles-per-baseline",
        type=int,
        default=DEFAULT_BENCHMARK_BATTLES_PER_BASELINE,
        help="Combates contra cada baseline; el valor recomendado es 500.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--battle-timeout", type=float, default=300)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.battles < 1:
        raise SystemExit("--battles debe ser mayor que cero.")
    if args.benchmark_battles_per_baseline < 1:
        raise SystemExit(
            "--benchmark-battles-per-baseline debe ser mayor que cero."
        )
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port debe estar entre 1 y 65535.")
    if len(args.checkpoint_sha256) != 64:
        raise SystemExit("--checkpoint-sha256 debe contener 64 caracteres.")
    if bool(args.team_a) != bool(args.team_b):
        raise SystemExit("--team-a y --team-b deben usarse juntos.")
    poke_env_metadata = installed_poke_env_metadata()
    missing_commands = [
        command for command in ("git", "node", "npm") if shutil.which(command) is None
    ]
    if missing_commands:
        raise RuntimeError(
            "Faltan comandos requeridos por Battle Lab: " + ", ".join(missing_commands)
        )
    node_major, node_version = installed_node_version()
    if node_major < MINIMUM_NODE_MAJOR:
        raise RuntimeError(
            f"Battle Lab requiere Node.js {MINIMUM_NODE_MAJOR} o posterior; "
            f"se detectó {node_version}."
        )

    runtime_root = args.runtime_root.resolve()
    results_dir = (args.results_dir or runtime_root / "results").resolve()
    run_id = datetime.now(timezone.utc).strftime("vgc-bench-%Y%m%dT%H%M%S-%fZ")
    run_root = runtime_root / "runs" / run_id
    logs_dir = run_root / "logs"
    local_replays = run_root / "replays"
    local_replays.mkdir(parents=True, exist_ok=True)
    showdown_commit = args.showdown_commit or read_showdown_commit()
    if args.team_a and args.team_b:
        corpus = explicit_pair(args.team_a, args.team_b)
        rotating_corpus = False
    else:
        corpus = load_bundled_corpus(args.corpus_manifest)
        corpus_format = corpus.metadata.get("format")
        if corpus_format != args.format:
            raise RuntimeError(
                f"El corpus declara {corpus_format!r}, pero --format es "
                f"{args.format!r}."
            )
        corpus = add_extra_teams(corpus, args.extra_teams_dir)
        rotating_corpus = True
    showdown_checkout = (
        args.showdown_checkout or runtime_root / "pokemon-showdown"
    ).resolve()
    vgc_bench_checkout = (
        args.vgc_bench_checkout or runtime_root / "vgc-bench"
    ).resolve()
    checkpoint = (
        args.checkpoint or runtime_root / "models" / "vgc-bench-bc-seed1-epoch100.zip"
    ).resolve()

    overall = Progress(9, "Fase 2")
    started = time.monotonic()
    if args.skip_setup:
        if git_revision(showdown_checkout) != showdown_commit:
            raise RuntimeError(
                f"--skip-setup requiere Showdown en {showdown_commit}; el checkout "
                f"está en {git_revision(showdown_checkout)}."
            )
        if not (showdown_checkout / "node_modules").is_dir() or not (
            showdown_checkout / "dist" / "sim"
        ).is_dir():
            raise RuntimeError(
                "--skip-setup requiere Showdown compilado y con dependencias."
            )
        showdown_setup: dict[str, Any] = {"skipped": True}
    else:
        showdown_setup = ensure_showdown_checkout(
            checkout=showdown_checkout,
            repository=args.showdown_repository,
            commit=showdown_commit,
            logs_dir=logs_dir,
        )
    install_runtime_config(showdown_checkout)
    overall.advance("Showdown fijado y compilado")

    valid_teams, validation_messages, ignored_teams = validate_team_corpus(
        showdown_checkout=showdown_checkout,
        battle_format=args.format,
        corpus=corpus,
    )
    for ignored in ignored_teams:
        reason = ignored.get("reason", "motivo desconocido").replace("\n", " ")
        print(
            f"⚠️ Equipo omitido: {ignored.get('path', '<sin ruta>')} · "
            f"{reason[:300]}",
            flush=True,
        )
    self_play_schedule: list[TeamPairing] = []
    benchmark_schedule: list[BenchmarkBattlePlan] = []
    if args.mode == "benchmark":
        base_pairing_count = (args.benchmark_battles_per_baseline + 1) // 2
        if rotating_corpus:
            base_pairings = build_pairing_schedule(
                valid_teams, count=base_pairing_count, seed=args.seed
            )
        else:
            base_pairings = [
                TeamPairing(alpha=valid_teams[0], beta=valid_teams[1])
                for _ in range(base_pairing_count)
            ]
        benchmark_schedule = build_mirrored_benchmark_schedule(
            base_pairings, count=args.benchmark_battles_per_baseline
        )
        rotation = benchmark_schedule_statistics(benchmark_schedule)
        rotation["sameScheduleForEveryBaseline"] = True
        rotation["baselineCount"] = len(BASELINE_SPECS)
        rotation["totalBattles"] = len(benchmark_schedule) * len(BASELINE_SPECS)
        if not rotating_corpus:
            rotation["teamPoolMode"] = "fixed-explicit-pair"
    else:
        if rotating_corpus:
            self_play_schedule = build_pairing_schedule(
                valid_teams, count=args.battles, seed=args.seed
            )
        else:
            self_play_schedule = [
                TeamPairing(alpha=valid_teams[0], beta=valid_teams[1])
                for _ in range(args.battles)
            ]
        rotation = pairing_statistics(self_play_schedule)
        if not rotating_corpus:
            rotation["mode"] = "fixed-explicit-pair"
    overall.advance(
        f"Corpus M-C: {len(valid_teams)} válidos · "
        f"{rotation['uniquePairings']} cruces"
    )
    negative_validation = verify_illegal_team_is_rejected(
        showdown_checkout, args.format, valid_teams[0].team_text
    )
    overall.advance("Control ilegal rechazado")

    if args.skip_setup:
        verify_vgc_bench_checkout(vgc_bench_checkout, args.vgc_bench_commit)
        vgc_setup: dict[str, Any] = {"skipped": True}
    else:
        vgc_setup = ensure_vgc_bench_checkout(
            checkout=vgc_bench_checkout,
            repository=args.vgc_bench_repository,
            commit=args.vgc_bench_commit,
        )
    overall.advance("Código oficial de VGC-Bench fijado")

    checkpoint_setup = download_checkpoint(
        destination=checkpoint,
        url=args.checkpoint_url,
        expected_sha256=args.checkpoint_sha256,
    )
    overall.advance("Checkpoint final verificado por SHA-256")
    model_runtime = load_model_runtime(
        checkout=vgc_bench_checkout,
        checkpoint=checkpoint,
        requested_device=args.device,
        seed=args.seed,
    )
    overall.advance(
        f"Modelo cargado en {model_runtime.metadata['device']} · modo determinista"
    )

    benchmark_result: dict[str, Any] | None = None
    with running_showdown(
        showdown_checkout, args.port, logs_dir / "showdown-server.log"
    ) as server:
        overall.advance(f"Servidor privado listo en 127.0.0.1:{args.port}")
        battle_started = time.monotonic()
        if args.mode == "benchmark":
            benchmark_result = asyncio.run(
                run_baseline_benchmark(
                    runtime=model_runtime,
                    port=args.port,
                    battle_format=args.format,
                    schedule=benchmark_schedule,
                    timeout=args.battle_timeout,
                    replay_dir=local_replays,
                    seed=args.seed,
                )
            )
            battles = benchmark_result["items"]
            benchmark_overall = benchmark_result["overall"]
            wins = {
                "vgcBench": benchmark_overall["wins"],
                "baselines": benchmark_overall["losses"],
                "ties": benchmark_overall["ties"],
            }
            aliases_removed = benchmark_result["aliasesRemoved"]
        else:
            battles, wins, aliases_removed = asyncio.run(
                run_vgc_bench_battles(
                    runtime=model_runtime,
                    port=args.port,
                    battle_format=args.format,
                    schedule=self_play_schedule,
                    timeout=args.battle_timeout,
                    replay_dir=local_replays,
                )
            )
        resources = environment_snapshot(server.process.pid)
        startup_seconds = server.startup_seconds
        battle_seconds = time.monotonic() - battle_started
    server_log = (logs_dir / "showdown-server.log").read_text(
        encoding="utf-8", errors="replace"
    )
    if "CRASH:" in server_log:
        raise RuntimeError(
            "Showdown registró un fallo interno.\n"
            + tail(logs_dir / "showdown-server.log")
        )
    overall.advance(f"{len(battles)} combates VGC-Bench terminados")

    shutil.copytree(logs_dir, local_replays / "logs", dirs_exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    replay_archive = Path(
        shutil.make_archive(
            str(results_dir / f"{run_id}-replays"), "zip", root_dir=local_replays
        )
    )
    result_path = results_dir / f"{run_id}.json"
    total_seconds = time.monotonic() - started
    origin_counts: dict[str, int] = {}
    for team in valid_teams:
        origin_counts[team.origin] = origin_counts.get(team.origin, 0) + 1
    if args.mode == "benchmark":
        assert benchmark_result is not None
        requested_battles = (
            args.benchmark_battles_per_baseline * len(BASELINE_SPECS)
        )
        player_metadata: dict[str, Any] = {
            "mode": "baseline-benchmark",
            "vgcBench": "deterministic neural policy",
            "baselines": [spec.result_metadata() for spec in BASELINE_SPECS],
        }
    else:
        requested_battles = args.battles
        player_metadata = {
            "mode": "self-play",
            "alpha": "VGC-Bench deterministic",
            "beta": "VGC-Bench deterministic",
        }
    payload = {
        "schemaVersion": 4,
        "runId": run_id,
        "createdAt": utc_now(),
        "status": "passed",
        "mode": args.mode,
        "projectCommit": git_revision(PROJECT_ROOT),
        "showdown": {
            "repository": args.showdown_repository,
            "commit": showdown_commit,
            "format": args.format,
            "port": args.port,
            "startupSeconds": startup_seconds,
            "setupTimings": showdown_setup,
        },
        "vgcBench": {
            "repository": args.vgc_bench_repository,
            "commit": args.vgc_bench_commit,
            "setupTimings": vgc_setup,
            "checkpoint": {
                "revision": VGC_BENCH_CHECKPOINT_REVISION,
                "url": args.checkpoint_url,
                "sha256": args.checkpoint_sha256,
                **checkpoint_setup,
            },
            "policy": model_runtime.metadata,
            "players": player_metadata,
            "pokeEnv": poke_env_metadata,
            "aliasesRemoved": aliases_removed,
            "trainingRegulations": ["M-A", "M-B"],
            "evaluationRegulation": "M-C",
        },
        "teams": {
            "corpus": corpus.metadata,
            "available": len(valid_teams),
            "byOrigin": origin_counts,
            "items": [
                team.result_metadata(validation_messages.get(team.id, ""))
                for team in valid_teams
            ],
            "ignored": ignored_teams,
            "rotation": rotation,
            "negativeControl": negative_validation,
        },
        "battles": {
            "requested": requested_battles,
            "completed": len(battles),
            "durationSeconds": round(battle_seconds, 3),
            "battlesPerMinute": round(len(battles) / battle_seconds * 60, 3),
            "wins": wins,
            "byTeam": results_by_team(battles),
            "items": battles,
        },
        "environment": resources,
        "artifacts": {"replaysZip": str(replay_archive)},
        "totalDurationSeconds": round(total_seconds, 3),
    }
    if benchmark_result is not None:
        payload["benchmark"] = {
            "method": "paired-mirrored-baseline-suite",
            "battlesPerBaseline": args.benchmark_battles_per_baseline,
            "sameScheduleForEveryBaseline": True,
            "randomSeed": args.seed,
            "schedule": rotation,
            "ratingModel": {
                "scope": "internal-only",
                "name": "logistic-400",
                "anchorRating": 1500,
                "anchorMeaning": (
                    "Cada rival, o el pool equiponderado, se trata como un "
                    "oponente interno de 1500. No equivale al Elo de Showdown."
                ),
                "pointEstimateSmoothing": "one-virtual-draw",
                "confidenceInterval": "Wilson 95% sobre puntuación de match",
            },
            "overall": benchmark_result["overall"],
            "opponents": benchmark_result["opponents"],
        }
    atomic_json(result_path, payload)
    overall.advance(f"Resultado persistido en {result_path}")

    print("\n✅ VGC-Bench jugó sin piedad")
    print(f"   Formato: {args.format} (evaluación fuera de M-A/M-B)")
    print(
        f"   Modelo: BC seed 1 · epoch 100 · determinista · "
        f"{model_runtime.metadata['device']}"
    )
    print(
        f"   Corpus: {len(valid_teams)} equipos válidos · "
        f"{rotation['uniquePairings']} cruces únicos"
    )
    if ignored_teams:
        print(f"   Omitidos: {len(ignored_teams)} equipos externos/duplicados")
    print(f"   Combates: {len(battles)}/{requested_battles} · victorias {wins}")
    if benchmark_result is not None:
        print("   Benchmark interno (cada rival anclado en 1500):")
        for spec in BASELINE_SPECS:
            report = benchmark_result["opponents"][spec.id]
            print(
                f"     {spec.label}: {report['wins']}-{report['losses']}-"
                f"{report['ties']} · {report['scorePercent']:.2f}% · "
                f"ΔElo {report['elo']['difference']:+.1f}"
            )
        benchmark_overall = benchmark_result["overall"]
        print(
            "     Pool equiponderado: "
            f"{benchmark_overall['scorePercent']:.2f}% · "
            f"Elo interno {benchmark_overall['elo']['performanceRating']:.1f}"
        )
        print("     ⚠️ Este Elo no es el rating oficial de Pokémon Showdown.")
    print(f"   Rendimiento: {payload['battles']['battlesPerMinute']} combates/min")
    print(f"   Resultado: {result_path}")
    print(f"   Replays: {replay_archive}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(
            "\nInterrumpido por el usuario; el servidor fue detenido.", file=sys.stderr
        )
        raise SystemExit(130)
