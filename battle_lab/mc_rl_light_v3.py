#!/usr/bin/env python3
"""Checkpoint-compatible Battle Lab M-C LIGHT runner.

VGC-Bench encodes abilities with a fixed embedding table. Regulation M-C adds
Aura Guard, which is absent from the pinned M-A/M-B catalog. Growing that list
changes the embedding shape and makes existing checkpoints unloadable.

For LIGHT we therefore alias the M-C-only Aura Guard value onto an ability slot
that cannot occur in official VGC (the CAP ability ``mountaineer``). The list
length and every other index remain unchanged, so the public baseline and our
49,152-step checkpoint stay binary-compatible. That reserved row can then learn
Aura Guard semantics during M-C PPO.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Sequence

from battle_lab import mc_rl_light_v2 as v2
from battle_lab.mc_training import working_directory

MC_ABILITY_SLOT_ALIASES = {"auraguard": "mountaineer"}


def alias_mc_runtime_catalogs(vgc_bench_checkout: Path) -> dict[str, list[str]]:
    """Alias M-C-only categories onto unreachable VGC slots without resizing.

    This is intentionally in-place and length preserving. Replacing a CAP-only
    ability keeps every tensor dimension compatible with checkpoints trained on
    the pinned VGC-Bench catalog. The operation is idempotent within a process.
    """

    checkout = str(vgc_bench_checkout)
    if checkout not in sys.path:
        sys.path.insert(0, checkout)

    aliases: list[str] = []
    with working_directory(vgc_bench_checkout):
        utils = importlib.import_module("vgc_bench.src.utils")
        before_len = len(utils.abilities)

        for mc_value, reserved_value in MC_ABILITY_SLOT_ALIASES.items():
            if mc_value in utils.abilities:
                aliases.append(f"{mc_value}@{utils.abilities.index(mc_value)}")
                continue
            if reserved_value not in utils.abilities:
                raise RuntimeError(
                    f"No existe el slot reservado {reserved_value!r} para mapear {mc_value!r}."
                )
            slot = utils.abilities.index(reserved_value)
            utils.abilities[slot] = mc_value
            aliases.append(f"{mc_value}@{slot}<-{reserved_value}")

        if len(utils.abilities) != before_len:
            raise RuntimeError("El alias M-C cambió el tamaño del catálogo de abilities.")

    return {"abilities": aliases, "items": []}


def main(argv: Sequence[str] | None = None) -> int:
    # v2 contains the visible/restartable training loop. Replace only the
    # catalog-preparation hook so checkpoint dimensions remain unchanged.
    v2.extend_mc_runtime_catalogs = alias_mc_runtime_catalogs
    return v2.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
