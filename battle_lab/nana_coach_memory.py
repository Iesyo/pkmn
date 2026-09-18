"""Model-agnostic, append-only CoachMemory / AdviceStore for Nana.

V1 intentionally supports one safe, useful advice family:
- scope: active species
- condition: a named battle mechanic is legal for that species this turn
- effect: soft preference for legal orders that use that mechanic

Human text is preserved literally. Structured fields are explicit and confirmed
by the UI before creation. Advice never creates actions: it only contributes a
bounded board-delta term to candidates already returned by LegalOrderSource.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable

from battle_lab.nana_recorder import utc_now
from battle_lab.nana_scorer import board_delta_term


COACH_MEMORY_SCHEMA_VERSION = 1
COACH_MEMORY_MODEL_VERSION = "nana-coach-memory-v1"
COACH_EVENT_SESSION = "coach-memory"
COACH_DEFAULT_STRENGTH = 0.20
COACH_MAX_STRENGTH = 0.35
COACH_DEFAULT_CONFIDENCE = 0.50
COACH_DEFAULT_PRIORITY = 1.0
SUPPORTED_MECHANICS = ("Mega", "Tera", "Z-Move", "Dynamax")
_ID_RE = re.compile(r"[^a-z0-9]+")


def _species_id(value: Any) -> str:
    # Scope matches the currently active species exactly after Showdown-style
    # normalization. Do not heuristically strip "mega": Yanmega is a base species,
    # and after a Pokémon has already Mega Evolved the "Mega is legal" condition
    # should naturally become false anyway.
    return _ID_RE.sub("", str(value or "").strip().lower())


def _safe_float(value: Any, default: float) -> float:
    try:
        rendered = float(value)
    except (TypeError, ValueError):
        return default
    if rendered != rendered or rendered in (float("inf"), float("-inf")):
        return default
    return rendered


def coach_memory_contract() -> dict[str, Any]:
    return {
        "schemaVersion": COACH_MEMORY_SCHEMA_VERSION,
        "modelVersion": COACH_MEMORY_MODEL_VERSION,
        "provenance": "user",
        "defaultMode": "soft",
        "supportedMechanics": list(SUPPORTED_MECHANICS),
        "conditionSpecVersion": 1,
        "effectSpecVersion": 1,
        "maxStrengthBoardDelta": COACH_MAX_STRENGTH,
        "legalBoundary": "LegalOrderSource+SafetyGate",
        "teacherIndependent": True,
        "mutation": "append-only-versioned",
    }


def _blank_memory(profile_id: str) -> dict[str, Any]:
    return {
        "schemaVersion": COACH_MEMORY_SCHEMA_VERSION,
        "modelVersion": COACH_MEMORY_MODEL_VERSION,
        "profileId": profile_id,
        "updatedAt": utc_now(),
        "contract": coach_memory_contract(),
        "advices": [],
    }


def _write_memory(profile_root: Path, memory: dict[str, Any]) -> Path:
    destination = Path(profile_root) / "coach_memory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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


def rebuild_coach_memory(
    events: Iterable[dict[str, Any]],
    *,
    profile_id: str,
) -> dict[str, Any]:
    advices: dict[str, dict[str, Any]] = {}
    materialized = [event for event in events if isinstance(event, dict)]
    materialized.sort(
        key=lambda event: (
            str(event.get("timestamp") or ""),
            str(event.get("sessionId") or ""),
            str(event.get("type") or ""),
        )
    )
    for event in materialized:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("type") or "")

        if event_type == "coach_advice_created":
            advice = payload.get("advice") if isinstance(payload.get("advice"), dict) else {}
            advice_id = str(advice.get("adviceId") or "")
            if advice_id:
                advices[advice_id] = json.loads(json.dumps(advice))
            continue

        advice_id = str(payload.get("adviceId") or "")
        if not advice_id or advice_id not in advices:
            continue
        advice = advices[advice_id]

        if event_type == "coach_advice_revoked":
            advice["status"] = "revoked"
            advice["revokedAt"] = str(event.get("timestamp") or "")
        elif event_type == "coach_advice_superseded":
            advice["status"] = "superseded"
            advice["supersededAt"] = str(event.get("timestamp") or "")
            advice["supersededBy"] = str(payload.get("supersededBy") or "")
        elif event_type == "coach_advice_applied":
            evidence = advice.setdefault("evidence", {})
            evidence["applied"] = int(evidence.get("applied") or 0) + 1
            evidence["lastAppliedAt"] = str(event.get("timestamp") or "")
        elif event_type == "coach_advice_skipped":
            evidence = advice.setdefault("evidence", {})
            evidence["skipped"] = int(evidence.get("skipped") or 0) + 1
            evidence["lastSkippedAt"] = str(event.get("timestamp") or "")

    memory = _blank_memory(profile_id)
    memory["advices"] = sorted(
        advices.values(),
        key=lambda item: (str(item.get("createdAt") or ""), str(item.get("adviceId") or "")),
        reverse=True,
    )
    return memory


class CoachMemory:
    def __init__(self, recorder: Any) -> None:
        self.recorder = recorder
        self.memory = self.rebuild()

    def rebuild(self) -> dict[str, Any]:
        memory = rebuild_coach_memory(
            self.recorder.iter_events(),
            profile_id=self.recorder.profile_id,
        )
        _write_memory(self.recorder.profile_root, memory)
        self.memory = memory
        return memory

    def list(self) -> dict[str, Any]:
        return self.memory

    def active(self) -> list[dict[str, Any]]:
        return [
            item for item in self.memory.get("advices") or []
            if isinstance(item, dict) and item.get("status") == "active"
        ]

    def create_species_mechanic(
        self,
        *,
        text: str,
        species: str,
        mechanic: str,
        strength: float = COACH_DEFAULT_STRENGTH,
        priority: float = COACH_DEFAULT_PRIORITY,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        literal = str(text or "").strip()
        species_id = _species_id(species)
        mechanic = str(mechanic or "").strip()
        if not literal:
            raise ValueError("El texto literal del tip es obligatorio.")
        if not species_id:
            raise ValueError("La especie del tip es obligatoria.")
        if mechanic not in SUPPORTED_MECHANICS:
            raise ValueError(f"Mecánica no soportada: {mechanic!r}.")
        bounded_strength = max(
            0.0,
            min(COACH_MAX_STRENGTH, _safe_float(strength, COACH_DEFAULT_STRENGTH)),
        )
        bounded_priority = max(0.1, min(1.0, _safe_float(priority, COACH_DEFAULT_PRIORITY)))
        advice_id = uuid.uuid4().hex[:16]
        now = utc_now()
        advice = {
            "schemaVersion": COACH_MEMORY_SCHEMA_VERSION,
            "adviceId": advice_id,
            "createdAt": now,
            "provenance": "user",
            "status": "active",
            "supersedes": str(supersedes or "") or None,
            "text": literal,
            "scope": {
                "level": "species",
                "species": species_id,
            },
            "condition": {
                "specVersion": 1,
                "kind": "mechanic-legal-for-active-species",
                "mechanic": mechanic,
            },
            "effect": {
                "specVersion": 1,
                "kind": "prefer",
                "target": {
                    "slotSpecies": species_id,
                    "flag": mechanic,
                },
                "strength": bounded_strength,
                "mode": "soft",
            },
            "priority": bounded_priority,
            "confidence": COACH_DEFAULT_CONFIDENCE,
            "evidence": {
                "applied": 0,
                "skipped": 0,
                "positive": 0,
                "negative": 0,
                "lastAppliedAt": None,
            },
        }
        self.recorder.append_event(
            COACH_EVENT_SESSION,
            "coach_advice_created",
            {"advice": advice},
        )
        if supersedes:
            self.recorder.append_event(
                COACH_EVENT_SESSION,
                "coach_advice_superseded",
                {
                    "adviceId": str(supersedes),
                    "supersededBy": advice_id,
                },
            )
        self.rebuild()
        return advice

    def revoke(self, advice_id: str) -> dict[str, Any]:
        advice_id = str(advice_id or "").strip()
        current = next(
            (
                item for item in self.memory.get("advices") or []
                if isinstance(item, dict) and item.get("adviceId") == advice_id
            ),
            None,
        )
        if current is None:
            raise KeyError(advice_id)
        if current.get("status") != "active":
            return current
        self.recorder.append_event(
            COACH_EVENT_SESSION,
            "coach_advice_revoked",
            {"adviceId": advice_id},
        )
        self.rebuild()
        return next(
            item for item in self.memory["advices"]
            if item.get("adviceId") == advice_id
        )


def _slot_species(model_state: dict[str, Any]) -> list[str]:
    active = model_state.get("ownActive") if isinstance(model_state.get("ownActive"), list) else []
    return [
        _species_id(item.get("species"))
        if isinstance(item, dict)
        else ""
        for item in active[:2]
    ]


def _action_halves(action: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        action.get("first") if isinstance(action.get("first"), dict) else {},
        action.get("second") if isinstance(action.get("second"), dict) else {},
    ]


def _advice_match_for_action(
    advice: dict[str, Any],
    *,
    model_state: dict[str, Any],
    action: dict[str, Any],
) -> bool:
    species = _species_id((advice.get("scope") or {}).get("species"))
    mechanic = str((advice.get("condition") or {}).get("mechanic") or "")
    slots = _slot_species(model_state)
    halves = _action_halves(action)
    for index, slot_species in enumerate(slots):
        if slot_species != species or index >= len(halves):
            continue
        flags = [str(flag) for flag in halves[index].get("flags") or []]
        if mechanic in flags:
            return True
    return False


def coach_context(
    memory: dict[str, Any] | None,
    *,
    model_state: dict[str, Any],
    legal_actions: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    advices = [
        item for item in ((memory or {}).get("advices") or [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    legal = [item for item in legal_actions if isinstance(item, dict)]
    applicable: list[dict[str, Any]] = []
    inactive: list[dict[str, Any]] = []

    for advice in advices:
        if any(
            _advice_match_for_action(advice, model_state=model_state, action=action)
            for action in legal
        ):
            applicable.append(advice)
        else:
            inactive.append(advice)
    return {
        "applicable": applicable,
        "conditionFalse": inactive,
    }


def coach_terms_for_action(
    context: dict[str, Any],
    *,
    model_state: dict[str, Any],
    action: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    terms: list[Any] = []
    matched: list[str] = []
    for advice in context.get("applicable") or []:
        if not isinstance(advice, dict):
            continue
        if not _advice_match_for_action(advice, model_state=model_state, action=action):
            continue
        effect = advice.get("effect") if isinstance(advice.get("effect"), dict) else {}
        strength = max(
            0.0,
            min(COACH_MAX_STRENGTH, _safe_float(effect.get("strength"), COACH_DEFAULT_STRENGTH)),
        )
        confidence = max(
            0.0,
            min(1.0, _safe_float(advice.get("confidence"), COACH_DEFAULT_CONFIDENCE)),
        )
        priority = max(
            0.1,
            min(1.0, _safe_float(advice.get("priority"), COACH_DEFAULT_PRIORITY)),
        )
        advice_id = str(advice.get("adviceId") or "")
        terms.append(
            board_delta_term(
                name=f"coach:{advice_id}",
                value=strength,
                confidence=confidence,
                weight=priority,
                source=f"coach-user:{advice_id}",
            )
        )
        matched.append(advice_id)
    return terms, matched


def coach_trace_for_selection(
    context: dict[str, Any],
    *,
    model_state: dict[str, Any],
    selected_action: dict[str, Any] | None,
) -> dict[str, Any]:
    selected_action = selected_action if isinstance(selected_action, dict) else {}
    matched = {
        advice_id
        for advice_id in (
            coach_terms_for_action(
                context,
                model_state=model_state,
                action=selected_action,
            )[1]
        )
    }
    applicable = [
        str(item.get("adviceId") or "")
        for item in context.get("applicable") or []
        if isinstance(item, dict)
    ]
    condition_false = [
        str(item.get("adviceId") or "")
        for item in context.get("conditionFalse") or []
        if isinstance(item, dict)
    ]
    return {
        "applicable": applicable,
        "matchedSelected": sorted(matched),
        "skipped": sorted(set(applicable) - matched),
        "conditionFalse": condition_false,
    }
