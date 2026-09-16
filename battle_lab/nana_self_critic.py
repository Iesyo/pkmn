"""Skip-aware self-critic extraction for Nana's own live interventions.

The generic LIGHT critic intentionally requires prompt generations to be strictly
contiguous. Nursery LIVE can legitimately insert model-only forced-switch prompts
that are recorded as benign ``nana_nursery_skip`` events instead of ``turn_choice``.
This adapter preserves the strict critic baseline and bridges only gaps whose
missing generations are fully explained by audited, non-blocking Nursery skips.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Iterable

from battle_lab import nana_light_critic as critic


BRIDGEABLE_SKIP_REASONS = frozenset(
    {
        "model-only-force-switch-no-human-prompt",
    }
)
BRIDGE_ACTOR = "nana-benign-skip-bridge"


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _timestamp(event: dict[str, Any]) -> str:
    return str(event.get("timestamp") or "")


def _skip_generation(payload: dict[str, Any]) -> int:
    direct = payload.get("generation")
    if isinstance(direct, int) and direct > 0:
        return direct
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    nested = details.get("generation")
    return int(nested) if isinstance(nested, int) and nested > 0 else 0


def _bridgeable_skip(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "nana_nursery_skip":
        return None
    payload = _payload(event)
    reason = str(payload.get("reason") or "")
    generation = _skip_generation(payload)
    if (
        generation <= 0
        or reason not in BRIDGEABLE_SKIP_REASONS
        or payload.get("promotionBlocking") is True
    ):
        return None
    return {
        "generation": generation,
        "turn": int(payload.get("turn") or 0),
        "reason": reason,
        "timestamp": _timestamp(event),
    }


def _bridge_plan(
    current: dict[str, Any],
    nxt: dict[str, Any],
    skips: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    current_payload = _payload(current)
    next_payload = _payload(nxt)
    current_generation = int(current_payload.get("generation") or 0)
    next_generation = int(next_payload.get("generation") or 0)
    current_turn = int(current_payload.get("turn") or 0)
    next_turn = int(next_payload.get("turn") or 0)

    if current_generation <= 0 or next_generation <= current_generation + 1:
        return None
    if next_turn not in {current_turn, current_turn + 1}:
        return None

    current_ts = _timestamp(current)
    next_ts = _timestamp(nxt)
    required = list(range(current_generation + 1, next_generation))
    selected: list[dict[str, Any]] = []
    for generation in required:
        candidates = [
            item
            for item in skips
            if item["generation"] == generation
            and item["turn"] in {current_turn, next_turn}
            and (not current_ts or item["timestamp"] >= current_ts)
            and (not next_ts or item["timestamp"] <= next_ts)
        ]
        if not candidates:
            return None
        selected.append(sorted(candidates, key=lambda item: item["timestamp"])[0])
    return selected


def _augment_with_benign_skip_bridges(
    events: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    materialized = [copy.deepcopy(event) for event in events if isinstance(event, dict)]
    sessions: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, event in enumerate(materialized):
        session_id = str(event.get("sessionId") or "")
        if session_id:
            sessions[session_id].append((index, event))

    synthetic: list[dict[str, Any]] = []
    bridge_meta: dict[tuple[str, int], dict[str, Any]] = {}

    for session_id, indexed_events in sessions.items():
        ordered = sorted(
            indexed_events,
            key=lambda item: (_timestamp(item[1]), item[0]),
        )
        turns = [
            event
            for _, event in ordered
            if event.get("type") == "turn_choice"
            and isinstance(_payload(event).get("generation"), int)
            and int(_payload(event).get("generation") or 0) > 0
        ]
        skips = [
            rendered
            for _, event in ordered
            if (rendered := _bridgeable_skip(event)) is not None
        ]
        for current, nxt in zip(turns, turns[1:]):
            selected = _bridge_plan(current, nxt, skips)
            if not selected:
                continue
            current_payload = _payload(current)
            next_payload = _payload(nxt)
            current_generation = int(current_payload.get("generation") or 0)
            next_timestamp = _timestamp(nxt)
            next_state = next_payload.get("state")
            if not isinstance(next_state, dict):
                continue

            bridge_meta[(session_id, current_generation)] = {
                "kind": "benign-skip-bridge",
                "skippedGenerations": [item["generation"] for item in selected],
                "skipReasons": [item["reason"] for item in selected],
            }
            for item in selected:
                synthetic.append(
                    {
                        "schemaVersion": 1,
                        "timestamp": next_timestamp,
                        "profileId": str(nxt.get("profileId") or current.get("profileId") or ""),
                        "sessionId": session_id,
                        "type": "turn_choice",
                        "payload": {
                            "turn": int(next_payload.get("turn") or current_payload.get("turn") or 0),
                            "generation": int(item["generation"]),
                            "state": copy.deepcopy(next_state),
                            "modelAction": {"bridge": True},
                            "modelActor": BRIDGE_ACTOR,
                            "teacher": copy.deepcopy(
                                current_payload.get("teacher")
                                if isinstance(current_payload.get("teacher"), dict)
                                else next_payload.get("teacher")
                            ),
                            "light": {},
                        },
                    }
                )

    return materialized + synthetic, bridge_meta


def extract_observations(
    events: Iterable[dict[str, Any]],
    *,
    actor_filter: str | None = "nana",
) -> list[dict[str, Any]]:
    """Extract Nana outcomes while bridging only fully explained benign gaps."""

    materialized = list(events)
    if actor_filter != "nana":
        return critic.extract_observations(materialized, actor_filter=actor_filter)

    augmented, bridge_meta = _augment_with_benign_skip_bridges(materialized)
    observations = critic.extract_observations(augmented, actor_filter=actor_filter)
    for observation in observations:
        key = (
            str(observation.get("sessionId") or ""),
            int(observation.get("generation") or 0),
        )
        continuity = bridge_meta.get(key)
        if continuity is not None:
            observation["continuity"] = copy.deepcopy(continuity)
    return observations


def build_actor_summary(
    events: Iterable[dict[str, Any]],
    *,
    actor: str,
    prior_trust: float,
    prior_weight: float,
    model_version: str,
    influence: float = 0.0,
) -> dict[str, Any]:
    """Build the normal critic summary from skip-aware Nana observations."""

    observations = extract_observations(events, actor_filter=actor)
    summary = critic._summary_from_observations(
        observations,
        prior_trust=prior_trust,
        prior_weight=prior_weight,
        model_version=model_version,
        influence=influence,
    )
    by_teacher: dict[str, list[dict[str, Any]]] = defaultdict(list)
    teacher_meta: dict[str, dict[str, Any]] = {}
    for observation in observations:
        key = str(observation.get("teacherKey") or "unknown")
        by_teacher[key].append(observation)
        teacher = observation.get("teacher")
        if isinstance(teacher, dict):
            teacher_meta[key] = teacher
    summary["teachers"] = {}
    for key, items in by_teacher.items():
        child = critic._summary_from_observations(
            items,
            prior_trust=prior_trust,
            prior_weight=prior_weight,
            model_version=model_version,
            influence=influence,
        )
        child["teacher"] = teacher_meta.get(key) or {"key": key}
        summary["teachers"][key] = child
    return summary
