"""Champion/candidate comparison using the established M-C benchmark protocol."""
from __future__ import annotations

import asyncio
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
from battle_lab.team_corpus import build_pairing_schedule


def evaluate(*, vgc_root: Path, showdown: Path, run: Path, champion: dict,
             candidate: dict, battles: int, seed: int, port: int, device: str,
             battle_format: str, code_sha: str, showdown_sha: str) -> dict:
    split_root = run / "split"
    split = read_json(split_root / "split_manifest.json")
    corpus = load_holdout_corpus(split_root / "holdout", split_root / "split_manifest.json",
                                 expected_count=split["holdoutTeams"], battle_format=battle_format)
    for side in ("train", "holdout"):
        actual = {p.name: sha256_file(p) for p in (split_root / side).glob("mc*.txt")}
        if actual != split["fileHashes"][side]:
            raise RuntimeError("The frozen team snapshot changed: " + side)
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
              "protocol": {"format": battle_format, "seed": seed, "schedule": meta,
                           "runtimeIdentity": identity, "showdownCommit": showdown_sha,
                           "pkmnCommit": code_sha, "catalogAliases": aliases,
                           "battlesPerControlPerModel": battles, "totalBattles": battles * 6},
              "interpretation": "Observed benchmark improvement, not statistical confirmation or human ladder strength.",
              "artifacts": {"replays": str(output / "replays")}}
    atomic_json(output / "comparison.json", result)
    return {"comparisonFile": str(output / "comparison.json"), "comparison": comparison,
            "freshHoldout": fresh, "battles": battles * 6}
