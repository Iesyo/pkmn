from __future__ import annotations
import argparse
import asyncio
import gc
import json
import math
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from battle_lab.benchmarking import BASELINE_SPECS, BenchmarkBattlePlan, benchmark_schedule_statistics, build_mirrored_benchmark_schedule, summarize_vgc_bench_record
from battle_lab.mc_rl_light_v3 import alias_mc_runtime_catalogs
from battle_lab.mc_training import Progress, atomic_json, human_seconds, sha256_file
from battle_lab.showdown_smoke import DEFAULT_SHOWDOWN_REPOSITORY, ensure_showdown_checkout, install_runtime_config, read_showdown_commit, running_showdown, validate_team
from battle_lab.team_corpus import TeamCorpus, TeamRecord, build_pairing_schedule, extract_roster, normalize_team_text, team_sha256
from battle_lab import vgc_bench_battle as battle
DEFAULT_HOLDOUT_COUNT = 36
DEFAULT_BATTLES_PER_BASELINE = 500
DEFAULT_SEED = 260913
PRIMARY_BASELINE_ID = 'simple-heuristics'
PUBLIC_MODEL_ID = 'bc-public'
LIGHT_MODEL_ID = 'mc-light-196608'

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_checkpoint(path: Path, expected_sha256: str, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f'No existe {label}: {path}')
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise RuntimeError(f'SHA-256 inválido para {label}: {actual}; se esperaba {expected_sha256}.')
    return actual

def load_holdout_corpus(holdout_dir: Path, split_manifest_path: Path, *, expected_count: int=DEFAULT_HOLDOUT_COUNT, battle_format: str) -> TeamCorpus:
    if not split_manifest_path.is_file():
        raise RuntimeError(f'Falta split_manifest.json: {split_manifest_path}')
    manifest = json.loads(split_manifest_path.read_text(encoding='utf-8'))
    declared_holdout = int(manifest.get('holdoutTeams', -1))
    leakage = int(manifest.get('signatureLeakage', -1))
    if declared_holdout != expected_count:
        raise RuntimeError(f'El split declara {declared_holdout} holdout; se esperaban {expected_count}.')
    if leakage != 0:
        raise RuntimeError(f'El split tiene fuga train/holdout: signatureLeakage={leakage}')
    root = holdout_dir.resolve()
    if not root.is_dir():
        raise RuntimeError(f'No existe el directorio holdout: {root}')
    paths = sorted(root.glob('mc*.txt'), key=lambda p: int(p.stem[2:]))
    if len(paths) != expected_count:
        raise RuntimeError(f'El directorio holdout contiene {len(paths)} equipos; se esperaban {expected_count}.')
    teams: list[TeamRecord] = []
    hashes: set[str] = set()
    ids: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError(f'Ruta fuera del holdout: {resolved}')
        team_text = normalize_team_text(resolved.read_text(encoding='utf-8'))
        digest = team_sha256(team_text)
        roster = extract_roster(team_text)
        team_id = path.stem.upper()
        if len(roster) != 6:
            raise RuntimeError(f'{team_id} contiene {len(roster)} sets; se esperaban seis.')
        if digest in hashes:
            raise RuntimeError(f'Holdout duplicado por contenido: {team_id}')
        if team_id in ids:
            raise RuntimeError(f'ID holdout duplicado: {team_id}')
        hashes.add(digest)
        ids.add(team_id)
        teams.append(TeamRecord(id=team_id, description=f'Holdout {team_id}', player='VGCPastes holdout', team_text=team_text, sha256=digest, roster=roster, origin='mc-holdout', path=resolved, metadata={'split': 'holdout', 'seed': manifest.get('seed')}))
    return TeamCorpus(metadata={'schemaVersion': 1, 'id': f"mc-holdout-seed-{manifest.get('seed', DEFAULT_SEED)}", 'format': battle_format, 'regulation': 'M-C', 'selection': 'Holdout persistido; signatureLeakage=0', 'splitManifest': str(split_manifest_path.resolve())}, teams=teams, ignored=[])

def _model_team_results(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        side = item['benchmark']['vgcBenchSide']
        pairing = item['pairing']
        team_id = pairing['alphaTeamId'] if side == 'alpha' else pairing['betaTeamId']
        entry = result.setdefault(team_id, {'games': 0, 'wins': 0, 'losses': 0, 'ties': 0})
        entry['games'] += 1
        winner = item.get('winnerAgent')
        if winner == 'vgcBench':
            entry['wins'] += 1
        elif winner == 'baseline':
            entry['losses'] += 1
        else:
            entry['ties'] += 1
    for entry in result.values():
        games = entry['games']
        entry['scorePercent'] = round(100.0 * (entry['wins'] + 0.5 * entry['ties']) / games, 2) if games else None
    return result

def _schedule_matched_delta(public_items: Sequence[dict[str, Any]], light_items: Sequence[dict[str, Any]]) -> dict[str, int]:
    def keyed(items: Sequence[dict[str, Any]]) -> dict[tuple[str, int], str]:
        return {(str(item['benchmark']['baselineId']), int(item['benchmark']['scheduleIndex'])): str(item.get('winnerAgent') or 'tie') for item in items}
    old = keyed(public_items)
    new = keyed(light_items)
    if set(old) != set(new):
        raise RuntimeError('Las ejecuciones no cubren exactamente la misma agenda.')
    upgraded = regressed = unchanged = 0
    for key in old:
        before = old[key]
        after = new[key]
        if before != 'vgcBench' and after == 'vgcBench':
            upgraded += 1
        elif before == 'vgcBench' and after != 'vgcBench':
            regressed += 1
        else:
            unchanged += 1
    return {'upgradedScheduleCells': upgraded, 'regressedScheduleCells': regressed, 'unchangedScheduleCells': unchanged, 'netUpgrades': upgraded - regressed}

def compare_model_reports(public: dict[str, Any], light: dict[str, Any]) -> dict[str, Any]:
    controls: dict[str, Any] = {}
    for spec in BASELINE_SPECS:
        old = public['opponents'][spec.id]
        new = light['opponents'][spec.id]
        controls[spec.id] = {'label': spec.label, 'publicScorePercent': old['scorePercent'], 'lightScorePercent': new['scorePercent'], 'deltaPercentagePoints': round(new['scorePercent'] - old['scorePercent'], 2), 'publicRecord': {k: old[k] for k in ('wins', 'losses', 'ties')}, 'lightRecord': {k: new[k] for k in ('wins', 'losses', 'ties')}}
    overall_delta = round(light['overall']['scorePercent'] - public['overall']['scorePercent'], 2)
    primary_delta = controls[PRIMARY_BASELINE_ID]['deltaPercentagePoints']
    if primary_delta > 0 and overall_delta >= 0:
        verdict = 'pass'
    elif primary_delta > 0:
        verdict = 'mixed'
    else:
        verdict = 'fail'
    return {'primaryMetric': PRIMARY_BASELINE_ID, 'gate': 'PASS requiere mejorar contra SimpleHeuristics y no retroceder en el score global equiponderado de los tres controles.', 'verdict': verdict, 'publicOverallScorePercent': public['overall']['scorePercent'], 'lightOverallScorePercent': light['overall']['scorePercent'], 'overallDeltaPercentagePoints': overall_delta, 'controls': controls, 'scheduleMatched': _schedule_matched_delta(public['items'], light['items']), 'caveat': 'Las celdas usan la misma agenda de equipos/lados, pero Showdown conserva RNG de batalla independiente entre ejecuciones; no es un test pareado de RNG.'}

def _combine_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    opponents: dict[str, dict[str, Any]] = {}
    aliases: dict[str, int] = {}
    for chunk in chunks:
        result = chunk['result']
        items.extend(result['items'])
        opponents.update(result['opponents'])
        aliases.update(result['aliasesRemoved'])
    overall = summarize_vgc_bench_record(wins=sum((int(report['wins']) for report in opponents.values())), losses=sum((int(report['losses']) for report in opponents.values())), ties=sum((int(report['ties']) for report in opponents.values())))
    return {'items': items, 'opponents': opponents, 'overall': overall, 'aliasesRemoved': aliases, 'byTeam': _model_team_results(items)}

def _valid_chunk(payload: dict[str, Any], *, checkpoint_sha256: str, schedule_sha256: str, baseline_id: str, battles_per_baseline: int) -> bool:
    return payload.get('schemaVersion') == 1 and payload.get('state') == 'completed' and (payload.get('checkpointSha256') == checkpoint_sha256) and (payload.get('scheduleSha256') == schedule_sha256) and (payload.get('baselineId') == baseline_id) and (int(payload.get('battles', -1)) == battles_per_baseline)

async def run_model_suite(*, model_id: str, runtime: Any, checkpoint_sha256: str, port: int, battle_format: str, schedule: Sequence[BenchmarkBattlePlan], schedule_sha256: str, timeout: float, replay_root: Path, chunk_root: Path, seed: int, battles_per_baseline: int, resume: bool, global_progress: Progress, completed_offset: int, status_path: Path) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    original_specs = battle.BASELINE_SPECS
    try:
        for control_index, spec in enumerate(BASELINE_SPECS, start=1):
            chunk_path = chunk_root / model_id / f'{spec.id}.json'
            chunk: dict[str, Any] | None = None
            if resume and chunk_path.is_file():
                try:
                    candidate = json.loads(chunk_path.read_text(encoding='utf-8'))
                except Exception:
                    candidate = {}
                if _valid_chunk(candidate, checkpoint_sha256=checkpoint_sha256, schedule_sha256=schedule_sha256, baseline_id=spec.id, battles_per_baseline=battles_per_baseline):
                    chunk = candidate
                    print(f'♻️ {model_id} × {spec.label}: reutilizando {battles_per_baseline} batallas', flush=True)
            if chunk is None:
                atomic_json(status_path, {'generatedAt': utc_now(), 'state': 'running', 'model': model_id, 'control': spec.id, 'completedChunks': completed_offset + control_index - 1, 'totalChunks': len(BASELINE_SPECS) * 2})
                print(f'\n⚔️ {model_id} vs {spec.label} · {battles_per_baseline} combates', flush=True)
                battle.BASELINE_SPECS = (spec,)
                started = time.monotonic()
                result = await battle.run_baseline_benchmark(runtime=runtime, port=port, battle_format=battle_format, schedule=schedule, timeout=timeout, replay_dir=replay_root / model_id, seed=seed)
                chunk = {'schemaVersion': 1, 'generatedAt': utc_now(), 'state': 'completed', 'modelId': model_id, 'checkpointSha256': checkpoint_sha256, 'scheduleSha256': schedule_sha256, 'baselineId': spec.id, 'battles': battles_per_baseline, 'seconds': round(time.monotonic() - started, 3), 'result': result}
                atomic_json(chunk_path, chunk)
            chunks.append(chunk)
            global_progress.update(completed_offset + control_index, f'{model_id} · {spec.label}', force=True)
    finally:
        battle.BASELINE_SPECS = original_specs
    return _combine_chunks(chunks)

def parse_args(argv: Sequence[str] | None=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--holdout-dir', type=Path, required=True)
    parser.add_argument('--split-manifest', type=Path, required=True)
    parser.add_argument('--baseline-checkpoint', type=Path, required=True)
    parser.add_argument('--candidate-checkpoint', type=Path, required=True)
    parser.add_argument('--baseline-sha256', default=battle.VGC_BENCH_CHECKPOINT_SHA256)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--format', default=battle.DEFAULT_FORMAT)
    parser.add_argument('--benchmark-battles-per-baseline', type=int, default=DEFAULT_BATTLES_PER_BASELINE)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--battle-timeout', type=float, default=300.0)
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument('--resume', action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(argv)

def main(argv: Sequence[str] | None=None) -> int:
    args = parse_args(argv)
    if args.benchmark_battles_per_baseline < 2 or args.benchmark_battles_per_baseline % 2:
        raise SystemExit('--benchmark-battles-per-baseline debe ser un número par >= 2.')
    if len(args.candidate_sha256) != 64 or len(args.baseline_sha256) != 64:
        raise SystemExit('Los SHA-256 deben tener 64 caracteres.')
    runtime_root = args.runtime_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / 'status.json'
    latest_result = output_dir / 'latest_result.json'
    chunks_root = output_dir / 'chunks'
    replay_root = output_dir / 'replays'
    replay_root.mkdir(parents=True, exist_ok=True)
    try:
        print('🐉 Benchmark holdout final Battle Lab M-C', flush=True)
        print('Fase 1/5 · verificando split y checkpoints...', flush=True)
        corpus = load_holdout_corpus(args.holdout_dir, args.split_manifest, expected_count=DEFAULT_HOLDOUT_COUNT, battle_format=args.format)
        public_sha = _safe_checkpoint(args.baseline_checkpoint.resolve(), args.baseline_sha256, 'checkpoint BC público')
        light_sha = _safe_checkpoint(args.candidate_checkpoint.resolve(), args.candidate_sha256, 'checkpoint LIGHT')
        print(f'✅ Holdout: {len(corpus.teams)} equipos · fuga=0 · BC={public_sha[:12]} · LIGHT={light_sha[:12]}', flush=True)
        print('Fase 2/5 · preparando Showdown y VGC-Bench fijados...', flush=True)
        showdown_root = runtime_root / 'pokemon-showdown'
        vgc_root = runtime_root / 'vgc-bench'
        logs_root = output_dir / 'logs'
        showdown_commit = read_showdown_commit()
        ensure_showdown_checkout(checkout=showdown_root, repository=DEFAULT_SHOWDOWN_REPOSITORY, commit=showdown_commit, logs_dir=logs_root)
        install_runtime_config(showdown_root)
        battle.ensure_vgc_bench_checkout(checkout=vgc_root, repository=battle.VGC_BENCH_REPOSITORY, commit=battle.VGC_BENCH_COMMIT)
        aliases = alias_mc_runtime_catalogs(vgc_root)
        print(f"✅ Alias M-C compatible: {aliases['abilities']}", flush=True)
        print('Fase 3/5 · validando los 36 equipos y congelando agenda...', flush=True)
        validation: dict[str, str] = {}
        for index, team in enumerate(corpus.teams, start=1):
            validation[team.id] = validate_team(showdown_root, args.format, team.team_text)
            if index % 6 == 0 or index == len(corpus.teams):
                print(f'  equipos validados: {index}/{len(corpus.teams)}', flush=True)
        base_pairings = build_pairing_schedule(corpus.teams, count=args.benchmark_battles_per_baseline // 2, seed=args.seed)
        schedule = build_mirrored_benchmark_schedule(base_pairings, count=args.benchmark_battles_per_baseline)
        schedule_meta = benchmark_schedule_statistics(schedule)
        schedule_meta.update({'holdoutOnly': True, 'holdoutTeams': len(corpus.teams), 'sameScheduleForBothCheckpoints': True, 'sameScheduleForEveryControl': True, 'models': 2, 'controls': len(BASELINE_SPECS), 'totalBattles': len(schedule) * len(BASELINE_SPECS) * 2})
        print(f"✅ Agenda: {schedule_meta['sha256'][:16]} · {schedule_meta['totalBattles']} batallas totales", flush=True)
        total_chunks = len(BASELINE_SPECS) * 2
        chunk_progress = Progress(total_chunks, 'Benchmark holdout')
        started = time.monotonic()
        with running_showdown(showdown_root, args.port, logs_root / 'holdout-showdown.log'):
            print('Fase 4/5 · BC público en holdout...', flush=True)
            public_runtime = battle.load_model_runtime(checkout=vgc_root, checkpoint=args.baseline_checkpoint.resolve(), requested_device=args.device, seed=args.seed)
            public = asyncio.run(run_model_suite(model_id=PUBLIC_MODEL_ID, runtime=public_runtime, checkpoint_sha256=public_sha, port=args.port, battle_format=args.format, schedule=schedule, schedule_sha256=schedule_meta['sha256'], timeout=args.battle_timeout, replay_root=replay_root, chunk_root=chunks_root, seed=args.seed, battles_per_baseline=args.benchmark_battles_per_baseline, resume=args.resume, global_progress=chunk_progress, completed_offset=0, status_path=status_path))
            public_metadata = public_runtime.metadata
            del public_runtime
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            print('Fase 5/5 · LIGHT nuevo en la misma agenda...', flush=True)
            light_runtime = battle.load_model_runtime(checkout=vgc_root, checkpoint=args.candidate_checkpoint.resolve(), requested_device=args.device, seed=args.seed)
            light = asyncio.run(run_model_suite(model_id=LIGHT_MODEL_ID, runtime=light_runtime, checkpoint_sha256=light_sha, port=args.port, battle_format=args.format, schedule=schedule, schedule_sha256=schedule_meta['sha256'], timeout=args.battle_timeout, replay_root=replay_root, chunk_root=chunks_root, seed=args.seed, battles_per_baseline=args.benchmark_battles_per_baseline, resume=args.resume, global_progress=chunk_progress, completed_offset=len(BASELINE_SPECS), status_path=status_path))
            light_metadata = light_runtime.metadata
        comparison = compare_model_reports(public, light)
        elapsed = time.monotonic() - started
        replay_archive = Path(shutil.make_archive(str(output_dir / 'holdout-replays'), 'zip', root_dir=replay_root))
        payload = {'schemaVersion': 1, 'generatedAt': utc_now(), 'state': 'completed', 'protocol': {'name': 'Battle Lab M-C holdout benchmark', 'seed': args.seed, 'format': args.format, 'holdoutTeams': len(corpus.teams), 'battlesPerControlPerModel': args.benchmark_battles_per_baseline, 'controls': [spec.result_metadata() for spec in BASELINE_SPECS], 'totalBattles': schedule_meta['totalBattles'], 'schedule': schedule_meta, 'catalogAliases': aliases}, 'models': {PUBLIC_MODEL_ID: {'checkpoint': str(args.baseline_checkpoint.resolve()), 'checkpointSha256': public_sha, 'runtime': public_metadata, 'results': public}, LIGHT_MODEL_ID: {'checkpoint': str(args.candidate_checkpoint.resolve()), 'checkpointSha256': light_sha, 'runtime': light_metadata, 'results': light}}, 'comparison': comparison, 'artifacts': {'replaysZip': str(replay_archive)}, 'seconds': round(elapsed, 3)}
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        result_path = output_dir / f'holdout-benchmark-{timestamp}.json'
        atomic_json(result_path, payload)
        atomic_json(latest_result, payload)
        atomic_json(status_path, {'generatedAt': utc_now(), 'state': 'completed', 'verdict': comparison['verdict'], 'result': str(result_path), 'seconds': round(elapsed, 3)})
        print('\n===== HOLDOUT FINAL =====', flush=True)
        print(f"BC público: {public['overall']['scorePercent']:.2f}% · LIGHT: {light['overall']['scorePercent']:.2f}% · Δ {comparison['overallDeltaPercentagePoints']:+.2f} pp", flush=True)
        simple = comparison['controls'][PRIMARY_BASELINE_ID]
        print(f"SimpleHeuristics: {simple['publicScorePercent']:.2f}% → {simple['lightScorePercent']:.2f}% ({simple['deltaPercentagePoints']:+.2f} pp)", flush=True)
        print(f"Veredicto: {comparison['verdict'].upper()}", flush=True)
        print(f'Resultado: {result_path}', flush=True)
        print(f'Replays: {replay_archive}', flush=True)
        print(f'Tiempo: {human_seconds(elapsed)}', flush=True)
        return 0
    except Exception as error:
        tb = traceback.format_exc()
        atomic_json(status_path, {'generatedAt': utc_now(), 'state': 'failed', 'error': f'{type(error).__name__}: {error}', 'traceback': tb[-16000:]})
        print(tb, file=sys.stderr, flush=True)
        raise
if __name__ == '__main__':
    raise SystemExit(main())
