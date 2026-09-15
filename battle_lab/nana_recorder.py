"""Persistent, append-only recorder for Nana 0.

Nana 0 observes Sparring without influencing LIGHT. Each profile receives an
append-only JSONL history and a derived habit summary that can be rebuilt from
that history at any time.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_PROFILE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_profile_id(value: str) -> str:
    normalized = _PROFILE_RE.sub("-", value.strip()).strip("-.")
    return normalized[:80] or "default"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class NanaRecorder:
    """Append-only personal Sparring recorder.

    Event history is the source of truth. ``habits.json`` is only a rebuildable
    cache for UI/dashboard consumption, so personal adaptation remains
    reversible and auditable.
    """

    def __init__(self, root: Path, *, profile_id: str = "default") -> None:
        self.root = Path(root).expanduser().resolve()
        self.profile_id = safe_profile_id(profile_id)
        self.profile_root = self.root / "profiles" / self.profile_id
        self.sessions_root = self.profile_root / "sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    def session_path(self, session_id: str) -> Path:
        safe_session = safe_profile_id(session_id)
        return self.sessions_root / f"{safe_session}.jsonl"

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "schemaVersion": 1,
            "timestamp": timestamp or utc_now(),
            "profileId": self.profile_id,
            "sessionId": session_id,
            "type": event_type,
            "payload": _json_safe(payload or {}),
        }
        path = self.session_path(session_id)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def start_session(
        self,
        session_id: str,
        *,
        opponent: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.append_event(
            session_id,
            "session_start",
            {"opponent": opponent, "context": context or {}},
        )

    def record_team_preview(
        self,
        session_id: str,
        *,
        side: str,
        order: list[int],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return self.append_event(
            session_id,
            "team_preview",
            {"side": side, "order": order, "state": state},
        )

    def record_turn(
        self,
        session_id: str,
        *,
        turn: int,
        state: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        human_action: dict[str, Any],
        model_action: dict[str, Any] | None = None,
        light: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.append_event(
            session_id,
            "turn_choice",
            {
                "turn": int(turn),
                "state": state,
                "legalActions": legal_actions,
                "humanAction": human_action,
                "modelAction": model_action,
                "light": light,
            },
        )

    def finish_session(
        self,
        session_id: str,
        *,
        result: dict[str, Any],
        final_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.append_event(
            session_id,
            "session_end",
            {"result": result, "finalState": final_state or {}},
        )
        self.rebuild_habits()
        return record

    def iter_events(self) -> Iterable[dict[str, Any]]:
        for path in sorted(self.sessions_root.glob("*.jsonl")):
            with path.open("r", encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as error:
                        raise RuntimeError(
                            f"Nana history is corrupt at {path}:{line_number}."
                        ) from error

    def rebuild_habits(self) -> dict[str, Any]:
        sessions: set[str] = set()
        completed = 0
        wins = 0
        losses = 0
        ties = 0
        turns = 0
        action_kinds: Counter[str] = Counter()
        move_values: Counter[str] = Counter()
        switch_values: Counter[str] = Counter()
        gimmicks: Counter[str] = Counter()
        targets: Counter[str] = Counter()

        for event in self.iter_events():
            session_id = str(event.get("sessionId") or "")
            if session_id:
                sessions.add(session_id)
            event_type = event.get("type")
            payload = event.get("payload") or {}
            if event_type == "session_end":
                completed += 1
                winner = (payload.get("result") or {}).get("winner")
                if winner == "human":
                    wins += 1
                elif winner == "model":
                    losses += 1
                else:
                    ties += 1
                continue
            if event_type != "turn_choice":
                continue
            turns += 1
            human = payload.get("humanAction") or {}
            for half in (human.get("first"), human.get("second")):
                if not isinstance(half, dict):
                    continue
                kind = str(half.get("kind") or "unknown")
                value = str(half.get("value") or "")
                action_kinds[kind] += 1
                if kind == "move" and value:
                    move_values[value] += 1
                if kind == "switch" and value:
                    switch_values[value] += 1
                target = half.get("target")
                if target not in (None, 0, ""):
                    targets[str(target)] += 1
                for gimmick in half.get("flags") or []:
                    gimmicks[str(gimmick)] += 1

        summary = {
            "schemaVersion": 1,
            "profileId": self.profile_id,
            "rebuiltAt": utc_now(),
            "sessions": len(sessions),
            "completedSessions": completed,
            "results": {"wins": wins, "losses": losses, "ties": ties},
            "turnChoices": turns,
            "actionKinds": dict(action_kinds.most_common()),
            "moves": dict(move_values.most_common()),
            "switches": dict(switch_values.most_common()),
            "gimmicks": dict(gimmicks.most_common()),
            "targets": dict(targets.most_common()),
        }
        destination = self.profile_root / "habits.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
        return summary
