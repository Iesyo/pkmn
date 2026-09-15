"""Versioned Nana 2 shadow v2.1 entrypoint.

v2.1 keeps every auditable/fault-isolation guardrail from shadow v2 and only
expands the conservative response proxy to damaging spread moves. The separate
model version prevents v2 and v2.1 evidence from being mixed in reports.
"""

from __future__ import annotations

from typing import Sequence

from battle_lab import nana_stage2_shadow_v2_runtime as _v2

STAGE2_MODEL_VERSION = "nana2-shadow-v2.1-spread-aware"


def main(argv: Sequence[str] | None = None) -> int:
    _v2.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION
    return _v2.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
