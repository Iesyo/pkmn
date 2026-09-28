"""Primer puente verificable entre un battle-XX.json de Ledger y Showdown.

El JSON de Ledger determina todas las acciones. El diagnóstico aporta los
nombres de los jugadores, los equipos del Team Preview y el resultado, que
todavía no son campos de Ledger. Un resultado sin evidencia OCR o un equipo
incompleto hace fallar la exportación.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


_SLOT = re.compile(r"^p[12][ab]$")
_HP = re.compile(r"^(\d+)/(\d+)$")
_WIN = re.compile(r"^You defeated (.+)!$", re.IGNORECASE)
_LOSS = re.compile(r"^You (?:lost to|were defeated by) (.+)!$", re.IGNORECASE)
_SUPPORTED = {
    "switch", "turn", "ability", "fieldstart", "fieldend", "mega", "move",
    "damage", "heal", "faint", "weather", "battle_end",
}


class ReplayEvidenceError(ValueError):
    """Falta evidencia o hay una contradicción para un replay fiel."""


def _atom(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayEvidenceError(f"Falta {label}.")
    result = value.strip()
    if any(char in result for char in "|\r\n<>"):
        raise ReplayEvidenceError(f"{label} contiene caracteres incompatibles con el protocolo.")
    return result


def _health(value: object, label: str) -> str:
    match = _HP.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ReplayEvidenceError(f"{label} no tiene PS completos: {value!r}.")
    current, maximum = map(int, match.groups())
    if maximum < 1 or not 0 <= current <= maximum:
        raise ReplayEvidenceError(f"{label} tiene PS fuera de rango: {value!r}.")
    return value


def _slot(value: object) -> str:
    if not isinstance(value, str) or not _SLOT.fullmatch(value):
        raise ReplayEvidenceError(f"Slot inválido: {value!r}.")
    return value


def _one_vote(votes: Counter[str], label: str) -> str:
    if not votes:
        raise ReplayEvidenceError(f"La traza no confirma {label}.")
    top = votes.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        raise ReplayEvidenceError(f"La traza no distingue {label}: {top!r}.")
    return top[0][0]


@dataclass(frozen=True)
class TraceContext:
    job_id: str
    p1: str
    p2: str
    format: str
    uploadtime: int
    winner: str
    winner_evidence: dict[str, Any]
    teams: dict[str, tuple[str, ...]]
    team_evidence: dict[str, Any]


def _team(value: object, label: str) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        return None
    team = tuple(_atom(species, f"especie de {label}") for species in value)
    keys = [re.sub(r"[^a-z0-9]", "", species.casefold()) for species in team]
    if not all(keys) or len(set(keys)) != 6:
        raise ReplayEvidenceError(f"El equipo de {label} tiene especies repetidas o inválidas.")
    return team


def _confirmed_team(
    votes: dict[tuple[str, ...], list[int]], side: str,
) -> tuple[tuple[str, ...] | None, dict[str, Any] | None]:
    if not votes:
        return None, None
    ranked = sorted(votes.items(), key=lambda item: len(item[1]), reverse=True)
    roster, frames = ranked[0]
    if len(frames) < 2 or (len(ranked) > 1 and len(frames) <= len(ranked[1][1])):
        raise ReplayEvidenceError(f"La traza no confirma un equipo único de seis para {side}.")
    return roster, {"source": "team_preview", "frames": frames, "votes": len(frames)}


def load_trace_context(diagnostic: Path, battle: dict[str, Any]) -> TraceContext:
    """Lee identidad, rosters y resultado de esta batalla, nunca el replay viejo."""
    with zipfile.ZipFile(diagnostic) as archive:
        job = json.loads(archive.read("job.json"))
        rows = archive.read("output/ocr.trace.jsonl").splitlines()
    job_id = _atom(job.get("id"), "ID del diagnóstico")
    if job_id not in diagnostic.name:
        raise ReplayEvidenceError("El ID del diagnóstico no corresponde al ZIP.")
    index = battle.get("battle_index")
    if not isinstance(index, int) or index < 0:
        raise ReplayEvidenceError("Ledger no identifica la batalla de la traza.")
    bounds = battle.get("first_frame"), battle.get("last_frame")
    if not all(isinstance(x, int) for x in bounds):
        raise ReplayEvidenceError("Ledger no delimita los fotogramas de la batalla.")
    ends = [e["frame"] for e in battle["events"] if e.get("kind") == "battle_end"
            and e.get("status") == "consistent"]
    if len(ends) != 1:
        raise ReplayEvidenceError("Hace falta un único cierre confirmado por Ledger.")
    players: dict[str, Counter[str]] = {"p1": Counter(), "p2": Counter()}
    team_votes: dict[str, dict[tuple[str, ...], list[int]]] = {"p1": {}, "p2": {}}
    outcomes: list[tuple[int, str, float]] = []
    for raw in rows:
        row = json.loads(raw)
        frame = row.get("frame")
        if row.get("battle_index") != index or not isinstance(frame, int) or not bounds[0] <= frame <= bounds[1]:
            continue
        detected = ((row.get("detections") or {}).get("players") or {})
        for side in ("p1", "p2"):
            name = detected.get(side)
            if isinstance(name, str) and name.strip() and name not in ("Player", "Rival"):
                players[side][_atom(name, f"jugador {side}")] += 1
        detections = row.get("detections") or {}
        if detections.get("team_preview"):
            detected_teams = detections.get("teams") or {}
            for side in ("p1", "p2"):
                roster = _team(detected_teams.get(side), side)
                if roster:
                    team_votes[side].setdefault(roster, []).append(frame)
        if frame < ends[0]:
            continue
        for part in row.get("ocr") or ():
            text = part.get("text", "").strip()
            confidence = part.get("confidence")
            if isinstance(confidence, (int, float)) and confidence >= 0.9 and (_WIN.fullmatch(text) or _LOSS.fullmatch(text)):
                outcomes.append((frame, text, float(confidence)))
    p1, p2 = (_one_vote(players[side], side) for side in ("p1", "p2"))
    if p1.casefold() == p2.casefold():
        raise ReplayEvidenceError("Los dos jugadores no pueden tener el mismo nombre.")
    winners: set[str] = set()
    valid: list[tuple[int, str, float]] = []
    for frame, text, confidence in outcomes:
        win, loss = _WIN.fullmatch(text), _LOSS.fullmatch(text)
        opponent = (win or loss).group(1)
        if opponent.casefold() == p2.casefold():
            winners.add("p1" if win else "p2")
            valid.append((frame, text, confidence))
    if len(winners) != 1:
        raise ReplayEvidenceError("El resultado OCR no confirma un ganador único para esta batalla.")
    source_frame, source_text, source_confidence = min(valid)
    teams: dict[str, tuple[str, ...]] = {}
    team_evidence: dict[str, Any] = {}
    configured = (job.get("context") or {}).get("teams") or {}
    for side in ("p1", "p2"):
        observed, evidence = _confirmed_team(team_votes[side], side)
        seeded = _team(configured.get(side), side)
        if seeded and observed and set(seeded) != set(observed):
            raise ReplayEvidenceError(f"El equipo de {side} en el diagnóstico contradice el Team Preview.")
        roster = (seeded if side == "p1" else observed) or observed or seeded
        if not roster:
            raise ReplayEvidenceError(f"Falta el equipo completo de seis Pokémon para {side}.")
        teams[side] = roster
        team_evidence[side] = evidence or {"source": "job.context.teams", "votes": 0, "frames": []}
    fmt = _atom((job.get("context") or {}).get("format"), "formato del diagnóstico")
    stamp = datetime.fromisoformat(_atom(job.get("created_at"), "fecha del diagnóstico"))
    return TraceContext(
        job_id=job_id, p1=p1, p2=p2, format=fmt, uploadtime=int(stamp.timestamp()),
        winner=p1 if "p1" in winners else p2,
        winner_evidence={"frame": source_frame, "text": source_text, "confidence": source_confidence},
        teams=teams, team_evidence=team_evidence,
    )


def _observed_targets(events: list[dict[str, Any]]) -> dict[int, str]:
    """Un solo daño al bando contrario permite recuperar el objetivo del golpe."""
    moves = {e["seq"]: e for e in events if e["kind"] == "move"}
    targets: dict[int, set[str]] = {seq: set() for seq in moves}
    for event in events:
        cause = event.get("cause")
        move = moves.get(cause) if isinstance(cause, int) else None
        if move and event["kind"] == "damage" and isinstance(event.get("slot"), str):
            if event["slot"][:2] != move["slot"][:2]:
                targets[cause].add(event["slot"])
    return {seq: next(iter(slots)) for seq, slots in targets.items() if len(slots) == 1}


def _intermediate_baseline(
    event: dict[str, Any], move: dict[str, Any] | None, inferred: str,
) -> dict[str, Any] | None:
    """Reconoce un HUD tomado durante el golpe, posterior al comienzo del movimiento.

    En la primera entrada se infiere el máximo. Si el primer HUD confirmado
    aparece después de la acción que causa el daño y antes de su valor final,
    el HUD es una lectura intermedia; no se fabrica una acción entre la entrada
    y ese fotograma.
    """
    baseline = event.get("hp_baseline") or {}
    if not move or baseline.get("state") != "confirmed" or event.get("hp_state") != "confirmed":
        return None
    prior, result = event.get("before"), event.get("after")
    if not isinstance(prior, str) or not isinstance(result, str):
        return None
    a, b, c = [int(hp.split("/")[0]) for hp in (inferred, prior, result)]
    maxima = [hp.split("/")[1] for hp in (inferred, prior, result)]
    if len(set(maxima)) != 1 or not (
        (event["kind"] == "damage" and a > b > c)
        or (event["kind"] == "heal" and a < b < c)
    ):
        return None
    move_frame, end_frame = move.get("frame"), event.get("frame")
    if not isinstance(move_frame, int) or not isinstance(end_frame, int):
        return None
    slot = _slot(event.get("slot"))
    literal = f"{b}%" if slot.startswith("p2") else prior
    # En esta primera regla sólo resolvemos motes que el mismo Ledger vincula
    # sin ambigüedad a la especie. Una abreviatura o un apodo ajeno no valida
    # el HUD del slot por el mero hecho de tener texto.
    species_key = re.sub(r"[^a-z0-9]", "", str(event.get("species") or "").casefold())
    for evidence in baseline.get("evidence") or ():
        frame = evidence.get("frame")
        nickname_key = re.sub(r"[^a-z0-9]", "", str(evidence.get("nickname") or "").casefold())
        if (isinstance(frame, int) and move_frame <= frame < end_frame
                and evidence.get("text") == literal and nickname_key == species_key and species_key):
            return {
                "ledger_seq": event["seq"], "causing_move_seq": move["seq"],
                "inferred_entry": inferred, "intermediate": prior,
                "final": result, "hud_frame": frame,
            }
    return None


def build_replay(battle: dict[str, Any], context: TraceContext) -> dict[str, Any]:
    if battle.get("issues"):
        raise ReplayEvidenceError("Ledger tiene avisos abiertos; no se exporta un replay.")
    if any(e.get("status") not in ("consistent", "suppressed") for e in battle.get("events", [])):
        raise ReplayEvidenceError("Ledger conserva sucesos pendientes de revisión.")
    events = [e for e in battle.get("events", []) if e.get("status") == "consistent"]
    if not events or any(e.get("kind") not in _SUPPORTED for e in events):
        unsupported = sorted({e.get("kind") for e in events if e.get("kind") not in _SUPPORTED})
        raise ReplayEvidenceError(f"Faltan sucesos o hay tipos sin exportar: {unsupported}.")
    sequences = [e["seq"] for e in events]
    if sequences != sorted(set(sequences)):
        raise ReplayEvidenceError("Los sucesos de Ledger están fuera de orden.")
    actors = battle.get("actors") or {}
    if not isinstance(actors, dict):
        raise ReplayEvidenceError("Ledger no proporciona identidades persistentes.")
    lines = [
        f"|player|p1|{context.p1}||", f"|player|p2|{context.p2}||",
        "|gametype|doubles", "|gen|9", f"|tier|{context.format}",
    ]
    for side in ("p1", "p2"):
        roster = context.teams[side]
        if len(roster) != 6:
            raise ReplayEvidenceError(f"Falta el equipo completo de {side}.")
        lines.append(f"|teamsize|{side}|6")
        lines.extend(f"|poke|{side}|{species}, L50|" for species in roster)
    lines.extend(["|teampreview", "|start"])
    active: dict[str, str] = {}
    known_hp: dict[str, str] = {}
    hp_source: dict[str, str] = {}
    known_species: dict[str, str] = {}
    fainted: set[str] = set()
    moves_target = _observed_targets(events)
    moves_by_seq = {e["seq"]: e for e in events if e["kind"] == "move"}
    missing_targets: list[int] = []
    intermediate_baselines: list[dict[str, Any]] = []
    turn = 0
    end_seen = False
    event_lines: list[dict[str, int]] = []

    def actor_at(event: dict[str, Any]) -> tuple[str, str]:
        slot = _slot(event.get("slot"))
        actor = event.get("actor_id")
        if not isinstance(actor, str) or active.get(slot) != actor:
            raise ReplayEvidenceError(f"Actor fuera de su slot en suceso {event.get('seq')} ({slot}).")
        return slot, f"{slot}: {known_species[actor]}"

    for event in events:
        seq, kind = event["seq"], event["kind"]
        if end_seen:
            raise ReplayEvidenceError(f"Suceso {seq} después del cierre.")
        start_line = len(lines) + 1
        if kind == "switch":
            slot = _slot(event.get("slot"))
            actor = _atom(event.get("actor_id"), f"actor en suceso {seq}")
            canonical = _atom((actors.get(actor) or {}).get("species"), f"especie del actor {actor}")
            species = _atom(event.get("species"), f"especie del cambio {seq}")
            if species != canonical or actor in fainted:
                raise ReplayEvidenceError(f"Identidad o estado inválido en cambio {seq}.")
            if actor in active.values() and active.get(slot) != actor:
                raise ReplayEvidenceError(f"El actor {actor} ocupa dos slots en cambio {seq}.")
            hp = _health(event.get("health"), f"cambio {seq}")
            if actor in known_hp and known_hp[actor] != hp:
                raise ReplayEvidenceError(f"PS contradictorios en reentrada {seq}.")
            active[slot], known_hp[actor], known_species[actor] = actor, hp, species
            hp_source[actor] = "inferred_entry" if event.get("hp_state") == "inferred" else "confirmed"
            lines.append(f"|switch|{slot}: {species}|{species}, L50|{hp}")
        elif kind == "turn":
            next_turn = event.get("turn")
            if not isinstance(next_turn, int) or next_turn != turn + 1:
                raise ReplayEvidenceError(f"Salto o repetición de turno en {seq}.")
            turn = next_turn
            lines.append(f"|turn|{turn}")
        elif kind == "mega":
            slot, identifier = actor_at(event)
            form = _atom(event.get("species"), f"forma Mega {seq}")
            base = known_species[active[slot]]
            if not form.startswith(base + "-Mega"):
                raise ReplayEvidenceError(f"La Mega en {seq} no pertenece al actor activo.")
            stone = _atom(event.get("value"), f"piedra de la Mega {seq}")
            lines.extend([
                f"|detailschange|{identifier}|{form}, L50|{known_hp[active[slot]]}",
                f"|-mega|{identifier}|{stone}",
            ])
        elif kind == "move":
            slot, identifier = actor_at(event)
            move = _atom(event.get("move"), f"movimiento {seq}")
            target = event.get("target_slot") or moves_target.get(seq)
            target_id = ""
            if target:
                target = _slot(target)
                if target not in active:
                    raise ReplayEvidenceError(f"Objetivo {target} ausente en movimiento {seq}.")
                target_id = f"{target}: {known_species[active[target]]}"
            else:
                missing_targets.append(seq)
            lines.append(f"|move|{identifier}|{move}|{target_id}")
        elif kind in ("damage", "heal"):
            slot, identifier = actor_at(event)
            actor = active[slot]
            prior = _health(event.get("before"), f"PS previos {seq}")
            result = _health(event.get("after"), f"PS finales {seq}")
            if prior != known_hp[actor]:
                bridge = (_intermediate_baseline(event, moves_by_seq.get(event.get("cause")), known_hp[actor])
                          if hp_source[actor] == "inferred_entry" else None)
                if bridge:
                    intermediate_baselines.append(bridge)
                else:
                    raise ReplayEvidenceError(f"PS no continuos en suceso {seq}: {known_hp[actor]} -> {prior}.")
            if event.get("health") != result:
                raise ReplayEvidenceError(f"PS finales contradictorios en suceso {seq}.")
            n0, n1 = int(known_hp[actor].split("/")[0]), int(result.split("/")[0])
            if (kind == "damage" and n1 >= n0) or (kind == "heal" and n1 <= n0):
                raise ReplayEvidenceError(f"Sentido de PS contradictorio en suceso {seq}.")
            known_hp[actor] = result
            hp_source[actor] = "confirmed"
            lines.append(f"|-{kind}|{identifier}|{result}")
        elif kind == "faint":
            slot, identifier = actor_at(event)
            actor = active[slot]
            if int(known_hp[actor].split("/")[0]) != 0:
                raise ReplayEvidenceError(f"Debilitamiento sin cero PS en suceso {seq}.")
            fainted.add(actor)
            lines.append(f"|faint|{identifier}")
        elif kind == "ability":
            _, identifier = actor_at(event)
            lines.append(f"|-ability|{identifier}|{_atom(event.get('value'), f'habilidad {seq}')}")
        elif kind in ("fieldstart", "fieldend", "weather"):
            lines.append(f"|-{kind}|{_atom(event.get('value'), f'efecto {seq}')}")
        elif kind == "battle_end":
            end_seen = True
            lines.append(f"|-message|{_atom(event.get('value'), f'cierre {seq}')}")
        event_lines.append({"ledger_seq": seq, "protocol_line": start_line})
    if not end_seen or not turn:
        raise ReplayEvidenceError("No hay partida completa con turnos y cierre.")
    lines.append(f"|win|{context.winner}")
    return {
        "log": "\n".join(lines), "inputlog": "", "uploadtime": context.uploadtime,
        "p1": context.p1, "p2": context.p2, "format": context.format,
        "source_battle_index": battle["battle_index"], "issues": [],
        "ledger_source": {
            "job_id": context.job_id, "battle_index": battle["battle_index"],
            "consistent_events": len(events), "winner_evidence": context.winner_evidence,
            "protocol_lines": event_lines, "target_unknown_at_seq": missing_targets,
            "intermediate_baselines": intermediate_baselines,
            "team_preview": context.team_evidence,
            "hp_units": "p1: observed actual/max; p2: normalized percent/100",
        },
    }


def render_html(document: dict[str, Any], replay_id: str) -> str:
    title = html.escape(f"{document['format']}: {document['p1']} vs. {document['p2']}")
    safe_id = html.escape(_atom(replay_id, "ID del replay"), quote=True)
    protocol = document["log"]
    if "</script" in protocol.lower():
        raise ReplayEvidenceError("El protocolo contiene una etiqueta HTML inesperada.")
    return f"""<!DOCTYPE html>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="upgrade-insecure-requests" />
<title>{title}</title>
<style>html,body{{font-family:Verdana,sans-serif;font-size:10pt;margin:0;padding:12px;background:#eef2f5}}
.wrapper{{max-width:1180px;margin:0 auto}}</style>
<div class="wrapper replay-wrapper">
  <input type="hidden" name="replayid" value="{safe_id}" />
  <div class="battle"></div><div class="battle-log"></div>
  <div class="replay-controls"></div><div class="replay-controls-2"></div>
  <h1>{title}</h1>
  <script type="text/plain" class="battle-log-data">{protocol}</script>
</div>
<script src="https://play.pokemonshowdown.com/js/replay-embed.js"></script>
"""


def export(ledger_path: Path, diagnostic: Path, output_stem: Path, *, force: bool = False) -> tuple[Path, ...]:
    battle = json.loads(ledger_path.read_text(encoding="utf-8"))
    document = build_replay(battle, load_trace_context(diagnostic, battle))
    paths = tuple(output_stem.with_suffix(ext) for ext in (".log", ".json", ".html"))
    if not force and any(p.exists() for p in paths):
        raise FileExistsError("Ya existe un artefacto; usa --force para reemplazarlo.")
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    paths[0].write_text(document["log"] + "\n", encoding="utf-8")
    paths[1].write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths[2].write_text(render_html(document, output_stem.name), encoding="utf-8")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Primer replay Showdown a partir de una batalla de Champions Ledger")
    parser.add_argument("--ledger", type=Path, required=True, help="battle-XX.json de Ledger")
    parser.add_argument("--diagnostic", type=Path, required=True, help="ZIP con job.json y ocr.trace.jsonl")
    parser.add_argument("--out", type=Path, required=True, help="Ruta base de los archivos de salida")
    parser.add_argument("--force", action="store_true", help="Sobrescribir resultado anterior")
    args = parser.parse_args()
    for path in export(args.ledger, args.diagnostic, args.out, force=args.force):
        print(path)


if __name__ == "__main__":
    main()
