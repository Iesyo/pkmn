"""Audit the latest Nana 0 Sparring recording.

This command is intentionally read-only. It validates that the observational
Nana layer persisted a coherent completed battle without requiring a human to
inspect JSONL files manually.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


DEFAULT_RUNTIME_ROOT = (
    Path.home() / ".local" / "share" / "like-no-one-ever-was" / "battle-lab"
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"JSONL corrupto en {path}:{line_number}: {error.msg}."
                ) from error
            if not isinstance(event, dict):
                raise RuntimeError(
                    f"Evento inválido en {path}:{line_number}: se esperaba objeto JSON."
                )
            events.append(event)
    return events


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _valid_preview(event: dict[str, Any], side: str) -> bool:
    payload = _event_payload(event)
    if payload.get("side") != side:
        return False
    order = payload.get("order")
    return (
        isinstance(order, list)
        and len(order) == 4
        and len(set(order)) == 4
        and all(isinstance(value, int) and 1 <= value <= 6 for value in order)
    )


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _turn_check(event: dict[str, Any]) -> tuple[bool, list[str]]:
    payload = _event_payload(event)
    turn = payload.get("turn")
    prefix = f"turno {turn}" if isinstance(turn, int) else "turno ?"
    issues: list[str] = []

    if not isinstance(turn, int) or turn < 0:
        issues.append(f"{prefix}: número de turno inválido")

    legal = payload.get("legalActions")
    human = payload.get("humanAction")
    model = payload.get("modelAction")
    light = payload.get("light")

    if not isinstance(legal, list) or not legal:
        issues.append(f"{prefix}: no hay acciones legales registradas")
        legal = []
    if not isinstance(human, dict):
        issues.append(f"{prefix}: falta humanAction")
        human = {}
    else:
        legal_ids = {
            candidate.get("id")
            for candidate in legal
            if isinstance(candidate, dict) and candidate.get("id") is not None
        }
        if human.get("id") not in legal_ids:
            issues.append(f"{prefix}: humanAction no pertenece a legalActions")
        for slot in ("first", "second"):
            half = human.get(slot)
            if not isinstance(half, dict):
                issues.append(f"{prefix}: humanAction.{slot} ausente")
            elif not str(half.get("kind") or ""):
                issues.append(f"{prefix}: humanAction.{slot}.kind ausente")

    if not isinstance(light, dict):
        issues.append(f"{prefix}: falta diagnóstico LIGHT")
        light = {}
    else:
        if light.get("waiting") is not False:
            issues.append(f"{prefix}: LIGHT quedó marcado como waiting")
        if light.get("selectionRule") != "sequential-greedy":
            issues.append(f"{prefix}: regla LIGHT inesperada")
        if light.get("branch2ConditionedOnFirst") is not True:
            issues.append(f"{prefix}: branch 2 no figura condicionada a branch 1")
        if not _finite_number(light.get("value")):
            issues.append(f"{prefix}: value de LIGHT inválido")

        canonical = light.get("canonicalAction")
        if not isinstance(canonical, dict):
            issues.append(f"{prefix}: canonicalAction de LIGHT ausente")
        else:
            indices = canonical.get("indices")
            labels = canonical.get("labels")
            if not (
                isinstance(indices, list)
                and len(indices) == 2
                and all(isinstance(index, int) for index in indices)
            ):
                issues.append(f"{prefix}: índices canónicos inválidos")
            if not (
                isinstance(labels, list)
                and len(labels) == 2
                and all(isinstance(label, str) and label for label in labels)
            ):
                issues.append(f"{prefix}: labels canónicos inválidos")
            if model != canonical:
                issues.append(f"{prefix}: modelAction no coincide con canonicalAction")

        branches = light.get("branches")
        if not isinstance(branches, list) or len(branches) != 2:
            issues.append(f"{prefix}: distribución LIGHT no tiene dos ramas")
        else:
            for branch_index, branch in enumerate(branches, start=1):
                if not isinstance(branch, dict):
                    issues.append(f"{prefix}: rama {branch_index} inválida")
                    continue
                scores = branch.get("scores")
                selected_index = branch.get("selectedIndex")
                if not isinstance(scores, list) or not scores:
                    issues.append(f"{prefix}: rama {branch_index} sin scores legales")
                    continue
                selected = [
                    score
                    for score in scores
                    if isinstance(score, dict) and score.get("selected") is True
                ]
                if len(selected) != 1:
                    issues.append(
                        f"{prefix}: rama {branch_index} no tiene exactamente un score seleccionado"
                    )
                elif selected[0].get("index") != selected_index:
                    issues.append(
                        f"{prefix}: rama {branch_index} selectedIndex no coincide con score seleccionado"
                    )
                for score in scores:
                    if not isinstance(score, dict):
                        issues.append(f"{prefix}: rama {branch_index} contiene score inválido")
                        break
                    probability = score.get("probability")
                    if not _finite_number(probability) or not 0 <= float(probability) <= 1:
                        issues.append(
                            f"{prefix}: rama {branch_index} contiene probabilidad inválida"
                        )
                        break

    return not issues, issues


def _profile_counts(sessions_root: Path) -> dict[str, int]:
    sessions: set[str] = set()
    completed = 0
    turns = 0
    wins = 0
    losses = 0
    ties = 0
    for path in sorted(sessions_root.glob("*.jsonl")):
        for event in _load_jsonl(path):
            session_id = str(event.get("sessionId") or "")
            if session_id:
                sessions.add(session_id)
            event_type = event.get("type")
            if event_type == "turn_choice":
                turns += 1
            elif event_type == "session_end":
                completed += 1
                winner = (_event_payload(event).get("result") or {}).get("winner")
                if winner == "human":
                    wins += 1
                elif winner == "model":
                    losses += 1
                else:
                    ties += 1
    return {
        "sessions": len(sessions),
        "completedSessions": completed,
        "turnChoices": turns,
        "wins": wins,
        "losses": losses,
        "ties": ties,
    }


def audit_latest(runtime_root: Path, profile_id: str) -> dict[str, Any]:
    profile_root = runtime_root / "nana" / "profiles" / profile_id
    sessions_root = profile_root / "sessions"
    issues: list[str] = []
    warnings: list[str] = []

    if not sessions_root.is_dir():
        return {
            "pass": False,
            "profileId": profile_id,
            "issues": [f"No existe el directorio de sesiones: {sessions_root}"],
            "warnings": [],
        }

    session_paths = sorted(
        sessions_root.glob("*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not session_paths:
        return {
            "pass": False,
            "profileId": profile_id,
            "issues": ["No hay sesiones JSONL para auditar."],
            "warnings": [],
        }

    session_path = session_paths[0]
    events = _load_jsonl(session_path)
    event_types = Counter(str(event.get("type") or "") for event in events)
    session_ids = {str(event.get("sessionId") or "") for event in events}
    session_ids.discard("")
    session_id = next(iter(session_ids)) if len(session_ids) == 1 else ""

    if len(session_ids) != 1:
        issues.append("El JSONL mezcla cero o múltiples sessionId.")
    if event_types["session_start"] != 1:
        issues.append("Debe existir exactamente un session_start.")
    if event_types["session_abort"]:
        issues.append("La sesión contiene session_abort.")
    if event_types["session_end"] != 1:
        issues.append("Debe existir exactamente un session_end.")

    previews = [event for event in events if event.get("type") == "team_preview"]
    if not any(_valid_preview(event, "human") for event in previews):
        issues.append("Falta Team Preview humano válido.")
    if not any(_valid_preview(event, "model") for event in previews):
        issues.append("Falta Team Preview de LIGHT válido.")

    turns = [event for event in events if event.get("type") == "turn_choice"]
    if not turns:
        issues.append("No se registraron turn_choice.")
    turn_issues: list[str] = []
    for event in turns:
        _, current = _turn_check(event)
        turn_issues.extend(current)
    issues.extend(turn_issues)

    session_end = next(
        (event for event in events if event.get("type") == "session_end"), None
    )
    result: dict[str, Any] = {}
    if isinstance(session_end, dict):
        payload = _event_payload(session_end)
        raw_result = payload.get("result")
        result = raw_result if isinstance(raw_result, dict) else {}
        winner = result.get("winner")
        if winner not in {"human", "model", "tie"}:
            issues.append("session_end tiene winner inválido.")
        if not isinstance(result.get("turns"), int) or result.get("turns", -1) < 0:
            issues.append("session_end tiene turns inválido.")
        if not str(result.get("battleTag") or ""):
            issues.append("session_end no tiene battleTag.")
        final_state = payload.get("finalState")
        if not isinstance(final_state, dict) or final_state.get("finished") is not True:
            issues.append("finalState no está marcado como finished.")

    habits_path = profile_root / "habits.json"
    habits: dict[str, Any] = {}
    if not habits_path.is_file():
        issues.append("No existe habits.json.")
    else:
        loaded = _load_json(habits_path)
        habits = loaded if isinstance(loaded, dict) else {}
        counts = _profile_counts(sessions_root)
        if habits.get("profileId") != profile_id:
            issues.append("habits.json pertenece a otro profileId.")
        for key in ("sessions", "completedSessions", "turnChoices"):
            if habits.get(key) != counts[key]:
                issues.append(
                    f"habits.json {key}={habits.get(key)!r}, esperado {counts[key]!r}."
                )
        results = habits.get("results")
        if not isinstance(results, dict):
            issues.append("habits.json no contiene results.")
        else:
            for key in ("wins", "losses", "ties"):
                if results.get(key) != counts[key]:
                    issues.append(
                        f"habits.json results.{key}={results.get(key)!r}, esperado {counts[key]!r}."
                    )

    battle_tag = str(result.get("battleTag") or "")
    replay_matches: list[str] = []
    replays_root = runtime_root / "replays"
    if battle_tag and replays_root.is_dir():
        for candidate in replays_root.rglob("*"):
            if candidate.is_file() and battle_tag.lower() in candidate.name.lower():
                replay_matches.append(str(candidate))
        if not replay_matches:
            warnings.append(
                "No se encontró un replay cuyo nombre contenga battleTag; "
                "esto no invalida el recorder porque poke-env puede usar otro nombre."
            )

    start_event = next(
        (event for event in events if event.get("type") == "session_start"), None
    )
    context = _event_payload(start_event or {}).get("context")
    if not isinstance(context, dict) or context.get("mode") != "Nana 0 observational":
        issues.append("session_start no confirma modo Nana 0 observational.")

    return {
        "pass": not issues,
        "profileId": profile_id,
        "sessionId": session_id,
        "sessionFile": str(session_path),
        "events": len(events),
        "teamPreviewEvents": len(previews),
        "turnChoices": len(turns),
        "winner": result.get("winner"),
        "battleTurns": result.get("turns"),
        "battleTag": battle_tag,
        "lightDecisionEvents": sum(
            1
            for event in turns
            if isinstance(_event_payload(event).get("light"), dict)
        ),
        "replayMatches": replay_matches,
        "habits": {
            "sessions": habits.get("sessions"),
            "completedSessions": habits.get("completedSessions"),
            "turnChoices": habits.get("turnChoices"),
            "results": habits.get("results"),
        },
        "issues": issues,
        "warnings": warnings,
    }


def _print_human(report: dict[str, Any]) -> None:
    status = "PASS" if report.get("pass") else "FAIL"
    print(f"Nana 0 audit: {status}")
    print(f"Profile: {report.get('profileId')}")
    if report.get("sessionId"):
        print(f"Session: {report.get('sessionId')}")
    if report.get("battleTag"):
        print(
            "Battle: "
            f"{report.get('battleTag')} · winner={report.get('winner')} · "
            f"turns={report.get('battleTurns')}"
        )
    if "turnChoices" in report:
        print(
            "Recorder: "
            f"events={report.get('events')} · previews={report.get('teamPreviewEvents')} · "
            f"turnChoices={report.get('turnChoices')} · "
            f"LIGHT={report.get('lightDecisionEvents')}"
        )
    habits = report.get("habits")
    if isinstance(habits, dict):
        print(
            "Habits: "
            f"sessions={habits.get('sessions')} · completed={habits.get('completedSessions')} · "
            f"turnChoices={habits.get('turnChoices')} · results={habits.get('results')}"
        )
    for warning in report.get("warnings") or []:
        print(f"WARN: {warning}")
    for issue in report.get("issues") or []:
        print(f"ERROR: {issue}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--nana-profile", default="default")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_latest(args.runtime_root.expanduser().resolve(), args.nana_profile)
    except Exception as error:
        report = {
            "pass": False,
            "profileId": args.nana_profile,
            "issues": [str(error)],
            "warnings": [],
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
