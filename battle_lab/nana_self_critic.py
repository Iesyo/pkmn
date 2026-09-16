"""Prompt-aware self-critic extraction for Nana's own live interventions.

LIGHT's generic critic intentionally learns only from model ``turn_choice``
transitions. Nursery has a different requirement: after Nana acts, Showdown can
legitimately ask the human for a same-turn forced switch while the model has no
simultaneous choice. That prompt advances the human-side generation and carries
the immediate post-action board state, but it does not produce a model
``turn_choice``.

For Nana only, the causal successor is therefore the next recorded
``human_choice_observed`` at exactly ``generation + 1``. Its state was captured
before the human submitted that next choice, so it is an auditable after-state
for Nana's preceding action. We never jump over a missing generation. If no such
prompt exists, only a genuinely terminal action may use ``session_end``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from battle_lab import nana_light_critic as critic


PROMPT_CONTINUITY_KIND = "next-human-prompt"
TERMINAL_CONTINUITY_KIND = "session-end"


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, dict) else {}


def _timestamp(event: dict[str, Any]) -> str:
    return str(event.get("timestamp") or "")


def _teacher_default(bundle: dict[str, Any]) -> dict[str, Any]:
    teacher = bundle.get("teacher")
    if isinstance(teacher, dict):
        return teacher
    return critic._legacy_teacher(bundle.get("context") or {})


def extract_observations(
    events: Iterable[dict[str, Any]],
    *,
    actor_filter: str | None = "nana",
) -> list[dict[str, Any]]:
    """Extract Nana outcomes from the next human prompt's pre-choice state.

    Other actors keep the generic LIGHT critic semantics unchanged. Nana only
    accepts a prompt successor at exactly generation + 1 and at the same or next
    Showdown turn. This covers human-only forced-switch prompts without relaxing
    generation continuity.
    """

    materialized = [event for event in events if isinstance(event, dict)]
    if actor_filter != "nana":
        return critic.extract_observations(materialized, actor_filter=actor_filter)

    sessions: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "modelTurns": [],
            "humanPrompts": [],
            "end": None,
            "teacher": None,
            "context": {},
        }
    )

    for index, event in enumerate(materialized):
        session_id = str(event.get("sessionId") or "")
        if not session_id:
            continue
        payload = _payload(event)
        event_type = str(event.get("type") or "")
        bundle = sessions[session_id]

        if event_type == "session_start":
            context = payload.get("context")
            if isinstance(context, dict):
                bundle["context"] = context
            continue

        if event_type == "nana_teacher_version":
            teacher = payload.get("teacher")
            if isinstance(teacher, dict) and teacher.get("key"):
                bundle["teacher"] = teacher
            continue

        if event_type == "turn_choice":
            state = payload.get("state")
            model_action = payload.get("modelAction")
            turn = payload.get("turn")
            generation = payload.get("generation")
            if (
                isinstance(state, dict)
                and isinstance(model_action, dict)
                and isinstance(turn, int)
                and turn > 0
            ):
                bundle["modelTurns"].append(
                    {
                        "index": index,
                        "timestamp": _timestamp(event),
                        "turn": turn,
                        "generation": int(generation) if isinstance(generation, int) else 0,
                        "state": state,
                        "modelAction": model_action,
                        "modelActor": str(payload.get("modelActor") or "light"),
                        "teacher": payload.get("teacher") if isinstance(payload.get("teacher"), dict) else None,
                        "light": payload.get("light") if isinstance(payload.get("light"), dict) else {},
                    }
                )
            continue

        if event_type == "human_choice_observed":
            state = payload.get("state")
            generation = payload.get("generation")
            turn = payload.get("turn")
            if (
                isinstance(state, dict)
                and isinstance(generation, int)
                and generation > 0
                and isinstance(turn, int)
                and turn > 0
            ):
                bundle["humanPrompts"].append(
                    {
                        "index": index,
                        "timestamp": _timestamp(event),
                        "turn": turn,
                        "generation": generation,
                        "state": state,
                    }
                )
            continue

        if event_type == "session_end":
            final_state = payload.get("finalState")
            if isinstance(final_state, dict):
                bundle["end"] = {
                    "index": index,
                    "timestamp": _timestamp(event),
                    "state": final_state,
                    "result": payload.get("result") if isinstance(payload.get("result"), dict) else {},
                }

    rendered: list[dict[str, Any]] = []
    for session_id, bundle in sessions.items():
        teacher_default = _teacher_default(bundle)
        model_turns = sorted(
            bundle["modelTurns"],
            key=lambda item: (item["timestamp"], item["index"]),
        )
        prompts = sorted(
            bundle["humanPrompts"],
            key=lambda item: (item["timestamp"], item["index"]),
        )

        for item in model_turns:
            if item.get("modelActor") != "nana":
                continue

            current_generation = int(item.get("generation") or 0)
            current_turn = int(item.get("turn") or 0)
            if current_generation <= 0:
                continue

            successor = next(
                (
                    prompt
                    for prompt in prompts
                    if prompt["index"] > item["index"]
                    and int(prompt.get("generation") or 0) == current_generation + 1
                    and int(prompt.get("turn") or 0) in {current_turn, current_turn + 1}
                ),
                None,
            )

            after_state: dict[str, Any] | None = None
            terminal = False
            result: dict[str, Any] = {}
            continuity: dict[str, Any] | None = None

            if isinstance(successor, dict):
                after_state = successor["state"]
                continuity = {
                    "kind": PROMPT_CONTINUITY_KIND,
                    "fromGeneration": current_generation,
                    "toGeneration": int(successor["generation"]),
                    "fromTurn": current_turn,
                    "toTurn": int(successor["turn"]),
                    "afterEventType": "human_choice_observed",
                }
            else:
                later_model_turn = any(
                    other["index"] > item["index"] for other in model_turns
                )
                end = bundle.get("end")
                if (
                    not later_model_turn
                    and isinstance(end, dict)
                    and end["index"] > item["index"]
                ):
                    after_state = end["state"]
                    result = end.get("result") or {}
                    terminal = True
                    continuity = {
                        "kind": TERMINAL_CONTINUITY_KIND,
                        "fromGeneration": current_generation,
                        "fromTurn": current_turn,
                        "afterEventType": "session_end",
                    }

            if not isinstance(after_state, dict):
                continue

            before_model = critic.model_perspective(item["state"])
            after_model = critic.model_perspective(after_state)
            outcome = critic.transition_outcome(before_model, after_model)
            keys = critic.context_keys(
                before_model,
                item["modelAction"],
                item["light"],
            )
            teacher = (
                item.get("teacher")
                if isinstance(item.get("teacher"), dict)
                else teacher_default
            )
            sequence_id = f"g{current_generation}"
            rendered.append(
                {
                    "id": f"{session_id}:{current_turn}:{sequence_id}",
                    "sessionId": session_id,
                    "turn": current_turn,
                    "generation": current_generation,
                    "timestamp": str(item.get("timestamp") or ""),
                    "terminal": terminal,
                    "battleResult": str(result.get("winner") or "") if terminal else "",
                    "modelActor": "nana",
                    "teacher": teacher,
                    "teacherKey": str(teacher.get("key") or "unknown"),
                    "before": before_model,
                    "after": after_model,
                    "modelAction": item["modelAction"],
                    "light": item["light"],
                    "lightValue": critic._safe_float(item["light"].get("value")),
                    "lightSelectedProbability": critic._selected_branch_probability(item["light"]),
                    "keys": keys,
                    "continuity": continuity,
                    **outcome,
                }
            )

    rendered.sort(
        key=lambda item: (
            item.get("timestamp") or "",
            item["sessionId"],
            int(item.get("generation") or 0),
            item["turn"],
        )
    )
    return rendered


def build_actor_summary(
    events: Iterable[dict[str, Any]],
    *,
    actor: str,
    prior_trust: float,
    prior_weight: float,
    model_version: str,
    influence: float = 0.0,
) -> dict[str, Any]:
    """Build the normal critic summary from Nana's prompt-aware outcomes."""

    if actor != "nana":
        return critic.build_actor_summary(
            events,
            actor=actor,
            prior_trust=prior_trust,
            prior_weight=prior_weight,
            model_version=model_version,
            influence=influence,
        )

    observations = extract_observations(events, actor_filter="nana")
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
