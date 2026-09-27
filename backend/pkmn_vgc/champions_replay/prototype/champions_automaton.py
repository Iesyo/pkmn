#!/usr/bin/env python3
"""Primer autómata temporal para trazas OCR de Pokémon Champions.

Lee una traza archivada, agrupa las lecturas de PS de cada animación y produce
un registro de sucesos con evidencia. Nunca vuelve a abrir el vídeo ni produce
un replay Showdown automáticamente: los sucesos marcados Revisar lo impedirían.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SLOTS = {"p1a", "p1b", "p2a", "p2b"}
ACTIVITY = {"move", "cant", "switch", "drag"}
HP_KINDS = {"damage", "heal"}
STATUS_NAMES = {"brn": "quemado", "par": "paralizado", "slp": "dormido", "frz": "congelado", "psn": "envenenado", "tox": "muy envenenado"}
RAW_ACTION = re.compile(r"\bused\s+(.+?)!$|\bfainted!$", re.IGNORECASE)
ANNOUNCED_ENTRY = re.compile(r"\bsent out\s+(.+?)!$|^Go!\s+(.+?)!$", re.IGNORECASE)
HP_TEXT = re.compile(r"(?<!\d)\d{1,4}\s*(?:%|/\s*\d{1,4})(?!\d)")
HUD_NUMBER = re.compile(r"^(\d{1,3})\s*%?$")
PLACEHOLDER = re.compile(r"^__champions_actor_[^_]+_\d+__$")


def health_ratio(health: str | None) -> float | None:
    if not health or "/" not in health:
        return None
    before, after = health.split("/", 1)
    try:
        return int(before) / int(after) if int(after) else None
    except ValueError:
        return None


def identity_species(species: str) -> str:
    return re.sub(r"-Mega(?:-[XY])?$", "", species)


def read_diagnostic(path: Path) -> tuple[list[dict[str, Any]], dict[int, str]]:
    with zipfile.ZipFile(path) as archive:
        frames = [json.loads(raw) for raw in archive.read("output/ocr.trace.jsonl").splitlines() if raw.strip()]
        baselines: dict[int, str] = {}
        for name in archive.namelist():
            if re.fullmatch(r"output/replay-\d{3}\.json", name):
                replay = json.loads(archive.read(name))
                baselines[int(replay["source_battle_index"])] = replay["log"]
    return frames, baselines


def read_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ordered_candidates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in frames:
        for index, event in enumerate(row.get("detections", {}).get("events", ())):
            candidates.append({"event": event, "observed_frame": row["frame"],
                               "observed_ms": row["timestamp_ms"], "ordinal": index})
    aliases: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
    resolutions: dict[str, str] = {}
    for row in frames:
        for side in aliases:
            aliases[side].update(row.get("resolved_aliases", {}).get(side) or {})
        resolutions.update(row.get("resolved_identities") or {})
    announcements: list[dict[str, Any]] = []
    for row in frames:
        for line in row.get("ocr", ()):
            text = line.get("text", "").strip()
            found = ANNOUNCED_ENTRY.search(text)
            if found and line.get("top", 0) >= .55:
                side = "p2" if found.group(1) else "p1"
                announced = found.group(1) or found.group(2)
                key = re.sub(r"\W+", "", announced.casefold())
                for alias, species in aliases[side].items():
                    normalized = re.sub(r"\W+", "", alias.casefold())
                    if len(normalized) >= 3 and normalized in key:
                        announcements.append({"frame": row["frame"], "side": side,
                                              "species": species, "text": text})
                # Los nombres sin mote también aparecen en los anuncios.
                if not any(a["frame"] == row["frame"] and a["side"] == side for a in announcements):
                    announcements.append({"frame": row["frame"], "side": side,
                                          "species": announced, "text": text})
    used_announcements: set[tuple[int, str, str]] = set()
    anchors: dict[tuple[int, str], int] = {}
    for item in candidates:
        event = item["event"]
        item["logical_frame"] = item["observed_frame"]
        if event["kind"] not in {"switch", "drag"}:
            continue
        slot = event.get("slot")
        if not slot:
            continue
        clean = resolutions.get(event.get("species"), event.get("species"))
        matches = [a for a in announcements
                   if a["side"] == slot[:2] and identity_species(a["species"]) == identity_species(clean or "")
                   and 0 <= item["observed_frame"] - a["frame"] <= 80
                   and (a["frame"], a["side"], a["species"]) not in used_announcements]
        if not matches:
            continue
        anchor = max(matches, key=lambda a: a["frame"])
        used_announcements.add((anchor["frame"], anchor["side"], anchor["species"]))
        item["logical_frame"] = anchor["frame"]
        item["anchor"] = anchor
        anchors[(item["observed_frame"], slot)] = anchor["frame"]
    for item in candidates:
        event = item["event"]
        if event["kind"] == "ability" and event.get("slot"):
            anchor = anchors.get((item["observed_frame"], event["slot"]))
            if anchor is not None:
                item["logical_frame"] = anchor
        if event["kind"] == "fieldstart" and "Terrain" in str(event.get("value")):
            value = str(event["value"])
            phrases = (["grass grew", "grassy terrain"] if "Grassy" in value
                       else ["psychic terrain", "weirdness filled", "battlefield got weird"]
                       if "Psychic" in value else [value.casefold()])
            hits = [(row["frame"], line["text"]) for row in frames
                    if 0 <= item["observed_frame"] - row["frame"] <= 80
                    for line in row.get("ocr", ())
                    if any(phrase in line.get("text", "").casefold() for phrase in phrases)]
            if hits:
                # Primera lectura del último episodio del anuncio.
                latest = hits[-1][0]
                episode = [hit for hit in hits if latest - hit[0] <= 6]
                item["logical_frame"] = episode[0][0]
                item["field_evidence"] = {"frame": episode[0][0], "text": episode[0][1]}
        if event["kind"] in {"switch", "drag"} and item.get("anchor"):
            start, end = item["logical_frame"], item["observed_frame"]
            item["late_health"] = any(
                other["event"]["kind"] in {"move", "damage", "heal"}
                and start < other["observed_frame"] < end
                for other in candidates
            )
    # El anuncio ocurre cuando entró el Pokémon; el HUD confirma el slot y la
    # especie más tarde. Se ordena por el anuncio y se guardan ambas horas.
    order = {"switch": 0, "drag": 0, "ability": 1}
    candidates.sort(key=lambda c: (c["logical_frame"], order.get(c["event"]["kind"], 2),
                                   c["observed_frame"], c["ordinal"]))
    # Al inicio el texto de una habilidad puede preceder al HUD del lead.
    # Se conserva la hora observada, pero la cronología causal registra primero
    # la entrada. Los turnos posteriores conservan el orden observado.
    first_turn = next((i for i, item in enumerate(candidates)
                       if item["event"]["kind"] == "turn" and item["event"].get("turn") == 1), None)
    if first_turn is not None:
        priority = {"switch": 0, "drag": 0, "ability": 1, "fieldstart": 2, "weather": 2}
        prelude = candidates[:first_turn]
        # Los cuatro leads entran como conjunto; para su representación
        # determinista se ordenan p1a,p1b,p2a,p2b, sin perder el instante
        # distinto en que cada uno fue anunciado.
        slot_order = {"p1a": 0, "p1b": 1, "p2a": 2, "p2b": 3}
        candidates[:first_turn] = sorted(
            prelude,
            key=lambda c: (priority.get(c["event"]["kind"], 3),
                           slot_order.get(c["event"].get("slot"), 4)
                           if c["event"]["kind"] in {"switch", "drag"} else c["logical_frame"]),
        )
    return candidates


@dataclass
class HpEpisode:
    actor_id: str | None
    slot: str | None
    species: str | None
    kind: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    narration: list[str] = field(default_factory=list)

    @property
    def first_ms(self) -> int:
        return int(self.candidates[0]["observed_ms"])

    @property
    def last_ms(self) -> int:
        return int(self.candidates[-1]["observed_ms"])


class BattleAutomaton:
    def __init__(self, battle_index: int, frames: list[dict[str, Any]]):
        self.battle_index = battle_index
        self.frames = frames
        self.frame_lookup = {row["frame"]: row for row in frames}
        self.id_resolution: dict[str, str] = {}
        for row in frames:
            self.id_resolution.update(row.get("resolved_identities") or {})
        self.events: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.active: dict[str, str] = {}
        self.actors: dict[str, dict[str, Any]] = {}
        self.actor_ids_by_species: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
        self.next_actor = 0
        self.turn = 0
        self.turn_activity = 0
        self.last_action: dict[str, Any] | None = None
        self.terrain: str | None = None
        self.hp_pending: dict[str, HpEpisode] = {}
        self.hp_order: list[str] = []

    def _resolve(self, species: str | None) -> str | None:
        return self.id_resolution.get(species, species) if species else None

    def _actor_for_entry(self, slot: str, species: str | None) -> str:
        side = slot[:2]
        raw = species or "unknown"
        clean = self._resolve(raw) or raw
        if PLACEHOLDER.match(raw):
            actor_id = raw
        else:
            previous = self.actor_ids_by_species.get((side, identity_species(clean)), [])
            actor_id = next((candidate for candidate in previous if candidate not in self.active.values()), "")
            if not actor_id:
                self.next_actor += 1
                actor_id = f"{side}-actor-{self.next_actor:02d}"
        if actor_id not in self.actors:
            self.actors[actor_id] = {"side": side, "species": clean, "forme": None, "health": None,
                                     "status": None, "item_lost": False, "fainted": False}
            self.actor_ids_by_species[(side, identity_species(clean))].append(actor_id)
        elif self.actors[actor_id]["species"] != clean:
            self.actors[actor_id]["species"] = clean
        return actor_id

    def _evidence(self, candidate: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        event = candidate["event"]
        frames = [event.get("source_frame"), candidate["observed_frame"]]
        if kind in {"ability", "fieldstart"} and candidate.get("logical_frame", 0) < candidate["observed_frame"]:
            frames += list(range(candidate["logical_frame"], candidate["observed_frame"] + 1))
        words = [str(event.get(key) or "") for key in ("move", "value", "species")]
        words = [w.casefold() for w in words if w and not PLACEHOLDER.match(w)]
        found: list[tuple[float, dict[str, Any]]] = []
        seen: set[tuple[int, str]] = set()
        for frame in frames:
            if frame is None:
                continue
            for around in (frame, frame - 1, frame + 1):
                row = self.frame_lookup.get(around)
                if not row:
                    continue
                for line in row.get("ocr", ()):
                    text = line.get("text", "").strip()
                    if not text or (around, text) in seen:
                        continue
                    seen.add((around, text))
                    low = text.casefold()
                    score = 0.0
                    if kind == "move" and "used " in low and words and words[0] in low:
                        score = 10
                    elif kind == "faint" and "fainted" in low:
                        score = 9
                    elif kind in HP_KINDS | {"hp_oscillation", "hp_ocr_conflict", "hp_zero_rebound"} and HP_TEXT.search(text):
                        slot = event.get("slot") or ""
                        if slot.startswith("p1") and line.get("top", 0) < .65:
                            continue
                        if slot.startswith("p2") and line.get("top", 1) > .35:
                            continue
                        current = str(event.get("health") or "").split("/", 1)[0]
                        score = 10 if re.fullmatch(rf"{re.escape(current)}\s*(?:%|/\s*\d+)", text) else 6
                    elif kind == "status" and any(s in low for s in ("paraly", "burn", "poison", "asleep", "froze")):
                        score = 8
                    elif kind in {"switch", "ability", "mega", "fieldstart"} and any(w in low for w in words):
                        score = 5
                    elif kind in {"cant", "message"} and len(text) > 12 and line.get("top", 1) > .60:
                        score = 3
                    if score:
                        found.append((score + float(line.get("confidence", 0)),
                                      {"frame": around, "text": text, "confidence": line.get("confidence")}))
        found.sort(key=lambda item: (-item[0], item[1]["frame"]))
        return [item for _, item in found[:3]]

    def _issue(self, code: str, message: str, frame: int, seq: int | None = None) -> None:
        self.issues.append({"code": code, "message": message, "frame": frame, "event_seq": seq})

    def _competing_hp_ocr(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Find a stronger, different number overlapping a percent reading in one HUD.

        A different HUD on the same screen is not competing evidence: a fainted
        partner can legitimately show 0% beside an active Pokémon at 88%.
        """
        health = str(candidate["event"].get("health") or "")
        if not re.fullmatch(r"\d+/100", health):
            return None
        expected = int(health.split("/", 1)[0])
        readings = []
        for line in self.frame_lookup.get(candidate["observed_frame"], {}).get("ocr", ()):
            match = HUD_NUMBER.fullmatch(line.get("text", "").strip())
            if match and all(key in line for key in ("left", "right", "top", "bottom")):
                value = int(match.group(1))
                if value <= 100:
                    readings.append((value, line))
        for value, suspect in readings:
            if value != expected or "%" not in suspect["text"]:
                continue
            for other_value, stronger in readings:
                if other_value == expected or stronger["confidence"] <= suspect["confidence"] + .04:
                    continue
                # Overlap or touch within the same HP label; Kingambit's distant
                # 0% and Rillaboom's 88% must never be paired as one reading.
                if (suspect["left"] > stronger["right"] + .02 or
                    stronger["left"] > suspect["right"] + .02 or
                    suspect["top"] > stronger["bottom"] + .005 or
                    stronger["top"] > suspect["bottom"] + .005):
                    continue
                return {"frame": candidate["observed_frame"],
                        "suspect": {"text": suspect["text"], "confidence": suspect["confidence"]},
                        "stronger": {"text": stronger["text"], "confidence": stronger["confidence"]}}
        return None

    def _append(self, candidate: dict[str, Any], *, kind: str | None = None,
                actor_id: str | None = None, status: str = "consistent",
                note: str | None = None, before: str | None = None,
                after: str | None = None, cause: int | str | None = None,
                observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        event = candidate["event"]
        kind = kind or event["kind"]
        evidence = self._evidence(candidate, kind)
        if candidate.get("anchor"):
            a = candidate["anchor"]
            evidence.insert(0, {"frame": a["frame"], "text": a["text"], "confidence": None})
        if candidate.get("field_evidence"):
            a = candidate["field_evidence"]
            evidence.insert(0, {"frame": a["frame"], "text": a["text"], "confidence": None})
        item = {
            "seq": len(self.events) + 1, "battle_index": self.battle_index,
            "turn": self.turn, "kind": kind, "status": status,
            "actor_id": actor_id, "slot": event.get("slot"),
            "species": self.actors[actor_id].get("forme") or self._resolve(event.get("species")) if actor_id in self.actors else self._resolve(event.get("species")),
            "target_slot": event.get("target_slot"), "move": event.get("move"),
            "value": event.get("value"), "health": event.get("health"),
            "before": before, "after": after, "cause": cause,
            "observed_ms": candidate["observed_ms"],
            "logical_frame": candidate.get("logical_frame", candidate["observed_frame"]),
            "logical_ms": self.frame_lookup.get(candidate.get("logical_frame"), {}).get(
                "timestamp_ms", candidate["observed_ms"]),
            "event_ms": event.get("timestamp_ms"),
            "frame": candidate["observed_frame"], "source_frame": event.get("source_frame"),
            "confidence": event.get("confidence"), "note": note,
            "evidence": evidence,
            "narration": [], "observations": observations or [],
        }
        self.events.append(item)
        return item

    def _flush_hp(self) -> None:
        episodes = sorted((self.hp_pending[key] for key in self.hp_order),
                          key=lambda episode: episode.first_ms)
        self.hp_pending.clear()
        self.hp_order.clear()
        for episode in episodes:
            first, last = episode.candidates[0], episode.candidates[-1]
            raw = last["event"]
            actor = self.actors.get(episode.actor_id or "")
            before = actor.get("health") if actor else None
            after = raw.get("health")
            observations = [{"frame": c["observed_frame"], "health": c["event"].get("health"),
                             "parser_kind": c["event"]["kind"], "evidence": self._evidence(c, c["event"]["kind"]),
                             **({"competing_ocr": c["competing_ocr"]} if c.get("competing_ocr") else {})}
                            for c in episode.candidates]
            # Un porcentaje aislado y contradicho por una lectura más fuerte
            # del mismo HUD no establece daño/curación ni modifica el estado.
            # Si se confirma en frames posteriores, llegará como otro episodio.
            if len(episode.candidates) == 1 and last.get("competing_ocr"):
                conflict = last["competing_ocr"]
                note = (f"OCR {conflict['suspect']['text']} contradicho en el mismo HUD por "
                        f"{conflict['stronger']['text']} de mayor confianza; PS sin confirmar.")
                item = self._append(last, kind="hp_ocr_conflict", actor_id=episode.actor_id,
                                    status="review", before=before, after=before,
                                    note=note, observations=observations)
                item["narration"].extend(episode.narration)
                self._issue("hp_ocr_conflict", note, item["frame"], item["seq"])
                continue
            result = "consistent"
            notes: list[str] = []
            old, new = health_ratio(before), health_ratio(after)
            derived = "damage" if old is not None and new is not None and new < old else "heal"
            if old is not None and new is not None and old == new:
                result = "suppressed"
                notes.append("Lectura repetida; no se emite transición de PS.")
            elif old is not None and new is not None and derived != episode.kind:
                result = "review"
                notes.append("El sentido de la lectura contradice los PS anteriores.")
            if actor and actor.get("fainted") and after and new != 0:
                result = "review"
                notes.append("Actor debilitado con PS positivos sin nueva entrada.")
            if before is None:
                notes.append("PS previo no observable; conservar como observación.")
            cause: int | str | None = None
            if episode.kind == "heal" and self.terrain and "Grassy Terrain" in self.terrain:
                cause = "posible efecto de Grassy Terrain; comprobar suelo y cuantía"
            elif self.last_action and last["observed_ms"] - self.last_action["observed_ms"] <= 20_000:
                cause = self.last_action["seq"]
            elif episode.kind == "heal":
                notes.append("Curación sin causa asignada en esta ventana.")
            item = self._append(last, kind=episode.kind, actor_id=episode.actor_id,
                                status=result, note=" ".join(notes) or None, before=before,
                                after=after, cause=cause, observations=observations)
            item["narration"].extend(episode.narration)
            if actor and result != "suppressed":
                actor["health"] = after
            if result == "review":
                self._issue("hp_transition", item["note"] or "Transición de PS dudosa.", item["frame"], item["seq"])

    def _hp(self, candidate: dict[str, Any]) -> None:
        event = candidate["event"]
        conflict = self._competing_hp_ocr(candidate)
        if conflict:
            candidate["competing_ocr"] = conflict
        slot = event.get("slot")
        actor_id = self.active.get(slot)
        # Una lectura tardía de un ocupante distinto no cambia el estado del
        # Pokémon que está ahora en el slot.
        if actor_id and event.get("species") and self._resolve(event["species"]) != self.actors[actor_id]["species"]:
            self._flush_hp()
            item = self._append(candidate, actor_id=actor_id, status="review",
                                note="HUD atribuido a otra especie que la ocupante del slot.")
            self._issue("hp_wrong_occupant", item["note"], item["frame"], item["seq"])
            return
        key = f"{slot or '?'}:{actor_id or '?'}"
        pending = self.hp_pending.get(key)
        stable = self.actors.get(actor_id or "", {}).get("health")
        # Durante la selección del nuevo turno, el HUD puede saltar 88→0→88
        # en dos frames sin que ocurra una acción. Conservamos ambos fotogramas
        # y descartamos juntos la falsa pérdida y la falsa curación.
        if (pending and pending.kind != event["kind"] and self.turn_activity == 0
            and candidate["observed_ms"] - pending.last_ms <= 1_500
            and stable == event.get("health") and stable != pending.candidates[-1]["event"].get("health")):
            reads = pending.candidates + [candidate]
            self.hp_pending.pop(key)
            self.hp_order.remove(key)
            conflict = next((x["competing_ocr"] for x in reads if x.get("competing_ocr")), None)
            note = "Dos lecturas opuestas antes de la primera acción regresan al PS previo."
            if conflict:
                note += (f" En el mismo HUD, {conflict['suspect']['text']} compite con "
                         f"{conflict['stronger']['text']} de mayor confianza OCR.")
            item = self._append(candidate, kind="hp_oscillation", actor_id=actor_id,
                                status="review", before=stable, after=stable,
                                note=note,
                                observations=[{"frame": x["observed_frame"], "health": x["event"].get("health"),
                                               "evidence": self._evidence(x, x["event"]["kind"]),
                                               **({"competing_ocr": x["competing_ocr"]} if x.get("competing_ocr") else {})}
                                              for x in reads])
            item["narration"].extend(pending.narration)
            self._issue("hp_oscillation", item["note"], item["frame"], item["seq"])
            return
        if pending and (pending.kind != event["kind"] or
                        candidate["observed_ms"] - pending.last_ms > 1_500):
            self._flush_hp()
            pending = None
        if (event["kind"] == "heal" and actor_id and
            health_ratio(self.actors[actor_id]["health"]) == 0):
            before = self.actors[actor_id]["health"]
            note = "PS en cero antes del debilitamiento; una lectura aislada no confirma reanimación."
            item = self._append(candidate, kind="hp_zero_rebound", actor_id=actor_id,
                                status="review", before=before, after=before, note=note,
                                observations=[{"frame": candidate["observed_frame"],
                                               "health": event.get("health"),
                                               "evidence": self._evidence(candidate, "heal")}])
            self._issue("hp_zero_rebound", note, item["frame"], item["seq"])
            return
        if pending is None:
            pending = HpEpisode(actor_id, slot, event.get("species"), event["kind"])
            self.hp_pending[key] = pending
            self.hp_order.append(key)
        pending.candidates.append(candidate)

    def _handle(self, candidate: dict[str, Any]) -> None:
        event = candidate["event"]
        kind = event["kind"]
        if kind in HP_KINDS:
            self._hp(candidate)
            return
        slot = event.get("slot")
        actor_id = self.active.get(slot)
        if kind == "message":
            value = str(event.get("value") or "")
            if "battle has ended" in value.casefold() or "forfeit" in value.casefold():
                self._flush_hp()
                self._append(candidate, kind="battle_end", note="Fin de la batalla observado en la pantalla.")
                return
            if self.hp_pending:
                newest = max(self.hp_pending.values(), key=lambda e: e.last_ms)
                newest.narration.append(value)
                return
            if self.events and self.events[-1]["kind"] != "turn":
                self.events[-1]["narration"].append(value)
            else:
                item = self._append(candidate, kind="unclassified_text", status="review",
                                    note="Mensaje sin evento causal identificable.")
                self._issue("unclassified_text", item["note"], item["frame"], item["seq"])
            return
        self._flush_hp()
        if kind in {"switch", "drag"} and slot in SLOTS:
            species = self._resolve(event.get("species"))
            if actor_id and identity_species(self.actors[actor_id]["species"]) == identity_species(species or ""):
                item = self._append(candidate, actor_id=actor_id, status="suppressed",
                                    note="Entrada de la especie que ya ocupaba el slot.")
                self._issue("reentry_without_exit", item["note"], item["frame"], item["seq"])
                return
            actor_id = self._actor_for_entry(slot, event.get("species"))
            if self.actors[actor_id]["fainted"]:
                item = self._append(candidate, actor_id=actor_id, status="suppressed",
                                    note="El HUD conserva la especie debilitada; no hubo reanimación observada.")
                self._issue("ghost_reentry_after_faint", item["note"], item["frame"], item["seq"])
                return
            self.active[slot] = actor_id
            self.actors[actor_id]["fainted"] = False
            late_health = bool(candidate.get("late_health") and event.get("health"))
            initial_health = None if late_health else event.get("health")
            self.actors[actor_id]["health"] = initial_health or self.actors[actor_id]["health"]
            item = self._append(candidate, actor_id=actor_id,
                                status="review" if late_health else "consistent")
            if late_health:
                item["observations"].append({"frame": item["frame"], "health": event["health"],
                                              "reason": "HUD confirmado después de comenzar una acción"})
                item["health"] = None
                self._issue("late_switch_health", "El PS leído al confirmar la entrada ya estaba en animación; PS de entrada desconocido.",
                            item["frame"], item["seq"])
            if candidate.get("anchor") and candidate["anchor"]["frame"] < candidate["observed_frame"]:
                item["note"] = f"Entrada anunciada en frame {candidate['anchor']['frame']}; HUD confirmó el slot en {item['frame']}."
            if late_health:
                item["note"] = (item["note"] or "") + " PS de entrada desconocido: la barra ya cambiaba."
            if self.turn:
                self.turn_activity += 1
            return
        if kind == "turn":
            turn = event.get("turn")
            if self.turn and self.turn_activity == 0:
                item = self._append(candidate, status="review",
                                    note="Nuevo turno sin acción/cambio/incapacidad en el anterior.")
                self._issue("empty_turn", item["note"], item["frame"], item["seq"])
            else:
                item = self._append(candidate)
            if isinstance(turn, int) and turn != self.turn + 1:
                item["status"] = "review"
                self._issue("turn_jump", f"Marcador de turno {turn} tras {self.turn}.", item["frame"], item["seq"])
            self.turn = int(turn or self.turn + 1)
            item["turn"] = self.turn
            self.turn_activity = 0
            self.last_action = None
            return
        if kind == "move":
            previous = self.last_action
            if (previous and previous["kind"] == "move" and previous["actor_id"] == actor_id
                and previous["move"] == event.get("move") and previous["turn"] == self.turn
                and candidate["observed_ms"] - previous["observed_ms"] <= 3_000
                and all(x["status"] == "suppressed" and x["kind"] == "move"
                        for x in self.events[previous["seq"]:])):
                previous["observations"].append({"frame": candidate["observed_frame"],
                                                  "text": "Aviso de movimiento repetido por OCR"})
                self._append(candidate, actor_id=actor_id, status="suppressed",
                             note="Mismo aviso de movimiento en fotogramas consecutivos.")
                return
            result = "consistent" if actor_id else "review"
            note = None if actor_id else "Movimiento sin actor activo en el slot."
            if actor_id and event.get("species") and self._resolve(event["species"]) != self.actors[actor_id]["species"]:
                result, note = "review", "El movimiento nombra otra especie que el ocupante del slot."
            item = self._append(candidate, actor_id=actor_id, status=result, note=note)
            self.last_action = item
            self.turn_activity += 1
            if note:
                self._issue("actor_mismatch", note, item["frame"], item["seq"])
            return
        if kind == "cant":
            self.turn_activity += 1
        if kind == "faint":
            item = self._append(candidate, actor_id=actor_id,
                                status="consistent" if actor_id else "review",
                                note=None if actor_id else "Debilitamiento sin actor activo.")
            if actor_id:
                self.actors[actor_id]["health"] = "0/100" if not self.actors[actor_id]["health"] else self.actors[actor_id]["health"]
                self.actors[actor_id]["fainted"] = True
                del self.active[slot]
            else:
                self._issue("faint_without_actor", item["note"], item["frame"], item["seq"])
            return
        if kind == "status" and actor_id:
            new = event.get("value")
            old = self.actors[actor_id]["status"]
            if old == new:
                self._append(candidate, actor_id=actor_id, status="suppressed",
                             note="Estado persistente; no se aplica una segunda vez.")
                return
            if old and old != new:
                item = self._append(candidate, actor_id=actor_id, status="review",
                                    note=f"Estado {old} reemplazado por {new} sin cura observada.")
                self._issue("status_transition", item["note"], item["frame"], item["seq"])
                return
            self.actors[actor_id]["status"] = new
        if kind == "curestatus" and actor_id:
            if not self.actors[actor_id]["status"]:
                item = self._append(candidate, actor_id=actor_id, status="review",
                                    note="Cura de estado sin estado previo.")
                self._issue("status_transition", item["note"], item["frame"], item["seq"])
                return
            self.actors[actor_id]["status"] = None
        if kind == "enditem" and actor_id:
            if self.actors[actor_id]["item_lost"]:
                self._append(candidate, actor_id=actor_id, status="suppressed",
                             note="El objeto ya se había consumido/perdido.")
                return
            self.actors[actor_id]["item_lost"] = True
        if kind == "mega":
            observed = self._resolve(event.get("species"))
            occupant = self.actors[actor_id]["species"] if actor_id else None
            if (not occupant or not observed or
                identity_species(observed) != identity_species(occupant)):
                item = self._append(candidate, actor_id=actor_id, status="suppressed",
                                    note="Megaevolución atribuida a un slot con otra especie o sin ocupante.")
                self._issue("mega_wrong_occupant", item["note"], item["frame"], item["seq"])
                return
            if event.get("forme"):
                self.actors[actor_id]["forme"] = event["forme"]
        if kind == "fieldstart" and "Terrain" in str(event.get("value")):
            self.terrain = str(event.get("value"))
        if kind == "fieldend" and "Terrain" in str(event.get("value")):
            self.terrain = None
        self._append(candidate, actor_id=actor_id)

    def _unparsed_actions(self) -> None:
        # Señales directas que desaparecieron antes de llegar a los eventos
        # candidatos. Son incidencias, no movimientos inventados.
        candidates = [item for item in self.events if item["kind"] in {"move", "faint"}]
        previous: dict[str, int] = {}
        for row in self.frames:
            frame = row["frame"]
            for line in row.get("ocr", ()):
                text = line.get("text", "").strip()
                found = RAW_ACTION.search(text)
                if not found or line.get("top", 0) < .55:
                    continue
                key = re.sub(r"\W+", "", text.casefold())
                if frame - previous.get(key, -100) <= 6:
                    previous[key] = frame
                    continue
                previous[key] = frame
                kind = "move" if found.group(1) else "faint"
                if any(item["kind"] == kind and abs(item["source_frame"] - frame) <= 8
                       and (kind == "faint" or difflib.SequenceMatcher(
                           a=(item.get("move") or "").casefold(), b=found.group(1).casefold()).ratio() >= .72)
                       for item in candidates if item.get("source_frame") is not None):
                    continue
                self._issue("unparsed_action_text", f"Texto OCR sin evento candidato: {text}", frame)

    def run(self) -> dict[str, Any]:
        for candidate in ordered_candidates(self.frames):
            self._handle(candidate)
        self._flush_hp()
        for actor_id, actor in self.actors.items():
            if actor["species"] == "unknown" or PLACEHOLDER.match(actor["species"]):
                first = next((item for item in self.events if item["actor_id"] == actor_id), None)
                self._issue("unresolved_identity", f"Identidad observada sin especie resuelta: {actor_id}.",
                            first["frame"] if first else self.frames[0]["frame"],
                            first["seq"] if first else None)
        if self.turn and self.turn_activity == 0 and not any(
            item["kind"] == "battle_end" and item["turn"] == self.turn for item in self.events
        ):
            self._issue("last_turn_without_action", "Último turno sin acción observable.",
                        self.frames[-1]["frame"])
        self._unparsed_actions()
        counts = collections.Counter(item["kind"] for item in self.events if item["status"] != "suppressed")
        return {"battle_index": self.battle_index, "first_frame": self.frames[0]["frame"],
                "last_frame": self.frames[-1]["frame"], "candidate_events": sum(
                    len(row["detections"]["events"]) for row in self.frames),
                "events": self.events, "issues": self.issues,
                "counts": dict(counts), "actors": self.actors}


def baseline_tokens(log: str) -> list[tuple[str, str, str]]:
    tokens = []
    for line in log.splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        kind = parts[1].removeprefix("-")
        if kind not in {"turn", "switch", "move", "faint", "ability", "mega"}:
            continue
        slot = parts[2].split(":", 1)[0] if kind != "turn" else ""
        value = (parts[3].split(", L", 1)[0] if len(parts) > 3 else "") if kind in {"move", "switch", "ability"} else ""
        if kind == "turn":
            value = parts[2]
        tokens.append((kind, slot, value))
    return tokens


def ledger_tokens(ledger: dict[str, Any]) -> list[tuple[str, str, str]]:
    tokens = []
    for item in ledger["events"]:
        kind = item["kind"]
        if item["status"] == "suppressed" or kind not in {"turn", "switch", "move", "faint", "ability", "mega"}:
            continue
        value = (item["move"] if kind == "move" else item["species"] if kind == "switch"
                 else item["value"] if kind == "ability" else str(item["turn"]) if kind == "turn" else "")
        tokens.append((kind, item["slot"] or "", value or ""))
    return tokens


def compare_baseline(ledger: dict[str, Any], log: str) -> dict[str, Any]:
    expected, actual = baseline_tokens(log), ledger_tokens(ledger)
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    differences = []
    for tag, i, j, x, y in matcher.get_opcodes():
        if tag != "equal":
            differences.append({"operation": tag, "baseline": expected[i:j], "automaton": actual[x:y]})
    baseline_hp = []
    for line in log.splitlines():
        parts = line.split("|")
        if len(parts) > 3 and parts[1] in {"-damage", "-heal"}:
            baseline_hp.append((parts[1][1:], parts[2].split(":", 1)[0], parts[3]))
    ledger_hp = [(item["kind"], item["slot"], item["after"])
                 for item in ledger["events"] if item["kind"] in HP_KINDS and item["status"] != "suppressed"]
    hp_matcher = difflib.SequenceMatcher(a=baseline_hp, b=ledger_hp, autojunk=False)
    hp_differences = []
    for tag, i, j, x, y in hp_matcher.get_opcodes():
        if tag != "equal":
            hp_differences.append({"operation": tag, "baseline": baseline_hp[i:j], "automaton": ledger_hp[x:y]})
    return {"baseline_core_events": len(expected), "automaton_core_events": len(actual),
            "aligned_events": sum(block.size for block in matcher.get_matching_blocks()),
            "exact_core_sequence": expected == actual, "first_differences": differences[:8],
            "baseline_hp_events": len(baseline_hp), "automaton_hp_episodes": len(ledger_hp),
            "aligned_hp_episodes": sum(block.size for block in hp_matcher.get_matching_blocks()),
            "exact_hp_sequence": baseline_hp == ledger_hp,
            "first_hp_differences": hp_differences[:8]}


def event_description(item: dict[str, Any]) -> str:
    kind, species, slot = item["kind"], item["species"] or item["actor_id"] or "?", item["slot"] or ""
    actor = f"{species} ({slot})" if slot else species
    if kind == "turn":
        return f"Inicio del turno {item['turn']}"
    if kind in {"switch", "drag"}:
        return f"Entra {actor}, PS {item['health'] or '?'}"
    if kind == "move":
        return f"{actor} usa {item['move']} → {item['target_slot'] or 'objetivo desconocido'}"
    if kind in HP_KINDS:
        return f"{actor}: PS {item['before'] or '?'} → {item['after'] or '?'} ({kind})"
    if kind in {"hp_ocr_conflict", "hp_zero_rebound"}:
        return f"{actor}: lecturas de PS en conflicto; se conserva {item['before'] or '?'}"
    if kind == "faint":
        return f"Se debilita {actor}"
    if kind == "status":
        return f"{actor}: {STATUS_NAMES.get(str(item['value']), item['value'])}"
    return f"{kind}: {actor} {item['value'] or ''}".strip()


def render_markdown(ledger: dict[str, Any], comparison: dict[str, Any] | None) -> str:
    lines = [f"# Batalla {ledger['battle_index'] + 1} — registro intermedio", "",
             f"Fotogramas {ledger['first_frame']}–{ledger['last_frame']}; "
             f"{ledger['candidate_events']} candidatos; {len(ledger['events'])} sucesos consolidados.",
             "", "**Consistente** significa que no se detectó contradicción estructural; no equivale a validación visual.", ""]
    if comparison:
        lines += [f"Referencia aceptada: {comparison['aligned_events']}/{comparison['baseline_core_events']} "
                  f"eventos principales alineados; PS {comparison['aligned_hp_episodes']}/"
                  f"{comparison['baseline_hp_events']}; secuencia principal exacta: "
                  f"{comparison['exact_core_sequence']}.", ""]
    section = None
    for item in ledger["events"]:
        if item["turn"] != section:
            section = item["turn"]
            lines += [f"## {'Apertura' if section == 0 else 'Turno ' + str(section)}", ""]
        if item["kind"] == "turn":
            continue
        if item["status"] == "suppressed":
            continue
        marker = "⚠️ " if item["status"] == "review" else ""
        mm, ss = divmod(item["logical_ms"] // 1000, 60)
        detail = f"{marker}{mm:02d}:{ss:02d} · {event_description(item)} [frame {item['source_frame']}]"
        if item["cause"] is not None:
            detail += f" · causa: {item['cause']}"
        lines += ["- " + detail]
        if item["note"]:
            lines += ["  - " + item["note"]]
        if item["kind"] in HP_KINDS and len(item["observations"]) > 1:
            seen = " → ".join(str(x["health"]) for x in item["observations"])
            lines += ["  - Lecturas durante la animación: " + seen]
        if item["evidence"]:
            lines += [f"  - Pantalla: «{item['evidence'][0]['text']}» (frame {item['evidence'][0]['frame']})."]
    lines += ["", "## Incidencias para revisión", ""]
    if ledger["issues"]:
        for issue in ledger["issues"]:
            lines += [f"- Frame {issue['frame']} · {issue['code']}: {issue['message']}"]
    else:
        lines += ["- Sin contradicciones estructurales detectadas en este primer corte."]
    lines += [""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diagnostic", type=Path, help="ZIP del diagnóstico, sin vídeo")
    source.add_argument("--trace", type=Path, help="ocr.trace.jsonl archivado")
    parser.add_argument("--out", type=Path, required=True, help="Directorio nuevo para el registro")
    args = parser.parse_args()
    if args.diagnostic:
        frames, baselines = read_diagnostic(args.diagnostic)
    else:
        frames, baselines = read_trace(args.trace), {}
    args.out.mkdir(parents=True, exist_ok=True)
    grouped: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for frame in frames:
        grouped[int(frame["battle_index"])].append(frame)
    report = {"source": args.diagnostic.name if args.diagnostic else args.trace.name,
              "sampled_frames": len(frames), "battles": []}
    for battle_index, group in sorted(grouped.items()):
        if not any(row["detections"].get("events") for row in group):
            continue
        ledger = BattleAutomaton(battle_index, group).run()
        comparison = compare_baseline(ledger, baselines[battle_index]) if battle_index in baselines else None
        (args.out / f"battle-{battle_index + 1:02d}.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.out / f"battle-{battle_index + 1:02d}.md").write_text(
            render_markdown(ledger, comparison), encoding="utf-8")
        report["battles"].append({"battle_index": battle_index,
                                  "candidate_events": ledger["candidate_events"],
                                  "ledger_events": len(ledger["events"]),
                                  "hp_episodes": sum(x["kind"] in HP_KINDS and x["status"] != "suppressed"
                                                     for x in ledger["events"]),
                                  "hp_conflicts": sum(x["kind"] == "hp_ocr_conflict" for x in ledger["events"]),
                                  "statuses": dict(collections.Counter(x["status"] for x in ledger["events"])),
                                  "issue_codes": dict(collections.Counter(x["code"] for x in ledger["issues"])),
                                  "comparison": comparison})
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in report["battles"]:
        comp = row["comparison"] or {}
        print(f"Batalla {row['battle_index'] + 1}: {row['candidate_events']} candidatos -> "
              f"{row['ledger_events']} sucesos; PS {row['hp_episodes']}; "
              f"incidencias {sum(row['issue_codes'].values())}; "
              f"referencia {comp.get('aligned_events', '-')}/{comp.get('baseline_core_events', '-')}")


if __name__ == "__main__":
    main()
