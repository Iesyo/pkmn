#!/usr/bin/env python3
"""Incrementally extend Battle Lab's Champions M-C BO3 replay census.

This companion command exists for the post-CENSUS gate check. It reuses the
persisted audit/log state from ``mc_census.py`` but touches only the BO3 ladder,
so increasing the human corpus does not waste time rescanning the low-OTS
regular M-C ladder.
"""

from __future__ import annotations

import argparse
import importlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Sequence

from battle_lab.mc_census import Progress, audit_replay, utc_now
from battle_lab.mc_training import DEFAULT_FORMAT_BO3, atomic_json, inject_mc_support


def _safe_log_json(scraper: Any, identifier: str) -> dict[str, Any] | None:
    try:
        return scraper.get_log_json(identifier)
    except Exception:
        return None


def extend_bo3_logs(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    max_logs: int = 10_000,
    num_workers: int = 16,
    read_increment: int = 5_000,
    max_passes: int = 50,
) -> dict[str, Any]:
    """Extend only the M-C BO3 accepted-log pool while preserving normal M-C stats."""

    if max_logs <= 0:
        raise ValueError("max_logs debe ser > 0")
    inject_mc_support(vgc_bench_checkout)
    scraper = importlib.import_module("vgc_bench.scrape_logs")

    logs_dir = data_root / "battle_logs"
    audit_dir = data_root / "scrape_audit"
    logs_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    battle_format = DEFAULT_FORMAT_BO3
    logs_path = logs_dir / f"logs_{battle_format}.json"
    audit_path = audit_dir / f"audit_{battle_format}.json"
    manifest_path = data_root / "logs_manifest.json"

    valid_logs = json.loads(logs_path.read_text(encoding="utf-8")) if logs_path.exists() else {}
    audit_state = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    seen: dict[str, dict[str, Any]] = audit_state.get("seen", {})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.setdefault("formats", {})

    starting_logs = len(valid_logs)
    if starting_logs >= max_logs:
        print(
            f"✅ {battle_format}: ya hay {starting_logs:,} logs; objetivo {max_logs:,} satisfecho.",
            flush=True,
        )

    passes = 0
    no_growth_passes = 0
    search_error: str | None = None
    started = time.monotonic()
    progress = Progress(max(max_logs - starting_logs, 1), "Extensión BO3 M-C")

    while len(valid_logs) < max_logs and passes < max_passes:
        passes += 1
        timestamps = [
            int(meta["uploadtime"])
            for meta in seen.values()
            if isinstance(meta, dict) and meta.get("uploadtime") is not None
        ]
        if not timestamps:
            timestamps = [int(value[0]) for value in valid_logs.values()]
        oldest = min(timestamps) if timestamps else 2_000_000_000
        newest = max(timestamps) if timestamps else None

        try:
            candidate_ids = scraper.get_battle_idents(
                read_increment,
                battle_format,
                oldest,
                newest,
            )
        except Exception as error:
            search_error = f"{type(error).__name__}: {error}"
            print(f"⚠️ {battle_format}: búsqueda detenida: {search_error}", flush=True)
            break

        candidate_ids = sorted(identifier for identifier in candidate_ids if identifier not in seen)
        if not candidate_ids:
            print(f"ℹ️ {battle_format}: no hay más candidatos inéditos.", flush=True)
            break

        before_valid = len(valid_logs)
        with ThreadPoolExecutor(max_workers=max(1, num_workers)) as executor:
            payloads = list(
                executor.map(lambda ident: _safe_log_json(scraper, ident), candidate_ids)
            )

        for identifier, payload in zip(candidate_ids, payloads):
            accepted, reason, log, uploadtime = audit_replay(payload, scraper)
            seen[identifier] = {
                "uploadtime": uploadtime,
                "accepted": accepted,
                "reason": reason,
            }
            if accepted and payload is not None and log is not None:
                valid_logs[identifier] = (payload["uploadtime"], log)
                if len(valid_logs) >= max_logs:
                    break

        no_growth_passes = no_growth_passes + 1 if len(valid_logs) == before_valid else 0
        added = len(valid_logs) - starting_logs
        progress.update(
            min(added, max_logs - starting_logs),
            f"{len(valid_logs):,}/{max_logs:,} válidos · {len(seen):,} candidatos",
            force=True,
        )

        atomic_json(
            audit_path,
            {
                "generatedAt": utc_now(),
                "battleFormat": battle_format,
                "seen": seen,
                "passes": int(audit_state.get("passes", 0)) + passes,
                "searchError": search_error,
            },
        )
        atomic_json(logs_path, valid_logs)
        if no_growth_passes >= 2:
            print("ℹ️ Dos pasadas sin nuevos logs válidos; se detiene la extensión.", flush=True)
            break

    rejects = Counter(
        meta.get("reason", "unknown")
        for meta in seen.values()
        if isinstance(meta, dict) and not meta.get("accepted")
    )
    format_summary = {
        "candidateReplays": len(seen),
        "acceptedReplays": len(valid_logs),
        "acceptanceRate": round(len(valid_logs) / len(seen), 4) if seen else 0.0,
        "discardByCause": dict(rejects.most_common()),
        "passes": int(audit_state.get("passes", 0)) + passes,
        "seconds": round(time.monotonic() - started, 3),
        "searchError": search_error,
    }
    manifest["generatedAt"] = utc_now()
    manifest["formats"][battle_format] = format_summary
    manifest["totalCandidates"] = sum(
        int(item.get("candidateReplays", 0)) for item in manifest["formats"].values()
    )
    manifest["totalLogs"] = sum(
        int(item.get("acceptedReplays", 0)) for item in manifest["formats"].values()
    )
    aggregate_rejects: Counter[str] = Counter()
    for item in manifest["formats"].values():
        aggregate_rejects.update(item.get("discardByCause", {}))
    manifest["discardByCause"] = dict(aggregate_rejects.most_common())
    manifest["extension"] = {
        "battleFormat": battle_format,
        "startingAcceptedLogs": starting_logs,
        "targetAcceptedLogs": max_logs,
        "endingAcceptedLogs": len(valid_logs),
        "addedAcceptedLogs": len(valid_logs) - starting_logs,
        "sourceExhausted": len(valid_logs) < max_logs and search_error is None,
    }
    atomic_json(manifest_path, manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extend only the Battle Lab M-C BO3 corpus")
    parser.add_argument("--vgc-bench", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--max-logs", type=int, default=10_000)
    parser.add_argument("--num-workers", type=int, default=16)
    parser.add_argument("--read-increment", type=int, default=5_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = extend_bo3_logs(
        vgc_bench_checkout=args.vgc_bench,
        data_root=args.data_root,
        max_logs=args.max_logs,
        num_workers=args.num_workers,
        read_increment=args.read_increment,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
