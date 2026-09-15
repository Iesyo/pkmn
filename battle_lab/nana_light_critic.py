"""Contextual observational critic for model decisions.

LIGHT's critic is derived from Nana's append-only battle history. It learns from
the branch that actually happened and therefore never treats a bad transition as
causal proof that an unplayed move was better. Evidence is additionally scoped
by teacher checkpoint/regulation so a future, better LIGHT starts with a fresh
strong prior instead of inheriting stale distrust from an older checkpoint.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from battle_lab.nana_recorder import utc_now
from battle_lab.nana_teacher import legacy_teacher_key


MODEL_VERSION = "light-critic-v2-teacher-aware"
SCHEMA_VERSION = 2
PRIOR_TRUST = 0.90
PRIOR_WEIGHT = 16.0
RECENCY_HALF_LIFE = 64.0
POSITIVE_DELTA = 0.15
NEGATIVE_DELTA = -0.15


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    return rendered if math.isfinite(rendered) else default


def _to_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def model_perspective(state: dict[str, Any] | None) -> dict[str, Any]:
    """Convert the human-side recorder snapshot to model perspective."""

    state = state if isinstance(state, dict) else {}
    return {
        "tag": str(state.get("tag") or ""),
        "turn": int(state.get("turn") or 0),
        "finished": bool(state.get("finished", False)),
        "won": bool(state.get("lost", False)),
        "lost": bool(state.get("won", False)),
        "weather": list(state.get("weather") or []),
        "fields": list(state.get("fields") or []),
        "ownActive": list(state.get("opponentActive") or []),
        "opponentActive": list(state.get("ownActive") or []),
        "ownTeam": list(state.get("opponentTeam") or []),
        "opponentTeam": list(state.get("ownTeam") or []),
    }


def _team_stats(team: Any) -> dict[str, float]:
    values = team if isinstance(team, list) else []
    alive = 0
    hp = 0.0
    statuses = 0
    for mon in values:
        if not isinstance(mon, dict):
            continue
        fainted = bool(mon.get("fainted", False))
        if not fainted:
            alive += 1
        hp += max(0.0, min(100.0, _safe_float(mon.get("hp")))) / 100.0
        status = mon.get("status")
        if status not in (None, "", "None", "none") and not fainted:
            statuses += 1
    return {"alive": float(alive), "hp": hp, "statuses": float(statuses)}


def board_score(state: dict[str, Any] | None) -> float:
    """Transparent local board proxy from the model's perspective."""

    state = state if isinstance(state, dict) else {}
    own = _team_stats(state.get("ownTeam"))
    opp = _team_stats(state.get("opponentTeam"))
    alive_delta = own["alive"] - opp["alive"]
    hp_delta = own["hp"] - opp["hp"]
    status_delta = opp["statuses"] - own["statuses"]
    return 1.75 * alive_delta + 0.35 * hp_delta + 0.12 * status_delta


def transition_outcome(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    before_score = board_score(before)
    after_score = board_score(after)
    delta = after_score - before_score
    if delta > POSITIVE_DELTA:
        label = "positive"
        outcome_score = 1.0
        base_weight = min(1.0, 0.55 + abs(delta) / 2.5)
    elif delta < NEGATIVE_DELTA:
        label = "negative"
        outcome_score = 0.0
        base_weight = min(1.0, 0.55 + abs(delta) / 2.5)
    else:
        label = "neutral"
        outcome_score = PRIOR_TRUST
        base_weight = 0.10
    return {
        "beforeScore": before_score,
        "afterScore": after_score,
        "delta": delta,
        "outcomeScore": outcome_score,
        "label": label,
        "baseWeight": base_weight,
    }


def _active_species(state: dict[str, Any], key: str) -> tuple[str, ...]:
    values: list[str] = []
    for mon in state.get(key) or []:
        if not isinstance(mon, dict):
            continue
        species = _to_id(mon.get("species") or mon.get("name"))
        if species:
            values.append(species)
    return tuple(sorted(values))


def _alive_delta(state: dict[str, Any]) -> int:
    own = _team_stats(state.get("ownTeam"))
    opp = _team_stats(state.get("opponentTeam"))
    return int(own["alive"] - opp["alive"])


def _hp_delta(state: dict[str, Any]) -> float:
    own = _team_stats(state.get("ownTeam"))
    opp = _team_stats(state.get("opponentTeam"))
    return own["hp"] - opp["hp"]


def _turn_bucket(turn: int) -> str:
    if turn <= 2:
        return "opening"
    if turn <= 5:
        return "mid"
    return "late"


def _material_bucket(delta: int) -> str:
    if delta > 0:
        return "ahead"
    if delta < 0:
        return "behind"
    return "even"


def _hp_bucket(delta: float) -> str:
    if delta >= 0.75:
        return "ahead"
    if delta <= -0.75:
        return "behind"
    return "near"


def _selected_branch_probability(light: dict[str, Any] | None) -> float:
    light = light if isinstance(light, dict) else {}
    selected: list[float] = []
    for branch in light.get("branches") or []:
        if not isinstance(branch, dict):
            continue
        selected_item = next(
            (
                item
                for item in branch.get("scores") or []
                if isinstance(item, dict) and item.get("selected") is True
            ),
            None,
        )
        if isinstance(selected_item, dict):
            probability = _safe_float(selected_item.get("probability"))
            if probability > 0:
                selected.append(probability)
    probability = 1.0
    for value in selected:
        probability *= value
    return probability if selected else 0.0


def _light_confidence_bucket(light: dict[str, Any] | None) -> str:
    probability = _selected_branch_probability(light)
    if probability >= 0.40:
        return "high"
    if probability >= 0.15:
        return "medium"
    return "low"


def _canonical_labels(
    action: dict[str, Any] | None,
    light: dict[str, Any] | None,
) -> list[str]:
    action = action if isinstance(action, dict) else {}
    labels = action.get("labels")
    if not isinstance(labels, list):
        canonical = light.get("canonicalAction") if isinstance(light, dict) else None
        labels = canonical.get("labels") if isinstance(canonical, dict) else None
    return [str(value or "") for value in labels] if isinstance(labels, list) else []


def _action_family(label: str) -> str:
    text = str(label or "").lower()
    if "switch" in text:
        return "switch"
    if "pass" in text:
        return "pass"
    if "move" in text or "attack" in text:
        return "move"
    return _to_id(text)[:24] or "other"


def _structured_halves(action: dict[str, Any] | None) -> list[dict[str, Any]]:
    action = action if isinstance(action, dict) else {}
    halves: list[dict[str, Any]] = []
    for key in ("first", "second"):
        value = action.get(key)
        if isinstance(value, dict):
            halves.append(value)
    return halves


def action_context(
    action: dict[str, Any] | None,
    light: dict[str, Any] | None,
) -> dict[str, str]:
    halves = _structured_halves(action)
    if halves:
        coarse_parts: list[str] = []
        exact_parts: list[str] = []
        for half in halves:
            kind = _to_id(half.get("kind")) or "other"
            value = _to_id(half.get("value")) or "unknown"
            target = int(half.get("target") or 0)
            flags = "+".join(sorted(_to_id(flag) for flag in half.get("flags") or [] if _to_id(flag)))
            coarse_parts.append(kind)
            exact_parts.append(f"{kind}:{value}:t{target}:{flags or '-'}")
        return {
            "coarse": "+".join(coarse_parts),
            "exact": "+".join(exact_parts),
        }

    labels = _canonical_labels(action, light)
    coarse = "+".join(_action_family(label) for label in labels) or "unknown"
    exact = "+".join(_to_id(label)[:48] or "unknown" for label in labels) or "unknown"
    return {"coarse": coarse, "exact": exact}


def context_keys(
    state: dict[str, Any],
    action: dict[str, Any] | None,
    light: dict[str, Any] | None,
) -> dict[str, str]:
    turn = int(state.get("turn") or 0)
    action_key = action_context(action, light)
    own_active = "+".join(_active_species(state, "ownActive")) or "unknown"
    opp_active = "+".join(_active_species(state, "opponentActive")) or "unknown"
    common = (
        f"turn={_turn_bucket(turn)}|material={_material_bucket(_alive_delta(state))}"
        f"|hp={_hp_bucket(_hp_delta(state))}|light={_light_confidence_bucket(light)}"
        f"|action={action_key['coarse']}"
    )
    return {
        "global": "global",
        "coarse": common,
        "matchup": f"{common}|own={own_active}|opp={opp_active}",
        "exact": f"{common}|own={own_active}|opp={opp_active}|exact={action_key['exact']}",
    }


def _event_timestamp(event: dict[str, Any]) -> str:
    return str(event.get("timestamp") or "")


def _legacy_teacher(context: dict[str, Any]) -> dict[str, Any]:
    battle_format = str(context.get("format") or "unknown")
    checkpoint = str(context.get("checkpoint") or "unknown")
    return {
        "key": legacy_teacher_key(checkpoint=checkpoint, battle_format=battle_format),
        "legacyKey": legacy_teacher_key(checkpoint=checkpoint, battle_format=battle_format),
        "format": battle_format,
        "checkpoint": checkpoint,
        "checkpointSha256": "",
        "policy": "Battle Lab LIGHT M-C",
        "legacy": True,
    }


def extract_observations(
    events: Iterable[dict[str, Any]],
    *,
    actor_filter: str | None = "light",
) -> list[dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"turns": [], "end": None, "teacher": None, "context": {}}
    )
    for event in events:
        if not isinstance(event, dict):
            continue
        session_id = str(event.get("sessionId") or "")
        if not session_id:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = event.get("type")
        if event_type == "session_start":
            context = payload.get("context")
            if isinstance(context, dict):
                sessions[session_id]["context"] = context
        elif event_type == "nana_teacher_version":
            teacher = payload.get("teacher")
            if isinstance(teacher, dict) and teacher.get("key"):
                sessions[session_id]["teacher"] = teacher
        elif event_type == "turn_choice":
            state = payload.get("state")
            model_action = payload.get("modelAction")
            light = payload.get("light")
            turn = payload.get("turn")
            model_actor = str(payload.get("modelActor") or "light")
            teacher = payload.get("teacher")
            if (
                isinstance(state, dict)
                and isinstance(model_action, dict)
                and isinstance(turn, int)
                and turn > 0
            ):
                sessions[session_id]["turns"].append(
                    {
                        "turn": turn,
                        "timestamp": _event_timestamp(event),
                        "state": state,
                        "modelAction": model_action,
                        "modelActor": model_actor,
                        "teacher": teacher if isinstance(teacher, dict) else None,
                        "light": light if isinstance(light, dict) else {},
                    }
                )
        elif event_type == "session_end":
            final_state = payload.get("finalState")
            if isinstance(final_state, dict):
                sessions[session_id]["end"] = {
                    "timestamp": _event_timestamp(event),
                    "state": final_state,
                    "result": payload.get("result") if isinstance(payload.get("result"), dict) else {},
                }

    rendered: list[dict[str, Any]] = []
    for session_id, bundle in sessions.items():
        teacher_default = bundle.get("teacher")
        if not isinstance(teacher_default, dict):
            teacher_default = _legacy_teacher(bundle.get("context") or {})
        turns = sorted(bundle["turns"], key=lambda item: (item["turn"], item["timestamp"]))
        for index, item in enumerate(turns):
            if actor_filter is not None and item.get("modelActor") != actor_filter:
                continue
            after_state: dict[str, Any] | None = None
            terminal = False
            result: dict[str, Any] = {}
            if index + 1 < len(turns):
                next_item = turns[index + 1]
                if int(next_item["turn"]) > int(item["turn"]):
                    after_state = next_item["state"]
            elif isinstance(bundle.get("end"), dict):
                after_state = bundle["end"]["state"]
                result = bundle["end"].get("result") or {}
                terminal = True
            if not isinstance(after_state, dict):
                continue

            before_model = model_perspective(item["state"])
            after_model = model_perspective(after_state)
            outcome = transition_outcome(before_model, after_model)
            keys = context_keys(before_model, item["modelAction"], item["light"])
            teacher = item.get("teacher") if isinstance(item.get("teacher"), dict) else teacher_default
            rendered.append(
                {
                    "id": f"{session_id}:{int(item['turn'])}",
                    "sessionId": session_id,
                    "turn": int(item["turn"]),
                    "timestamp": str(item["timestamp"] or ""),
                    "terminal": terminal,
                    "battleResult": str(result.get("winner") or "") if terminal else "",
                    "modelActor": str(item.get("modelActor") or "light"),
                    "teacher": teacher,
                    "teacherKey": str(teacher.get("key") or "unknown"),
                    "before": before_model,
                    "after": after_model,
                    "modelAction": item["modelAction"],
                    "light": item["light"],
                    "lightValue": _safe_float(item["light"].get("value")),
                    "lightSelectedProbability": _selected_branch_probability(item["light"]),
                    "keys": keys,
                    **outcome,
                }
            )

    rendered.sort(key=lambda item: (item.get("timestamp") or "", item["sessionId"], item["turn"]))
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


def _render_bucket(
    bucket: dict[str, Any],
    *,
    prior_trust: float,
    prior_weight: float,
) -> dict[str, Any]:
    weight = _safe_float(bucket.get("effectiveWeight"))
    posterior = (
        prior_trust * prior_weight + _safe_float(bucket.get("weightedOutcome"))
    ) / (prior_weight + weight)
    confidence = weight / (prior_weight + weight)
    return {
        "level": bucket["level"],
        "key": bucket["key"],
        "samples": int(bucket["samples"]),
        "effectiveWeight": weight,
        "trust": max(0.0, min(1.0, posterior)),
        "confidence": max(0.0, min(1.0, confidence)),
        "meanDelta": _safe_float(bucket.get("weightedDelta")) / weight if weight > 0 else 0.0,
        "positive": int(bucket["positive"]),
        "neutral": int(bucket["neutral"]),
        "negative": int(bucket["negative"]),
        "lastTimestamp": str(bucket.get("lastTimestamp") or ""),
    }


def _recent_summary(
    observations: list[dict[str, Any]],
    count: int,
    *,
    prior_trust: float,
) -> dict[str, Any]:
    selected = observations[-count:]
    if not selected:
        return {
            "n": 0,
            "meanDelta": None,
            "meanOutcomeScore": None,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
        }
    def outcome(item: dict[str, Any]) -> float:
        if item.get("label") == "positive":
            return 1.0
        if item.get("label") == "negative":
            return 0.0
        return prior_trust
    return {
        "n": len(selected),
        "meanDelta": sum(_safe_float(item.get("delta")) for item in selected) / len(selected),
        "meanOutcomeScore": sum(outcome(item) for item in selected) / len(selected),
        "positive": sum(item.get("label") == "positive" for item in selected),
        "neutral": sum(item.get("label") == "neutral" for item in selected),
        "negative": sum(item.get("label") == "negative" for item in selected),
    }


def _summary_from_observations(
    observations: list[dict[str, Any]],
    *,
    prior_trust: float,
    prior_weight: float,
    model_version: str,
    influence: float,
) -> dict[str, Any]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    total = len(observations)
    for index, observation in enumerate(observations):
        age = total - 1 - index
        recency = 0.5 ** (age / RECENCY_HALF_LIFE) if RECENCY_HALF_LIFE > 0 else 1.0
        effective_weight = _safe_float(observation.get("baseWeight"), 0.10) * recency
        if observation.get("label") == "positive":
            outcome_score = 1.0
        elif observation.get("label") == "negative":
            outcome_score = 0.0
        else:
            outcome_score = prior_trust
        for level, key in (observation.get("keys") or {}).items():
            bucket_key = (str(level), str(key))
            bucket = buckets.setdefault(bucket_key, _blank_bucket(str(level), str(key)))
            bucket["samples"] += 1
            bucket["effectiveWeight"] += effective_weight
            bucket["weightedOutcome"] += effective_weight * outcome_score
            bucket["weightedDelta"] += effective_weight * _safe_float(observation.get("delta"))
            label = str(observation.get("label") or "neutral")
            if label in {"positive", "neutral", "negative"}:
                bucket[label] += 1
            bucket["lastTimestamp"] = max(
                str(bucket.get("lastTimestamp") or ""),
                str(observation.get("timestamp") or ""),
            )

    rendered_buckets = {
        f"{level}:{key}": _render_bucket(
            bucket,
            prior_trust=prior_trust,
            prior_weight=prior_weight,
        )
        for (level, key), bucket in buckets.items()
    }
    global_bucket = rendered_buckets.get("global:global") or _render_bucket(
        _blank_bucket("global", "global"),
        prior_trust=prior_trust,
        prior_weight=prior_weight,
    )
    labels = {
        "positive": sum(item.get("label") == "positive" for item in observations),
        "neutral": sum(item.get("label") == "neutral" for item in observations),
        "negative": sum(item.get("label") == "negative" for item in observations),
    }
    level_counts = {
        level: sum(1 for bucket in rendered_buckets.values() if bucket.get("level") == level)
        for level in ("coarse", "matchup", "exact")
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "modelVersion": model_version,
        "rebuiltAt": utc_now(),
        "causalStatus": "observational-only",
        "influence": influence,
        "prior": {"trust": prior_trust, "weight": prior_weight},
        "recency": {"halfLifeDecisions": RECENCY_HALF_LIFE},
        "observations": total,
        "labels": labels,
        "global": global_bucket,
        "contextCounts": level_counts,
        "recent10": _recent_summary(observations, 10, prior_trust=prior_trust),
        "recent30": _recent_summary(observations, 30, prior_trust=prior_trust),
        "buckets": rendered_buckets,
        "observationIds": [str(item.get("id") or "") for item in observations],
    }


def build_actor_summary(
    events: Iterable[dict[str, Any]],
    *,
    actor: str,
    prior_trust: float,
    prior_weight: float,
    model_version: str,
    influence: float = 0.0,
) -> dict[str, Any]:
    observations = extract_observations(events, actor_filter=actor)
    summary = _summary_from_observations(
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
        child = _summary_from_observations(
            items,
            prior_trust=prior_trust,
            prior_weight=prior_weight,
            model_version=model_version,
            influence=influence,
        )
        child["teacher"] = teacher_meta.get(key) or {"key": key}
        summary["teachers"][key] = child
    return summary


def build_summary(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return build_actor_summary(
        events,
        actor="light",
        prior_trust=PRIOR_TRUST,
        prior_weight=PRIOR_WEIGHT,
        model_version=MODEL_VERSION,
        influence=0.0,
    )


def trust_for(
    summary: dict[str, Any],
    state: dict[str, Any],
    action: dict[str, Any] | None,
    light: dict[str, Any] | None,
    *,
    teacher_key: str | None = None,
    fallback_teacher_key: str | None = None,
) -> dict[str, Any]:
    """Hierarchical read-only trust query with optional teacher isolation."""

    prior = summary.get("prior") if isinstance(summary.get("prior"), dict) else {}
    prior_trust = max(0.0, min(1.0, _safe_float(prior.get("trust"), PRIOR_TRUST)))
    source = summary
    teacher_source = "all-history"
    if teacher_key:
        teachers = summary.get("teachers") if isinstance(summary.get("teachers"), dict) else {}
        exact = teachers.get(teacher_key)
        fallback = teachers.get(fallback_teacher_key) if fallback_teacher_key else None
        if isinstance(exact, dict):
            source = exact
            teacher_source = "exact"
        elif isinstance(fallback, dict):
            source = fallback
            teacher_source = "legacy-fallback"
        else:
            source = {"buckets": {}, "prior": prior, "observations": 0}
            teacher_source = "fresh-prior"

    keys = context_keys(state, action, light)
    buckets = source.get("buckets") if isinstance(source.get("buckets"), dict) else {}
    level_weights = {"global": 0.10, "coarse": 0.25, "matchup": 0.30, "exact": 0.35}
    components: list[dict[str, Any]] = []
    weighted_trust = 0.0
    weight_total = 0.0
    for level in ("global", "coarse", "matchup", "exact"):
        bucket = buckets.get(f"{level}:{keys[level]}")
        if not isinstance(bucket, dict):
            continue
        confidence = max(0.0, min(1.0, _safe_float(bucket.get("confidence"))))
        level_weight = level_weights[level] * max(0.05, confidence)
        trust = max(0.0, min(1.0, _safe_float(bucket.get("trust"), prior_trust)))
        weighted_trust += level_weight * trust
        weight_total += level_weight
        components.append(
            {
                "level": level,
                "key": keys[level],
                "trust": trust,
                "confidence": confidence,
                "samples": int(bucket.get("samples") or 0),
            }
        )
    blended = weighted_trust / weight_total if weight_total > 0 else prior_trust
    max_confidence = max((item["confidence"] for item in components), default=0.0)
    if teacher_source == "legacy-fallback":
        max_confidence = min(max_confidence, 0.15)
    return {
        "trust": blended,
        "confidence": max_confidence,
        "components": components,
        "teacherSource": teacher_source,
        "teacherKey": teacher_key,
        "causalStatus": "observational-only",
    }


def write_summary(profile_root: Path, summary: dict[str, Any]) -> Path:
    destination = Path(profile_root) / "light_critic.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return destination


def rebuild_for_recorder(recorder: Any) -> dict[str, Any]:
    summary = build_summary(recorder.iter_events())
    write_summary(recorder.profile_root, summary)
    return summary
