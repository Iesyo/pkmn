"""Superseded Nana 2 shadow v1 compatibility shim.

This module is retained only so older imports remain readable. New evidence must
use ``battle_lab.nana_stage2_shadow_v2_runtime``. Direct execution is blocked.
"""

from __future__ import annotations

from battle_lab.nana_stage2_shadow_common import response_utility


def main() -> int:
    raise SystemExit(
        "Nana 2 shadow v1 fue sustituida por nana_stage2_shadow_v2_runtime. "
        "Usa: python -m battle_lab.nana_stage2_shadow_v2_runtime --nana-profile <perfil>"
    )


if __name__ == "__main__":
    raise SystemExit(main())
