"""Champion/candidate comparison using the established M-C benchmark protocol."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import random
import time
from contextlib import suppress
from pathlib import Path

from battle_lab import vgc_bench_battle as battle
from battle_lab.benchmarking import (BASELINE_SPECS, benchmark_schedule_statistics,
                                    build_mirrored_benchmark_schedule)
from battle_lab.mc_holdout_benchmark import (load_holdout_corpus, run_model_suite,
                                           compare_model_reports, _model_team_results)
from battle_lab.mc_refresh_data import read_json
from battle_lab.mc_rl_light_v3 import alias_mc_runtime_catalogs
from battle_lab.mc_training import Progress, atomic_json, sha256_file, sha256_text, utc_now
from battle_lab.showdown_smoke import running_showdown, validate_team
from battle_lab.team_corpus import build_pairing_schedule, normalize_team_text
from battle_lab.mc_team_split import team_signature


DIRECT_PROTOCOL = "direct-v1"
DIRECT_OPPONENTS = ("production", "base", "simple-heuristics")


def direct_record(items: list[dict]) -> dict:
    wins = sum(item["winnerAgent"] == "candidate" for item in items)
    losses = sum(item["winnerAgent"] == "opponent" for item in items)
    ties = sum(item["winnerAgent"] == "tie" for item in items)
    if not items or wins + losses + ties != len(items):
        raise RuntimeError("Resultados directos vacíos o inválidos")
    return {"games": len(items), "wins": wins, "losses": losses, "ties": ties,
            "scorePercent": round(100 * (wins + .5 * ties) / len(items), 2)}


def validate_direct_chunk(chunk: dict, identity: str, opponent: str, schedule, offset: int) -> list[dict]:
    items = chunk.get("items", [])
    if (chunk.get("identity") != identity or chunk.get("opponentId") != opponent
            or chunk.get("state") != "completed" or len(items) != len(schedule)
            or chunk.get("itemsSha256") != sha256_text(json.dumps(items, sort_keys=True))):
        raise RuntimeError("El bloque directo guardado está incompleto o pertenece a otro benchmark")
    tags = set()
    for index, (item, plan) in enumerate(zip(items, schedule), offset):
        expected = {"opponentId": opponent, "scheduleIndex": index, **plan.result_metadata()}
        if item.get("benchmark") != expected or item.get("winnerAgent") not in {"candidate", "opponent", "tie"}:
            raise RuntimeError("El bloque directo no coincide con la agenda")
        if not item.get("battleTag") or item["battleTag"] in tags:
            raise RuntimeError("Batalla duplicada o sin identificador")
        tags.add(item["battleTag"])
    return items


async def run_direct_matchup(*, candidate_runtime, opponent_runtime, opponent_id: str,
                             port: int, battle_format: str, schedule, offset: int,
                             replay_dir: Path, seed: int, progress: Progress,
                             completed: int, timeout: float = 300) -> list[dict]:
    """Play two distinct policies (or Simple Heuristics), swapping teams/sides."""
    from poke_env import AccountConfiguration, ServerConfiguration
    from poke_env.player import SimpleHeuristicsPlayer

    random.seed(seed + offset)
    suffix = sha256_text(str(time.time_ns()))[:8]
    replay_dir.mkdir(parents=True, exist_ok=True)
    common = {"battle_format": battle_format, "max_concurrent_battles": 1,
              "accept_open_team_sheet": True, "log_level": logging.WARNING,
              "server_configuration": ServerConfiguration(
                  f"ws://127.0.0.1:{port}/showdown/websocket",
                  "https://play.pokemonshowdown.com/action.php?")}
    first = schedule[0]
    candidate_team = first.pairing.alpha if first.vgc_bench_side == "alpha" else first.pairing.beta
    opponent_team = first.pairing.beta if first.vgc_bench_side == "alpha" else first.pairing.alpha
    players = []
    items = []
    try:
        candidate = candidate_runtime.player_class(
            account_configuration=AccountConfiguration("Candidate" + suffix, None),
            policy=candidate_runtime.policy, deterministic=True, team=candidate_team.team_text,
            save_replays=str(replay_dir), **common)
        players.append(candidate)
        opponent_class = opponent_runtime.player_class if opponent_runtime is not None else SimpleHeuristicsPlayer
        kwargs = {"policy": opponent_runtime.policy, "deterministic": True} if opponent_runtime is not None else {}
        opponent = opponent_class(account_configuration=AccountConfiguration("Opponent" + suffix, None),
                                  team=opponent_team.team_text, **kwargs, **common)
        players.append(opponent)
        for index, plan in enumerate(schedule, offset):
            alpha, beta = (candidate, opponent) if plan.vgc_bench_side == "alpha" else (opponent, candidate)
            alpha.update_team(plan.pairing.alpha.team_text)
            beta.update_team(plan.pairing.beta.team_text)
            previous = set(candidate.battles)
            started = time.monotonic()
            await asyncio.wait_for(alpha.battle_against(beta, n_battles=1), timeout=timeout)
            tags = set(candidate.battles) - previous
            if len(tags) != 1:
                raise RuntimeError("Se esperaba una única batalla directa nueva")
            played = candidate.battles[tags.pop()]
            if not played.finished:
                raise RuntimeError("La batalla directa no terminó")
            # Read the candidate's perspective even when it plays on the beta side.
            summary = battle.battle_summary(played, time.monotonic() - started, candidate.username, opponent.username)
            summary.update(
                winnerAgent="candidate" if played.won else "opponent" if played.lost else "tie",
                candidateUsername=candidate.username, opponentUsername=opponent.username,
                replayFile=str(replay_dir / f"{candidate.username} - {played.battle_tag}.html"),
                benchmark={"opponentId": opponent_id, "scheduleIndex": index, **plan.result_metadata()})
            items.append(summary)
            progress.update(completed + len(items), f"{opponent_id} · partida {index + 1}")
            candidate.reset_battles()
            opponent.reset_battles()
    finally:
        for player in players:
            with suppress(Exception):
                await player.ps_client.stop_listening()
    return items


def evaluate_direct(*, vgc_root: Path, showdown: Path, run: Path, production: dict,
                    candidate: dict, battles: int, seed: int, port: int, device: str,
                    battle_format: str, code_sha: str, showdown_sha: str,
                    runtime_versions: dict) -> dict:
    from battle_lab.mc_training import download_baseline, VGC_BENCH_CHECKPOINT_SHA256

    if battles < 2 or battles % 2:
        raise ValueError("Las batallas por rival deben ser pares y al menos 2")
    for spec in (production, candidate):
        if sha256_file(Path(spec["checkpoint"])) != spec["sha256"]:
            raise RuntimeError("Los pesos cambiaron antes del benchmark directo")
    # The public VGC-Bench control is an evaluation dependency, not one of our
    # retained models. Keep it in the ephemeral Colab runtime so Drive contains
    # only the canonical production checkpoint and the current candidate.
    baseline_path = vgc_root.parent / "benchmark-cache" / "vgc-bench-ma-mb-100.zip"
    base = {"id": "VGC-Bench-public-BC", "sha256": VGC_BENCH_CHECKPOINT_SHA256,
            "checkpoint": str(download_baseline(baseline_path))}
    corpus, split = evaluation_corpus(run, battle_format)
    aliases = alias_mc_runtime_catalogs(vgc_root)
    for team in corpus.teams:
        validate_team(showdown, battle_format, team.team_text)
    pairings = build_pairing_schedule(corpus.teams, count=battles // 2, seed=seed)
    schedule = build_mirrored_benchmark_schedule(pairings, count=battles)
    meta = benchmark_schedule_statistics(schedule)
    contract = {"protocol": DIRECT_PROTOCOL, "candidateSha256": candidate["sha256"],
                "productionSha256": production["sha256"], "baseSha256": base["sha256"],
                "scheduleSha256": meta["sha256"], "holdoutHashes": split["fileHashes"]["holdout"],
                "sourceManifestSha256": split["sourceManifestSha256"], "format": battle_format,
                "seed": seed, "device": device, "codeSha": code_sha, "showdownSha": showdown_sha,
                "vgcBenchSha": battle.VGC_BENCH_COMMIT, "runtimeVersions": runtime_versions}
    identity = sha256_text(json.dumps(contract, sort_keys=True))
    output = run / "evaluation" / DIRECT_PROTOCOL / identity[:16]
    atomic_json(output / "contract.json", contract)
    opponents = {"production": production, "base": base, "simple-heuristics": {"class": "SimpleHeuristicsPlayer"}}
    all_items, results, runtimes = [], {}, {}
    progress = Progress(3 * battles, "Candidato vs productivo / base / Simple Heuristics")
    candidate_runtime = battle.load_model_runtime(checkout=vgc_root, checkpoint=Path(candidate["checkpoint"]),
                                                  requested_device=device, seed=seed)
    runtimes["candidate"] = candidate_runtime.metadata
    with running_showdown(showdown, port, output / "showdown.log"):
        for opponent_id, spec in opponents.items():
            opponent_runtime = None
            if "checkpoint" in spec:
                opponent_runtime = battle.load_model_runtime(checkout=vgc_root, checkpoint=Path(spec["checkpoint"]),
                                                             requested_device=device, seed=seed)
                runtimes[opponent_id] = opponent_runtime.metadata
            items = []
            # Atomic blocks of 20 limit repeated work after a Colab interruption.
            for offset in range(0, battles, 20):
                block = schedule[offset:offset + 20]
                path = output / "chunks" / opponent_id / f"{offset:06d}.json"
                chunk = read_json(path)
                if chunk is None:
                    played = asyncio.run(run_direct_matchup(
                        candidate_runtime=candidate_runtime, opponent_runtime=opponent_runtime,
                        opponent_id=opponent_id, port=port, battle_format=battle_format,
                        schedule=block, offset=offset, replay_dir=output / "replays" / opponent_id,
                        seed=seed, progress=progress, completed=len(all_items) + len(items)))
                    chunk = {"identity": identity, "opponentId": opponent_id, "state": "completed",
                             "items": played, "itemsSha256": sha256_text(json.dumps(played, sort_keys=True))}
                    validate_direct_chunk(chunk, identity, opponent_id, block, offset)
                    atomic_json(path, chunk)
                items.extend(validate_direct_chunk(chunk, identity, opponent_id, block, offset))
                progress.update(len(all_items) + len(items), opponent_id, force=True)
            results[opponent_id] = {**direct_record(items), "opponent": spec,
                                    "replays": str(output / "replays" / opponent_id)}
            all_items.extend(items)
            del opponent_runtime
    if len(all_items) != 3 * battles:
        raise RuntimeError("Benchmark directo incompleto")
    # No mean across unrelated opponents: the direct production match is primary.
    prod = results["production"]
    comparison = {"protocol": DIRECT_PROTOCOL, "opponents": results,
                  "verdict": "pass" if prod["wins"] > prod["losses"] else "fail",
                  "criterion": "Observed candidate score >50% directly against the pinned production policy; ties count 0.5. Base and SH are separate diagnostics."}
    result = {"generatedAt": utc_now(), "state": "completed", "contract": contract,
              "candidate": candidate, "comparison": comparison, "runtime": runtimes,
              "schedule": meta, "catalogAliases": aliases, "items": all_items,
              "battles": len(all_items), "replays": str(output / "replays"),
              "holdoutView": {k: split[k] for k in ("holdoutTeams", "sourceHoldoutTeams", "removedDuplicates", "sourceManifestSha256")},
              "interpretation": "Descriptive head-to-head results on reused holdout teams. Not statistical confirmation, human strength, or evaluation of Nana's personal adaptation. Production is a pinned documented reference, not a live ROG attestation."}
    atomic_json(output / "comparison.json", result)
    with (output / "comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["opponent", "wins", "losses", "ties", "candidate_score_pct", "replays"])
        for opponent_id, record in results.items():
            writer.writerow([opponent_id, record["wins"], record["losses"], record["ties"], record["scorePercent"], record["replays"]])
    return {"comparisonFile": str(output / "comparison.json"), "comparison": comparison,
            "battles": len(all_items), "holdoutView": result["holdoutView"], "replays": result["replays"]}


def evaluation_corpus(run: Path, battle_format: str):
    """Build a deduplicated evaluation view without changing the frozen dataset."""
    split_root = run / "split"
    split = read_json(split_root / "split_manifest.json")
    for side in ("train", "holdout"):
        actual = {p.name: sha256_file(p) for p in (split_root / side).glob("mc*.txt")}
        if actual != split["fileHashes"][side]:
            raise RuntimeError("The frozen team snapshot changed: " + side)
        if set(actual) != set(split[side + "Files"]) or len(actual) != split[side + "Teams"]:
            raise RuntimeError("Inconsistent frozen split manifest: " + side)
    train_signatures = {team_signature(p) for p in (split_root / "train").glob("mc*.txt")}
    view = run / "evaluation" / "corpus"
    view.mkdir(parents=True, exist_ok=True)
    groups, texts = {}, {}
    for name in sorted(split["holdoutFiles"], key=lambda n: int(Path(n).stem[2:])):
        path = split_root / "holdout" / name
        if team_signature(path) in train_signatures:
            raise RuntimeError("Train/holdout signature leakage before evaluation")
        text = normalize_team_text(path.read_text(encoding="utf-8"))
        digest = sha256_text(text)
        groups.setdefault(digest, []).append(name)
        texts[digest] = text
    if len(groups) < 2:
        raise RuntimeError("Fewer than two distinct holdout teams")
    kept, fresh, removed = [], [], []
    for digest, aliases in groups.items():
        name = aliases[0]
        kept.append(name)
        (view / name).write_text(texts[digest], encoding="utf-8")
        # An alias cannot make an old team fresh.
        if all(n in split["freshHoldoutFiles"] for n in aliases):
            fresh.append(name)
        removed.extend({"file": n, "sameAs": name, "normalizedSha256": digest} for n in aliases[1:])
    for stale in view.glob("mc*.txt"):
        if stale.name not in kept:
            stale.unlink()
    manifest = {**split, "holdoutTeams": len(kept), "holdoutFiles": kept,
                "freshHoldoutFiles": fresh, "freshHoldoutTeams": len(fresh),
                "holdoutDir": str(view),
                "fileHashes": {**split["fileHashes"],
                               "holdout": {n: sha256_file(view / n) for n in kept}},
                "sourceManifestSha256": sha256_file(split_root / "split_manifest.json"),
                "sourceHoldoutTeams": split["holdoutTeams"], "removedDuplicates": removed}
    atomic_json(view / "split_manifest.json", manifest)
    corpus = load_holdout_corpus(view, view / "split_manifest.json", expected_count=len(kept),
                                 battle_format=battle_format)
    print(f"Holdout de evaluación: {len(kept)} equipos únicos; {len(removed)} copias equivalentes omitidas. Snapshot original conservado.", flush=True)
    return corpus, manifest


def evaluate(*, vgc_root: Path, showdown: Path, run: Path, champion: dict,
             candidate: dict, battles: int, seed: int, port: int, device: str,
             battle_format: str, code_sha: str, showdown_sha: str) -> dict:
    corpus, split = evaluation_corpus(run, battle_format)
    for model in (champion, candidate):
        if sha256_file(Path(model["checkpoint"])) != model["sha256"]:
            raise RuntimeError("Model bytes changed before evaluation")
    aliases = alias_mc_runtime_catalogs(vgc_root)
    for team in corpus.teams:
        validate_team(showdown, battle_format, team.team_text)
    pairings = build_pairing_schedule(corpus.teams, count=battles // 2, seed=seed)
    schedule = build_mirrored_benchmark_schedule(pairings, count=battles)
    meta = benchmark_schedule_statistics(schedule)
    # Include runtime identity as well as the agenda in every resumable chunk.
    identity = sha256_text(f"{meta['sha256']}|{code_sha}|{showdown_sha}|{battle.VGC_BENCH_COMMIT}|{device}")
    output = run / "evaluation"
    output.mkdir(exist_ok=True)
    models = {}
    progress = Progress(6, "Evaluación champion/candidato")
    with running_showdown(showdown, port, output / "showdown.log"):
        for i, (name, spec) in enumerate((("champion", champion), ("candidate", candidate))):
            runtime = battle.load_model_runtime(checkout=vgc_root, checkpoint=Path(spec["checkpoint"]),
                                                requested_device=device, seed=seed)
            result = asyncio.run(run_model_suite(
                model_id=name, runtime=runtime, checkpoint_sha256=spec["sha256"], port=port,
                battle_format=battle_format, schedule=schedule, schedule_sha256=identity,
                timeout=300, replay_root=output / "replays", chunk_root=output / "chunks",
                seed=seed, battles_per_baseline=battles, resume=True, global_progress=progress,
                completed_offset=i * 3, status_path=output / "status.json"))
            if len(result["items"]) != battles * len(BASELINE_SPECS):
                raise RuntimeError("Incomplete evaluation; no comparison is valid")
            models[name] = {**spec, "results": result, "runtime": runtime.metadata}
            del runtime
    comparison = compare_model_reports(models["champion"]["results"], models["candidate"]["results"])
    fresh_ids = {Path(n).stem.upper() for n in split["freshHoldoutFiles"]}
    fresh = {}
    for name, model in models.items():
        by_team = _model_team_results(model["results"]["items"])
        selected = [v for k, v in by_team.items() if k in fresh_ids]
        games = sum(v["games"] for v in selected)
        wins = sum(v["wins"] for v in selected)
        ties = sum(v["ties"] for v in selected)
        fresh[name] = {"games": games, "teamsTested": len(selected),
                       "scorePercent": 100 * (wins + .5 * ties) / games if games else None}
    result = {"generatedAt": utc_now(), "state": "completed", "models": models,
              "comparison": comparison, "freshHoldout": fresh,
              "holdoutView": {k: split[k] for k in ("holdoutTeams", "sourceHoldoutTeams", "removedDuplicates", "sourceManifestSha256")},
              "protocol": {"format": battle_format, "seed": seed, "schedule": meta,
                           "runtimeIdentity": identity, "showdownCommit": showdown_sha,
                           "pkmnCommit": code_sha, "catalogAliases": aliases,
                           "battlesPerControlPerModel": battles, "totalBattles": battles * 6},
              "interpretation": "Observed benchmark improvement, not statistical confirmation or human ladder strength.",
              "artifacts": {"replays": str(output / "replays")}}
    atomic_json(output / "comparison.json", result)
    return {"comparisonFile": str(output / "comparison.json"), "comparison": comparison,
            "freshHoldout": fresh, "battles": battles * 6, "holdoutView": result["holdoutView"]}
