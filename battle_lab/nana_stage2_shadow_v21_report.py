"""Read-only report entrypoint for Nana 2 shadow v2.1.

It reuses the v2 audit logic but filters exclusively by the v2.1 shadow model
version, so the first auditable v2 BO1 is preserved as historical evidence and
never mixed with spread-aware sessions.
"""

from __future__ import annotations

from typing import Sequence

from battle_lab import nana_stage2_shadow_v2_runtime as _runtime

STAGE2_MODEL_VERSION = "nana2-shadow-v2.1-spread-aware"
_runtime.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION

from battle_lab import nana_stage2_shadow_v2_report as _report  # noqa: E402

_report.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION


def main(argv: Sequence[str] | None = None) -> int:
    return _report.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
