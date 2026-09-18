"""Persistent, model-agnostic TeamMemory for Nana.

TeamMemory is derived exclusively from Nana's real observed intervention
outcomes. It never owns raw battle history: JSONL remains source of truth and
this summary is rebuildable. Old exact-Team signature versions are ignored so
pre-TeamMemory identity experiments cannot silently seed L2 evidence.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from battle_lab.nana_recorder import utc_now
from battle_lab.nana_self_critic import extract_observations
from battle_lab.nana_team_identity import (
    EXACT_TEAM_SIGNATURE_SPEC_VERSION,
    team_scope_keys,
)


TEAM_MEMORY_SCHEMA_VERSION = 1
TEAM_MEMORY_MODEL_VERSION = "nana-team-memory-v2"
TEAM_MEMORY_MIN_SCOPE_SAMPLES = 3
TEAM_MEMORY_MAX_BLEND = 0.35
TEAM_MEMORY_PRIOR_TRUST = 0.50
TEAM_MEMORY_PRIOR_WEIGHT = 6.0
TEAM_MEMORY_RECENCY_HALF_LIFE = 64.0
_SCOPE_WEIGHTS = {
    "global": 0.10,
    "archetype": 0.20,
    "roster": 0.30,
    "exactTeam": 0.40,
}


def team_memory_contract() -> dict[str, Any]:
    return {
        "modelVersion": TEAM_MEMORY_MODEL_VERSION,
        "schemaVersion": TEAM_MEMORY_SCHEMA_VERSION,
        "exactTeamSignatureSpecVersion": EXACT_TEAM_SIGNATURE_SPEC_VERSION,
        "scopeOrder": ["global", "archetype", "roster", "exactTeam"],
        "minScopeSamples": TEAM_MEMORY_MIN_SCOPE_SAMPLES,
        "maxBlend": TEAM_MEMORY_MAX_BLEND,
        "blendMode": "n2-caution-only-v1",
        "priorTrust": TEAM_MEMORY_PRIOR_TRUST,
        "priorWeight": TEAM_MEMORY_PRIOR_WEIGHT,
        "recencyHalfLife": TEAM_MEMORY_RECENCY_HALF_LIFE,
        "causalStatus": "observational-only",
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    if rendered != rendered or rendered in (float("inf"), float("-inf")):
        return default
    return rendered


def _session_team_contexts(events: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], int]:
    contexts: dict[str, dict[str, Any]] = {}
    ignored_legacy = 0
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "session_start":
            continue
        session_id = str(event.get("sessionId") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        team = context.get("teamIdentity") if isinstance(context.get("teamIdentity"), dict) else {}
        if not session_id or team.get("resolved") is not True:
            continue
        exact = str(team.get("exactTeamSignature") or "")
        if not exact.startswith(f"team:v{EXACT_TEAM_SIGNATURE_SPEC_VERSION}:"):
            ignored_legacy += 1
            continue
        contexts[session_id] = {
            "rosterSignature": str(team.get("rosterSignature") or ""),
            "exactTeamSignature": exact,
            "opponentArchetype": str(context.get("opponentArchetype") or ""),
        }
    return contexts, ignored_legacy


def _scope_rows(context: dict[str, Any]) -> list[tuple[str, str]]:
    keys = team_scope_keys(
        roster_signature=str(context.get("rosterSignature") or ""),
        exact_team_signature=str(context.get("exactTeamSignature") or ""),
        opponent_archetype=str(context.get("opponentArchetype") or ""),
    )
    rendered: list[tuple[str, str]] = []
    for key in keys:
        if key == "global":
            level = "global"
        elif key.startswith("archetype:"):
            level = "archetype"
        elif key.startswith("roster:v"):
            level = "roster"
        elif key.startswith("team:v"):
            level = "exactTeam"
        else:
            continue
        rendered.append((level, key))
    return rendered


def _blank_bucket(level: str, key: str) -> dict[str, Any]:
    return {
        "level": level,
        "key": key,
        "samples": 0,
        "effectiveWeight": 0.0,
        "weightedOutcome": 0.0,
        "weightedDelta": 0.0,
        "positive": 0,
        "neutral": 0,
        "negative": 0,
        "lastTimestamp": "",
    }


def _render_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    weight = _safe_float(bucket.get("effectiveWeight"))
    posterior = (
        TEAM_MEMORY_PRIOR_TRUST * TEAM_MEMORY_PRIOR_WEIGHT
        + _safe_float(bucket.get("weightedOutcome"))
    ) / (TEAM_MEMORY_PRIOR_WEIGHT + weight)
    confidence = weight / (TEAM_MEMORY_PRIOR_WEIGHT + weight)
    return {
        "level": bucket["level"],
        "key": bucket["key"],
        "samples": int(bucket["samples"]),
        "effectiveWeight": weight,
        "trust": max(0.0, min(1.0, posterior)),
        "confidence": max(0.0, min(1.0, confidence)),
        "meanDelta": (
            _safe_float(bucket.get("weightedDelta")) / weight if weight > 0 else None
        ),
        "positive": int(bucket["positive"]),
        "neutral": int(bucket["neutral"]),
        "negative": int(bucket["negative"]),
        "lastTimestamp": str(bucket.get("lastTimestamp") or ""),
    }


def build_team_memory(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = [event for event in events if isinstance(event, dict)]
    contexts, ignored_legacy = _session_team_contexts(materialized)
    observations = extract_observations(materialized, actor_filter="nana")
    tagged = [
        item for item in observations
        if str(item.get("sessionId") or "") in contexts
    ]
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    total = len(tagged)

    for index, observation in enumerate(tagged):
        context = contexts[str(observation["sessionId"])]
        age = total - 1 - index
        recency = (
            0.5 ** (age / TEAM_MEMORY_RECENCY_HALF_LIFE)
            if TEAM_MEMORY_RECENCY_HALF_LIFE > 0
            else 1.0
        )
        effective_weight = max(0.0, _safe_float(observation.get("baseWeight"), 0.10)) * recency
        if observation.get("label") == "positive":
            outcome = 1.0
        elif observation.get("label") == "negative":
            outcome = 0.0
        else:
            outcome = TEAM_MEMORY_PRIOR_TRUST

        for level, key in _scope_rows(context):
            bucket = buckets.setdefault((level, key), _blank_bucket(level, key))
            bucket["samples"] += 1
            bucket["effectiveWeight"] += effective_weight
            bucket["weightedOutcome"] += effective_weight * outcome
            bucket["weightedDelta"] += effective_weight * _safe_float(observation.get("delta"))
            label = str(observation.get("label") or "neutral")
            if label in {"positive", "neutral", "negative"}:
                bucket[label] += 1
            bucket["lastTimestamp"] = max(
                str(bucket.get("lastTimestamp") or ""),
                str(observation.get("timestamp") or ""),
            )

    rendered = {
        f"{level}:{key}": _render_bucket(bucket)
        for (level, key), bucket in buckets.items()
    }
    return {
        "schemaVersion": TEAM_MEMORY_SCHEMA_VERSION,
        "modelVersion": TEAM_MEMORY_MODEL_VERSION,
        "updatedAt": utc_now(),
        "causalStatus": "observational-only",
        "contract": team_memory_contract(),
        "observations": total,
        "taggedSessions": len({str(item.get("sessionId") or "") for item in tagged}),
        "ignoredLegacyTeamSessions": ignored_legacy,
        "buckets": rendered,
        "observationIds": [str(item.get("id") or "") for item in tagged],
    }


def write_team_memory(profile_root: Path, summary: dict[str, Any]) -> Path:
    destination = Path(profile_root) / "team_memory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return destination


def rebuild_team_memory_for_recorder(recorder: Any) -> dict[str, Any]:
    summary = build_team_memory(recorder.iter_events())
    write_team_memory(recorder.profile_root, summary)
    return summary


def team_trust_for(
    summary: dict[str, Any],
    team_context: dict[str, Any] | None,
    *,
    opponent_archetype: str = "",
) -> dict[str, Any]:
    """Hierarchical global→archetype→roster→exactTeam read with evidence gates."""

    team_context = team_context if isinstance(team_context, dict) else {}
    if team_context.get("resolved") is not True:
        return {
            "trust": TEAM_MEMORY_PRIOR_TRUST,
            "confidence": 0.0,
            "eligible": False,
            "reason": "team-identity-unresolved",
            "components": [],
        }

    keys = _scope_rows(
        {
            "rosterSignature": team_context.get("rosterSignature"),
            "exactTeamSignature": team_context.get("exactTeamSignature"),
            "opponentArchetype": opponent_archetype,
        }
    )
    buckets = summary.get("buckets") if isinstance(summary.get("buckets"), dict) else {}
    components: list[dict[str, Any]] = []

    for level, key in keys:
        bucket = buckets.get(f"{level}:{key}")
        if not isinstance(bucket, dict):
            continue
        samples = int(bucket.get("samples") or 0)
        confidence = max(0.0, min(1.0, _safe_float(bucket.get("confidence"))))
        trust = max(
            0.0,
            min(1.0, _safe_float(bucket.get("trust"), TEAM_MEMORY_PRIOR_TRUST)),
        )
        components.append(
            {
                "level": level,
                "key": key,
                "samples": samples,
                "trust": trust,
                "confidence": confidence,
                "ready": samples >= TEAM_MEMORY_MIN_SCOPE_SAMPLES,
            }
        )

    ready = [item for item in components if item["ready"]]
    if not ready:
        return {
            "trust": TEAM_MEMORY_PRIOR_TRUST,
            "confidence": 0.0,
            "eligible": False,
            "reason": "team-memory-cold-start",
            "selectedScope": None,
            "components": components,
        }

    # Keys arrive least→most specific. True backoff selects exactly one scope,
    # preventing the same observations from being counted again at every level.
    selected = ready[-1]
    return {
        "trust": selected["trust"],
        "confidence": selected["confidence"],
        "eligible": True,
        "reason": "team-memory-ready",
        "selectedScope": selected["level"],
        "selectedKey": selected["key"],
        "samples": selected["samples"],
        "components": components,
    }


def blend_self_with_team(
    self_trust: dict[str, Any],
    team_trust: dict[str, Any],
) -> dict[str, Any]:
    """Conservatively add model-agnostic TeamMemory to existing self trust."""

    base = dict(self_trust) if isinstance(self_trust, dict) else {}
    if team_trust.get("eligible") is not True:
        return {
            **base,
            "teamMemory": team_trust,
            "teamMemoryBlend": 0.0,
        }

    base_trust = max(0.0, min(1.0, _safe_float(base.get("trust"), TEAM_MEMORY_PRIOR_TRUST)))
    base_confidence = max(0.0, min(1.0, _safe_float(base.get("confidence"))))
    team_value = max(0.0, min(1.0, _safe_float(team_trust.get("trust"), TEAM_MEMORY_PRIOR_TRUST)))
    team_confidence = max(0.0, min(1.0, _safe_float(team_trust.get("confidence"))))

    # N2 TeamMemory is monotonic-caution only. Positive TeamMemory may be useful
    # to a future common scorer, but while self_trust only powers a low-trust
    # veto it must never raise trust and thereby remove Nana's own safety signal.
    if team_value >= base_trust:
        return {
            **base,
            "teamMemory": team_trust,
            "teamMemoryBlend": 0.0,
            "teamMemoryEffect": "no-relaxation",
        }

    blend = min(TEAM_MEMORY_MAX_BLEND, TEAM_MEMORY_MAX_BLEND * team_confidence)
    return {
        **base,
        "trust": (1.0 - blend) * base_trust + blend * team_value,
        "confidence": max(base_confidence, blend * team_confidence),
        "teamMemory": team_trust,
        "teamMemoryBlend": blend,
        "teamMemoryEffect": "added-caution",
    }
