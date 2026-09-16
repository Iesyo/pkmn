"""Incremental M-C data and persistent train/holdout assignments.

Only the coordinator writes durable data. Network workers return values; the
historical LIGHT split is imported once and no known signature changes sides.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import re
import shutil
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from battle_lab.mc_census import audit_replay, preview_species, winner_role
from battle_lab.mc_team_split import team_signature
from battle_lab.mc_training import (
    DEFAULT_FORMAT, DEFAULT_FORMAT_BO3, VGCPASTES_CSV_URL, Progress,
    atomic_json, fetch_text, parse_vgcpastes_mc, pokepaste_raw_url,
    sha256_file, sha256_text, utc_now,
)


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def resolve_workers(requested: int = 0) -> dict[str, Any]:
    """40_017 auto90 pattern, capped for public HTTP and parser memory."""
    cpu = os.cpu_count() or 1
    try:
        import psutil
        memory = psutil.virtual_memory().available / 1024**3
    except ImportError:
        memory = 2.0
    safe = max(1, min(cpu, int(memory / 2), 8))
    workers = max(1, min(requested or max(1, math.floor(safe * .9)), safe))
    return {"workers": workers, "cpuCount": cpu, "availableMemoryGiB": round(memory, 2),
            "safeCapacity": safe, "utilizationTarget": .9}


def complete_team(text: str) -> bool:
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return len(blocks) == 6 and all(
        len(re.findall(r"(?m)^\s*-\s+\S", block)) == 4
        and re.search(r"(?m)^Ability:\s*\S", block)
        and re.search(r"(?m)^\S.* Nature\s*$", block)
        and re.search(r"(?m)^(?:EVs|Stat Points|SPs):\s*\S", block)
        for block in blocks
    )


def sync_teams(*, output: Path, cache: Path, validate: Callable[[str], Any],
               workers: int, extra_dir: Path | None = None, minimum: int = 150) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    csv_text = fetch_text(VGCPASTES_CSV_URL, timeout=90)
    entries = parse_vgcpastes_mc(csv_text)
    for stale in output.glob("mc*.txt"):
        stale.unlink()
    (output / "source.csv").write_text(csv_text, encoding="utf-8")
    progress = Progress(len(entries), "Pastes actuales")

    def download(entry: dict) -> tuple[dict, str | None, str | None]:
        try:
            url = pokepaste_raw_url(entry["Pokepaste"])
            cached = cache / (sha256_text(url) + ".txt")
            text = cached.read_text(encoding="utf-8") if cached.exists() else fetch_text(url, timeout=30)
            return entry, text.strip().replace("\r\n", "\n") + "\n", None
        except Exception as error:
            return entry, None, str(error)

    records, errors, hashes = [], [], set()
    def accept(team_id: str, text: str, meta: dict) -> None:
        digest = sha256_text(text)
        if digest in hashes:
            raise ValueError("duplicate_content")
        if not complete_team(text):
            raise ValueError("incomplete_paste: require six complete published sets")
        validate(text)
        name = team_id.lower() + ".txt"
        path = output / name
        path.write_text(text, encoding="utf-8")
        signature = team_signature(path)
        hashes.add(digest)
        records.append({"teamId": team_id, "file": name, "sha256": digest,
                        "signature": list(signature), **meta})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (entry, text, error) in enumerate(pool.map(download, entries), 1):
            try:
                if error or text is None:
                    raise ValueError(error)
                accept(entry["Team ID"], text, {"source": "VGCPastes", "url": entry["Pokepaste"],
                       "dateShared": entry.get("Date Shared"), "event": entry.get("Tournament / Event")})
                (cache / (sha256_text(pokepaste_raw_url(entry["Pokepaste"])) + ".txt")).write_text(text, encoding="utf-8")
            except Exception as error:
                errors.append({"teamId": entry["Team ID"], "reason": str(error)})
            progress.update(i, f"{len(records)} válidos", force=i == len(entries))
    if extra_dir and extra_dir.is_dir():
        for file in sorted(extra_dir.glob("*.txt")):
            try:
                text = file.read_text(encoding="utf-8").strip().replace("\r\n", "\n") + "\n"
                team_id = "MC" + str(10**15 + int(sha256_text(text)[:12], 16))
                accept(team_id, text, {"source": "extra-paste", "sourceFile": file.name})
            except Exception as error:
                errors.append({"teamId": file.name, "reason": str(error)})
    if len(records) < minimum:
        raise RuntimeError(f"Solo {len(records)} pastes completos y legales; mínimo {minimum}. No se entrena.")
    manifest = {"generatedAt": utc_now(), "format": DEFAULT_FORMAT,
                "sourceSha256": sha256_text(csv_text), "usableTeams": len(records),
                "teams": records, "errors": errors}
    atomic_json(output / "manifest.json", manifest)
    return manifest


def signature_key(signature: tuple[str, ...] | list[str]) -> str:
    if len(signature) != 6:
        raise ValueError("A partition signature must contain six Pokémon")
    return "|".join(sorted(signature))


def assign_signature(state: dict, signature: tuple[str, ...] | list[str],
                     forced: str | None = None) -> str:
    key = signature_key(signature)
    old = state["assignments"].get(key)
    if old:
        if forced and old != forced:
            raise RuntimeError("Historical train/holdout overlap: " + key)
        return old
    value = int(sha256_text(f"{state['seed']}|{key}")[:16], 16) / 2**64
    partition = forced or ("holdout" if value < .20 else "train")
    state["assignments"][key] = partition
    return partition


def load_partitions(registry: Path, legacy_split: Path, seed: int = 260913) -> dict:
    if registry.is_file():
        state = read_json(registry)
        if state.get("seed") != seed or state.get("format") != DEFAULT_FORMAT:
            raise RuntimeError("Partition seed/format changed; use a separate regulation lineage.")
        for old in state["legacyFiles"]:
            if sha256_file(legacy_split / old["side"] / old["file"]) != old["sha256"]:
                raise RuntimeError("El split histórico LIGHT fue modificado.")
        return state
    manifest = read_json(legacy_split / "split_manifest.json")
    if not manifest or manifest.get("signatureLeakage") != 0:
        raise RuntimeError("Falta el split histórico LIGHT verificable; no se puede reconstruir al azar.")
    state = {"schemaVersion": 1, "format": DEFAULT_FORMAT, "seed": seed,
             "assignments": {}, "legacySplit": str(legacy_split), "legacyFiles": []}
    for side in ("train", "holdout"):
        names = manifest.get(side + "Files", [])
        if len(names) != manifest.get(side + "Teams") or not names:
            raise RuntimeError("El split histórico está incompleto: " + side)
        for name in names:
            if Path(name).name != name:
                raise ValueError("Invalid historical filename")
            file = legacy_split / side / name
            assign_signature(state, team_signature(file), forced=side)
            state["legacyFiles"].append({"side": side, "file": name, "sha256": sha256_file(file)})
    return state


def snapshot_split(*, teams: Path, output: Path, registry: Path, legacy_split: Path,
                   seed: int = 260913) -> dict:
    state = load_partitions(registry, legacy_split, seed)
    previous = set(state["assignments"])
    files = {"train": [], "holdout": []}
    fresh = []
    for side in files:
        directory = output / side
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    # Persist original holdout even if a paste drops out of the live source.
    sources = {sha256_file(p): p for p in (legacy_split / "holdout").glob("mc*.txt")}
    sources.update({sha256_file(p): p for p in teams.glob("mc*.txt")})
    for digest, file in sorted(sources.items()):
        signature = team_signature(file)
        side = assign_signature(state, signature)
        name = "mc" + str(int(digest[:16], 16)) + ".txt"
        target = output / side / name
        if target.exists():
            raise RuntimeError("Snapshot filename collision")
        shutil.copy2(file, target)
        files[side].append(name)
        if side == "holdout" and signature_key(signature) not in previous:
            fresh.append(name)
    if len(files["train"]) < 2 or len(files["holdout"]) < 2:
        raise RuntimeError("Insufficient train/holdout teams")
    atomic_json(registry, state)
    manifest = {"generatedAt": utc_now(), "seed": seed, "signatureLeakage": 0,
                "trainTeams": len(files["train"]), "holdoutTeams": len(files["holdout"]),
                "trainFiles": files["train"], "holdoutFiles": files["holdout"],
                "freshHoldoutFiles": fresh, "freshHoldoutTeams": len(fresh),
                "trainDir": str(output / "train"), "holdoutDir": str(output / "holdout"),
                "fileHashes": {side: {n: sha256_file(output / side / n) for n in names}
                               for side, names in files.items()}}
    atomic_json(output / "split_manifest.json", manifest)
    return manifest


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url, timeout=30, attempts=2))


def refresh_replays(*, cache: Path, legacy_data: Path, workers: int, max_pages: int = 100,
                    getter: Callable = fetch_json, scraper: Any = None) -> dict:
    """Always visit new pages, with a per-run page budget, never a lifetime cap."""
    scraper = scraper or importlib.import_module("vgc_bench.scrape_logs")
    (cache / "battle_logs").mkdir(parents=True, exist_ok=True)
    (cache / "scrape_audit").mkdir(parents=True, exist_ok=True)
    summaries = {}
    for fmt in (DEFAULT_FORMAT_BO3, DEFAULT_FORMAT):
        log_path = cache / "battle_logs" / f"logs_{fmt}.json"
        audit_path = cache / "scrape_audit" / f"audit_{fmt}.json"
        logs = read_json(log_path, read_json(legacy_data / "battle_logs" / log_path.name, {}))
        audit = read_json(audit_path, {"seen": {}})
        seen = audit.setdefault("seen", {})
        start_count = len(logs)
        old_newest = max((int(v[0]) for v in logs.values()), default=0)
        cursor = 2_000_000_000
        phase, stopped, pages, retries = "new", "page_budget", 0, 0
        progress = Progress(max_pages, f"Replays {fmt}")
        def get_log(ident):
            try:
                return getter(f"https://replay.pokemonshowdown.com/{ident}.json")
            except Exception:
                return None
        for page_index in range(max_pages):
            page = getter("https://replay.pokemonshowdown.com/search.json?" +
                          urllib.parse.urlencode({"format": fmt, "before": cursor + 1}))
            if not isinstance(page, list):
                raise RuntimeError("Unexpected Showdown search response")
            pages += 1
            if not page:
                if phase == "new" and old_newest and not audit.get("historyExhausted"):
                    phase, cursor = "history", int(audit.get("historyCursor", old_newest))
                    continue
                audit["historyExhausted"] = True
                stopped = "source_exhausted"
                break
            ids = []
            for row in page:
                ident = str(row.get("id", ""))
                if not re.fullmatch(re.escape(fmt) + r"-\d+(?:-[a-z0-9]+)?", ident):
                    continue
                meta = seen.get(ident, {})
                if ident not in logs and (not meta or meta.get("reason") == "download_failed"):
                    ids.append(ident)
            ids = sorted(set(ids))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for ident, payload in zip(ids, pool.map(get_log, ids)):
                    accepted, reason, log, uploaded = audit_replay(payload, scraper)
                    seen[ident] = {"reason": reason, "accepted": accepted, "uploadtime": uploaded}
                    if accepted:
                        logs[ident] = [int(uploaded), log]
                    if reason == "download_failed":
                        retries += 1
            oldest = min(int(row["uploadtime"]) for row in page)
            atomic_json(log_path, logs)
            if phase == "history":
                audit["historyCursor"] = oldest
            atomic_json(audit_path, audit)
            progress.update(pages, f"+{len(logs)-start_count} válidos · {phase}", force=True)
            if phase == "new" and old_newest and oldest <= old_newest:
                if audit.get("historyExhausted"):
                    stopped = "new_head_complete"
                    break
                phase, cursor = "history", int(audit.get("historyCursor", min(int(v[0]) for v in logs.values())))
                continue
            if oldest >= cursor:
                stopped = "pagination_stalled"
                break
            cursor = oldest
        atomic_json(log_path, logs)
        atomic_json(audit_path, audit)
        summaries[fmt] = {"before": start_count, "accepted": len(logs), "added": len(logs)-start_count,
                          "pages": pages, "stopReason": stopped, "downloadFailures": retries,
                          "discardByCause": dict(Counter(v["reason"] for v in seen.values() if not v.get("accepted")))}
    result = {"generatedAt": utc_now(), "formats": summaries,
              "totalLogs": sum(v["accepted"] for v in summaries.values()),
              "addedLogs": sum(v["added"] for v in summaries.values())}
    atomic_json(cache / "logs_manifest.json", result)
    return result


def filter_training_logs(*, cache: Path, output: Path, registry: Path, min_rating: int = 1200) -> dict:
    """Exclude both perspectives of any holdout matchup BEFORE conversion/BC."""
    state = read_json(registry)
    output.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    digests = set()
    for source in sorted((cache / "battle_logs").glob("logs_*.json")):
        keep = {}
        for ident, (uploaded, log) in sorted(read_json(source, {}).items()):
            signatures = [preview_species(log, role) for role in ("p1", "p2")]
            if any(len(s) != 6 for s in signatures):
                counts["invalid_preview"] += 1
                continue
            if any(assign_signature(state, s) == "holdout" for s in signatures):
                counts["holdout_matchup"] += 1
                continue
            role = winner_role(log)
            rating = re.search(r"(?m)^\|player\|" + re.escape(role) + r"\|[^|]*\|[^|]*\|(\d+)", log)
            if role not in {"p1", "p2"} or not rating or int(rating[1]) < min_rating:
                counts["winner_rating"] += 1
                continue
            digest = sha256_text(log)
            if digest in digests:
                counts["duplicate_log"] += 1
                continue
            digests.add(digest)
            keep[ident] = [uploaded, log]
            counts["eligibleLogs"] += 1
        atomic_json(output / source.name, keep)
    atomic_json(registry, state)
    result = {"generatedAt": utc_now(), "minRating": min_rating, "onlyWinner": True,
              "eligibleLogs": counts["eligibleLogs"], "counts": dict(counts),
              "contentSha256": sha256_text("\n".join(sorted(digests)))}
    atomic_json(output.parent / "log_filter_manifest.json", result)
    return result
