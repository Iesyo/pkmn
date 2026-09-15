"""Nana 2.2 shadow runtime with a persistent observational LIGHT critic.

LIGHT remains the real policy and Nana's live influence stays at 0.0. The new
critic learns, between battles, from the observed local board transition that
followed each real LIGHT decision. Its cache is rebuilt from Nana's append-only
history, so the adaptation is reversible and auditable.
"""

from __future__ import annotations

import copy
from typing import Any, Sequence

from battle_lab import nana_stage2_shadow_v2_runtime as stage2_v2
from battle_lab.nana_light_critic import MODEL_VERSION as LIGHT_CRITIC_MODEL_VERSION
from battle_lab.nana_light_critic import rebuild_for_recorder
from battle_lab.nana_runtime import install_reusable_viewer, parse_nana_args


STAGE2_MODEL_VERSION = "nana2-shadow-v2.2-light-critic"


def install_light_critic_service(*, profile_id: str) -> type:
    """Layer LIGHT outcome learning on top of the auditable v2.1 shadow service."""

    stage2_v2.STAGE2_MODEL_VERSION = STAGE2_MODEL_VERSION
    service_class = stage2_v2.install_nana_stage2_shadow_v2_service(
        profile_id=profile_id
    )
    if getattr(service_class, "_nana_light_critic_v1", False):
        service_class.nana_profile_id = profile_id
        return service_class

    original_init = service_class.__init__
    original_ensure_ready = service_class.ensure_ready
    original_start = service_class.start
    original_snapshot = service_class.snapshot
    original_finish = service_class._nana_finish

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._nana_light_critic_finished: set[str] = set()
        # Historical sessions are immediately useful: the critic backfills from
        # turn_choice + session_end rather than requiring new battles from zero.
        try:
            self.light_critic_summary = rebuild_for_recorder(self.nana)
        except Exception:
            self.light_critic_summary = {
                "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
                "causalStatus": "observational-only",
                "influence": 0.0,
                "observations": 0,
                "global": {"trust": 0.90, "confidence": 0.0},
            }

    async def ensure_ready(self: Any) -> None:
        await original_ensure_ready(self)
        nana_meta = self.runtime_metadata.setdefault("nana", {})
        nana_meta["stage"] = 2
        nana_meta["mode"] = "shadow-v2.2-light-critic"
        nana_meta["influence"] = 0.0
        nana_meta["shadowModel"] = STAGE2_MODEL_VERSION
        summary = self.light_critic_summary
        nana_meta["lightCritic"] = {
            "enabled": True,
            "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
            "causalStatus": "observational-only",
            "influence": 0.0,
            "observations": int(summary.get("observations") or 0),
            "globalTrust": float(
                (summary.get("global") or {}).get("trust") or 0.90
            ),
            "globalConfidence": float(
                (summary.get("global") or {}).get("confidence") or 0.0
            ),
        }

    async def start(self: Any, request: Any):
        session = await original_start(self, request)
        summary = self.light_critic_summary
        self.nana.append_event(
            session.id,
            "nana_light_critic_version",
            {
                "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
                "stage2Model": STAGE2_MODEL_VERSION,
                "causalStatus": "observational-only",
                "influence": 0.0,
                "historicalBackfill": True,
                "observationsBeforeSession": int(
                    summary.get("observations") or 0
                ),
                "globalTrustBeforeSession": float(
                    (summary.get("global") or {}).get("trust") or 0.90
                ),
                "globalConfidenceBeforeSession": float(
                    (summary.get("global") or {}).get("confidence") or 0.0
                ),
            },
        )
        return session

    def snapshot(self: Any, session: Any) -> dict[str, Any]:
        data = original_snapshot(self, session)
        summary = self.light_critic_summary
        nana = data.setdefault("nana", {})
        nana.update(
            {
                "stage": 2,
                "mode": "shadow-v2.2-light-critic",
                "influence": 0.0,
                "shadowModel": STAGE2_MODEL_VERSION,
                "lightCritic": {
                    "enabled": True,
                    "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
                    "causalStatus": "observational-only",
                    "influence": 0.0,
                    "observations": int(summary.get("observations") or 0),
                    "globalTrust": float(
                        (summary.get("global") or {}).get("trust") or 0.90
                    ),
                    "globalConfidence": float(
                        (summary.get("global") or {}).get("confidence") or 0.0
                    ),
                },
            }
        )
        return data

    def _nana_finish(self: Any, session: Any) -> None:
        if session.id in self._nana_light_critic_finished:
            return
        self._nana_light_critic_finished.add(session.id)
        # Let Nana 0/1/2 persist the canonical session events first. Then rebuild
        # the critic from source-of-truth history, including this completed BO1.
        original_finish(self, session)
        try:
            before = copy.deepcopy(self.light_critic_summary)
            self.light_critic_summary = rebuild_for_recorder(self.nana)
            after = self.light_critic_summary
            self.nana.append_event(
                session.id,
                "nana_light_critic_rebuild",
                {
                    "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
                    "stage2Model": STAGE2_MODEL_VERSION,
                    "causalStatus": "observational-only",
                    "influence": 0.0,
                    "observationsBefore": int(
                        before.get("observations") or 0
                    ),
                    "observationsAfter": int(
                        after.get("observations") or 0
                    ),
                    "globalTrustBefore": float(
                        (before.get("global") or {}).get("trust") or 0.90
                    ),
                    "globalTrustAfter": float(
                        (after.get("global") or {}).get("trust") or 0.90
                    ),
                    "recent30": copy.deepcopy(after.get("recent30") or {}),
                },
            )
        except Exception as error:
            try:
                self.nana.append_event(
                    session.id,
                    "nana_light_critic_error",
                    {
                        "modelVersion": LIGHT_CRITIC_MODEL_VERSION,
                        "influence": 0.0,
                        "error": f"{type(error).__name__}: {error}",
                    },
                )
            except Exception:
                pass

    service_class.__init__ = __init__
    service_class.ensure_ready = ensure_ready
    service_class.start = start
    service_class.snapshot = snapshot
    service_class._nana_finish = _nana_finish
    service_class._nana_light_critic_v1 = True
    service_class.nana_profile_id = profile_id
    return service_class


def main(argv: Sequence[str] | None = None) -> int:
    nana_args, remaining = parse_nana_args(argv)
    install_light_critic_service(profile_id=nana_args.nana_profile)
    from battle_lab import local_runtime

    install_reusable_viewer(local_runtime)
    return local_runtime.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
