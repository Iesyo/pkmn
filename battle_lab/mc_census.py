#!/usr/bin/env python3
"""Audited Champions M-C replay census for Battle Lab.

This module keeps the first BATTLE-LAB-MC-TRAIN-001 delivery separate from the
training loop. It uses VGC-Bench's own replay search helpers and exactly the
same structural OTS filters, while recording why candidate replays are rejected
and summarizing Elo, winners, species, team signatures, recurring cores and
storage. Accepted logs are written in the same battle_logs JSON format expected
by VGC-Bench's logs2trajs.py.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterator, Sequence

from battle_lab.mc_training import (
    DEFAULT_FORMAT,
    DEFAULT_FORMAT_BO3,
    atomic_json,
    human_seconds,
    inject_mc_support,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Progress:
    total: int
    label: str
    width: int = 28

    def __post_init__(self) -> None:
        self.started = time.monotonic()
        self.last_print = 0.0

    def update(self, completed: int, detail: str = "", *, force: bool = False) -> None:
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


def audit_replay(
    log_json: dict[str, Any] | None,
    scraper: Any,
) -> tuple[bool, str, str | None, int | None]:
    """Apply VGC-Bench's filters while preserving one deterministic reject reason."""

    if log_json is None:
        return False, "download_failed", None, None
    log = str(log_json.get("log") or "")
    uploadtime = log_json.get("uploadtime")
    if not log:
        return False, "empty_log", None, uploadtime
    if log.count("|poke|p1|") != 6:
        return False, "p1_not_six", log, uploadtime
    if log.count("|poke|p2|") != 6:
        return False, "p2_not_six", log, uploadtime
    if "|turn|1" not in log:
        return False, "missing_turn_1", log, uploadtime
    header = log.split("\n|\n")[0]
    if "|showteam|" not in header:
        return False, "missing_open_team_sheet", log, uploadtime
    try:
        if not scraper.can_distinguish_team_members(header, "p1"):
            return False, "indistinguishable_p1", log, uploadtime
        if not scraper.can_distinguish_team_members(header, "p2"):
            return False, "indistinguishable_p2", log, uploadtime
    except Exception:
        return False, "team_parse_error", log, uploadtime
    if "Zoroark" in log or "Zorua" in log:
        return False, "zoroark_family", log, uploadtime
    return True, "accepted", log, uploadtime


def scrape_mc_logs(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    max_logs_per_format: int,
    num_workers: int = 16,
    read_increment: int = 5_000,
    include_bo3: bool = True,
    max_passes: int = 50,
) -> dict[str, Any]:
    """Collect M-C replays, accepted logs, reject causes and visible progress/ETA."""

    inject_mc_support(vgc_bench_checkout)
    import importlib

    scraper = importlib.import_module("vgc_bench.scrape_logs")
    formats = [DEFAULT_FORMAT] + ([DEFAULT_FORMAT_BO3] if include_bo3 else [])
    logs_dir = data_root / "battle_logs"
    audit_dir = data_root / "scrape_audit"
    logs_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"generatedAt": utc_now(), "formats": {}}
    progress = Progress(len(formats), "Replays humanos M-C")

    for format_index, battle_format in enumerate(formats, start=1):
        started = time.monotonic()
        logs_path = logs_dir / f"logs_{battle_format}.json"
        audit_path = audit_dir / f"audit_{battle_format}.json"
        valid_logs = json.loads(logs_path.read_text(encoding="utf-8")) if logs_path.exists() else {}
        audit_state = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
        seen: dict[str, dict[str, Any]] = audit_state.get("seen", {})
        passes = 0
        no_growth_passes = 0
        search_error: str | None = None

        while len(valid_logs) < max_logs_per_format and passes < max_passes:
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
                print(
                    f"⚠️ {battle_format}: Showdown no devolvió una página utilizable: {error}",
                    flush=True,
                )
                break

            candidate_ids = sorted(identifier for identifier in candidate_ids if identifier not in seen)
            if not candidate_ids:
                break

            before_valid = len(valid_logs)
            with ThreadPoolExecutor(max_workers=max(1, num_workers)) as executor:
                payloads = list(executor.map(scraper.get_log_json, candidate_ids))

            for identifier, payload in zip(candidate_ids, payloads):
                accepted, reason, log, uploadtime = audit_replay(payload, scraper)
                seen[identifier] = {
                    "uploadtime": uploadtime,
                    "accepted": accepted,
                    "reason": reason,
                }
                if accepted and payload is not None and log is not None:
                    valid_logs[identifier] = (payload["uploadtime"], log)
                    if len(valid_logs) >= max_logs_per_format:
                        break

            no_growth_passes = no_growth_passes + 1 if len(valid_logs) == before_valid else 0
            rejects = Counter(
                meta.get("reason", "unknown")
                for meta in seen.values()
                if not meta.get("accepted")
            )
            print(
                f"{battle_format}: pasada {passes} · candidatos {len(seen):,} · "
                f"aceptados {len(valid_logs):,}/{max_logs_per_format:,} · "
                f"descartados {sum(rejects.values()):,}",
                flush=True,
            )
            atomic_json(
                audit_path,
                {
                    "generatedAt": utc_now(),
                    "battleFormat": battle_format,
                    "seen": seen,
                    "passes": passes,
                    "searchError": search_error,
                },
            )
            atomic_json(logs_path, valid_logs)
            if no_growth_passes >= 2:
                break

        rejects = Counter(
            meta.get("reason", "unknown")
            for meta in seen.values()
            if not meta.get("accepted")
        )
        format_summary = {
            "candidateReplays": len(seen),
            "acceptedReplays": len(valid_logs),
            "acceptanceRate": round(len(valid_logs) / len(seen), 4) if seen else 0.0,
            "discardByCause": dict(rejects.most_common()),
            "passes": passes,
            "seconds": round(time.monotonic() - started, 3),
            "searchError": search_error,
        }
        summary["formats"][battle_format] = format_summary
        progress.update(
            format_index,
            f"{battle_format}: {len(valid_logs):,} válidos / {len(seen):,} candidatos",
            force=True,
        )

    summary["totalCandidates"] = sum(item["candidateReplays"] for item in summary["formats"].values())
    summary["totalLogs"] = sum(item["acceptedReplays"] for item in summary["formats"].values())
    aggregate_rejects: Counter[str] = Counter()
    for item in summary["formats"].values():
        aggregate_rejects.update(item["discardByCause"])
    summary["discardByCause"] = dict(aggregate_rejects.most_common())
    atomic_json(data_root / "logs_manifest.json", summary)
    return summary


def normalize_species(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def preview_species(log: str, role: str) -> tuple[str, ...]:
    species: list[str] = []
    marker = f"|poke|{role}|"
    for line in log.splitlines():
        if not line.startswith(marker):
            continue
        details = line[len(marker) :]
        name = details.split(",", 1)[0].strip()
        normalized = normalize_species(name)
        if normalized:
            species.append(normalized)
    return tuple(sorted(species))


def source_team_signatures(team_dir: Path) -> tuple[set[tuple[str, ...]], Counter[str]]:
    from poke_env.teambuilder import Teambuilder

    signatures: set[tuple[str, ...]] = set()
    species_counter: Counter[str] = Counter()
    for path in sorted(team_dir.glob("mc*.txt")):
        try:
            builders = Teambuilder.parse_showdown_team(path.read_text(encoding="utf-8"))
            names: list[str] = []
            for pokemon in builders:
                name = str(pokemon.species or pokemon.nickname or "")
                normalized = normalize_species(name)
                if normalized:
                    names.append(normalized)
                    species_counter[normalized] += 1
            if len(names) == 6:
                signatures.add(tuple(sorted(names)))
        except Exception:
            continue
    return signatures, species_counter


def rating_histogram(values: Sequence[int]) -> dict[str, int]:
    buckets = [
        (0, 999, "<1000"),
        (1000, 1199, "1000-1199"),
        (1200, 1399, "1200-1399"),
        (1400, 1599, "1400-1599"),
        (1600, 1799, "1600-1799"),
        (1800, 1999, "1800-1999"),
        (2000, 9999, "2000+"),
    ]
    result = {label: 0 for _, _, label in buckets}
    for value in values:
        for low, high, label in buckets:
            if low <= value <= high:
                result[label] += 1
                break
    return result


def winner_role(log: str) -> str:
    players: dict[str, str] = {}
    winner = None
    tied = False
    for line in log.splitlines():
        if line.startswith("|player|p1|") or line.startswith("|player|p2|"):
            parts = line.split("|")
            if len(parts) >= 4:
                players[parts[2]] = parts[3]
        elif line.startswith("|win|"):
            winner = line.split("|", 2)[2]
        elif line == "|tie|":
            tied = True
    if tied:
        return "tie"
    for role, name in players.items():
        if winner == name:
            return role
    return "unknown"


def build_log_census(
    *,
    vgc_bench_checkout: Path,
    data_root: Path,
    team_dir: Path,
) -> dict[str, Any]:
    inject_mc_support(vgc_bench_checkout)
    import importlib

    scraper = importlib.import_module("vgc_bench.scrape_logs")
    source_signatures, source_species_counter = source_team_signatures(team_dir)
    observed_signatures: Counter[tuple[str, ...]] = Counter()
    species_counter: Counter[str] = Counter()
    cores2: Counter[tuple[str, ...]] = Counter()
    cores3: Counter[tuple[str, ...]] = Counter()
    ratings: list[int] = []
    winner_ratings: list[int] = []
    winners: Counter[str] = Counter()
    total_log_bytes = 0
    accepted_battles = 0

    for path in sorted((data_root / "battle_logs").glob("logs_*.json")):
        logs = json.loads(path.read_text(encoding="utf-8"))
        for value in logs.values():
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            log = str(value[1])
            accepted_battles += 1
            total_log_bytes += len(log.encode("utf-8"))
            role_ratings: dict[str, int] = {}
            for role in ("p1", "p2"):
                try:
                    rating = scraper.get_rating(log, role)
                except (ValueError, IndexError):
                    rating = None
                if rating is not None:
                    rating = int(rating)
                    ratings.append(rating)
                    role_ratings[role] = rating
                signature = preview_species(log, role)
                if len(signature) != 6:
                    continue
                observed_signatures[signature] += 1
                species_counter.update(signature)
                cores2.update(combinations(signature, 2))
                cores3.update(combinations(signature, 3))
            winner = winner_role(log)
            winners[winner] += 1
            if winner in role_ratings:
                winner_ratings.append(role_ratings[winner])

    covered_source_species = set(source_species_counter) & set(species_counter)
    matched_source_teams = set(observed_signatures) & source_signatures
    traj_files = sorted((data_root / "trajs").glob("*.pkl"))
    traj_bytes = sum(path.stat().st_size for path in traj_files)
    data_bytes = sum(path.stat().st_size for path in data_root.rglob("*") if path.is_file())
    team_bytes = sum(path.stat().st_size for path in team_dir.rglob("*") if path.is_file())
    mean_log_bytes = total_log_bytes / accepted_battles if accepted_battles else 0
    mean_traj_bytes = traj_bytes / len(traj_files) if traj_files else 0
    ratings_sorted = sorted(ratings)
    winner_ratings_sorted = sorted(winner_ratings)

    return {
        "ratings": {
            "samples": len(ratings),
            "min": min(ratings) if ratings else None,
            "median": ratings_sorted[len(ratings_sorted) // 2] if ratings else None,
            "max": max(ratings) if ratings else None,
            "histogram": rating_histogram(ratings),
            "winnerSamples": len(winner_ratings),
            "winnerMedian": winner_ratings_sorted[len(winner_ratings_sorted) // 2]
            if winner_ratings
            else None,
        },
        "winners": dict(winners),
        "speciesCoverage": {
            "observedUnique": len(species_counter),
            "sourceUnique": len(source_species_counter),
            "sourceCovered": len(covered_source_species),
            "sourceCoverageRate": round(len(covered_source_species) / len(source_species_counter), 4)
            if source_species_counter
            else 0.0,
            "topObserved": [
                {"species": species, "appearances": count}
                for species, count in species_counter.most_common(30)
            ],
        },
        "teamCoverage": {
            "observedUniqueSignatures": len(observed_signatures),
            "sourceSignatures": len(source_signatures),
            "matchedSourceSignatures": len(matched_source_teams),
            "sourceCoverageRate": round(len(matched_source_teams) / len(source_signatures), 4)
            if source_signatures
            else 0.0,
            "topObserved": [
                {"species": list(signature), "appearances": count}
                for signature, count in observed_signatures.most_common(20)
            ],
        },
        "archetypeCoreCoverage": {
            "definition": (
                "Proxy reproducible: núcleos de 2 y 3 especies que coaparecen en Team Preview; "
                "no etiqueta estilos semánticos a mano."
            ),
            "uniqueCores2": len(cores2),
            "uniqueCores3": len(cores3),
            "topCores2": [
                {"species": list(core), "appearances": count}
                for core, count in cores2.most_common(20)
            ],
            "topCores3": [
                {"species": list(core), "appearances": count}
                for core, count in cores3.most_common(20)
            ],
        },
        "storage": {
            "dataBytes": data_bytes,
            "teamSnapshotBytes": team_bytes,
            "meanAcceptedLogBytes": round(mean_log_bytes, 1),
            "meanTrajectoryBytes": round(mean_traj_bytes, 1),
            "projected20kAcceptedLogsBytes": round(mean_log_bytes * 20_000),
        },
    }


def build_census_report(
    *,
    vgc_bench_checkout: Path,
    team_dir: Path,
    data_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    manifest_path = team_dir / "manifest.json"
    team_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    logs_manifest_path = data_root / "logs_manifest.json"
    logs_manifest = json.loads(logs_manifest_path.read_text(encoding="utf-8")) if logs_manifest_path.exists() else {}
    traj_manifest_path = data_root / "trajs_manifest.json"
    traj_manifest = json.loads(traj_manifest_path.read_text(encoding="utf-8")) if traj_manifest_path.exists() else {}
    audit = build_log_census(
        vgc_bench_checkout=vgc_bench_checkout,
        data_root=data_root,
        team_dir=team_dir,
    )
    summary = {
        "generatedAt": utc_now(),
        "teams": {
            "usable": team_manifest.get("usableTeams", len(list(team_dir.glob("mc*.txt")))),
            "sourceRows": team_manifest.get("declaredRows"),
            "source": "VGCPastes Repository / Champions M-C",
        },
        "candidateReplays": logs_manifest.get("totalCandidates", 0),
        "humanLogs": logs_manifest.get("totalLogs", 0),
        "discardByCause": logs_manifest.get("discardByCause", {}),
        "formats": logs_manifest.get("formats", {}),
        "trajectories": traj_manifest.get("trajectories", 0),
        "transitions": traj_manifest.get("transitions", 0),
        "officialPublicMCDataset": False,
        "strategy": (
            "BC real M-C if enough OTS logs, then PPO self-play on full VGCPastes M-C team corpus; "
            "otherwise PPO self-play starts from the official M-A/M-B BC baseline."
        ),
        **audit,
    }
    atomic_json(output_root / "census.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battle Lab audited Champions M-C census")
    parser.add_argument("--vgc-bench", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--team-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape")
    scrape.add_argument("--max-logs-per-format", type=int, default=5_000)
    scrape.add_argument("--num-workers", type=int, default=16)
    scrape.add_argument("--read-increment", type=int, default=5_000)
    scrape.add_argument("--no-bo3", action="store_true")

    sub.add_parser("report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scrape":
        result = scrape_mc_logs(
            vgc_bench_checkout=args.vgc_bench,
            data_root=args.data_root,
            max_logs_per_format=args.max_logs_per_format,
            num_workers=args.num_workers,
            read_increment=args.read_increment,
            include_bo3=not args.no_bo3,
        )
    else:
        result = build_census_report(
            vgc_bench_checkout=args.vgc_bench,
            team_dir=args.team_dir,
            data_root=args.data_root,
            output_root=args.output_root,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
