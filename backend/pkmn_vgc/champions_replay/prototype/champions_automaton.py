#!/usr/bin/env python3
"""Champions Ledger: autómata temporal para trazas OCR de Pokémon Champions.

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
HP_NARRATION = re.compile(
    r"(The opposing )?(.+?) (was hurt by its burn!|was damaged by the recoil!|"
    r"had its HP restored\.)", re.I)
HP_EFFECTS = {"was hurt by its burn!": "burn", "was damaged by the recoil!": "recoil",
              "had its hp restored.": "restoration"}
HP_NARRATION_WINDOW_MS = 3_000
HP_NARRATION_AMBIGUITY_MS = 500
MEGA_NARRATION = re.compile(
    r"(The opposing )?(.+?)[’']s (\S+) is reacting to .+?[’']s Omni Ring!", re.I)
FAINT_NARRATION = re.compile(r"(The opposing )?(.+?) fainted!", re.I)
STATUS_NAMES = {"brn": "quemado", "par": "paralizado", "slp": "dormido", "frz": "congelado", "psn": "envenenado", "tox": "muy envenenado"}
RAW_ACTION = re.compile(r"\bused\s+(.+?)!$|\bfainted!$", re.IGNORECASE)
ANNOUNCED_ENTRY = re.compile(r"\bsent out\s+(.+?)!$|^Go!\s+(.+?)!$", re.IGNORECASE)
HP_TEXT = re.compile(r"(?<!\d)\d{1,4}\s*(?:%|/\s*\d{1,4})(?!\d)")
HUD_NUMBER = re.compile(r"^(\d{1,3})\s*%?$")
PLACEHOLDER = re.compile(r"^__champions_actor_[^_]+_\d+__$")
# Zonas normalizadas de los cuatro HUD de Champions. Se comprueba el slot
# antes de aceptar una lectura: el 0% de p2b no puede confirmar PS de p2a.
HP_HUD_AREAS = {
    "p1a": (.10, .27, .87, .98), "p1b": (.30, .48, .87, .98),
    "p2a": (.66, .82, .08, .20), "p2b": (.89, .99, .08, .20),
}
NAME_HUD_AREAS = {
    "p1a": (.05, .27, .82, .91), "p1b": (.27, .49, .82, .91),
    "p2a": (.57, .81, .02, .10), "p2b": (.81, .99, .02, .10),
}


def hud_nickname(row: dict[str, Any], slot: str) -> str | None:
    left, right, top, bottom = NAME_HUD_AREAS[slot]
    matches = [line for line in row.get("ocr", ())
               if left <= line.get("left", -1) <= right and
               top <= line.get("top", -1) <= bottom and
               line.get("confidence", 0) >= .9 and
               re.search(r"[A-Za-z]{3}", line.get("text", ""))]
    return max(matches, key=lambda line: line["confidence"])["text"].casefold() if matches else None


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
    # A provisional detector ID can be reused when another slot appears.
    # Keep the first established nickname association, and resolve the ID
    # against the nickname actually displayed for this *appearance*.
    aliases: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
    resolutions: dict[str, set[str]] = collections.defaultdict(set)
    frame_lookup = {row["frame"]: row for row in frames}
    for row in frames:
        for side in aliases:
            for name, species in (row.get("resolved_aliases", {}).get(side) or {}).items():
                aliases[side].setdefault(name.casefold(), species)
        for raw, species in (row.get("resolved_identities") or {}).items():
            resolutions[raw].add(species)
    unambiguous = {raw: next(iter(species)) for raw, species in resolutions.items()
                   if len(species) == 1}
    for item in candidates:
        event = item["event"]
        slot = event.get("slot")
        frame = item["observed_frame"]
        if event["kind"] == "move" and slot in SLOTS and event.get("move"):
            # Narration names the actor; a repeated provisional ID sometimes
            # points the move at its partner instead. Correct only when one
            # nearby HUD displays that exact nickname on the same side.
            for row_frame in range(frame - 3, frame + 2):
                for line in frame_lookup.get(row_frame, {}).get("ocr", ()):
                    match = re.search(r"(?:The opposing )?(.+?) used\s+" +
                                      re.escape(event["move"]) + r"!$", line.get("text", ""), re.I)
                    if not match or line.get("top", 0) < .55:
                        continue
                    nickname = match.group(1).casefold()
                    named_species = aliases[slot[:2]].get(nickname)
                    if named_species and PLACEHOLDER.match(str(event.get("species"))):
                        item["canonical_species"] = named_species
                    peers = [s for s in SLOTS if s.startswith(slot[:2]) and
                             any(hud_nickname(frame_lookup.get(f, {}), s) == nickname
                                 for f in range(frame - 4, frame + 2))]
                    if len(peers) == 1 and peers[0] != slot:
                        item["event"] = event = {**event, "slot": peers[0]}
                        item["slot_correction"] = slot
                    break
                else:
                    continue
                break
        slot = event.get("slot")
        raw = event.get("species")
        if slot in SLOTS and raw and PLACEHOLDER.match(raw):
            names = [hud_nickname(frame_lookup.get(f, {}), slot)
                     for f in range(frame - 2, frame + 3)]
            named_species = next((aliases[slot[:2]][name] for name in names
                                  if name in aliases[slot[:2]]), None)
            local = frame_lookup.get(frame, {}).get("resolved_identities", {}).get(raw)
            clean = item.get("canonical_species") or named_species or local or unambiguous.get(raw)
            if clean:
                item["canonical_species"] = clean
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
        clean = item.get("canonical_species") or unambiguous.get(event.get("species"), event.get("species"))
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
    # Champions shows the apparent species until Zoroark's Ilusión breaks.
    # The detector describes that reveal as a switch, although the occupant
    # did not leave the slot. Link the visible disguise back to its real actor
    # before replaying candidates, so HP, items and earlier moves stay together.
    for index, item in enumerate(candidates):
        event = item["event"]
        if event["kind"] != "switch" or "Zoroark" not in str(event.get("species")):
            continue
        slot = event.get("slot")
        previous_index = next((i for i in range(index - 1, -1, -1)
                               if candidates[i]["event"]["kind"] in {"switch", "drag"} and
                               candidates[i]["event"].get("slot") == slot), None)
        if previous_index is None:
            continue
        apparent = candidates[previous_index]["event"].get("species")
        real = item.get("canonical_species") or unambiguous.get(event.get("species"), event.get("species"))
        if not apparent or identity_species(apparent) == identity_species(real or ""):
            continue
        observations = [(f, line["text"]) for f in range(item["observed_frame"], item["observed_frame"] + 9)
                        for line in frame_lookup.get(f, {}).get("ocr", ())
                        if "illusion wore off" in line.get("text", "").casefold()]
        if not observations:
            continue
        recent_health = next((c["event"].get("health") for c in reversed(candidates[previous_index:index])
                              if c["event"].get("slot") == slot and c["event"].get("health")), None)
        if recent_health and event.get("health") and recent_health != event["health"]:
            continue
        if any("sent out" in line.get("text", "").casefold() or "withdrew" in line.get("text", "").casefold()
               for f in range(item["observed_frame"] - 6, item["observed_frame"] + 1)
               for line in frame_lookup.get(f, {}).get("ocr", ())):
            continue
        for disguised in candidates[previous_index:index]:
            if disguised["event"].get("slot") == slot:
                disguised["canonical_species"] = real
                if disguised["event"].get("species"):
                    disguised["display_species"] = unambiguous.get(
                        disguised["event"]["species"], disguised["event"]["species"])
        item["canonical_species"] = real
        item["illusion_reveal"] = {"apparent": apparent, "actual": real,
                                   "frame": observations[0][0], "text": observations[0][1]}
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
        observed_resolutions: dict[str, set[str]] = collections.defaultdict(set)
        for row in frames:
            for raw, species in (row.get("resolved_identities") or {}).items():
                observed_resolutions[raw].add(species)
        self.id_resolution = {raw: next(iter(values)) for raw, values in observed_resolutions.items()
                              if len(values) == 1}
        self.nickname_species: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
        for row in frames:
            for side in self.nickname_species:
                for nickname, species in (row.get("resolved_aliases", {}).get(side) or {}).items():
                    self.nickname_species[side].setdefault(nickname.casefold(), species)
        self.events: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.resolved_issues: list[dict[str, Any]] = []
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
        self.hp_messages: list[dict[str, Any]] = []
        self.narration_links: list[dict[str, Any]] = []

    def _hp_message_actor(self, value: str, observed_ms: int | None = None) -> str | None:
        match = HP_NARRATION.fullmatch(value)
        if not match:
            return None
        side = "p2" if match.group(1) else "p1"
        named = match.group(2).casefold()
        named = self.nickname_species[side].get(named, named).casefold()
        matches = [actor_id for slot, actor_id in self.active.items()
                   if slot.startswith(side) and
                   named in {self.actors[actor_id]["species"].casefold(),
                             self.actors[actor_id]["species"].casefold().split("-", 1)[0]}]
        if not matches and observed_ms is not None:
            # Recoil narration may follow the faint announcement. The last
            # occupant remains identifiable until another actor enters.
            matches = list({item["actor_id"] for item in self.events
                            if item["kind"] == "faint" and item["status"] == "consistent" and
                            item["slot"].startswith(side) and item["slot"] not in self.active and
                            0 <= observed_ms - item["observed_ms"] <= HP_NARRATION_WINDOW_MS and
                            named in {self.actors[item["actor_id"]]["species"].casefold(),
                                      self.actors[item["actor_id"]]["species"].casefold().split("-", 1)[0]}})
        return matches[0] if len(matches) == 1 else None

    def _resolve(self, species: str | None) -> str | None:
        return self.id_resolution.get(species, species) if species else None

    def _actor_for_entry(self, slot: str, species: str | None) -> str:
        side = slot[:2]
        raw = species or "unknown"
        clean = self._resolve(raw) or raw
        if PLACEHOLDER.match(raw) and raw not in self.actors and raw not in self.active.values():
            actor_id = raw
        else:
            previous = self.actor_ids_by_species.get((side, identity_species(clean)), [])
            actor_id = next((candidate for candidate in previous if candidate not in self.active.values()), "")
            if not actor_id:
                self.next_actor += 1
                actor_id = f"{side}-actor-{self.next_actor:02d}"
        if actor_id not in self.actors:
            self.actors[actor_id] = {"side": side, "species": clean, "forme": None, "health": None,
                                     "health_state": "unknown", "status": None,
                                     "item_lost": False, "fainted": False}
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
                if other_value == expected or stronger["confidence"] <= suspect["confidence"]:
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

    def _hp_support(self, slot: str | None, health: str | None, frame: int) -> dict[str, Any]:
        """Confirm a proposed HP value using complete OCR in the correct HUD.

        The trace is archived, so the next two sampled frames may corroborate
        an animation. A new occupant in that interval ends the evidence window.
        """
        if slot not in HP_HUD_AREAS or not health or not re.fullmatch(r"\d+/\d+", health):
            return {"state": "unconfirmed", "reason": "PS o slot sin formato comprobable", "evidence": []}
        current, maximum = map(int, health.split("/"))
        percent_hud = slot.startswith("p2")
        left, right, top, bottom = HP_HUD_AREAS[slot]
        full: list[dict[str, Any]] = []
        split: list[dict[str, Any]] = []
        bare: list[dict[str, Any]] = []
        repaired: list[dict[str, Any]] = []
        for offset in range(3):
            row = self.frame_lookup.get(frame + offset)
            if not row:
                continue
            if offset and any(e["kind"] in {"switch", "drag"} and e.get("slot") == slot
                              for e in row.get("detections", {}).get("events", ())):
                break
            lines = [line for line in row.get("ocr", ())
                     if (left <= line.get("left", -1) <= right and
                         top <= line.get("top", -1) <= bottom and
                         line.get("right", right) <= right + .02 and
                         line.get("bottom", bottom) <= bottom + .02)]
            for line in lines:
                value = re.sub(r"\s+", "", line.get("text", ""))
                entry = {"frame": frame + offset, "text": line["text"],
                         "confidence": float(line.get("confidence", 0))}
                if percent_hud and value == f"{current}%":
                    full.append(entry)
                elif not percent_hud and value == health:
                    full.append(entry)
                elif (not percent_hud and value == f"{current}1{maximum}" and
                      entry["confidence"] >= .9):
                    entry["separator_repaired"] = True
                    full.append(entry)
                elif (not percent_hud and re.fullmatch(
                        rf"{current}\d{maximum}", value) and entry["confidence"] >= .9):
                    # A second digit may replace the slash; require a second
                    # observation before repairing any separator except '1'.
                    entry["separator_repaired"] = True
                    repaired.append(entry)
                elif percent_hud and value == str(current):
                    bare.append(entry)
                    percent = next((other for other in lines
                                    if other is not line and other.get("text", "").strip() == "%" and
                                    -.015 <= other.get("left", -1) - line.get("right", 1) <= .025 and
                                    other.get("confidence", 0) >= .9 and
                                    abs(other.get("top", 0) - line.get("top", 1)) <= .035), None)
                    if percent:
                        entry["percent"] = {"text": "%", "confidence": percent["confidence"]}
                        split.append(entry)
        threshold = .97 if percent_hud and current < 10 else .9
        strong = [x for x in full if x["confidence"] >= threshold]
        if strong:
            return {"state": "confirmed", "reason": "OCR completo del HUD",
                    "evidence": [max(strong, key=lambda x: x["confidence"])]}
        if len({x["frame"] for x in full if x["confidence"] >= .9}) >= 2:
            return {"state": "confirmed", "reason": "OCR repetido del HUD", "evidence": full}
        if len({x["frame"] for x in repaired}) >= 2:
            return {"state": "confirmed", "reason": "separador OCR reparado en dos frames",
                    "evidence": repaired}
        if any(x["confidence"] >= .9 for x in split):
            return {"state": "confirmed", "reason": "número y porcentaje separados",
                    "evidence": [max(split, key=lambda x: x["confidence"])]}
        return {"state": "unconfirmed", "reason": "lectura parcial o sin confirmación en el HUD",
                "evidence": full + repaired + split + bare}

    def _stable_hp_before_conflict(self, slot: str | None, health: str | None,
                                   frame: int, stronger: dict[str, Any]) -> dict[str, Any] | None:
        """Find repeated complete HP just before a weaker overlapping OCR fragment."""
        if not slot or not slot.startswith("p2") or slot not in HP_HUD_AREAS or not health:
            return None
        target = health.split("/", 1)[0]
        if stronger["text"].strip() != target or stronger["confidence"] < .97:
            return None
        left, right, top, bottom = HP_HUD_AREAS[slot]

        def in_hud(line: dict[str, Any]) -> bool:
            return (left <= line.get("left", -1) <= right and
                    top <= line.get("top", -1) <= bottom and
                    line.get("right", right) <= right + .02 and
                    line.get("bottom", bottom) <= bottom + .02)

        current = self.frame_lookup.get(frame, {})
        if not any(in_hud(line) and line.get("text", "").strip() == target and
                   line.get("confidence", 0) >= stronger["confidence"]
                   for line in current.get("ocr", ())):
            return None
        evidence = []
        for number in range(frame - 1, frame - 4, -1):
            row = self.frame_lookup.get(number)
            if not row:
                continue
            if any(event["kind"] in {"switch", "drag"} and event.get("slot") == slot
                   for event in row.get("detections", {}).get("events", ())):
                break
            for line in row.get("ocr", ()):
                if in_hud(line) and line.get("text", "").replace(" ", "") == target + "%" and \
                        line.get("confidence", 0) >= .9:
                    evidence.append({"frame": number, "text": line["text"],
                                     "confidence": line["confidence"]})
                    break
        if len(evidence) < 2:
            return None
        return {"state": "confirmed", "reason": "PS completos repetidos antes del fragmento OCR",
                "evidence": list(reversed(evidence))}

    def _terrain_restoration_context(self, slot: str | None, actor_id: str | None,
                                     before: str | None, partial: dict[str, Any],
                                     healing: dict[str, Any]) -> dict[str, Any] | None:
        """Corroborate a terrain tick after an overlapping, truncated HP reading."""
        if (not slot or not slot.startswith("p2") or not actor_id or not before or
            not self.terrain or "Grassy Terrain" not in self.terrain or
            not partial.get("competing_ocr")):
            return None
        after = healing["event"].get("health")
        old, new = health_ratio(before), health_ratio(after)
        if old is None or new is None or not .045 <= new - old <= .075:
            return None
        conflict = partial["competing_ocr"]
        suspect = HUD_NUMBER.fullmatch(conflict["suspect"]["text"])
        stronger = HUD_NUMBER.fullmatch(conflict["stronger"]["text"])
        if not suspect or not stronger:
            return None
        prior_number = int(before.split("/", 1)[0])
        final_number = int(after.split("/", 1)[0])
        if not (str(prior_number).endswith(suspect.group(1)) and
                prior_number < int(stronger.group(1)) < final_number):
            return None
        support = self._hp_support(slot, after, healing["observed_frame"])
        if support["state"] != "confirmed":
            return None
        for number in range(healing["observed_frame"] + 1,
                            healing["observed_frame"] + 7):
            row = self.frame_lookup.get(number, {})
            for event in row.get("detections", {}).get("events", ()):
                if (event["kind"] in {"switch", "drag"} and event.get("slot") == slot or
                    event["kind"] == "fieldend" and "Terrain" in str(event.get("value"))):
                    return None
                message = str(event.get("value") or "")
                if (event["kind"] == "message" and message.endswith("had its HP restored.") and
                    self._hp_message_actor(message) == actor_id):
                    return {"state": "confirmed", "reason": "cura de terreno de ~1/16 y mensaje del actor",
                            "evidence": support["evidence"] + [{"frame": number, "text": message}]}
        return None

    def _entry_baseline(self, candidate: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Find a complete, stable HUD value after entry and before any action.

        A directly observed pre-action value takes precedence over the
        first-appearance full-health assumption.
        """
        slot = candidate["event"].get("slot")
        if slot not in SLOTS:
            return None
        start = candidate.get("logical_frame", candidate["observed_frame"])
        species = candidate.get("canonical_species") or self._resolve(candidate["event"].get("species"))
        observations: list[tuple[int, str]] = []
        left, right, top, bottom = HP_HUD_AREAS[slot]
        for frame in range(start, min(start + 121, self.frames[-1]["frame"] + 1)):
            row = self.frame_lookup.get(frame)
            if not row:
                continue
            if frame > start and any(e["kind"] in {"move", "damage", "heal"} or
                                     (e["kind"] in {"switch", "drag"} and e.get("slot") == slot)
                                     for e in row.get("detections", {}).get("events", ())):
                break
            nickname = hud_nickname(row, slot)
            identified = self.nickname_species[slot[:2]].get(nickname or "", nickname or "")
            if not species or not nickname or (identity_species(identified).casefold() !=
                                               identity_species(species).casefold() and
                                               nickname != species.casefold().split("-", 1)[0]):
                continue
            for line in row.get("ocr", ()):
                if not (left <= line.get("left", -1) <= right and
                        top <= line.get("top", -1) <= bottom and
                        line.get("confidence", 0) >= .9):
                    continue
                value = line.get("text", "").replace(" ", "")
                if slot.startswith("p2") and re.fullmatch(r"\d{1,3}%", value):
                    health = value[:-1] + "/100"
                elif slot.startswith("p1") and re.fullmatch(r"\d{1,4}/\d{1,4}", value):
                    health = value
                else:
                    continue
                numerator, denominator = map(int, health.split("/"))
                if 0 <= numerator <= denominator and denominator:
                    observations.append((frame, health))
        if not observations or len({value for _, value in observations}) != 1:
            return None
        frame, health = observations[0]
        support = self._hp_support(slot, health, frame)
        if support["state"] != "confirmed":
            return None
        support = {**support, "reason": "OCR del HUD anterior a la primera acción"}
        return health, support

    def _pre_impact_baseline(self, episode: HpEpisode, actor: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Use a complete HP reading immediately before an animation changes it.

        The move announcement precedes the impact, so stopping at the move
        would discard a genuine last pre-hit HUD. Require the same occupant's
        nickname and one unambiguous value in the preceding three frames.
        """
        slot = episode.slot
        if slot not in SLOTS or not actor.get("species"):
            return None
        first_frame = episode.candidates[0]["observed_frame"]
        left, right, top, bottom = HP_HUD_AREAS[slot]
        found: list[tuple[int, str, dict[str, Any]]] = []
        for frame in range(first_frame - 3, first_frame):
            row = self.frame_lookup.get(frame)
            if not row:
                continue
            if any(e["kind"] in HP_KINDS and e.get("slot") == slot
                   for e in row.get("detections", {}).get("events", ())):
                return None
            if any(e["kind"] in {"switch", "drag"} and e.get("slot") == slot
                   for e in row.get("detections", {}).get("events", ())):
                found.clear()
                continue
            nickname = hud_nickname(row, slot)
            species = self.nickname_species[slot[:2]].get(nickname or "", nickname or "")
            if not nickname or (identity_species(species).casefold() !=
                                identity_species(actor["species"]).casefold() and
                                nickname != actor["species"].casefold().split("-", 1)[0]):
                continue
            for line in row.get("ocr", ()):
                if not (left <= line.get("left", -1) <= right and
                        top <= line.get("top", -1) <= bottom):
                    continue
                value = line.get("text", "").replace(" ", "")
                if slot.startswith("p2") and re.fullmatch(r"\d{1,3}%", value):
                    health = value[:-1] + "/100"
                elif slot.startswith("p1") and re.fullmatch(r"\d{1,4}/\d{1,4}", value):
                    health = value
                else:
                    continue
                numerator, denominator = map(int, health.split("/"))
                threshold = .97 if slot.startswith("p2") and numerator < 10 else .96
                if not denominator or numerator > denominator or line.get("confidence", 0) < threshold:
                    continue
                found.append((frame, health, {"frame": frame, "text": line["text"],
                                              "nickname": nickname,
                                              "confidence": line["confidence"]}))
        if len({value for _, value, _ in found}) != 1:
            return None
        _, health, evidence = max(found, key=lambda item: item[0])
        return health, {"state": "confirmed", "reason": "HUD inmediatamente antes del cambio de PS",
                        "evidence": [evidence]}

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
        if candidate.get("illusion_reveal"):
            a = candidate["illusion_reveal"]
            evidence.insert(0, {"frame": a["frame"], "text": a["text"], "confidence": None})
        item = {
            "seq": len(self.events) + 1, "battle_index": self.battle_index,
            "turn": self.turn, "kind": kind, "status": status,
            "actor_id": actor_id, "slot": event.get("slot"),
            "species": ((self.actors[actor_id].get("forme")
                         if actor_id in self.actors and self.actors[actor_id].get("forme") and
                         identity_species(self.actors[actor_id]["forme"]) == identity_species(
                             candidate.get("canonical_species") or self.actors[actor_id]["species"])
                         else candidate.get("canonical_species")) or
                        (self.actors[actor_id].get("forme") or self._resolve(event.get("species"))
                         if actor_id in self.actors else self._resolve(event.get("species")))),
            "display_species": candidate.get("display_species"),
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
        if candidate.get("slot_correction"):
            item["original_slot"] = candidate["slot_correction"]
            item["note"] = ((item["note"] + " ") if item["note"] else "") + (
                f"Slot corregido desde {candidate['slot_correction']} por mote visible en el HUD.")
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
            support = self._hp_support(episode.slot, after, last["observed_frame"])
            baseline = (self._pre_impact_baseline(episode, actor)
                        if actor and (before is None or actor["health_state"] == "inferred")
                        and support["state"] == "confirmed" else None)
            if baseline:
                expected, measured = health_ratio(baseline[0]), health_ratio(after)
                if expected is None or measured is None or not (
                    (episode.kind == "damage" and expected > measured) or
                    (episode.kind == "heal" and expected < measured)):
                    baseline = None
                else:
                    before = baseline[0]
            if (not baseline and actor and actor["health_state"] == "inferred" and
                before is None and support["state"] == "confirmed" and after and
                re.fullmatch(r"\d+/[1-9]\d*", after)):
                maximum = after.split("/", 1)[1]
                before = f"{maximum}/{maximum}"
                entry_seq = actor.get("first_entry_seq")
                if entry_seq:
                    self.events[entry_seq - 1]["health"] = before
                    self.events[entry_seq - 1]["note"] = (
                        (self.events[entry_seq - 1]["note"] or "") +
                        f" Máximo {maximum} deducido del primer HUD completo.")
            inferred_baseline = bool(actor and actor["health_state"] == "inferred" and not baseline and before)
            previous = (self._hp_support(episode.slot, before, last["observed_frame"] - 2)
                        if actor and before and before != after and support["state"] != "confirmed"
                        else {"state": "unconfirmed", "evidence": []})
            hud_box = HP_HUD_AREAS.get(episode.slot)
            before_label = (before.split("/", 1)[0] + "%" if episode.slot and
                            episode.slot.startswith("p2") and before else before)
            previous_still_visible = bool(
                previous["state"] == "confirmed" and hud_box and before_label and
                any(line.get("text", "").replace(" ", "") == before_label and
                    line.get("confidence", 0) >= .9 and
                    hud_box[0] <= line.get("left", -1) <= hud_box[1] and
                    hud_box[2] <= line.get("top", -1) <= hud_box[3]
                    for line in self.frame_lookup.get(last["observed_frame"], {}).get("ocr", ())))
            if previous_still_visible:
                item = self._append(last, kind="hp_rejected_reading", actor_id=episode.actor_id,
                                    status="suppressed", before=before, after=before,
                                    note="El HUD conserva los PS previos; lectura candidata descartada.",
                                    observations=observations)
                item["narration"].extend(episode.narration)
                item["hp_state"], item["hp_support"] = "rejected", previous
                continue
            # Un porcentaje aislado y contradicho por una lectura más fuerte
            # del mismo HUD no establece daño/curación ni modifica el estado.
            # Si se confirma en frames posteriores, llegará como otro episodio.
            if len(episode.candidates) == 1 and last.get("competing_ocr"):
                conflict = last["competing_ocr"]
                restored = last.get("restoration_context")
                if restored:
                    note = (f"OCR {conflict['suspect']['text']} recortado durante una cura de "
                            f"Grassy Terrain; PS previos {before} y finales confirmados, "
                            "con mensaje de recuperación del mismo actor.")
                    item = self._append(last, kind="hp_rejected_reading", actor_id=episode.actor_id,
                                        status="suppressed", before=before, after=before,
                                        note=note, observations=observations)
                    item["narration"].extend(episode.narration)
                    item["hp_state"], item["hp_support"] = "rejected", restored
                    continue
                stable = self._stable_hp_before_conflict(
                    episode.slot, before, last["observed_frame"], conflict["stronger"])
                if stable:
                    note = (f"OCR {conflict['suspect']['text']} recortado: "
                            f"{conflict['stronger']['text']} coincide con los PS completos "
                            "repetidos en este HUD antes del conflicto.")
                    item = self._append(last, kind="hp_rejected_reading", actor_id=episode.actor_id,
                                        status="suppressed", before=before, after=before,
                                        note=note, observations=observations)
                    item["narration"].extend(episode.narration)
                    item["hp_state"], item["hp_support"] = "rejected", stable
                    continue
                note = (f"OCR {conflict['suspect']['text']} contradicho en el mismo HUD por "
                        f"{conflict['stronger']['text']} de mayor confianza; PS sin confirmar.")
                item = self._append(last, kind="hp_ocr_conflict", actor_id=episode.actor_id,
                                    status="review", before=before, after=before,
                                    note=note, observations=observations)
                item["narration"].extend(episode.narration)
                item["hp_state"] = "rejected"
                self._issue("hp_ocr_conflict", note, item["frame"], item["seq"])
                continue
            if support["state"] != "confirmed" and before != after:
                note = (f"PS propuestos {after or '?'} sin respaldo suficiente del HUD {episode.slot}; "
                        "la transición queda pendiente de revisión.")
                item = self._append(last, kind="hp_unconfirmed", actor_id=episode.actor_id,
                                    status="review", before=before, after=None, note=note,
                                    observations=observations)
                item["narration"].extend(episode.narration)
                item["hp_state"], item["hp_support"] = "unconfirmed", support
                if actor:
                    actor["health"], actor["health_state"] = None, "unconfirmed"
                self._issue("hp_unconfirmed", note, item["frame"], item["seq"])
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
                result = "review"
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
            item["hp_state"], item["hp_support"] = support["state"], support
            if last.get("terrain_heal_confirmed"):
                item["terrain_restoration_support"] = last["terrain_heal_confirmed"]
            if baseline:
                item["hp_baseline"] = baseline[1]
            elif inferred_baseline:
                item["hp_baseline"] = {"state": "inferred", "reason": "primer avistamiento; PS iniciales al máximo",
                                       "evidence": []}
            if actor and result == "consistent":
                actor["health"], actor["health_state"] = after, "confirmed"
            elif actor and result == "review" and before is None and support["state"] == "confirmed":
                # The final value is observed, but its transition from an
                # unknown baseline has not been established.
                actor["health"], actor["health_state"] = after, "confirmed"
            elif actor and result == "review" and not actor.get("fainted"):
                actor["health"], actor["health_state"] = None, "unconfirmed"
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
        if (actor_id and event.get("species") and
            (candidate.get("canonical_species") or self._resolve(event["species"])) != self.actors[actor_id]["species"]):
            self._flush_hp()
            item = self._append(candidate, actor_id=actor_id, status="review",
                                note="HUD atribuido a otra especie que la ocupante del slot.")
            self._issue("hp_wrong_occupant", item["note"], item["frame"], item["seq"])
            return
        key = f"{slot or '?'}:{actor_id or '?'}"
        pending = self.hp_pending.get(key)
        current_actor = self.actors.get(actor_id or "", {})
        stable = current_actor.get("health") if current_actor.get("health_state") == "confirmed" else None
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
            suspect = pending.candidates[-1]
            stable_before = (self._stable_hp_before_conflict(
                slot, stable, suspect["observed_frame"], suspect["competing_ocr"]["stronger"])
                if suspect.get("competing_ocr") else None)
            stable_after = (self._hp_support(slot, stable, candidate["observed_frame"])
                            if stable_before else None)
            confirmed_fragment = bool(stable_after and stable_after["state"] == "confirmed")
            note = "Dos lecturas opuestas antes de la primera acción regresan al PS previo."
            if confirmed_fragment:
                note = (f"El fragmento OCR {conflict['suspect']['text']} contradice "
                        f"{conflict['stronger']['text']} en el mismo HUD; los PS completos "
                        "son estables antes y después, sin acción entre ambas lecturas.")
            elif conflict:
                note += (f" En el mismo HUD, {conflict['suspect']['text']} compite con "
                         f"{conflict['stronger']['text']} de mayor confianza OCR.")
            item = self._append(candidate, kind="hp_rejected_reading" if confirmed_fragment else "hp_oscillation",
                                actor_id=actor_id, status="suppressed" if confirmed_fragment else "review",
                                before=stable, after=stable,
                                note=note,
                                observations=[{"frame": x["observed_frame"], "health": x["event"].get("health"),
                                               "evidence": self._evidence(x, x["event"]["kind"]),
                                               **({"competing_ocr": x["competing_ocr"]} if x.get("competing_ocr") else {})}
                                              for x in reads])
            item["narration"].extend(pending.narration)
            item["hp_state"] = "rejected"
            if confirmed_fragment:
                item["hp_support"] = {"state": "confirmed",
                                      "reason": "PS completos estables antes y después del fragmento OCR",
                                      "evidence": stable_before["evidence"] + stable_after["evidence"]}
            else:
                self._issue("hp_oscillation", item["note"], item["frame"], item["seq"])
            return
        if (pending and len(pending.candidates) == 1 and pending.kind == "damage" and
            event["kind"] == "heal" and stable and
            candidate["observed_ms"] - pending.last_ms <= 1_500):
            restoration = self._terrain_restoration_context(
                slot, actor_id, stable, pending.candidates[0], candidate)
            if restoration:
                pending.candidates[0]["restoration_context"] = restoration
                candidate["terrain_heal_confirmed"] = restoration
        # El detector puede llamar "heal" a un valor intermedio de una barra
        # que sigue bajando (o viceversa). La dirección de una animación se
        # decide por los PS estables, no por la etiqueta de cada fotograma.
        if pending and pending.kind != event["kind"] and stable and (
            candidate["observed_ms"] - pending.last_ms <= 1_500 and
            not any(x.get("competing_ocr") for x in pending.candidates + [candidate])
        ):
            old = health_ratio(stable)
            middle = health_ratio(pending.candidates[-1]["event"].get("health"))
            new = health_ratio(event.get("health"))
            if old is not None and middle is not None and new is not None and (
                old > middle > new or old < middle < new
            ):
                pending.kind = "damage" if old > new else "heal"
                pending.candidates.append(candidate)
                return
        if pending and (pending.kind != event["kind"] or
                        candidate["observed_ms"] - pending.last_ms > 1_500):
            self._flush_hp()
            pending = None
        if (event["kind"] == "heal" and actor_id and
            self.actors[actor_id]["health_state"] == "confirmed" and
            health_ratio(self.actors[actor_id]["health"]) == 0):
            before = self.actors[actor_id]["health"]
            note = "PS en cero antes del debilitamiento; una lectura aislada no confirma reanimación."
            item = self._append(candidate, kind="hp_zero_rebound", actor_id=actor_id,
                                status="review", before=before, after=before, note=note,
                                observations=[{"frame": candidate["observed_frame"],
                                               "health": event.get("health"),
                                               "evidence": self._evidence(candidate, "heal")}])
            item["hp_state"] = "rejected"
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
            row = self.frame_lookup.get(candidate["observed_frame"], {})
            menu_labels = {line.get("text", "").casefold() for line in row.get("ocr", ())}
            if ("move time" in menu_labels or "battle info" in menu_labels) and (
                "has no energy left to battle!" in value.casefold() or
                "can't use its sealed" in value.casefold()
            ):
                self._append(candidate, kind="ui_text", status="suppressed",
                             note="Aviso del menú de selección; no ocurrió como acción de batalla.")
                return
            if "battle has ended" in value.casefold() or "forfeit" in value.casefold():
                self._flush_hp()
                self._append(candidate, kind="battle_end", note="Fin de la batalla observado en la pantalla.")
                return
            hp_message = HP_NARRATION.fullmatch(value)
            if hp_message:
                hp_actor = self._hp_message_actor(value, candidate["observed_ms"])
                hp_slot = next((s for s, a in self.active.items() if a == hp_actor), None)
                if hp_actor and not hp_slot:
                    hp_slot = next(item["slot"] for item in reversed(self.events)
                                   if item["kind"] == "faint" and item["actor_id"] == hp_actor)
                self.hp_messages.append({
                    "frame": candidate["observed_frame"], "source_frame": event.get("source_frame"),
                    "observed_ms": candidate["observed_ms"], "text": value,
                    "effect": HP_EFFECTS[hp_message.group(3).casefold()],
                    "actor_id": hp_actor,
                    "slot": hp_slot,
                    "turn": self.turn, "confidence": event.get("confidence"),
                })
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
            species = candidate.get("canonical_species") or self._resolve(event.get("species"))
            if candidate.get("illusion_reveal"):
                old_health = self.actors[actor_id]["health"] if actor_id else None
                support = (self._hp_support(slot, event["health"], candidate["observed_frame"])
                           if event.get("health") else None)
                confirmed = (bool(actor_id) and self.actors[actor_id]["species"] == species and
                             (not support or support["state"] == "confirmed") and
                             (not old_health or not event.get("health") or old_health == event["health"]))
                reveal = candidate["illusion_reveal"]
                note = (f"La Ilusión de {reveal['apparent']} terminó; el mismo actor es {species}."
                        if confirmed else "Revelación de Ilusión sin continuidad de identidad/PS confirmada.")
                item = self._append(candidate, kind="illusion_reveal", actor_id=actor_id,
                                    status="consistent" if confirmed else "review",
                                    note=note, before=old_health,
                                    after=(event.get("health") or old_health) if confirmed else
                                    old_health if not event.get("health") or old_health == event["health"] else None)
                item["hp_state"] = support["state"] if support else "unknown"
                item["hp_support"] = support
                if actor_id and confirmed and support and not old_health:
                    self.actors[actor_id]["health"] = event["health"]
                    self.actors[actor_id]["health_state"] = "confirmed"
                elif actor_id and not confirmed and old_health and event.get("health") != old_health:
                    self.actors[actor_id]["health"] = None
                    self.actors[actor_id]["health_state"] = "unconfirmed"
                if not confirmed:
                    self._issue("illusion_reveal_mismatch", note, item["frame"], item["seq"])
                return
            if actor_id and identity_species(self.actors[actor_id]["species"]) == identity_species(species or ""):
                item = self._append(candidate, actor_id=actor_id, status="suppressed",
                                    note="Entrada de la especie que ya ocupaba el slot.")
                original = next((e for e in reversed(self.events[:-1])
                                 if e["kind"] in {"switch", "drag"} and e["slot"] == slot and
                                 e["status"] != "suppressed"), None)
                since = self.events[original["seq"]:-1] if original else []
                if (original and candidate["observed_frame"] - original["frame"] <= 80 and
                    not any(e["kind"] in HP_KINDS | {"move"} for e in since)):
                    if original["health"] is None and event.get("health") and (
                        self.actors[actor_id]["health"] is None or
                        self.actors[actor_id]["health"] == event["health"]):
                        support = self._hp_support(slot, event["health"], candidate["observed_frame"])
                        if support["state"] == "confirmed":
                            self.actors[actor_id]["health"] = event["health"]
                            self.actors[actor_id]["health_state"] = "confirmed"
                            original["health"] = event["health"]
                            original["hp_state"], original["hp_support"] = "confirmed", support
                            original["observations"].append({"frame": item["frame"], "health": event["health"],
                                                             "reason": "Confirmación posterior antes de la acción"})
                    item["note"] = "Segunda lectura de la misma entrada antes de actuar."
                else:
                    self._issue("reentry_without_exit", item["note"], item["frame"], item["seq"])
                return
            actor_id = self._actor_for_entry(slot, candidate.get("canonical_species") or event.get("species"))
            first_appearance = not self.actors[actor_id].get("seen_entry", False)
            if self.actors[actor_id]["fainted"]:
                item = self._append(candidate, actor_id=actor_id, status="suppressed",
                                    note="El HUD conserva la especie debilitada; no hubo reanimación observada.")
                self._issue("ghost_reentry_after_faint", item["note"], item["frame"], item["seq"])
                return
            self.active[slot] = actor_id
            self.actors[actor_id]["fainted"] = False
            self.actors[actor_id]["seen_entry"] = True
            late_health = bool(candidate.get("late_health") and event.get("health"))
            support = (self._hp_support(slot, event["health"], candidate["observed_frame"])
                       if event.get("health") else None)
            baseline = (self._entry_baseline(candidate) if self.actors[actor_id]["health"] is None and
                        (not support or late_health or support["state"] != "confirmed") else None)
            entry_conflict = self._competing_hp_ocr(candidate) if support else None
            if entry_conflict:
                support = {"state": "unconfirmed", "reason": "OCR numérico contradictorio en el mismo HUD",
                           "evidence": [entry_conflict]}
            initial_health = (baseline[0] if baseline else event.get("health") if not late_health and support and
                              support["state"] == "confirmed" else None)
            assume_full = first_appearance and not baseline and (
                not event.get("health") or late_health) and initial_health is None
            if assume_full and slot.startswith("p2"):
                initial_health = "100/100"
            if event.get("health") or baseline:
                self.actors[actor_id]["health"] = initial_health
                self.actors[actor_id]["health_state"] = (
                    "confirmed" if initial_health else "unknown" if late_health else "unconfirmed")
            if assume_full:
                self.actors[actor_id]["health"] = initial_health
                self.actors[actor_id]["health_state"] = "inferred"
            item = self._append(candidate, actor_id=actor_id,
                                status="review" if not baseline and not assume_full and
                                (late_health or (support and support["state"] != "confirmed"))
                                else "consistent")
            if assume_full:
                item["health"] = initial_health
                item["hp_state"] = "inferred"
                item["hp_support"] = {"state": "inferred", "reason": "primer avistamiento; PS iniciales al máximo",
                                      "evidence": []}
                self.actors[actor_id]["first_entry_seq"] = item["seq"]
                if initial_health is None:
                    item["note"] = "Primer avistamiento: PS al máximo inferidos; máximo aún desconocido."
            if baseline:
                item["health"] = baseline[0]
                item["hp_state"], item["hp_support"] = "confirmed", baseline[1]
                item["observations"].append({"frame": baseline[1]["evidence"][0]["frame"],
                                             "health": baseline[0], "reason": baseline[1]["reason"]})
            if not event.get("health") and self.actors[actor_id]["health_state"] == "confirmed":
                item["last_confirmed_health"] = self.actors[actor_id]["health"]
            if support and not assume_full:
                item["hp_state"], item["hp_support"] = (
                    "unknown" if late_health else support["state"]), support
            if late_health and not baseline:
                item["observations"].append({"frame": item["frame"], "health": event["health"],
                                              "reason": "HUD después de comenzar una acción; lectura transitoria"})
                if not assume_full:
                    item["health"] = None
                    self._issue("late_switch_health", "El PS leído al confirmar la entrada ya estaba en animación; PS de entrada desconocido.",
                                item["frame"], item["seq"])
            elif support and support["state"] != "confirmed" and not baseline:
                item["observations"].append({"frame": item["frame"], "health": event["health"],
                                              "reason": support["reason"]})
                item["health"] = None
                self._issue("hp_unconfirmed", "PS de entrada sin lectura confirmada en el HUD del slot.",
                            item["frame"], item["seq"])
            if candidate.get("anchor") and candidate["anchor"]["frame"] < candidate["observed_frame"]:
                item["note"] = f"Entrada anunciada en frame {candidate['anchor']['frame']}; HUD confirmó el slot en {item['frame']}."
            if late_health and not baseline:
                item["note"] = (item["note"] or "") + (
                    " PS iniciales al máximo inferidos; el HUD ya cambiaba." if assume_full else
                    " PS de entrada desconocido: la barra ya cambiaba.")
            elif support and support["state"] != "confirmed" and not baseline:
                item["note"] = (item["note"] or "") + " PS de entrada pendiente de revisión."
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
            if (actor_id and event.get("species") and
                (candidate.get("canonical_species") or self._resolve(event["species"])) != self.actors[actor_id]["species"]):
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
                actor = self.actors[actor_id]
                if health_ratio(actor["health"]) != 0:
                    # Faint implies zero but does not establish a measured
                    # numerator/denominator for this HUD.
                    actor["health"], actor["health_state"] = None, "unknown"
                actor["fainted"] = True
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
                item["mega_candidate"] = {"species": observed, "forme": event.get("forme"),
                                           "stone": event.get("value")}
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

    def _reconcile_hp_narration(self) -> None:
        """Link typed messages after all HP episodes have been consolidated.

        Display order varies by effect. Search both sides of the message in
        a bounded window, using the actor captured during the forward pass.
        No HP value, event order or rejected observation changes in this pass.
        """
        episodes = []
        for item in self.events:
            if (item["kind"] not in HP_KINDS or item["status"] != "consistent" or
                item.get("hp_state") != "confirmed"):
                continue
            times = [self.frame_lookup[obs["frame"]]["timestamp_ms"]
                     for obs in item["observations"] if obs["frame"] in self.frame_lookup]
            episodes.append((item, min(times, default=item["observed_ms"]),
                             max(times, default=item["observed_ms"])))
        barriers = [item for item in self.events if item["status"] != "suppressed" and
                    item["kind"] in {"turn", "move", "cant", "switch", "drag", "battle_end"}]
        proposals = []
        for message in reversed(self.hp_messages):
            kind = "heal" if message["effect"] == "restoration" else "damage"
            matches = []
            for item, start_ms, end_ms in reversed(episodes):
                if (not message["actor_id"] or item["actor_id"] != message["actor_id"] or
                    item["slot"] != message["slot"] or item["turn"] != message["turn"] or
                    item["kind"] != kind):
                    continue
                ms = message["observed_ms"]
                nearest_ms = min(max(ms, start_ms), end_ms)
                distance = abs(ms - nearest_ms)
                if distance > HP_NARRATION_WINDOW_MS:
                    continue
                low, high = sorted((ms, nearest_ms))
                if any(low < barrier["logical_ms"] <= high and
                       (barrier["kind"] not in {"switch", "drag"} or
                        barrier["slot"] == item["slot"])
                       for barrier in barriers):
                    continue
                matches.append((distance, item["seq"], ms - start_ms))
            matches.sort()
            link = {**message, "status": "unmatched", "event_seq": None,
                    "candidate_event_seqs": [seq for _, seq, _ in matches]}
            if not matches:
                link["reason"] = ("Actor del mensaje sin identidad unívoca en ese instante."
                                  if not message["actor_id"] else
                                  "Sin episodio de PS confirmado y compatible en la ventana de 3 s.")
            elif len(matches) > 1 and matches[1][0] - matches[0][0] <= HP_NARRATION_AMBIGUITY_MS:
                link["status"] = "ambiguous"
                link["reason"] = "Varios episodios compatibles a distancias similares (margen ≤ 0,5 s)."
            else:
                distance, seq, delta = matches[0]
                link.update(status="linked", event_seq=seq, delta_ms=delta, distance_ms=distance)
            proposals.append(link)
        # A single consolidated episode cannot explain two different effects.
        # Keep both messages auditable instead of overwriting one cause.
        effects: dict[int, set[str]] = collections.defaultdict(set)
        for link in proposals:
            if link["status"] == "linked":
                effects[link["event_seq"]].add(link["effect"])
        for link in reversed(proposals):
            if link["status"] == "linked" and len(effects[link["event_seq"]]) > 1:
                link.update(status="ambiguous", event_seq=None,
                            reason="Mensajes de efectos distintos compiten por el mismo episodio.")
                link.pop("delta_ms", None)
                link.pop("distance_ms", None)
            self.narration_links.append(link)
            if link["status"] != "linked":
                self._issue("hp_narration_" + link["status"], link["reason"], link["frame"])
                continue
            item = self.events[link["event_seq"] - 1]
            if link["text"] not in item["narration"]:
                item["narration"].append(link["text"])
            item.setdefault("causal_evidence", []).append({
                key: link[key] for key in ("frame", "source_frame", "observed_ms", "text",
                                          "effect", "delta_ms", "confidence")})
            if link["effect"] == "burn":
                item["cause"] = "quemadura observada"
            elif link["effect"] == "recoil":
                item["cause"] = "retroceso observado"
            elif item.get("terrain_restoration_support"):
                item["cause"] = "Grassy Terrain corroborado por HUD y mensaje"
            elif not (isinstance(item["cause"], str) and "Grassy Terrain" in item["cause"]):
                item["cause"] = "recuperación observada; origen sin confirmar"

    def _reconcile_mega_candidates(self) -> None:
        """Resolve a wrong-slot duplicate only with a supported accepted Mega.

        The rejected candidate remains in the event stream. Two distinct
        sampled frames must repeat the subject, side and stone of the accepted
        announcement; a nearby detector candidate alone is insufficient.
        """
        accepted = [item for item in self.events if item["kind"] == "mega" and
                    item["status"] == "consistent"]
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "mega_wrong_occupant":
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            original = rejected["mega_candidate"]
            matches = []
            for item in accepted:
                if (not original["species"] or not original["forme"] or not original["stone"] or
                    item["species"] != original["forme"] or item["value"] != original["stone"] or
                    identity_species(item["species"]) != identity_species(original["species"]) or
                    item["slot"] == rejected["slot"] or item["turn"] != rejected["turn"] or
                    abs(item["observed_ms"] - rejected["observed_ms"]) > 3_000):
                    continue
                first, last = sorted((item["seq"], rejected["seq"]))
                if any(e["status"] != "suppressed" and e["kind"] in ACTIVITY | {"turn", "faint"}
                       for e in self.events[first:last - 1]):
                    continue
                evidence = []
                for row in self.frames:
                    if (abs(row["timestamp_ms"] - item["observed_ms"]) > 3_000 or
                        abs(row["timestamp_ms"] - rejected["observed_ms"]) > 3_000):
                        continue
                    for line in row.get("ocr", ()):
                        if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                            continue
                        match = MEGA_NARRATION.fullmatch(line.get("text", "").strip())
                        if not match:
                            continue
                        side = "p2" if match.group(1) else "p1"
                        named = match.group(2).casefold()
                        species = self.nickname_species[side].get(named, named)
                        if (item["slot"].startswith(side) and
                            identity_species(species).casefold() == identity_species(item["species"]).casefold() and
                            match.group(3).casefold() == item["value"].casefold()):
                            evidence.append({"frame": row["frame"], "text": line["text"],
                                             "confidence": line["confidence"]})
                            break
                if len({e["frame"] for e in evidence}) >= 2:
                    matches.append((item, evidence))
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            item, evidence = matches[0]
            resolution = {"state": "resolved", "event_seq": item["seq"],
                          "actor_id": item["actor_id"], "slot": item["slot"],
                          "reason": "Candidato de Mega duplicado en otro slot; anuncio correcto repetido y evento aceptado.",
                          "evidence": evidence}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_faint_hud_entries(self) -> None:
        """Recognize a lingering HUD around a corroborated faint announcement."""
        faints = [e for e in self.events if e["kind"] == "faint" and e["status"] == "consistent"]
        unresolved = []
        for issue in self.issues:
            if issue["code"] not in {"ghost_reentry_after_faint", "reentry_without_exit"}:
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            if rejected["health"] is not None and health_ratio(rejected["health"]) != 0:
                unresolved.append(issue)
                continue
            matches = []
            for faint in faints:
                if (faint["actor_id"] != rejected["actor_id"] or faint["slot"] != rejected["slot"] or
                    faint["turn"] != rejected["turn"] or
                    abs(faint["observed_ms"] - rejected["observed_ms"]) > 3_000):
                    continue
                first, last = sorted((faint["seq"], rejected["seq"]))
                if any(e["status"] != "suppressed" and e["kind"] in ACTIVITY | {"turn"}
                       for e in self.events[first:last - 1]):
                    continue
                hp = next((e for e in reversed(self.events[:faint["seq"] - 1])
                           if e["actor_id"] == faint["actor_id"] and e["status"] != "suppressed" and
                           e["kind"] in HP_KINDS | {"hp_unconfirmed", "hp_zero_rebound", "hp_ocr_conflict", "hp_oscillation"}), None)
                if (not hp or hp["kind"] != "damage" or hp["status"] != "consistent" or
                    hp.get("hp_state") != "confirmed" or health_ratio(hp["after"]) != 0):
                    continue
                # The HUD fades or slides away during faint. Its last stable
                # zero may be in either of the two preceding sampled frames.
                support = next((proof for offset in (0, -1, -2)
                                for proof in [self._hp_support(faint["slot"], hp["after"], faint["frame"] + offset)]
                                if proof["state"] == "confirmed"), None)
                if not support:
                    continue
                evidence = []
                announced_entry = False
                for row in self.frames:
                    if (abs(row["timestamp_ms"] - faint["observed_ms"]) > 3_000 or
                        abs(row["timestamp_ms"] - rejected["observed_ms"]) > 3_000):
                        continue
                    for line in row.get("ocr", ()):
                        if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                            continue
                        text = line.get("text", "").strip()
                        if ANNOUNCED_ENTRY.search(text):
                            announced_entry = True
                        match = FAINT_NARRATION.fullmatch(text)
                        if not match:
                            continue
                        side = "p2" if match.group(1) else "p1"
                        named = match.group(2).casefold()
                        species = identity_species(self.nickname_species[side].get(named, named)).casefold()
                        expected = identity_species(faint["species"]).casefold()
                        if faint["slot"].startswith(side) and species in {expected, expected.split("-", 1)[0]}:
                            evidence.append({"frame": row["frame"], "text": text,
                                             "confidence": line["confidence"], "kind": "faint_narration"})
                if not announced_entry and len({e["frame"] for e in evidence}) >= 2:
                    matches.append((faint, hp, [{**e, "kind": "zero_hp"} for e in support["evidence"]] + evidence))
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            faint, hp, evidence = matches[0]
            resolution = {"state": "resolved", "event_seq": faint["seq"], "hp_event_seq": hp["seq"],
                          "actor_id": faint["actor_id"], "slot": faint["slot"],
                          "reason": "HUD transitorio durante el debilitamiento: PS cero confirmados y anuncio repetido del mismo actor.",
                          "evidence": evidence}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def run(self) -> dict[str, Any]:
        for candidate in ordered_candidates(self.frames):
            self._handle(candidate)
        self._flush_hp()
        self._reconcile_hp_narration()
        self._reconcile_mega_candidates()
        self._reconcile_faint_hud_entries()
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
                "events": self.events, "issues": self.issues, "resolved_issues": self.resolved_issues,
                "narration_links": self.narration_links,
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
    display = item.get("display_species")
    if display and identity_species(display) != identity_species(species):
        species = f"{species} (apariencia: {display})"
    actor = f"{species} ({slot})" if slot else species
    if kind == "turn":
        return f"Inicio del turno {item['turn']}"
    if kind in {"switch", "drag"}:
        previous = (f" (último confirmado: {item['last_confirmed_health']})"
                    if not item['health'] and item.get('last_confirmed_health') else "")
        inferred = " (inferidos al máximo)" if item.get("hp_state") == "inferred" else ""
        return f"Entra {actor}, PS {item['health'] or '?'}{inferred}{previous}"
    if kind == "illusion_reveal":
        return f"Se rompe la Ilusión: {item['species']} aparece en {slot}; PS {item['after'] or '?'}"
    if kind == "move":
        return f"{actor} usa {item['move']} → {item['target_slot'] or 'objetivo desconocido'}"
    if kind in HP_KINDS:
        return f"{actor}: PS {item['before'] or '?'} → {item['after'] or '?'} ({kind})"
    if kind == "hp_unconfirmed":
        return (f"{actor}: lectura propuesta {item['health'] or '?'} sin confirmar; "
                f"PS previos {item['before'] or '?'}")
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
             "", "**Consistente** significa que no se detectó contradicción estructural; no equivale a validación visual. "
             "Las lecturas de PS tienen confirmación OCR separada de la causalidad del cambio.", ""]
    if comparison:
        lines += [f"Replay archivado: {comparison['aligned_events']}/{comparison['baseline_core_events']} "
                  f"eventos principales alineados; PS {comparison['aligned_hp_episodes']}/"
                  f"{comparison['baseline_hp_events']}; secuencia principal exacta: "
                  f"{comparison['exact_core_sequence']}.", ""]
        if any(item["kind"] == "illusion_reveal" for item in ledger["events"]):
            lines += ["El replay archivado contó la ruptura de Ilusión como un cambio de Pokémon "
                      "y llamó Kingambit al Zoroark inicial; esas diferencias son intencionales.", ""]
    links = ledger.get("narration_links", [])
    if links:
        linked = sum(link["status"] == "linked" for link in links)
        lines += [f"Pasada retrospectiva: {linked}/{len(links)} mensajes de PS asociados; "
                  f"{len(links) - linked} pendientes de revisión.", ""]
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
        if item.get("hp_baseline"):
            if item["hp_baseline"]["state"] == "inferred":
                lines += ["  - PS previos inferidos al máximo por primer avistamiento; no confirmados por OCR."]
            else:
                prior = item["hp_baseline"]["evidence"][0]
                lines += [f"  - PS previos en el HUD de {prior['nickname']}: "
                          f"{prior['text']} (frame {prior['frame']}, antes del cambio)."]
        if item["evidence"]:
            lines += [f"  - Pantalla: «{item['evidence'][0]['text']}» (frame {item['evidence'][0]['frame']})."]
        for evidence in item.get("causal_evidence", []):
            delta = evidence["delta_ms"] / 1000
            lines += [f"  - Narración asociada: «{evidence['text']}» (frame {evidence['frame']}; "
                      f"{delta:+g} s respecto al inicio del cambio de PS)."]
    lines += ["", "## Incidencias para revisión", ""]
    if ledger["issues"]:
        for issue in ledger["issues"]:
            lines += [f"- Frame {issue['frame']} · {issue['code']}: {issue['message']}"]
    else:
        lines += ["- Sin contradicciones estructurales detectadas en este primer corte."]
    for link in links:
        if link["status"] != "linked":
            lines += [f"- Mensaje conservado, frame {link['frame']}: «{link['text']}»; "
                      f"{link['reason']}"]
    if ledger.get("resolved_issues"):
        lines += ["", "## Incidencias resueltas con evidencia", ""]
        for issue in ledger["resolved_issues"]:
            resolution = issue["resolution"]
            frames = ", ".join(str(f) for f in sorted({e["frame"] for e in resolution["evidence"]}))
            lines += [f"- Frame {issue['frame']} · {issue['code']}: {resolution['reason']} "
                      f"Evento {resolution['event_seq']} en {resolution['slot']}; "
                      f"evidencia en frames {frames}. El candidato original sigue suprimido en JSON."]
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
                                  "hp_confirmed_observations": sum(
                                      x["kind"] in HP_KINDS and x["status"] != "suppressed" and
                                      x.get("hp_state") == "confirmed"
                                      for x in ledger["events"]),
                                  "hp_inferred_entry_baselines": sum(
                                      x["kind"] in {"switch", "drag"} and x.get("hp_state") == "inferred"
                                      for x in ledger["events"]),
                                  "hp_inferred_transition_baselines": sum(
                                      x["kind"] in HP_KINDS and x.get("hp_baseline", {}).get("state") == "inferred"
                                      for x in ledger["events"]),
                                  "hp_unconfirmed": sum(x.get("hp_state") == "unconfirmed"
                                                        for x in ledger["events"]),
                                  "hp_review_transitions": sum(x["kind"] in HP_KINDS and
                                                                x["status"] == "review"
                                                                for x in ledger["events"]),
                                  "hp_conflicts": sum(x["kind"] == "hp_ocr_conflict" for x in ledger["events"]),
                                  "hp_narration": dict(collections.Counter(
                                      link["status"] for link in ledger["narration_links"])),
                                  "statuses": dict(collections.Counter(x["status"] for x in ledger["events"])),
                                  "issue_codes": dict(collections.Counter(x["code"] for x in ledger["issues"])),
                                  "resolved_issue_codes": dict(collections.Counter(
                                      x["code"] for x in ledger["resolved_issues"])),
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
