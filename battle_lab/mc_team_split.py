#!/usr/bin/env python3
"""Deterministic train/holdout split for Battle Lab M-C teams.

The split groups teams by their six-species Team Preview signature before
assignment, so alternate sets of the same species composition cannot leak
between training and holdout evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


DEFAULT_SEED = 260913
DEFAULT_HOLDOUT_FRACTION = 0.20


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(path)


def normalize_species(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def species_from_header(header: str) -> str:
    """Extract a Showdown species from a team block's first line."""
    head = header.split(" @ ", 1)[0].strip()
    head = re.sub(r"\s+\((?:M|F)\)$", "", head, flags=re.IGNORECASE)
    matches = re.findall(r"\(([^()]+)\)", head)
    if matches:
        candidate = matches[-1].strip()
        if candidate.upper() not in {"M", "F"}:
            return candidate
    return head


def team_signature_text(text: str, *, source: str = "team") -> tuple[str, ...]:
    """Return the canonical six-species roster signature from Showdown text."""

    blocks = [block for block in re.split(r"\n\s*\n", str(text or "").strip()) if block.strip()]
    species: list[str] = []
    for block in blocks:
        first = next((line.strip() for line in block.splitlines() if line.strip()), "")
        if not first:
            continue
        normalized = normalize_species(species_from_header(first))
        if normalized:
            species.append(normalized)
    if len(species) != 6:
        raise RuntimeError(
            f"{source}: se esperaban 6 Pokémon y se detectaron {len(species)}"
        )
    return tuple(sorted(species))


def team_signature(path: Path) -> tuple[str, ...]:
    return team_signature_text(
        path.read_text(encoding="utf-8"),
        source=path.name,
    )


def deterministic_split(
    *,
    source_dir: Path,
    output_root: Path,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if not 0 < holdout_fraction < 0.5:
        raise ValueError("holdout_fraction debe estar entre 0 y 0.5")

    source_files = sorted(source_dir.glob("mc*.txt"))
    if len(source_files) < 10:
        raise RuntimeError(f"No hay suficientes equipos en {source_dir}: {len(source_files)}")

    groups: dict[tuple[str, ...], list[Path]] = defaultdict(list)
    for path in source_files:
        groups[team_signature(path)].append(path)

    ranked_groups = sorted(
        groups.items(),
        key=lambda item: hashlib.sha256(
            f"{seed}|{'|'.join(item[0])}".encode("utf-8")
        ).hexdigest(),
    )
    target_holdout = max(1, round(len(source_files) * holdout_fraction))
    holdout_files: list[Path] = []
    train_files: list[Path] = []
    holdout_signatures: set[tuple[str, ...]] = set()

    for signature, paths in ranked_groups:
        if len(holdout_files) < target_holdout:
            holdout_files.extend(paths)
            holdout_signatures.add(signature)
        else:
            train_files.extend(paths)

    if len(train_files) < 2 or len(holdout_files) < 2:
        raise RuntimeError(
            f"Split inválido: train={len(train_files)} holdout={len(holdout_files)}"
        )

    train_signatures = {team_signature(path) for path in train_files}
    overlap = train_signatures & holdout_signatures
    if overlap:
        raise RuntimeError(f"Fuga de firmas train/holdout detectada: {len(overlap)}")

    train_dir = output_root / "train"
    holdout_dir = output_root / "holdout"
    for directory in (train_dir, holdout_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    for source in sorted(train_files):
        shutil.copy2(source, train_dir / source.name)
    for source in sorted(holdout_files):
        shutil.copy2(source, holdout_dir / source.name)

    manifest = {
        "generatedAt": utc_now(),
        "seed": seed,
        "holdoutFractionRequested": holdout_fraction,
        "holdoutFractionActual": round(len(holdout_files) / len(source_files), 4),
        "sourceTeams": len(source_files),
        "uniqueSpeciesSignatures": len(groups),
        "trainTeams": len(train_files),
        "trainSignatures": len(train_signatures),
        "holdoutTeams": len(holdout_files),
        "holdoutSignatures": len(holdout_signatures),
        "signatureLeakage": 0,
        "trainDir": str(train_dir),
        "holdoutDir": str(holdout_dir),
        "trainFiles": sorted(path.name for path in train_files),
        "holdoutFiles": sorted(path.name for path in holdout_files),
    }
    atomic_json(output_root / "split_manifest.json", manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Battle Lab M-C deterministic team split")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = deterministic_split(
        source_dir=args.source_dir,
        output_root=args.output_root,
        holdout_fraction=args.holdout_fraction,
        seed=args.seed,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
