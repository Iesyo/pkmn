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
import gzip
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from ..pokemon_names import strip_pokemon_title
else:
    # Keep the standalone CLI dependency-free while sharing the parser's
    # exact title rules instead of maintaining a second list.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pokemon_names import strip_pokemon_title


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
RESULT_NARRATION = re.compile(r"You (?:defeated|lost to|were defeated by) .+!", re.I)
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
# The clock may be merged with the team icons to its right. Classify its
# origin (left/top), not the merged box's centre, in normalized video space.
CLOCK_AREAS = {"p1_clock": (.15, .25, .78, .855),
               "p2_clock": (.77, .86, .145, .205)}


def clock_region(line: dict[str, Any]) -> str | None:
    if not re.search(r"\d", line.get("text", "")):
        return None
    for name, (left, right, top, bottom) in CLOCK_AREAS.items():
        if (left <= line.get("left", -1) <= right and
            top <= line.get("top", -1) <= bottom and
            line.get("bottom", bottom) <= bottom):
            return name
    return None


def hud_nickname(row: dict[str, Any], slot: str) -> str | None:
    left, right, top, bottom = NAME_HUD_AREAS[slot]
    matches = [line for line in row.get("ocr", ())
               if left <= line.get("left", -1) <= right and
               top <= line.get("top", -1) <= bottom and
               line.get("confidence", 0) >= .9 and
               sum(char.isalpha() for char in line.get("text", "")) >= 2]
    return max(matches, key=lambda line: line["confidence"])["text"].casefold() if matches else None


def corroborated_digit_aliases(frames: list[dict[str, Any]],
                               aliases: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Join a CJK nickname with one OCR digit to repeated faint narration.

    The same alias must be stationary in one HUD for two consecutive samples,
    the complete faint sentence must repeat, and no second alias may fit.
    Do not guess from arbitrary similar names or from an unlocated sentence.
    """
    narrated: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in frames:
        for line in row.get("ocr", ()):
            match = FAINT_NARRATION.fullmatch(line.get("text", "").strip())
            if match and line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95:
                side = "p2" if match[1] else "p1"
                narrated[(side, match[2].casefold())].append(
                    {"frame": row["frame"], "text": line["text"],
                     "confidence": line["confidence"]})
    proofs = []
    for (side, name), sentence in narrated.items():
        if name in aliases[side] or len({e["frame"] for e in sentence}) < 2:
            continue
        fits = []
        for alias, species in aliases[side].items():
            if (len(name) < 4 or len(name) != len(alias) or
                sum(a != b for a, b in zip(alias, name)) != 1 or
                not any(a.isdigit() and b.isalpha() and not b.isascii()
                        for a, b in zip(alias, name) if a != b)):
                continue
            hud = [(row["frame"], slot) for row in frames
                   for slot in SLOTS if slot.startswith(side) and hud_nickname(row, slot) == alias]
            consecutive = next(((a, b) for a, b in zip(hud, hud[1:])
                                if a[1] == b[1] and b[0] == a[0] + 1), None)
            if consecutive:
                fits.append((alias, species, consecutive))
        if len(fits) != 1:
            continue
        alias, species, hud = fits[0]
        proofs.append({"side": side, "nickname": name, "hud_nickname": alias,
                       "species": species, "slot": hud[0][1],
                       "evidence": [{"frame": f, "text": alias, "kind": "hud_name"} for f, _ in hud] +
                       [{**e, "kind": "faint_narration"} for e in sentence]})
    return proofs


def status_panel_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    """The status inspection overlay is not the battle HUD or narration.

    Recognize its complete heading, independent of the selected effect or
    Pokémon. A generic menu label or a terrain description is insufficient.
    Keep the unmodified OCR and candidates for audit outside the event stream.
    """
    heading = next((line for line in row.get("ocr", ()) if line.get("confidence", 0) >= .9 and
                    re.fullmatch(r"active\s+statuses\s*(?:&|and)\s*effects",
                                 line.get("text", "").strip(), re.I)), None)
    if not heading:
        return None
    return {"frame": row["frame"], "observed_ms": row["timestamp_ms"], "screen": "status_panel",
            "reason": "Pantalla Active Statuses & Effects; contenido informativo excluido del estado de batalla.",
            "heading": dict(heading), "ocr": row.get("ocr", []),
            "detections": row.get("detections", {})}


def corroborate_entry_identity(candidate: dict[str, Any], frames: dict[int, dict[str, Any]],
                               aliases: dict[str, str]) -> dict[str, Any]:
    """Resolve conflicting HUD names only after the entry's HUD stops moving.

    A sliding partner name can briefly occupy this slot's rectangle. Require
    two consecutive, stationary readings after the candidate; never borrow a
    name from beyond an action or a replacement. A menu turn is not a barrier.
    """
    slot = candidate["event"]["slot"]
    start = candidate["observed_frame"]
    start_ms = candidate["observed_ms"]
    result: dict[str, Any] = {"state": "unconfirmed", "from": "provisional",
                              "raw_identity": candidate["event"]["species"],
                              "evidence": []}
    previous = None
    previous_ms = start_ms
    for number in range(start, start + 4):
        row = frames.get(number)
        if row is None:
            break
        observed_ms = row["timestamp_ms"]
        if not 0 <= observed_ms - start_ms <= 1_500 or not 0 <= observed_ms - previous_ms <= 1_000:
            break
        events = row.get("detections", {}).get("events", ())
        if row.get("detections", {}).get("battle_complete") or any(
               e["kind"] in {"move", "cant", "mega", "faint", "battle_end"} or
               (e["kind"] == "message" and any(word in str(e.get("value", "")).casefold()
                                                for word in ("battle has ended", "forfeit"))) or
               (number > start and e["kind"] in {"switch", "drag"} and e.get("slot") == slot)
               for e in events):
            break
        if any(line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
               (RAW_ACTION.search(line.get("text", "")) or MEGA_NARRATION.fullmatch(line.get("text", "")) or
                (number > start and (ANNOUNCED_ENTRY.search(line.get("text", "")) or
                                     "withdrew" in line.get("text", "").casefold())))
               for line in row.get("ocr", ())):
            break
        name = hud_nickname(row, slot)
        left, right, top, bottom = NAME_HUD_AREAS[slot]
        lines = [line for line in row.get("ocr", ())
                 if line.get("text", "").casefold() == name and line.get("confidence", 0) >= .95 and
                 left <= line.get("left", -1) <= right and top <= line.get("top", -1) <= bottom]
        peers = [s for s in SLOTS if s.startswith(slot[:2]) and hud_nickname(row, s) == name]
        current = None
        if name in aliases and len(lines) == 1 and peers == [slot]:
            line = lines[0]
            current = {"frame": number, "observed_ms": observed_ms, "nickname": name,
                       "species": aliases[name], "left": line["left"], "top": line["top"],
                       "confidence": line["confidence"]}
            if result["state"] == "confirmed" and (
                result["evidence"][-1]["nickname"] != name or
                abs(result["evidence"][-1]["left"] - current["left"]) > .01 or
                abs(result["evidence"][-1]["top"] - current["top"]) > .01):
                result = {k: v for k, v in result.items() if k not in {"species", "confirmed_frame"}}
                result.update(state="unconfirmed", evidence=[])
            if (previous and previous["nickname"] == name and
                abs(previous["left"] - current["left"]) <= .01 and
                abs(previous["top"] - current["top"]) <= .01 and result["state"] != "confirmed"):
                result.update(state="confirmed", species=aliases[name], confirmed_frame=number,
                              evidence=[previous, current])
        elif name in aliases and result["state"] == "confirmed":
            result = {k: v for k, v in result.items() if k not in {"species", "confirmed_frame"}}
            result.update(state="unconfirmed", evidence=[])
        previous, previous_ms = current, observed_ms
    return result


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


def narration_signature(text: str) -> tuple[str, str, str, str | None] | None:
    """Strict semantics for a legible action/entry announcement."""
    match = MEGA_NARRATION.fullmatch(text)
    if match:
        return "mega", "p2" if match[1] else "p1", match[2], match[3]
    match = FAINT_NARRATION.fullmatch(text)
    if match:
        return "faint", "p2" if match[1] else "p1", match[2], None
    match = re.fullmatch(r"(The opposing )?(.+?) used (.+?)!", text, re.I)
    if match:
        return "move", "p2" if match[1] else "p1", match[2], match[3]
    match = ANNOUNCED_ENTRY.search(text)
    if match:
        return "switch", "p2" if match[1] else "p1", match[1] or match[2], None
    return None


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


def read_diagnostic_context(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("job.json")).get("context", {}) if "job.json" in archive.namelist() else {}


@lru_cache(maxsize=1)
def species_abilities() -> dict[str, set[str]]:
    """Use the repository's pinned dex, without importing the production OCR."""
    path = Path(__file__).resolve().parents[4] / "public/data/showdown-dex.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            dex = json.load(stream)
        return {entry["name"]: set(entry.get("abilities", ())) for entry in dex["species"].values()}
    except (OSError, ValueError, KeyError):
        return {}


def merge_recovered_candidates(candidates: list[dict[str, Any]], recovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Preserve the established lead ordering; only insert new observations.
    result = list(candidates)
    for item in sorted(recovered, key=lambda c: (c["logical_frame"], c["ordinal"])):
        index = next((i for i, existing in enumerate(result) if existing["logical_frame"] > item["logical_frame"] or
                      (existing["logical_frame"] == item["logical_frame"] and existing["ordinal"] > item["ordinal"])), len(result))
        result.insert(index, item)
    return result


def complete_hud_health(row: dict[str, Any], slot: str) -> list[tuple[str, dict[str, Any]]]:
    """Strict whole HP readings for a retrospective entry checkpoint."""
    left, right, top, bottom = HP_HUD_AREAS[slot]
    readings = []
    for line in row.get("ocr", ()):
        value = line.get("text", "").replace(" ", "")
        if not (line.get("confidence", 0) >= .9 and left <= line.get("left", -1) <= right and
                top <= line.get("top", -1) <= bottom):
            continue
        if slot.startswith("p2"):
            if not re.fullmatch(r"\d{1,3}%", value):
                continue
            value = value[:-1] + "/100"
        if re.fullmatch(r"\d{1,4}/\d{1,4}", value):
            current, total = map(int, value.split("/"))
            if 0 <= current <= total and total:
                readings.append((value, line))
    return readings


def reconstruct_entry_health(slot: str, name: str, initial: str, start: int, end: int,
                             frames: dict[int, dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Recover omitted HUD changes only within an already verified appearance.

    Every change needs a complete value and the unique nickname in that HUD.
    Each animation must reach a repeated endpoint before another action. The
    last animation may continue into the detector's first normal HP samples.
    These are observations, not new HP rules: the usual episode machine still
    consolidates them and associates restoration narration.
    """
    observations = []
    previous = initial
    left, right, top, bottom = NAME_HUD_AREAS[slot]

    def named(row: dict[str, Any]) -> bool:
        labels = [line for line in row.get("ocr", ()) if line.get("text", "").casefold() == name and
                  line.get("confidence", 0) >= .95 and left <= line.get("left", -1) <= right and
                  top <= line.get("top", -1) <= bottom]
        return len(labels) == 1 and [s for s in SLOTS if s.startswith(slot[:2]) and
                                   hud_nickname(row, s) == name] == [slot]

    def boundary(row: dict[str, Any]) -> bool:
        return bool(row.get("detections", {}).get("battle_complete") or any(
            e["kind"] in {"move", "cant", "mega", "battle_end"} or
            (e["kind"] in {"switch", "drag", "faint"} and e.get("slot") == slot)
            for e in row.get("detections", {}).get("events", ())) or any(
                line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                (RAW_ACTION.search(line.get("text", "")) or ANNOUNCED_ENTRY.search(line.get("text", "")))
                for line in row.get("ocr", ())))

    for number in range(start, end + 1):
        row = frames[number]
        readings = complete_hud_health(row, slot)
        if not readings:
            continue
        if len(readings) != 1:
            return None
        health, line = readings[0]
        if health == previous:
            continue
        if (not named(row) or line["confidence"] < .95 or health.split("/")[1] != initial.split("/")[1]
            or health_ratio(previous) == 0):
            return None
        observations.append({"frame": number, "observed_ms": row["timestamp_ms"], "health": health,
                             "previous": previous, "kind": "damage" if health_ratio(health) < health_ratio(previous)
                             else "heal", "text": line["text"], "confidence": line["confidence"],
                             "nickname": name})
        previous = health
    for index, observation in enumerate(observations):
        following = observations[index + 1] if index + 1 < len(observations) else None
        if following and following["kind"] == observation["kind"] and (
            following["observed_ms"] - observation["observed_ms"] <= 1_500) and not any(
                boundary(frames[n]) for n in range(observation["frame"] + 1, following["frame"] + 1)):
            continue
        # A repeated endpoint is necessary; a one-frame digit cannot create
        # an omitted damage/heal pair. Do not confirm across another action.
        value, last_ms = observation["health"], observation["observed_ms"]
        confirmation = None
        for number in range(observation["frame"] + 1, observation["frame"] + 11):
            row = frames.get(number)
            if row is None or not 0 <= row["timestamp_ms"] - last_ms <= 1_000 or (
                row["timestamp_ms"] - observation["observed_ms"] > 5_000):
                break
            last_ms = row["timestamp_ms"]
            events = row.get("detections", {}).get("events", ())
            if boundary(row):
                break
            readings = complete_hud_health(row, slot)
            if not readings:
                continue
            if len(readings) != 1 or not named(row) or readings[0][1]["confidence"] < .95:
                break
            health, line = readings[0]
            if health == value:
                confirmation = {"frame": number, "health": health, "text": line["text"],
                                "confidence": line["confidence"]}
                break
            # The delayed entry itself may be in the middle of an impact.
            # Continue only through real, matching detector HP candidates.
            direction = "damage" if health_ratio(health) < health_ratio(value) else "heal"
            if (number <= end or direction != observation["kind"] or
                health.split("/")[1] != initial.split("/")[1] or
                not any(e["kind"] in HP_KINDS and e.get("slot") == slot and e.get("health") == health
                        for e in events)):
                break
            value = health
        if not confirmation:
            return None
        observation["endpoint_confirmation"] = confirmation
    return observations


def corroborate_delayed_entry(item: dict[str, Any], announcements: list[dict[str, Any]],
                             candidates: list[dict[str, Any]], frames: dict[int, dict[str, Any]],
                             aliases: dict[str, str]) -> dict[str, Any] | None:
    """Bridge a late detector entry to repeated announcement + stable HUD.

    This fallback requires a slot explicitly vacated by faint. The short
    announcement-to-HUD window stays bounded; the later detector candidate
    is attached only within that same, uninterrupted appearance. Changed HP
    must be reconstructed independently from complete, named HUD observations.
    """
    event, end = item["event"], item["observed_frame"]
    slot = event["slot"]
    species = identity_species(item.get("canonical_species") or event.get("species") or "")
    matches = [a for a in announcements if a["side"] == slot[:2] and
               identity_species(a["species"]) == species and end - a["frame"] > 80]
    if not matches:
        return None
    latest = max(a["frame"] for a in matches)
    # Only the most recent repeated announcement of this actor is eligible.
    episode = [a for a in matches if latest - a["frame"] <= 6]
    announcement_evidence = []
    for a in episode:
        match = ANNOUNCED_ENTRY.search(a["text"])
        name = strip_pokemon_title(match.group(1) or match.group(2)).casefold()
        if identity_species(aliases.get(name, "")) != species:
            continue
        for line in frames[a["frame"]].get("ocr", ()):
            if line.get("text", "").strip() == a["text"] and line.get("confidence", 0) >= .95:
                announcement_evidence.append({**a, "nickname": name, "confidence": line["confidence"]})
                break
    if len({a["frame"] for a in announcement_evidence}) < 2 or len({
        a["nickname"] for a in announcement_evidence}) != 1:
        return None
    anchor = min(announcement_evidence, key=lambda a: a["frame"])
    start, name = anchor["frame"], anchor["nickname"]
    previous = [c for c in candidates if c is not item and c["event"].get("slot") == slot and
                c["event"]["kind"] in {"switch", "drag", "faint"} and
                c.get("logical_frame", c["observed_frame"]) < start]
    vacated = max(previous, key=lambda c: c.get("logical_frame", c["observed_frame"])) if previous else None
    if not vacated or vacated["event"]["kind"] != "faint":
        return None
    if any(c is not item and c["event"].get("slot") == slot and
           c["event"]["kind"] in {"switch", "drag", "faint"} and
           start <= c.get("logical_frame", c["observed_frame"]) <= end for c in candidates):
        return None
    hud_evidence = []
    previous_hud = None
    previous_ms = frames[start]["timestamp_ms"]
    for number in range(start, min(end, start + 80) + 1):
        row = frames.get(number)
        if row is None or not 0 <= row["timestamp_ms"] - previous_ms <= 1_000:
            return None
        previous_ms = row["timestamp_ms"]
        if row["timestamp_ms"] - frames[start]["timestamp_ms"] > 40_000:
            break
        events = row.get("detections", {}).get("events", ())
        if row.get("detections", {}).get("battle_complete") or any(
            e["kind"] in {"move", "cant", "mega", "damage", "heal", "battle_end"} for e in events):
            break
        if any(line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
               (RAW_ACTION.search(line.get("text", "")) or MEGA_NARRATION.fullmatch(line.get("text", "")))
               for line in row.get("ocr", ())):
            break
        peers = [s for s in SLOTS if s.startswith(slot[:2]) and hud_nickname(row, s) == name]
        left, right, top, bottom = NAME_HUD_AREAS[slot]
        labels = [line for line in row.get("ocr", ()) if line.get("text", "").casefold() == name and
                  line.get("confidence", 0) >= .95 and left <= line.get("left", -1) <= right and
                  top <= line.get("top", -1) <= bottom]
        health = complete_hud_health(row, slot)
        current_hud = None
        if peers == [slot] and len(labels) == 1 and len(health) == 1 and health_ratio(health[0][0]) > 0:
            current_hud = {"frame": number, "observed_ms": row["timestamp_ms"], "nickname": name,
                           "health": health[0][0], "text": health[0][1]["text"],
                           "confidence": health[0][1]["confidence"], "name_confidence": labels[0]["confidence"],
                           "left": labels[0]["left"], "top": labels[0]["top"]}
            if (previous_hud and previous_hud["health"] == current_hud["health"] and
                abs(previous_hud["left"] - current_hud["left"]) <= .01 and
                abs(previous_hud["top"] - current_hud["top"]) <= .01):
                hud_evidence = [previous_hud, current_hud]
                break
        previous_hud = current_hud
    if not hud_evidence:
        return None
    if any(c["event"]["kind"] in HP_KINDS and c["event"].get("slot") == slot and
           start <= c["observed_frame"] <= end for c in candidates):
        return None
    # Validate the entire bridge, not just the two matching endpoints.
    previous_ms = frames[start]["timestamp_ms"]
    for number in range(start, end + 1):
        row = frames.get(number)
        if row is None or not 0 <= row["timestamp_ms"] - previous_ms <= 1_000:
            return None
        previous_ms = row["timestamp_ms"]
        if row.get("detections", {}).get("battle_complete"):
            return None
        seen = hud_nickname(row, slot)
        if number >= hud_evidence[0]["frame"] and seen and seen != name and (
            identity_species(aliases.get(seen, seen)).casefold() != species.casefold()):
            return None
        for line in row.get("ocr", ()):
            text = line.get("text", "").strip()
            if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                continue
            faint = FAINT_NARRATION.fullmatch(text)
            if faint and ("p2" if faint[1] else "p1") == slot[:2] and (
                identity_species(aliases.get(faint[2].casefold(), faint[2])).casefold() == species.casefold()):
                return None
            if (name in text.casefold() and ("withdrew" in text.casefold() or "come back" in text.casefold())) or \
               "battle has ended" in text.casefold() or "forfeit" in text.casefold():
                return None
    health_observations = reconstruct_entry_health(slot, name, hud_evidence[0]["health"],
                                                  hud_evidence[-1]["frame"], end, frames)
    if health_observations is None:
        return None
    last_health = health_observations[-1]["health"] if health_observations else hud_evidence[0]["health"]
    if event.get("health") and event["health"] != last_health:
        return None
    return {"state": "confirmed", "anchor": anchor, "health": hud_evidence[0]["health"],
            "announcements": announcement_evidence, "evidence": hud_evidence,
            "confirmed_frame": hud_evidence[-1]["frame"], "vacated_frame": vacated["observed_frame"],
            "raw_event": dict(event), "observed_frame": end,
            **({"hp_observations": health_observations} if health_observations else {}),
            "reason": "Entrada anunciada y HUD estable anteriores al candidato tardío"}


def reconstruct_entry_actions(entry: dict[str, Any], candidates: list[dict[str, Any]],
                              frames: dict[int, dict[str, Any]]) -> None:
    """Place buffered moves using their original clock and repeated narration.

    The confirmed appearance establishes the slot even when the HUD is hidden
    during an attack. Never choose an earlier use merely by its move name.
    """
    proof = entry["entry_reconstruction"]
    slot, species = entry["event"]["slot"], proof["anchor"]["species"]
    name = proof["anchor"]["nickname"]
    for item in candidates:
        event = item["event"]
        if (event["kind"] != "move" or not (event.get("slot") or "").startswith(slot[:2]) or
            not event.get("move") or not isinstance(event.get("timestamp_ms"), (int, float)) or
            identity_species(item.get("canonical_species") or event.get("species") or "") != identity_species(species)):
            continue
        origins = [row for row in frames.values() if proof["confirmed_frame"] <= row["frame"] <= proof["observed_frame"]
                   and abs(row["timestamp_ms"] - event["timestamp_ms"]) <= 1 and
                   event.get("source_frame") in {row["frame"], row["frame"] - 1}]
        if len(origins) != 1:
            continue
        origin = origins[0]
        if origin["frame"] == item["observed_frame"] and event["slot"] == slot:
            continue
        evidence = []
        for number in range(origin["frame"], min(origin["frame"] + 4, proof["observed_frame"] + 1)):
            for line in frames.get(number, {}).get("ocr", ()):
                signature = narration_signature(line.get("text", "").strip())
                if (signature and signature[0] == "move" and signature[1] == slot[:2] and
                    signature[2].casefold() == name and signature[3].casefold() == event["move"].casefold() and
                    line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95):
                    evidence.append({"frame": number, "text": line["text"], "confidence": line["confidence"]})
        if len({e["frame"] for e in evidence}) < 2:
            continue
        item["action_reconstruction"] = {"raw_event": dict(event), "detected_frame": item["observed_frame"],
                                          "detected_ms": item["observed_ms"], "evidence": evidence,
                                          "entry_frame": proof["anchor"]["frame"],
                                          "reason": "Reloj original y narración repetida dentro de la aparición confirmada"}
        if event["slot"] != slot:
            item["slot_correction"] = event["slot"]
            item["event"] = {**event, "slot": slot}
        item["logical_frame"] = item["observed_frame"] = origin["frame"]
        item["observed_ms"] = origin["timestamp_ms"]


def corroborate_delayed_voluntary_entry(item: dict[str, Any], announcements: list[dict[str, Any]],
                                        candidates: list[dict[str, Any]],
                                        frames: dict[int, dict[str, Any]],
                                        aliases: dict[str, str]) -> dict[str, Any] | None:
    """Date a buffered entry from its own withdrawal, announcement and ability.

    A detector may emit the switch only when the next turn's HUD returns. Its
    original timestamp is a clue, never sufficient evidence on its own. The
    late HUD establishes identity, but cannot confirm HP at the earlier entry.
    """
    event, end = item["event"], item["observed_frame"]
    slot = event.get("slot")
    event_ms = event.get("timestamp_ms")
    if (slot not in SLOTS or not isinstance(event_ms, (int, float)) or
            item["observed_ms"] - event_ms <= 3_000):
        return None
    species = identity_species(item.get("canonical_species") or event.get("species") or "")
    possible = [a for a in announcements if a["side"] == slot[:2] and
                identity_species(a["species"]) == species and 80 < end - a["frame"] <= 120]
    for latest in sorted(possible, key=lambda a: a["frame"], reverse=True):
        episode = [a for a in possible if a["text"] == latest["text"] and
                   0 <= latest["frame"] - a["frame"] <= 5]
        anchor = min(episode, key=lambda a: a["frame"])
        start = anchor["frame"]
        found = ANNOUNCED_ENTRY.search(anchor["text"])
        name = strip_pokemon_title(found.group(1) or found.group(2)).casefold()
        if identity_species(aliases.get(name, "")) != species:
            continue
        announced = [{**a, "confidence": line["confidence"]} for a in possible
                     if 0 <= a["frame"] - start <= 5 and a["text"] == anchor["text"]
                     for line in frames[a["frame"]].get("ocr", ())
                     if line.get("text", "").strip() == a["text"] and
                     line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95]
        if len({a["frame"] for a in announced}) < 2:
            continue
        withdrawals = []
        for number in range(start - 20, start):
            for line in frames.get(number, {}).get("ocr", ()):
                text = line.get("text", "").strip()
                match = (re.fullmatch(r".+? withdrew (.+?)!", text, re.I) if slot.startswith("p2")
                         else re.fullmatch(r"(.+?), come back!", text, re.I))
                if (match and line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                        abs(frames[number]["timestamp_ms"] - event_ms) <= 3_000):
                    withdrawals.append({"frame": number, "name": match[1].casefold(), **line})
        if len({w["frame"] for w in withdrawals}) < 2 or len({w["name"] for w in withdrawals}) != 1:
            continue
        old_name = withdrawals[0]["name"]
        previous = [c for c in candidates if c is not item and
                    c["event"].get("slot") == slot and c["event"]["kind"] in {"switch", "drag", "faint"} and
                    c["observed_frame"] < withdrawals[0]["frame"]]
        if not previous or previous[-1]["event"]["kind"] == "faint" or (
                identity_species(aliases.get(old_name, "")) !=
                identity_species(previous[-1].get("canonical_species") or
                                 previous[-1]["event"].get("species") or "")):
            continue
        old_hud = [n for n in range(withdrawals[0]["frame"] - 6, withdrawals[0]["frame"])
                   if hud_nickname(frames.get(n, {}), slot) == old_name]
        if len(old_hud) < 2:
            continue
        new_hud = [n for n in range(end, end + 4)
                   if hud_nickname(frames.get(n, {}), slot) == name and
                   len(complete_hud_health(frames[n], slot)) == 1]
        if (len(new_hud) < 2 or new_hud[1] != new_hud[0] + 1 or
                (event.get("health") and complete_hud_health(frames[new_hud[0]], slot)[0][0] !=
                 event["health"])):
            continue
        ability = next((c for c in candidates if c["observed_frame"] == end and
                        c["event"]["kind"] == "ability" and c["event"].get("slot") == slot and
                        c["event"].get("value")), None)
        if not ability:
            continue
        panels = []
        for number in range(start, min(start + 21, end)):
            row = frames.get(number, {})
            panel = [line for line in row.get("ocr", ()) if
                     (.75 <= line.get("left", -1) <= .99 if slot.startswith("p2")
                      else .02 <= line.get("left", -1) <= .30) and
                     .30 <= line.get("top", -1) <= .55 and line.get("confidence", 0) >= .95]
            if (any(re.sub(r"[’']s$", "", line["text"].casefold()) == name for line in panel) and
                    any(line["text"] == ability["event"]["value"] for line in panel)):
                panels.append({"frame": number, "text": ability["event"]["value"],
                               "owner": name, "confidence": min(line["confidence"] for line in panel)})
        if not any(b["frame"] == a["frame"] + 1 for a, b in zip(panels, panels[1:])):
            continue
        # Another occupant or withdrawal in this slot would break continuity.
        if any(c is not item and c["event"].get("slot") == slot and
               c["event"]["kind"] in {"switch", "drag", "faint"} and
               start <= c["observed_frame"] < end for c in candidates):
            continue
        if any(hud_nickname(frames[n], slot) not in {None, name} for n in range(start, end + 1)
               if n in frames and n >= panels[0]["frame"]):
            continue
        if any(re.search(r"\b(withdrew|come back|illusion wore off)\b", line.get("text", ""), re.I)
               and name in line.get("text", "").casefold() for n in range(start, end + 1)
               for line in frames.get(n, {}).get("ocr", ())):
            continue
        if any(n not in frames or frames[n]["timestamp_ms"] - frames[n - 1]["timestamp_ms"] > 1_000
               for n in range(start + 1, end + 1)):
            continue
        return {"anchor": anchor, "nickname": name, "slot": slot,
                "withdrawals": withdrawals, "outgoing_hud_frames": old_hud,
                "announcements": announced, "ability_panel": panels,
                "incoming_hud_frames": new_hud[:2],
                "detected_frame": end, "raw_event": dict(event),
                "reason": "Retirada y anuncio repetidos, habilidad y HUD del mismo ocupante."}
    return None


def recover_opening_ability_panels(candidates: list[dict[str, Any]],
                                   frames: list[dict[str, Any]],
                                   aliases: dict[str, dict[str, str]]) -> None:
    """Anchor opening abilities to repeated owner panels before the first turn.

    Opening switches and ability candidates may all be buffered until the
    first stable HUD. Only a unique nickname in two HUD frames, a known
    species/ability pair, and two consecutive panel readings can recover an
    earlier activation. Separate panel episodes are separate activations.
    """
    first_turn = next((c for c in candidates if c["event"]["kind"] == "turn" and
                       c["event"].get("turn") == 1), None)
    if not first_turn:
        return
    turn_frame = first_turn["observed_frame"]
    lookup = {row["frame"]: row for row in frames}
    catalog = species_abilities()
    owners = {}
    for item in candidates:
        event, slot = item["event"], item["event"].get("slot")
        if event["kind"] not in {"switch", "drag"} or slot not in SLOTS or item["observed_frame"] != turn_frame:
            continue
        species = item.get("canonical_species") or event.get("species") or ""
        if PLACEHOLDER.fullmatch(species):
            continue
        names = [hud_nickname(lookup.get(n, {}), slot) for n in (turn_frame, turn_frame + 1)]
        if (not names[0] or names[0] != names[1] or
            any(hud_nickname(lookup.get(turn_frame, {}), peer) == names[0]
                for peer in SLOTS if peer != slot and peer.startswith(slot[:2]))):
            continue
        name = names[0]
        named_species = aliases[slot[:2]].get(name, name)
        if identity_species(named_species).casefold() != identity_species(species).casefold():
            continue
        owners[(slot[:2], name)] = (slot, species, item)
    if not owners:
        return
    start = min(item.get("logical_frame", turn_frame) for _, _, item in owners.values())
    if turn_frame - start > 80:
        return
    hits: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in frames:
        if not start <= row["frame"] < turn_frame:
            continue
        for (side, name), (slot, species, _) in owners.items():
            area = (.02, .30) if side == "p1" else (.75, .99)
            panel = [line for line in row.get("ocr", ()) if
                     area[0] <= line.get("left", -1) <= area[1] and
                     .30 <= line.get("top", -1) <= .55 and line.get("confidence", 0) >= .95]
            named = [line for line in panel
                     if (owner := re.sub(r"[’']s$", "", line["text"].casefold())) == name or
                     (aliases[side].get(owner) == species and
                      sum(identity_species(other_species) == identity_species(species)
                          for (other_side, _), (_, other_species, _) in owners.items()
                          if other_side == side) == 1)]
            abilities = [line for line in panel if line["text"] in catalog.get(species, ())]
            if len(named) != 1 or len(abilities) != 1:
                continue
            ability = abilities[0]["text"]
            owner = re.sub(r"[’']s$", "", named[0]["text"].casefold())
            hits[(slot, ability)].append({"frame": row["frame"], "text": ability,
                                          "owner": owner, "confidence": min(named[0]["confidence"],
                                                                           abilities[0]["confidence"])})
    for (slot, ability), evidence in hits.items():
        episodes: list[list[dict[str, Any]]] = []
        for line in evidence:
            if not episodes or line["frame"] != episodes[-1][-1]["frame"] + 1:
                episodes.append([])
            episodes[-1].append(line)
        episodes = [episode for episode in episodes if len(episode) >= 2]
        existing = [c for c in candidates if c["event"]["kind"] == "ability" and
                    c["event"].get("slot") == slot and c["event"].get("value") == ability and
                    c["observed_frame"] == turn_frame]
        assignments = []
        for item in existing:
            source = item["event"].get("source_frame")
            nearby = [(abs(source - episode[0]["frame"]), index)
                      for index, episode in enumerate(episodes) if source is not None and
                      episode[0]["frame"] - 2 <= source <= episode[-1]["frame"] + 1]
            if not nearby:
                break
            _, index = min(nearby)
            assignments.append((item, index))
        if (len(assignments) != len(existing) or
            len({index for _, index in assignments}) != len(assignments)):
            continue
        assigned = {index for _, index in assignments}
        for item, index in assignments:
            episode = episodes[index]
            origin = episode[0]["frame"]
            event = item["event"]
            item.update(observed_frame=origin, observed_ms=lookup[origin]["timestamp_ms"],
                        logical_frame=origin,
                        ability_reconstruction={"detected_frame": turn_frame, "raw_event": dict(event),
                                                "evidence": episode,
                                                "reason": "Panel inicial de habilidad repetido y ocupante corroborado por HUD"})
            item["event"] = {**event, "source_frame": origin,
                             "timestamp_ms": item["observed_ms"]}
        species = next(species for (side, owner), (current_slot, species, _) in owners.items()
                       if current_slot == slot)
        for index, episode in enumerate(episodes):
            if index in assigned:
                continue
            origin = episode[0]["frame"]
            candidates.append({"event": {"kind": "ability", "slot": slot, "species": species,
                                         "value": ability, "source_frame": origin,
                                         "timestamp_ms": lookup[origin]["timestamp_ms"],
                                         "confidence": min(e["confidence"] for e in episode)},
                               "observed_frame": origin, "logical_frame": origin,
                               "observed_ms": lookup[origin]["timestamp_ms"], "ordinal": 0,
                               "ability_reconstruction": {"detected_frame": None, "evidence": episode,
                                                          "reason": "Panel inicial de habilidad repetido, especie y slot confirmados por HUD"}})


def corroborate_delayed_opponent_switch(item: dict[str, Any],
                                        announcements: list[dict[str, Any]],
                                        candidates: list[dict[str, Any]],
                                        frames: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """Date a late opponent HUD from a repeated voluntary switch announcement.

    Require a named outgoing HUD, a repeated withdrawal and matching entry,
    then a stable named incoming HUD. Nothing else may act in that slot in
    between. The delayed HP display establishes identity, not entry HP.
    """
    event, end = item["event"], item["observed_frame"]
    slot = event.get("slot")
    if slot not in {"p2a", "p2b"} or event["kind"] != "switch":
        return None
    species = identity_species(item.get("canonical_species") or event.get("species") or "").casefold()
    matches = [a for a in announcements if a["side"] == "p2" and
               identity_species(a["species"]).casefold() == species and
               80 < end - a["frame"] <= 120]
    for latest in sorted(matches, key=lambda a: a["frame"], reverse=True):
        start = latest["frame"]
        entries = [{"frame": n, "text": line["text"], "confidence": line["confidence"]}
                   for n in range(start, start + 4) for line in frames.get(n, {}).get("ocr", ())
                   if line.get("text", "").strip() == latest["text"] and
                   line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95]
        if len({proof["frame"] for proof in entries}) < 2:
            continue
        outgoing = [c for c in candidates if c is not item and c["event"].get("slot") == slot and
                    c["event"]["kind"] in {"switch", "drag", "faint"} and c["observed_frame"] < start]
        if not outgoing or outgoing[-1]["event"]["kind"] == "faint":
            continue
        previous = outgoing[-1]["event"].get("species") or ""
        withdraw = [{"frame": n, "text": line["text"], "confidence": line["confidence"]}
                    for n in range(start - 20, start) for line in frames.get(n, {}).get("ocr", ())
                    if line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                    (found := re.fullmatch(r"(.+?) withdrew (.+)!", line.get("text", ""), re.I)) and
                    (identity_species(strip_pokemon_title(found[2])).casefold() ==
                     identity_species(previous).casefold() or
                     identity_species(previous).casefold().startswith(
                         identity_species(strip_pokemon_title(found[2])).casefold() + "-"))]
        if len({proof["frame"] for proof in withdraw}) < 2:
            continue
        first = withdraw[0]["frame"]
        old_name = re.fullmatch(r"(.+?) withdrew (.+)!", withdraw[0]["text"], re.I)[2].casefold()
        old_hud = [n for n in range(first - 5, first) if hud_nickname(frames.get(n, {}), slot) == old_name]
        incoming = strip_pokemon_title(ANNOUNCED_ENTRY.search(latest["text"])[1]).casefold()
        new_hud = [n for n in range(end, end + 4) if
                   hud_nickname(frames.get(n, {}), slot) == incoming and
                   len(complete_hud_health(frames[n], slot)) == 1]
        if (len(old_hud) < 2 or len(new_hud) < 2 or new_hud[1] != new_hud[0] + 1 or
            (event.get("health") and complete_hud_health(frames[new_hud[0]], slot)[0][0] != event["health"])):
            continue
        if any(c is not item and c["event"].get("slot") == slot and
               c["event"]["kind"] in ACTIVITY | HP_KINDS | {"faint", "mega"} and
               first <= c["observed_frame"] < end for c in candidates):
            continue
        if any(frames[n].get("detections", {}).get("battle_complete") or
               (seen := hud_nickname(frames[n], slot)) and seen not in {old_name, incoming}
               for n in range(first, end) if n in frames):
            continue
        return {"state": "confirmed", "anchor": latest, "nickname": incoming,
                "withdrawals": withdraw, "announcements": entries,
                "outgoing_hud_frames": old_hud, "incoming_hud_frames": new_hud[:2],
                "reason": "Retirada y entrada repetidas; HUD anterior y posterior fijan un único slot"}
    return None


def ordered_candidates(frames: list[dict[str, Any]],
                       alias_reconstructions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in frames:
        for index, event in enumerate(row.get("detections", {}).get("events", ())):
            candidates.append({"event": event, "observed_frame": row["frame"],
                               "observed_ms": row["timestamp_ms"], "ordinal": index})
        # The terminal card sometimes has a battle_complete detection and
        # legible result OCR but no generic message event. Keep the same
        # strict screen/OCR gate used by _handle for ordinary result messages.
        if (row.get("detections", {}).get("battle_complete") and
            not any(e["kind"] == "message" and RESULT_NARRATION.fullmatch(str(e.get("value") or ""))
                    for e in row.get("detections", {}).get("events", ()))):
            result = next((line for line in row.get("ocr", ())
                           if RESULT_NARRATION.fullmatch(line.get("text", "").strip()) and
                           line.get("confidence", 0) >= .9 and line.get("top", 0) >= .55), None)
            if result:
                candidates.append({"event": {"kind": "message", "value": result["text"],
                                             "timestamp_ms": row["timestamp_ms"],
                                             "source_frame": row["frame"],
                                             "confidence": result["confidence"]},
                                   "observed_frame": row["frame"], "observed_ms": row["timestamp_ms"],
                                   "ordinal": len(row.get("detections", {}).get("events", ())),
                                   "result_reconstruction": "OCR de resultado y bandera de fin en el mismo fotograma"})
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
    for proof in alias_reconstructions if alias_reconstructions is not None else corroborated_digit_aliases(frames, aliases):
        aliases[proof["side"]][proof["nickname"]] = proof["species"]
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
            if event["kind"] in {"switch", "drag"} and len({
                identity_species(aliases[slot[:2]][name]) for name in names
                if name in aliases[slot[:2]]}) > 1:
                support = corroborate_entry_identity(item, frame_lookup, aliases[slot[:2]])
                support["observed_names"] = names
                item["identity_support"] = support
                if support["state"] == "confirmed":
                    named_species = support["species"]
                else:
                    # No global ID/alias fallback may silently settle a local
                    # conflict; retain the candidate for review without entry.
                    continue
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
                announced = strip_pokemon_title(found.group(1) or found.group(2))
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
    delayed_voluntary: dict[tuple[int, str], dict[str, Any]] = {}
    delayed_hud_checkpoints: list[dict[str, Any]] = []
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
            proof = (corroborate_delayed_opponent_switch(item, announcements, candidates, frame_lookup) or
                     corroborate_delayed_voluntary_entry(
                         item, announcements, candidates, frame_lookup, aliases[slot[:2]]))
            if proof:
                item["delayed_voluntary_entry"] = proof
                delayed_voluntary[(item["observed_frame"], slot)] = proof
                anchor = proof["anchor"]
                hud = complete_hud_health(frame_lookup[item["observed_frame"]], slot)[0]
                delayed_hud_checkpoints.append({
                    "event": {"kind": "hp_checkpoint", "slot": slot, "species": clean,
                              "health": hud[0], "source_frame": item["observed_frame"],
                              "timestamp_ms": item["observed_ms"],
                              "confidence": hud[1]["confidence"]},
                    "observed_frame": item["observed_frame"], "logical_frame": item["observed_frame"],
                    "observed_ms": item["observed_ms"], "ordinal": len(frame_lookup[item["observed_frame"]]
                                                                  .get("detections", {}).get("events", ())),
                    "checkpoint_reconstruction": {"entry_frame": anchor["frame"],
                                                  "detected_frame": item["observed_frame"],
                                                  "nickname": proof["nickname"],
                                                  "hud_frames": proof["incoming_hud_frames"]}})
            else:
                proof = corroborate_delayed_entry(item, announcements, candidates, frame_lookup, aliases[slot[:2]])
                if not proof:
                    continue
                item["entry_reconstruction"] = proof
                anchor = proof["anchor"]
        else:
            anchor = max(matches, key=lambda a: a["frame"])
        used_announcements.add((anchor["frame"], anchor["side"], anchor["species"]))
        item["logical_frame"] = anchor["frame"]
        item["anchor"] = anchor
        anchors[(item["observed_frame"], slot)] = anchor["frame"]
    recovered_hp = []
    for entry in candidates:
        proof = entry.get("entry_reconstruction")
        if not proof:
            continue
        reconstruct_entry_actions(entry, candidates, frame_lookup)
        for observation in proof.get("hp_observations", ()):
            number = observation["frame"]
            recovered_hp.append({
                "event": {"kind": observation["kind"], "slot": entry["event"]["slot"],
                          "species": proof["anchor"]["species"], "health": observation["health"],
                          "timestamp_ms": observation["observed_ms"], "source_frame": number,
                          "confidence": observation["confidence"]},
                "observed_frame": number, "logical_frame": number, "observed_ms": observation["observed_ms"],
                "ordinal": len(frame_lookup[number].get("detections", {}).get("events", ())),
                "hp_reconstruction": {**observation, "entry_frame": proof["anchor"]["frame"],
                                      "detected_entry_frame": proof["observed_frame"]}})
    candidates.extend(recovered_hp)
    candidates.extend(delayed_hud_checkpoints)
    for item in candidates:
        event = item["event"]
        if event["kind"] == "ability" and event.get("slot"):
            if item.get("ability_reconstruction"):
                continue
            anchor = anchors.get((item["observed_frame"], event["slot"]))
            if anchor is not None:
                item["logical_frame"] = anchor
            proof = delayed_voluntary.get((item["observed_frame"], event["slot"]))
            if proof and proof.get("ability_panel") and event.get("value") == proof["ability_panel"][0]["text"]:
                origin = proof["ability_panel"][0]["frame"]
                item["ability_reconstruction"] = {
                    "detected_frame": item["observed_frame"], "raw_event": dict(event),
                    "evidence": proof["ability_panel"],
                    "reason": "Panel de habilidad repetido junto al anuncio de entrada"}
                item["logical_frame"] = item["observed_frame"] = origin
                item["observed_ms"] = frame_lookup[origin]["timestamp_ms"]
                item["event"] = {**event, "source_frame": origin,
                                 "timestamp_ms": item["observed_ms"]}
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
    recover_opening_ability_panels(candidates, frames, aliases)
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


def confirmed_transition_prefix(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A detector reset may put the previous result card in the next index.

    Exclude only an inactive prefix followed by a repeated team preview and
    a new battle start. Never discard an earlier battle's actions or make a
    result-only/truncated clip look complete. The raw trace remains intact.
    """
    for index, (first, second) in enumerate(zip(frames, frames[1:])):
        if not (second["frame"] == first["frame"] + 1 and
                0 < second["timestamp_ms"] - first["timestamp_ms"] <= 1_500 and
                all(row.get("detections", {}).get("team_preview") and
                    not row["detections"].get("battle_started") and
                    not row["detections"].get("battle_complete") for row in (first, second))):
            continue
        prefix = frames[:index]
        if not prefix:
            return None
        # Events and unparsed narration both protect real earlier activity.
        if any(row.get("detections", {}).get("battle_started") or
               any(e["kind"] != "message" for e in row.get("detections", {}).get("events", ())) or
               any(line.get("confidence", 0) >= .9 and line.get("top", 0) >= .55 and
                   (RAW_ACTION.search(line.get("text", "")) or
                    ANNOUNCED_ENTRY.search(line.get("text", "")) or
                    MEGA_NARRATION.fullmatch(line.get("text", "")))
                   for line in row.get("ocr", ())) for row in prefix):
            return None
        results = [{"frame": row["frame"], "observed_ms": row["timestamp_ms"],
                    "text": line["text"], "confidence": line["confidence"]}
                   for row in prefix if row.get("detections", {}).get("battle_complete")
                   for line in row.get("ocr", ())
                   if RESULT_NARRATION.fullmatch(line.get("text", "").strip()) and
                   line.get("confidence", 0) >= .9 and line.get("top", 0) >= .55]
        started = next((row for row in frames[index + 2:] if
                        row.get("detections", {}).get("battle_started") and
                        not row["detections"].get("battle_complete")), None)
        if not results or started is None:
            return None
        return {"first_frame": prefix[0]["frame"], "last_frame": prefix[-1]["frame"],
                "frame_count": len(prefix), "preview_frames": [first["frame"], second["frame"]],
                "battle_start_frame": started["frame"], "result_evidence": results,
                "reason": "Resultado anterior a la selección repetida y al inicio de esta batalla; "
                          "sin actividad de combate en el tramo excluido."}
    return None


class BattleAutomaton:
    def __init__(self, battle_index: int, frames: list[dict[str, Any]], context: dict[str, Any] | None = None):
        self.battle_index = battle_index
        self.context = context or {}
        self.raw_frames = frames
        self.transition_prefix = confirmed_transition_prefix(frames)
        if self.transition_prefix:
            frames = [row for row in frames if row["frame"] > self.transition_prefix["last_frame"]]
        self.ignored_ui_frames = [proof for row in frames if (proof := status_panel_evidence(row))]
        ignored = {proof["frame"] for proof in self.ignored_ui_frames}
        # Keep timestamps so temporal windows still measure real elapsed
        # time, but exclude overlay text, HP, aliases and detector events
        # before ANY pass can use them. Never mutate the archived trace.
        frames = [{**row, "ocr": [], "resolved_aliases": {}, "resolved_identities": {},
                   "detections": {"events": []}} if row["frame"] in ignored else row for row in frames]
        # Clock digits are UI, even when OCR turns ':' into '/' or merges
        # a party icon into the seconds. Keep the raw trace and coordinates
        # for provenance, but never use these numbers as HP in any pass.
        self.clock_readings = {row["frame"]: [dict(line, region=region)
                               for line in row.get("ocr", ()) if (region := clock_region(line))]
                               for row in frames}
        frames = [{**row, "ocr": [line for line in row.get("ocr", ()) if not clock_region(line)]}
                  if self.clock_readings[row["frame"]] else row for row in frames]
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
        self.alias_reconstructions = corroborated_digit_aliases(frames, self.nickname_species)
        for proof in self.alias_reconstructions:
            self.nickname_species[proof["side"]][proof["nickname"]] = proof["species"]
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
        self.trick_room_start: dict[str, Any] | None = None
        self.trick_room_end: dict[str, Any] | None = None
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

    def _move_side_support(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Corroborate a damaged opponent prefix before a move changes state.

        Only the side marker may differ by one character; the actor name and
        move must remain exact. Consecutive complete narration, a unique active
        opponent, and an uninterrupted announcement establish the correction.
        Detector aliases alone cannot turn the damaged prefix into a nickname.
        """
        event = candidate["event"]
        if not (event.get("slot") or "").startswith("p1") or not event.get("move"):
            return None
        number = candidate["observed_frame"]
        expected = identity_species(candidate.get("canonical_species") or self._resolve(event.get("species")) or "").casefold()
        move = event["move"].casefold()

        def prefix_matches(prefix: str) -> bool:
            a, b = re.sub(r"\s+", "", prefix.casefold()), "theopposing"
            if len(a) == len(b):
                return sum(x != y for x, y in zip(a, b)) <= 1
            longer, shorter = (a, b) if len(a) > len(b) else (b, a)
            return len(longer) - len(shorter) == 1 and any(
                longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))

        def subject(line: dict[str, Any]) -> str | None:
            signature = narration_signature(line.get("text", "").strip())
            if (line.get("top", 0) < .55 or line.get("confidence", 0) < .55 or
                not signature or signature[0] != "move" or signature[3].casefold() != move):
                return None
            return (("the opposing " if signature[1] == "p2" else "") + signature[2]).casefold()

        matches = []
        for slot, actor_id in self.active.items():
            actor = self.actors[actor_id]
            if not slot.startswith("p2") or identity_species(actor["species"]).casefold() != expected:
                continue
            names = {actor["species"].casefold(), (actor.get("forme") or actor["species"]).casefold()}
            names.update(name for name, species in self.nickname_species["p2"].items()
                         if identity_species(species).casefold() == expected)
            for line in self.frame_lookup.get(number, {}).get("ocr", ()):
                named = subject(line)
                if not named:
                    continue
                for name in names:
                    if not named.endswith(" " + name) or not prefix_matches(named[:-len(name)].strip()):
                        continue
                    # A real HUD nickname has independent evidence. A parser
                    # alias made solely from this malformed sentence does not.
                    if any(hud_nickname(row, s) == named for row in self.frames
                           for s in ("p1a", "p1b")):
                        continue
                    matches.append((slot, actor_id, name, named, line))
        if not matches:
            return None
        proof = {"state": "unconfirmed", "from": "provisional", "raw_event": dict(event),
                 "reason": "Prefijo rival dudoso: falta un actor único y narración completa repetida.",
                 "evidence": [{"frame": number, **m[4]} for m in matches]}
        if len({(m[0], m[2]) for m in matches}) != 1:
            return proof
        slot, actor_id, name, damaged, _ = matches[0]
        complete = "the opposing " + name
        start_ms = candidate["observed_ms"]
        evidence = []

        def compatible(row: dict[str, Any]) -> bool:
            events = row.get("detections", {}).get("events", ())
            if row.get("detections", {}).get("battle_complete") or any(
                e["kind"] not in {"move", "message"} or
                (e["kind"] == "move" and ((e.get("move") or "").casefold() != move or
                 identity_species(self._resolve(e.get("species")) or "").casefold() != expected))
                for e in events):
                return False
            readings = [line for line in row.get("ocr", ())
                        if line.get("top", 0) >= .55 and line.get("confidence", 0) >= .55]
            if any((RAW_ACTION.search(line.get("text", "")) and subject(line) not in {damaged, complete}) or
                   ANNOUNCED_ENTRY.search(line.get("text", "")) or
                   MEGA_NARRATION.fullmatch(line.get("text", "")) or
                   re.search(r"\b(come back|went back|withdrew)\b", line.get("text", ""), re.I)
                   for line in readings):
                return False
            return any(subject(line) in {damaged, complete} for line in readings)

        if not compatible(self.frame_lookup[number]):
            return proof
        rows = [self.frame_lookup[number]]
        for direction in (-1, 1):
            previous_ms = start_ms
            for offset in range(1, 7):
                row = self.frame_lookup.get(number + direction * offset)
                if (not row or abs(row["timestamp_ms"] - start_ms) > 3_000 or
                    not 0 < direction * (row["timestamp_ms"] - previous_ms) <= 1_000 or not compatible(row)):
                    break
                rows.append(row)
                previous_ms = row["timestamp_ms"]
        for row in sorted(rows, key=lambda r: r["frame"]):
            for line in row.get("ocr", ()):
                if subject(line) == complete and line.get("confidence", 0) >= .95:
                    evidence.append({"frame": row["frame"], "text": line["text"], "confidence": line["confidence"]})
        if len({e["frame"] for e in evidence}) < 2:
            return proof
        proof.update(state="confirmed", slot=slot, actor_id=actor_id,
                     reason="Lado rival corroborado por narración completa repetida del mismo anuncio.",
                     evidence=evidence)
        proof["suspect_readings"] = [{"frame": number, **m[4]} for m in matches]
        return proof

    def _unconfirmed_faint_text(self, candidate: dict[str, Any], actor_id: str) -> dict[str, Any] | None:
        """OCR confidence alone cannot identify the subject of a faint.

        Check the actual narration before mutating the active actor. A parsed
        event can still come from an incomplete nickname with very high OCR
        confidence. Sources without faint narration retain their existing
        candidate handling; this guard addresses explicit suspect readings.
        """
        slot = candidate["event"]["slot"]
        expected = identity_species(self.actors[actor_id]["species"]).casefold()
        readings = []
        for line in self.frame_lookup[candidate["observed_frame"]].get("ocr", ()):
            text = line.get("text", "").strip()
            match = FAINT_NARRATION.fullmatch(text)
            if not match or line.get("top", 0) < .55:
                continue
            side = "p2" if match[1] else "p1"
            named = match[2].casefold()
            species = identity_species(self.nickname_species[side].get(named, named)).casefold()
            if (side == slot[:2] and species in {expected, expected.split("-", 1)[0]} and
                line.get("confidence", 0) >= .95):
                return None
            readings.append({"frame": candidate["observed_frame"], "text": text,
                             "confidence": line.get("confidence", 0), "side": side,
                             "subject": named, "resolved_species": species})
        if not readings:
            return None
        return {"state": "unconfirmed", "from": "provisional", "evidence": readings,
                "raw_event": dict(candidate["event"]),
                "reason": "Narración de debilitamiento sin sujeto fiable del actor; confianza OCR insuficiente por sí sola."}

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
                    elif (kind in {"fieldstart", "fieldend"} and
                          str(event.get("value") or "").removeprefix("move: ") == "Trick Room" and
                          line.get("top", 0) >= .55 and
                          ("twisted the dimensions" in low if kind == "fieldstart" else
                           "twisted dimensions returned to normal" in low)):
                        score = 9
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

    def _confirmation_rows(self, frame: int, *, include_boundary: bool = False,
                           faint_slot: str | None = None) -> list[dict[str, Any]]:
        """Bound pending evidence by elapsed time and the next battle action."""
        start = self.frame_lookup[frame]["timestamp_ms"]
        rows = []
        previous_ms = start
        for row in self.frames:
            elapsed = row["timestamp_ms"] - start
            if elapsed < 0:
                continue
            if elapsed > 3_000 or row["timestamp_ms"] - previous_ms > 1_000:
                break
            previous_ms = row["timestamp_ms"]
            boundary = elapsed > 0 and any(
                e["kind"] in ACTIVITY | {"turn", "mega", "battle_end", "faint"} and
                not (e["kind"] == "faint" and faint_slot and e.get("slot") == faint_slot)
                for e in row.get("detections", {}).get("events", ()))
            boundary = boundary or (elapsed > 0 and any(
                line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                (ANNOUNCED_ENTRY.search(line.get("text", "")) or
                 re.search(r"\bused .+!$|\b(come back|went back|withdrew)\b", line.get("text", ""), re.I))
                for line in row.get("ocr", ())))
            if boundary:
                if include_boundary:
                    rows.append(row)
                break
            rows.append(row)
        return rows

    def _hp_support(self, slot: str | None, health: str | None, frame: int,
                    confirmation_frames: list[int] | None = None) -> dict[str, Any]:
        """Confirm a proposed HP value using complete OCR in the correct HUD.

        The trace is archived, so the next two sampled frames may corroborate
        an animation. A new occupant in that interval ends the evidence window.
        """
        if slot not in HP_HUD_AREAS or not health or not re.fullmatch(r"\d+/\d+", health):
            return {"state": "unconfirmed", "reason": "PS o slot sin formato comprobable", "evidence": []}
        current, maximum = map(int, health.split("/"))
        percent_hud = slot.startswith("p2")
        if maximum <= 0 or current > maximum or (percent_hud and maximum != 100):
            return {"state": "unconfirmed", "reason": "PS incompatibles con el formato del HUD", "evidence": []}
        left, right, top, bottom = HP_HUD_AREAS[slot]
        full: list[dict[str, Any]] = []
        split: list[dict[str, Any]] = []
        bare: list[dict[str, Any]] = []
        frames = list(range(frame, frame + 3)) if confirmation_frames is None else confirmation_frames
        for offset, observed_frame in enumerate(frames):
            row = self.frame_lookup.get(observed_frame)
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
                entry = {"frame": observed_frame, "text": line["text"],
                         "confidence": float(line.get("confidence", 0))}
                if percent_hud and value == f"{current}%":
                    full.append(entry)
                elif not percent_hud and value == health:
                    full.append(entry)
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
        if any(x["confidence"] >= .9 for x in split):
            return {"state": "confirmed", "reason": "número y porcentaje separados",
                    "evidence": [max(split, key=lambda x: x["confidence"])]}
        return {"state": "unconfirmed", "reason": "lectura parcial o sin confirmación en el HUD",
                "evidence": full + split + bare}

    def _invalid_own_hp_source(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Own HP requires a literal current/maximum reading, never repaired digits.

        Keep confidence validation separate: a weak but complete fraction is
        provisional evidence; a malformed string is no evidence of a change.
        """
        event = candidate["event"]
        slot, health = event.get("slot"), event.get("health")
        if slot not in {"p1a", "p1b"} or not health:
            return None
        left, right, top, bottom = HP_HUD_AREAS[slot]
        readings = [line for line in self.frame_lookup[candidate["observed_frame"]].get("ocr", ())
                    if left <= line.get("left", -1) <= right and top <= line.get("top", -1) <= bottom
                    and line.get("right", right) <= right + .02
                    and line.get("bottom", bottom) <= bottom + .02]
        for line in readings:
            value = re.sub(r"\s+", "", line.get("text", ""))
            if re.fullmatch(r"\d{1,4}/\d{1,4}", value) and value == health:
                current, maximum = map(int, value.split("/"))
                if 0 <= current <= maximum and maximum > 0:
                    return None
        return {"state": "rejected", "reason": "PS propios sin lectura literal actual/máximo en su HUD",
                "raw_event": dict(event), "evidence": [dict(line, frame=candidate["observed_frame"])
                                                         for line in readings]}

    def _clock_hp_source(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Identify an archived HP candidate whose number comes from a clock.

        Even a weak matching number in the actual HUD takes precedence. Do not infer
        provenance from a nearby time or simply from an unusual denominator.
        """
        event = candidate["event"]
        health, slot, number = event.get("health"), event.get("slot"), candidate["observed_frame"]
        if not health or slot not in SLOTS:
            return None
        sources = []
        for line in self.clock_readings.get(number, ()):
            value = re.sub(r"\s+", "", line["text"]).replace("O", "0").replace("o", "0")
            fraction = re.fullmatch(r"(\d{1,4})\D+(\d{1,4})", value)
            percentage = re.fullmatch(r"(\d{1,3})%", value)
            parsed = (f"{int(fraction[1])}/{int(fraction[2])}" if fraction else
                      f"{int(percentage[1])}/100" if percentage else None)
            if parsed == health:
                sources.append({"frame": number, **line})
        if not sources or self._hp_support(slot, health, number, [number])["evidence"]:
            return None
        return {"state": "rejected", "reason": "cifra localizada en la zona del reloj, fuera del HUD de PS",
                "evidence": sources, "raw_event": dict(event)}

    def _confirm_pending_hp(self, episode: HpEpisode, support: dict[str, Any]) -> dict[str, Any]:
        """Keep weak final readings provisional until stable evidence arrives."""
        actor = self.actors.get(episode.actor_id or "")
        slot, last = episode.slot, episode.candidates[-1]
        health = last["event"].get("health")
        if (support["state"] == "confirmed" or not actor or slot not in SLOTS or not health or
            any(c.get("competing_ocr") for c in episode.candidates)):
            return support
        zero = health_ratio(health) == 0 and episode.kind == "damage"
        rows = self._confirmation_rows(last["observed_frame"], faint_slot=slot if zero else None)
        expected = identity_species(actor["species"]).casefold()
        label = health.split("/", 1)[0] + "%" if slot.startswith("p2") else health
        left, right, top, bottom = HP_HUD_AREAS[slot]
        safe_rows, zeros, faint_texts = [], [], []
        for row in rows:
            name = hud_nickname(row, slot)
            named = self.nickname_species[slot[:2]].get(name, name) if name else None
            if named and identity_species(named).casefold() != expected:
                break
            if row["frame"] != last["observed_frame"] and any(
                e["kind"] in HP_KINDS and e.get("slot") == slot and e.get("health") != health
                for e in row.get("detections", {}).get("events", ())):
                break
            hud = [line for line in row.get("ocr", ())
                   if left <= line.get("left", -1) <= right and top <= line.get("top", -1) <= bottom and
                   line.get("right", right) <= right + .02 and line.get("bottom", bottom) <= bottom + .02]
            if any(line.get("confidence", 0) >= .9 and HP_TEXT.fullmatch(line.get("text", "").strip()) and
                   re.sub(r"\s+", "", line["text"]) != label for line in hud):
                break
            safe_rows.append(row)
            if named:
                zeros.extend({"frame": row["frame"], "text": line["text"], "confidence": line["confidence"],
                              "kind": "zero_hp", "nickname": name}
                             for line in hud if zero and line.get("confidence", 0) >= .9 and
                             re.sub(r"\s+", "", line.get("text", "")) == label)
            for line in row.get("ocr", ()):
                match = FAINT_NARRATION.fullmatch(line.get("text", "").strip())
                if not match or line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                    continue
                side = "p2" if match.group(1) else "p1"
                faint_name = match.group(2).casefold()
                species = self.nickname_species[side].get(faint_name, faint_name)
                if side == slot[:2] and identity_species(species).casefold() == expected:
                    faint_texts.append({"frame": row["frame"], "text": line["text"],
                                        "confidence": line["confidence"], "kind": "faint_narration"})
        proof = self._hp_support(slot, health, last["observed_frame"], [r["frame"] for r in safe_rows])
        if proof["state"] != "confirmed" and zero and zeros and len({e["frame"] for e in faint_texts}) >= 2:
            proof = {"state": "confirmed", "reason": "cero visible corroborado por faint repetido del mismo actor",
                     "evidence": zeros + faint_texts}
        if proof["state"] != "confirmed" and episode.kind == "damage":
            proof = self._narrated_partial_opponent_hp(episode, actor) or proof
        if proof["state"] == "confirmed":
            proof["confirmation"] = {"from": "provisional", "to": "confirmed",
                                     "candidate_frame": last["observed_frame"],
                                     "confirmed_frame": max(e["frame"] for e in proof["evidence"]),
                                     "reason": "corroboración temporal sin cruzar otra acción"}
            return proof
        return support

    def _narrated_partial_opponent_hp(self, episode: HpEpisode,
                                      actor: dict[str, Any]) -> dict[str, Any] | None:
        """Confirm a weak percent only with its strong number, prior HUD and repeated text.

        The number and percent from one frame alone are not independent OCR
        confirmation. Require an earlier complete value for this occupant and
        a later matching loss message before any new action.
        """
        slot = episode.slot
        if (not slot or not slot.startswith("p2") or not actor or
            actor.get("health_state") != "confirmed" or not actor.get("health") or
            episode.kind != "damage" or any(c.get("competing_ocr") for c in episode.candidates)):
            return None
        first, last = episode.candidates[0], episode.candidates[-1]
        health = last["event"].get("health")
        before, after = health_ratio(actor["health"]), health_ratio(health)
        values = [health_ratio(c["event"].get("health")) for c in episode.candidates]
        if (not health or not re.fullmatch(r"\d+/100", health) or before is None or after is None or
            not before > after or any(v is None for v in values) or
            any(left < right for left, right in zip([before] + values, values))):
            return None
        previous_frame = first["observed_frame"] - 1
        previous = self._hp_support(slot, actor["health"], previous_frame, [previous_frame])
        if previous["state"] != "confirmed":
            return None
        prior_row = self.frame_lookup.get(previous_frame, {})
        prior_name = hud_nickname(prior_row, slot)
        prior_species = self.nickname_species["p2"].get(prior_name or "", prior_name or "")
        if not prior_name or identity_species(prior_species).casefold() != identity_species(
            actor["species"]).casefold():
            return None
        row = self.frame_lookup.get(last["observed_frame"], {})
        name = hud_nickname(row, slot)
        species = self.nickname_species[slot[:2]].get(name or "", name or "")
        if not name or identity_species(species).casefold() != identity_species(actor["species"]).casefold():
            return None
        left, right, top, bottom = HP_HUD_AREAS[slot]
        hud = [line for line in row.get("ocr", ()) if
               left <= line.get("left", -1) <= right and top <= line.get("top", -1) <= bottom and
               line.get("right", right) <= right + .02 and line.get("bottom", bottom) <= bottom + .02]
        value = health.split("/", 1)[0]
        bare = next((line for line in hud if line.get("text", "").strip() == value and
                     line.get("confidence", 0) >= .99), None)
        partial = next((line for line in hud if line.get("text", "").strip() == value + "%" and
                        line.get("confidence", 0) >= .8), None)
        if (not bare or not partial or any(
            re.fullmatch(r"\d+%", line.get("text", "").strip()) and
            line.get("text", "").strip() != value + "%" and line.get("confidence", 0) >= .9
            for line in hud)):
            return None
        messages = []
        for future in self._confirmation_rows(last["observed_frame"])[1:]:
            for line in future.get("ocr", ()):
                match = re.fullmatch(r"(The opposing )?(.+?) lost some of its HP!",
                                     line.get("text", "").strip(), re.I)
                if not match or line.get("top", 0) < .55 or line.get("confidence", 0) < .95 or not match.group(1):
                    continue
                narrated = match.group(2).casefold()
                narrated = self.nickname_species["p2"].get(narrated, narrated)
                if identity_species(narrated).casefold() != identity_species(actor["species"]).casefold():
                    continue
                messages.append({"frame": future["frame"], "text": line["text"],
                                 "confidence": line["confidence"]})
        if len({x["frame"] for x in messages}) < 2:
            return None
        number = last["observed_frame"]
        evidence = previous["evidence"] + [
            {"frame": number, "text": bare["text"], "confidence": bare["confidence"]},
            {"frame": number, "text": partial["text"], "confidence": partial["confidence"]},
        ] + messages[:2]
        return {"state": "confirmed", "reason": "número del HUD y mensaje de pérdida repetido",
                "evidence": evidence, "narrated_partial": messages[0]}

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

    def _sliding_hud_hp_fragment(self, episode: HpEpisode, before: str | None,
                                 support: dict[str, Any]) -> dict[str, Any] | None:
        """Reject a shifted HUD's clipped suffix when its name and HP move together.

        A truncated percentage alone cannot establish a transition. Two stable
        complete readings and matching displacement of the occupant's name
        distinguish a moving panel from a real zero or a different occupant.
        """
        if (len(episode.candidates) != 1 or episode.kind != "damage" or
            support["state"] == "confirmed" or not episode.slot or
            not episode.slot.startswith("p2") or not before or
            not episode.actor_id or self.actors[episode.actor_id]["health_state"] != "confirmed"):
            return None
        candidate = episode.candidates[0]
        frame = candidate["observed_frame"]
        after = candidate["event"].get("health")
        old, new = health_ratio(before), health_ratio(after)
        if old is None or new is None or not old > new:
            return None
        slot = episode.slot
        rows = [self.frame_lookup.get(number) for number in (frame - 2, frame - 1, frame)]
        if any(row is None for row in rows):
            return None
        earlier, prior, current = rows
        if (current["timestamp_ms"] - earlier["timestamp_ms"] > 1_500 or
            any(event["kind"] in ACTIVITY | {"faint", "switch", "drag"} for row in (prior, current)
                for event in row.get("detections", {}).get("events", ()) if event is not candidate["event"] and
                event["kind"] not in HP_KINDS)):
            return None
        hp_left, hp_right, hp_top, hp_bottom = HP_HUD_AREAS[slot]
        name_left, name_right, name_top, name_bottom = NAME_HUD_AREAS[slot]
        full_hp = before.split("/", 1)[0] + "%"
        prior_hp = []
        prior_names = []
        for row in (earlier, prior):
            hp = next((line for line in row["ocr"] if line.get("text", "").replace(" ", "") == full_hp
                       and line.get("confidence", 0) >= .97 and
                       hp_left <= line.get("left", -1) <= hp_right and
                       hp_top <= line.get("top", -1) <= hp_bottom), None)
            name = next((line for line in row["ocr"] if line.get("confidence", 0) >= .97 and
                         name_left <= line.get("left", -1) <= name_right and
                         name_top <= line.get("top", -1) <= name_bottom and
                         line.get("text", "").isalpha()), None)
            if not hp or not name or (prior_names and name["text"] != prior_names[0]["text"]):
                return None
            prior_hp.append(hp)
            prior_names.append(name)
        if abs(prior_hp[0]["left"] - prior_hp[1]["left"]) > .015 or \
                abs(prior_names[0]["left"] - prior_names[1]["left"]) > .015:
            return None
        actor_species = identity_species(self.actors[episode.actor_id]["species"]).casefold()
        name = prior_names[-1]["text"]
        if identity_species(self.nickname_species[slot[:2]].get(name.casefold(), name)).casefold() != actor_species:
            return None
        for line in current["ocr"]:
            fragment = line.get("text", "").replace(" ", "")
            if (fragment == full_hp or len(fragment) < 2 or not full_hp.endswith(fragment) or
                not fragment.endswith("%") or not fragment[:-1].isdigit() or
                int(fragment[:-1]) != int(after.split("/", 1)[0]) or
                line.get("confidence", 0) < .97 or
                not hp_left <= line.get("left", -1) <= hp_right or
                not hp_top <= line.get("top", -1) <= hp_bottom):
                continue
            hp_shift = line["left"] - prior_hp[-1]["left"]
            if hp_shift < .05:
                continue
            for partial_name in current["ocr"]:
                clipped = partial_name.get("text", "")
                if (not clipped or clipped == name or not name.endswith(clipped) or
                    partial_name.get("confidence", 0) < .97 or
                    not name_left <= partial_name.get("left", -1) <= name_right or
                    not name_top <= partial_name.get("top", -1) <= name_bottom):
                    continue
                name_shift = partial_name["left"] - prior_names[-1]["left"]
                if abs(name_shift - hp_shift) > .025:
                    continue
                return {"state": "rejected", "reason": "nombre y PS recortados por desplazamiento del HUD",
                        "evidence": [{"frame": row["frame"], "text": hp["text"],
                                      "confidence": hp["confidence"], "name": nm["text"]}
                                     for row, hp, nm in zip((earlier, prior), prior_hp, prior_names)] +
                                    [{"frame": frame, "text": fragment, "confidence": line["confidence"],
                                      "name": clipped, "shift": round(hp_shift, 4)}]}
        return None

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
        stronger_number = int(stronger.group(1))
        stable_hud = stronger_number == prior_number and conflict["stronger"]["confidence"] >= .99
        if stable_hud:
            row = self.frame_lookup.get(partial["observed_frame"], {})
            nickname = hud_nickname(row, slot)
            named = self.nickname_species["p2"].get(nickname or "", nickname or "")
            if not nickname or identity_species(named).casefold() != identity_species(
                self.actors[actor_id]["species"]).casefold():
                return None
        elif not (str(prior_number).endswith(suspect.group(1)) and
                  prior_number < stronger_number < final_number):
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
                            "evidence": ([{"frame": partial["observed_frame"],
                                           "text": conflict["stronger"]["text"],
                                           "confidence": conflict["stronger"]["confidence"]}]
                                         if stable_hud else []) + support["evidence"] + [
                                {"frame": number, "text": message}]}
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
        observed_name = hud_nickname(self.frame_lookup[candidate["observed_frame"]], slot)
        # The entry already establishes this occupant. A previously unknown
        # nickname can corroborate later HP without becoming a global alias.
        if observed_name in self.nickname_species[slot[:2]] and identity_species(
            self.nickname_species[slot[:2]][observed_name]) != identity_species(species or ""):
            observed_name = None
        observations: list[tuple[int, str]] = []
        left, right, top, bottom = HP_HUD_AREAS[slot]
        for frame in range(start, min(start + 121, self.frames[-1]["frame"] + 1)):
            row = self.frame_lookup.get(frame)
            if not row:
                continue
            if frame > start and any(e["kind"] in {"move", "damage", "heal"} or
                                     (frame != candidate["observed_frame"] and
                                      e["kind"] in {"switch", "drag"} and e.get("slot") == slot)
                                     for e in row.get("detections", {}).get("events", ())):
                break
            if frame > start and (row.get("detections", {}).get("battle_complete") or any(
                line.get("confidence", 0) >= .95 and line.get("top", 0) >= .55 and
                (RAW_ACTION.search(line.get("text", "")) or MEGA_NARRATION.fullmatch(line.get("text", "")) or
                 re.search(r"\b(come back|went back|withdrew|forfeit)\b", line.get("text", ""), re.I))
                for line in row.get("ocr", ()))):
                break
            nickname = hud_nickname(row, slot)
            identified = self.nickname_species[slot[:2]].get(nickname or "", nickname or "")
            if (observed_name and frame > candidate["observed_frame"] and nickname and
                nickname != observed_name and identity_species(identified).casefold() != identity_species(species or "").casefold()):
                break
            same_name = (frame >= candidate["observed_frame"] and nickname == observed_name and
                         sum(hud_nickname(row, s) == nickname for s in SLOTS if s[:2] == slot[:2]) == 1)
            if not species or not nickname or (identity_species(identified).casefold() !=
                                               identity_species(species).casefold() and
                                               nickname != species.casefold().split("-", 1)[0] and not same_name):
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
        if event.get("tags"):
            item["tags"] = []
            for tag in event["tags"]:
                reference = re.fullmatch(r"\[of\] (p[12][ab]): (.+)", tag) if isinstance(tag, str) else None
                if reference:
                    slot, named = reference.groups()
                    resolved = self.id_resolution.get(named) or self.nickname_species[slot[:2]].get(named.casefold())
                    if not resolved and PLACEHOLDER.fullmatch(named):
                        # A detector placeholder may be reused for another
                        # Pokémon later in this battle. Its local resolution
                        # is valid only while that species occupies this slot.
                        local = self.frame_lookup.get(candidate["observed_frame"], {}).get(
                            "resolved_identities", {}).get(named)
                        active_id = self.active.get(slot)
                        if (local and active_id and
                            identity_species(local) == identity_species(self.actors[active_id]["species"])):
                            resolved = local
                    if resolved:
                        tag = f"[of] {slot}: {resolved}"
                item["tags"].append(tag)
            if item["tags"] != list(event["tags"]):
                item["raw_tags"] = list(event["tags"])
        if candidate.get("slot_correction"):
            item["original_slot"] = candidate["slot_correction"]
            item["note"] = ((item["note"] + " ") if item["note"] else "") + (
                f"Slot corregido desde {candidate['slot_correction']} por " +
                ("narración rival corroborada." if candidate.get("move_narration_support") else
                 "mote visible en el HUD."))
        if candidate.get("move_narration_support"):
            item["move_narration_support"] = candidate["move_narration_support"]
        if candidate.get("identity_support"):
            item["identity_support"] = candidate["identity_support"]
        if candidate.get("ignored_hp_reading"):
            item["ignored_hp_reading"] = candidate["ignored_hp_reading"]
        if candidate.get("entry_reconstruction"):
            item["entry_reconstruction"] = candidate["entry_reconstruction"]
        if candidate.get("delayed_voluntary_entry"):
            item["delayed_voluntary_entry"] = candidate["delayed_voluntary_entry"]
            item["evidence"] = candidate["delayed_voluntary_entry"]["announcements"] + item["evidence"]
        if candidate.get("ability_reconstruction"):
            item["ability_reconstruction"] = candidate["ability_reconstruction"]
            item["evidence"] = candidate["ability_reconstruction"]["evidence"] + item["evidence"]
        if candidate.get("checkpoint_reconstruction"):
            item["checkpoint_reconstruction"] = candidate["checkpoint_reconstruction"]
        if candidate.get("withdrawal_reconstruction"):
            item["withdrawal_reconstruction"] = candidate["withdrawal_reconstruction"]
        if candidate.get("action_reconstruction"):
            item["action_reconstruction"] = candidate["action_reconstruction"]
            item["evidence"] = candidate["action_reconstruction"]["evidence"] + item["evidence"]
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
                             **({"hp_reconstruction": c["hp_reconstruction"]} if c.get("hp_reconstruction") else {}),
                             **({"competing_ocr": c["competing_ocr"]} if c.get("competing_ocr") else {})}
                            for c in episode.candidates]
            support = self._hp_support(episode.slot, after, last["observed_frame"])
            support = self._confirm_pending_hp(episode, support)
            sliding = self._sliding_hud_hp_fragment(episode, before, support)
            if sliding:
                note = (f"HUD desplazado: {sliding['evidence'][-1]['text']} es un fragmento "
                        f"de {before}; el nombre se desplaza junto con los PS.")
                item = self._append(last, kind="hp_rejected_reading", actor_id=episode.actor_id,
                                    status="suppressed", before=before, after=before,
                                    note=note, observations=observations)
                item["narration"].extend(episode.narration)
                item["hp_state"], item["hp_support"] = "rejected", sliding
                continue
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
            # Item attribution can appear only on the first intermediate HP
            # reading. Preserve it when all attributed readings agree.
            tag_sets = {tuple(c["event"]["tags"]) for c in episode.candidates if c["event"].get("tags")}
            tagged_last = ({**last, "event": {**last["event"], "tags": list(next(iter(tag_sets)))}}
                           if len(tag_sets) == 1 else last)
            item = self._append(tagged_last, kind=episode.kind, actor_id=episode.actor_id,
                                status=result, note=" ".join(notes) or None, before=before,
                                after=after, cause=cause, observations=observations)
            if len(tag_sets) > 1:
                item.pop("tags", None)
                item.pop("raw_tags", None)
                item["tag_conflicts"] = [list(tags) for tags in sorted(tag_sets)]
            item["narration"].extend(episode.narration)
            item["hp_state"], item["hp_support"] = support["state"], support
            narrated = support.get("narrated_partial") if result == "consistent" else None
            if narrated:
                row = self.frame_lookup[narrated["frame"]]
                raw_message = next((e for e in row["detections"].get("events", ()) if
                                    e["kind"] == "message" and e.get("value") == narrated["text"]), None)
                self.hp_messages.append({
                    "frame": narrated["frame"],
                    "source_frame": raw_message.get("source_frame") if raw_message else narrated["frame"],
                    "observed_ms": row["timestamp_ms"], "text": narrated["text"],
                    "effect": "self_damage", "actor_id": episode.actor_id,
                    "slot": episode.slot, "turn": item["turn"], "confidence": narrated["confidence"],
                })
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
        clock = self._clock_hp_source(candidate) or self._invalid_own_hp_source(candidate)
        if clock:
            actor_id = self.active.get(event.get("slot"))
            before = self.actors.get(actor_id or "", {}).get("health")
            item = self._append(candidate, kind="hp_rejected_reading", actor_id=actor_id,
                                status="suppressed", before=before, after=before, note=clock["reason"])
            item["hp_state"], item["hp_support"] = "rejected", clock
            return
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

    def _move_menu_label_evidence(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """A short OCR fragment is a move label only within the corroborated menu."""
        number = candidate["observed_frame"]
        row = self.frame_lookup.get(number, {})
        value = str(candidate["event"].get("value") or "").strip()
        if not value or len(value.split()) > 4 or not re.fullmatch(r"[\w\s'’-]+", value):
            return None

        def has_label(source: dict[str, Any], label: str) -> bool:
            return any(str(line.get("text", "")).strip().casefold() == label and
                       line.get("confidence", 0) >= .95 for line in source.get("ocr", ()))

        if not all(has_label(row, label) for label in ("battle info", "move time", "close")):
            return None
        if not any(has_label(self.frame_lookup.get(number - step, {}), "move info")
                   for step in (1, 2)):
            return None
        source = next((line for line in row.get("ocr", ())
                       if str(line.get("text", "")).strip() == value and
                       line.get("confidence", 0) >= .9 and
                       .4 <= line.get("top", -1) <= .55 and
                       .5 <= line.get("left", -1) <= .85), None)
        if source is None:
            return None
        support: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for later in range(number + 1, number + 5):
            next_row = self.frame_lookup.get(later, {})
            if (not next_row or next_row["timestamp_ms"] - row["timestamp_ms"] > 2_000 or
                    next_row.get("detections", {}).get("events") or
                    not all(has_label(next_row, label) for label in ("battle info", "move time", "close"))):
                continue
            for line in next_row.get("ocr", ()):
                label = str(line.get("text", "")).strip()
                if (line.get("confidence", 0) < .95 or
                        abs(line.get("top", -1) - source["top"]) > .03 or
                        abs(line.get("left", -1) - source["left"]) > .03 or
                        difflib.SequenceMatcher(None, value.casefold(), label.casefold()).ratio() < .88):
                    continue
                support[label.casefold()].append({"frame": later, "text": label,
                                                   "confidence": line["confidence"]})
        confirmed = next((items for items in support.values()
                          if len({item["frame"] for item in items}) >= 2), None)
        if not confirmed:
            return None
        return {"source": "move_info_panel", "menu_frame": number,
                "ocr": [{"frame": number, "text": value, "confidence": source["confidence"]},
                        *confirmed], "ui_cues": ["Battle Info", "Move Info", "MOVE TIME", "Close"]}

    def _handle(self, candidate: dict[str, Any]) -> None:
        event = candidate["event"]
        kind = event["kind"]
        if kind in HP_KINDS:
            self._hp(candidate)
            return
        if kind in {"switch", "drag"} and (invalid := self._invalid_own_hp_source(candidate)):
            support = self._hp_support(event.get("slot"), event.get("health"), candidate["observed_frame"])
            candidate = {**candidate, "ignored_hp_reading": invalid,
                         "event": {**event, "health": event["health"] if support["state"] == "confirmed" else None}}
            event = candidate["event"]
        if kind == "hp_checkpoint":
            slot = event.get("slot")
            actor_id = self.active.get(slot)
            health = event.get("health")
            support = self._hp_support(slot, health, candidate["observed_frame"])
            if (actor_id and identity_species(self.actors[actor_id]["species"]) ==
                    identity_species(event.get("species") or "") and
                    self.actors[actor_id]["health"] == health and support["state"] == "confirmed"):
                self.actors[actor_id]["health_state"] = "confirmed"
                item = self._append(candidate, actor_id=actor_id,
                                    note="HUD posterior confirma los PS actuales, no los de la entrada.")
                item["hp_state"], item["hp_support"] = "confirmed", support
            else:
                item = self._append(candidate, status="review", actor_id=actor_id,
                                    note="El HUD posterior no concuerda con el ocupante y PS previos.")
                self._issue("hp_checkpoint_conflict", item["note"], item["frame"], item["seq"])
            return
        if kind == "move" and (support := self._move_side_support(candidate)):
            candidate = {**candidate, "move_narration_support": support}
            if support["state"] != "confirmed":
                item = self._append(candidate, kind="move_text_unconfirmed", status="review",
                                    note=support["reason"])
                self._issue("move_text_unconfirmed", item["note"], item["frame"], item["seq"])
                return
            candidate["slot_correction"] = event["slot"]
            candidate["event"] = event = {**event, "slot": support["slot"]}
        slot = event.get("slot")
        actor_id = self.active.get(slot)
        if kind == "message":
            value = str(event.get("value") or "")
            row = self.frame_lookup.get(candidate["observed_frame"], {})
            menu_labels = {line.get("text", "").casefold() for line in row.get("ocr", ())}
            if support := self._move_menu_label_evidence(candidate):
                item = self._append(candidate, kind="ui_text", status="suppressed",
                                    note="Etiqueta del menú de movimientos, confirmada por OCR posterior; no fue una acción.")
                item["evidence"] = support["ocr"]
                item["ui_support"] = support
                return
            if ("move time" in menu_labels or "battle info" in menu_labels) and (
                "has no energy left to battle!" in value.casefold() or
                "can't use its sealed" in value.casefold()
            ):
                self._append(candidate, kind="ui_text", status="suppressed",
                             note="Aviso del menú de selección; no ocurrió como acción de batalla.")
                return
            if re.fullmatch(r"Are you sure you wish to forfeit\?", value, re.I):
                self._append(candidate, kind="ui_text", status="suppressed",
                             note="Confirmación del menú de abandono; aún no terminó la batalla.")
                return
            if value.casefold() == "a critical hit!":
                # Do not interrupt an HP animation or guess which spread-move
                # target was hit. The bridge can resolve the complete action.
                self._append(candidate, kind="message",
                             note="Crítico observado; el objetivo se comprueba con la acción completa.")
                return
            if "battle has ended" in value.casefold():
                self._flush_hp()
                self._append(candidate, kind="battle_end", note="Fin de la batalla observado en la pantalla.")
                return
            # Some battles end directly on the result card. The detector emits
            # it as a generic message, without a preceding forfeit/end event.
            # Require the terminal-screen flag and the exact, high-confidence
            # narration in the same frame; a stray message alone cannot close.
            if RESULT_NARRATION.fullmatch(value) and row.get("detections", {}).get("battle_complete"):
                result_line = next((line for line in row.get("ocr", ())
                                    if line.get("text", "").strip() == value and
                                    line.get("confidence", 0) >= .9 and
                                    line.get("top", 0) >= .55), None)
                if result_line and not any(item["kind"] == "battle_end" and
                                           item["status"] == "consistent" for item in self.events):
                    self._flush_hp()
                    item = self._append(candidate, kind="battle_end",
                                        note="Resultado corroborado por OCR en la pantalla de fin de batalla.")
                    item["evidence"] = [{"frame": candidate["observed_frame"],
                                         "text": value, "confidence": result_line["confidence"]}]
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
            if kind == "switch" and (ghost := self._ghost_placeholder_after_faint(candidate)):
                item = self._append(candidate, status="suppressed", actor_id=ghost["actor_id"],
                                    note=ghost["reason"])
                item["resolution"] = ghost
                return
            if candidate.get("identity_support", {}).get("state") == "unconfirmed":
                item = self._append(candidate, status="suppressed",
                                    note="Motes contradictorios durante la entrada del HUD; identidad pendiente.")
                self._issue("entry_identity_unconfirmed", item["note"], item["frame"], item["seq"])
                return
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
            reconstruction = candidate.get("entry_reconstruction")
            if reconstruction:
                baseline = (reconstruction["health"], {"state": "confirmed", "reason": reconstruction["reason"],
                                                       "evidence": reconstruction["evidence"]})
            entry_conflict = self._competing_hp_ocr(candidate) if support else None
            if entry_conflict:
                support = {"state": "unconfirmed", "reason": "OCR numérico contradictorio en el mismo HUD",
                           "evidence": [entry_conflict]}
            reentry = None
            previous_health = self.actors[actor_id]["health"]
            previous_state = self.actors[actor_id]["health_state"]
            if (late_health and not first_appearance and not baseline and not entry_conflict and
                    previous_health and previous_state in {"confirmed", "inferred"} and
                    support and support["state"] == "confirmed"):
                previous = next((e for e in reversed(self.events) if e["actor_id"] == actor_id and
                                 e["status"] == "consistent" and e["kind"] in HP_KINDS | {"switch", "drag"}
                                 and (e["after"] or e["health"]) == previous_health), None)
                if previous and not any(e["actor_id"] == actor_id and e["status"] == "review" and
                                        e["kind"] in HP_KINDS | {"switch", "drag"}
                                        for e in self.events[previous["seq"]:]):
                    previous_proof = (previous.get("hp_support") or {}).get("evidence") or previous["evidence"]
                    if event["health"] == previous_health:
                        reentry = {"previous_seq": previous["seq"], "health": previous_health,
                                   "state": previous_state, "evidence": previous_proof + support["evidence"],
                                   "reason": "PS anteriores del mismo actor y HUD posterior idéntico"}
                    elif candidate.get("anchor") and (health_ratio(previous_health) or 0) > (
                            health_ratio(event["health"]) or 1):
                        start, end = candidate["anchor"]["frame"], candidate["observed_frame"]
                        attacks = [(row["frame"], hit) for row in self.frames if start <= row["frame"] < end
                                   for hit in row.get("detections", {}).get("events", ())
                                   if hit["kind"] == "move" and hit.get("slot", "")[:2] != slot[:2]]
                        impacts = [(row["frame"], hit) for row in self.frames if end < row["frame"] <= end + 3
                                   for hit in row.get("detections", {}).get("events", ())
                                   if hit["kind"] == "damage" and hit.get("slot") == slot and
                                   (health_ratio(hit.get("health")) or 1) < (health_ratio(event["health"]) or 0)]
                        if len(attacks) == len(impacts) == 1:
                            final_proof = self._hp_support(slot, impacts[0][1]["health"], impacts[0][0])
                            if final_proof["state"] == "confirmed":
                                reentry = {"previous_seq": previous["seq"], "health": previous_health,
                                           "state": previous_state, "evidence": previous_proof + final_proof["evidence"],
                                           "intermediate_hud": support["evidence"],
                                           "causing_move_frame": attacks[0][0], "final_hp_frame": impacts[0][0],
                                           "reason": "PS anteriores del mismo actor; HUD posterior intermedio durante un golpe corroborado"}
            initial_health = (baseline[0] if baseline else event.get("health") if not late_health and support and
                              support["state"] == "confirmed" else reentry["health"] if reentry else None)
            assume_full = first_appearance and not baseline and (
                not event.get("health") or late_health) and initial_health is None
            if assume_full and slot.startswith("p2"):
                initial_health = "100/100"
            if event.get("health") or baseline:
                self.actors[actor_id]["health"] = initial_health
                self.actors[actor_id]["health_state"] = (
                    reentry["state"] if reentry else
                    "confirmed" if initial_health else "unknown" if late_health else "unconfirmed")
            if assume_full:
                self.actors[actor_id]["health"] = initial_health
                self.actors[actor_id]["health_state"] = "inferred"
            item = self._append(candidate, actor_id=actor_id,
                                status="review" if not baseline and not reentry and not assume_full and
                                (late_health or (support and support["state"] != "confirmed"))
                                else "consistent")
            if reentry:
                item["health"], item["hp_state"] = reentry["health"], reentry["state"]
                item["reentry_health_support"] = reentry
                item["hp_support"] = {"state": reentry["state"], "reason": reentry["reason"],
                                      "evidence": reentry["evidence"]}
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
            if support and not reentry and not assume_full and not reconstruction and not baseline:
                item["hp_state"], item["hp_support"] = (
                    "unknown" if late_health else support["state"]), support
            if late_health and not baseline:
                item["observations"].append({"frame": item["frame"], "health": event["health"],
                                              "reason": "HUD después de comenzar una acción; lectura transitoria"})
                if not assume_full and not reentry:
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
                confirmed = reconstruction["confirmed_frame"] if reconstruction else item["frame"]
                item["note"] = f"Entrada anunciada en frame {candidate['anchor']['frame']}; HUD confirmó el slot en {confirmed}."
            if late_health and not baseline:
                item["note"] = (item["note"] or "") + (
                    " " + reentry["reason"] + "."
                    if reentry else
                    " PS iniciales al máximo inferidos; el HUD posterior no confirma la entrada."
                    if candidate.get("delayed_voluntary_entry") and assume_full else
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
            support = self._unconfirmed_faint_text(candidate, actor_id) if actor_id else None
            if support:
                item = self._append(candidate, actor_id=actor_id, status="review", note=support["reason"])
                item["text_support"] = support
                item["value"] = max(support["evidence"], key=lambda e: e["confidence"])["text"]
                self._issue("faint_text_unconfirmed", item["note"], item["frame"], item["seq"])
                # The candidate is auditable, but it has not vacated the slot
                # or changed the actor's HP/fainted state.
                return
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
        if kind in {"crit", "item"} and (not actor_id or (event.get("species") and
                identity_species(self._resolve(event["species"]) or "") !=
                identity_species(self.actors[actor_id]["species"]))):
            item = self._append(candidate, actor_id=actor_id, status="review",
                                note="El suceso nombra un Pokémon distinto del ocupante activo.")
            self._issue("actor_mismatch", item["note"], item["frame"], item["seq"])
            return
        if kind == "item" and actor_id:
            actor = self.actors[actor_id]
            if actor.get("item") == event.get("value") and not actor["item_lost"]:
                self._append(candidate, actor_id=actor_id, status="suppressed",
                             note="El mismo objeto ya estaba revelado para este Pokémon.")
                return
            actor["item"] = event.get("value")
            actor["item_lost"] = False
        if kind == "enditem" and actor_id:
            if self.actors[actor_id]["item_lost"]:
                self._append(candidate, actor_id=actor_id, status="suppressed",
                             note="El objeto ya se había consumido/perdido.")
                return
            self.actors[actor_id]["item_lost"] = True
            self.actors[actor_id]["item"] = None
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
        if (kind in {"fieldstart", "fieldend"} and
                str(event.get("value") or "").removeprefix("move: ") == "Trick Room"):
            # A persistent effect is a state, not one instance per OCR reading.
            # Keep each candidate and its evidence, but export each transition once.
            previous = self.trick_room_start if kind == "fieldstart" else self.trick_room_end
            if previous is not None:
                if kind == "fieldstart" and any(
                        item["kind"] == "move" and item["status"] == "consistent" and
                        item.get("move") == "Trick Room" for item in self.events[previous["seq"]:]):
                    item = self._append(candidate, status="review",
                                        note="Otro uso de Trick Room sin cierre del estado activo.")
                    self._issue("field_transition", item["note"], item["frame"], item["seq"])
                    return
                item = self._append(candidate, status="suppressed",
                                    note="Lectura repetida del estado de Trick Room; no es otra transición.")
                item["field_state_seq"] = previous["seq"]
                previous["observations"].append({"frame": item["frame"], "source_frame": item["source_frame"],
                                                 "event_seq": item["seq"], "evidence": item["evidence"]})
            elif kind == "fieldend" and self.trick_room_start is None:
                item = self._append(candidate, status="review", note="Fin de Trick Room sin inicio observado.")
                self._issue("field_transition", item["note"], item["frame"], item["seq"])
            elif kind == "fieldstart":
                self.trick_room_start = self._append(candidate)
                self.trick_room_end = None
            else:
                self.trick_room_end = self._append(candidate)
                self.trick_room_start = None
            return
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
            elif link["effect"] == "self_damage":
                item["cause"] = "pérdida de PS observada; origen sin confirmar"
            elif item.get("terrain_restoration_support"):
                item["cause"] = "Grassy Terrain corroborado por HUD y mensaje"
            elif not (isinstance(item["cause"], str) and "Grassy Terrain" in item["cause"]):
                item["cause"] = "recuperación observada; origen sin confirmar"

    def _reconcile_terrain_hp_fragments(self) -> None:
        """Resolve a truncated rival percent after the final terrain heal is known.

        The first intermediate heal value can be smaller than a terrain tick.
        Wait until its whole episode and the same actor's restoration message
        are linked; a conflicting fragment alone never establishes HP.
        """
        for issue in list(self.issues):
            if issue["code"] != "hp_ocr_conflict" or issue["event_seq"] is None:
                continue
            item = self.events[issue["event_seq"] - 1]
            if (item["kind"] != "hp_ocr_conflict" or item["status"] != "review" or
                not item["slot"] or not item["slot"].startswith("p2") or not item["before"]):
                continue
            conflict = next((obs.get("competing_ocr") for obs in item["observations"] if
                             obs.get("competing_ocr")), None)
            if not conflict or conflict["stronger"]["confidence"] < .99 or \
                    conflict["stronger"]["text"] != item["before"].split("/", 1)[0]:
                continue
            row = self.frame_lookup.get(item["frame"], {})
            nickname = hud_nickname(row, item["slot"])
            species = self.nickname_species["p2"].get(nickname or "", nickname or "")
            if (not nickname or identity_species(species).casefold() !=
                    identity_species(item["species"]).casefold()):
                continue
            heal = next((event for event in self.events[issue["event_seq"]:] if
                         event["kind"] == "heal" and event["status"] == "consistent" and
                         event["actor_id"] == item["actor_id"] and event["slot"] == item["slot"] and
                         event["turn"] == item["turn"] and event["before"] == item["before"] and
                         0 < event["frame"] - item["frame"] <= 4), None)
            if not heal or "Grassy Terrain" not in str(heal.get("cause")) or not any(
                proof["effect"] == "restoration" and 0 <= proof["frame"] - heal["frame"] <= 6
                for proof in heal.get("causal_evidence", ())):
                continue
            before, after = health_ratio(heal["before"]), health_ratio(heal["after"])
            if before is None or after is None or not .045 <= after - before <= .075 or any(
                event["status"] != "suppressed" and event["kind"] in ACTIVITY | {"turn", "faint", "battle_end"} and
                item["logical_ms"] < event["logical_ms"] <= heal["logical_ms"]
                for event in self.events):
                continue
            proof = [{"frame": item["frame"], "text": conflict["stronger"]["text"],
                      "confidence": conflict["stronger"]["confidence"]}] + heal["hp_support"]["evidence"] + [
                {"frame": line["frame"], "text": line["text"], "confidence": line["confidence"]}
                for line in heal["causal_evidence"] if line["effect"] == "restoration"]
            reason = ("Fragmento OCR superpuesto al PS previo confirmado; cura completa de "
                      "Grassy Terrain y mensaje del mismo actor corroborados.")
            item.update(kind="hp_rejected_reading", status="suppressed", note=reason,
                        hp_state="rejected", hp_support={"state": "confirmed", "reason": reason,
                                                        "evidence": proof})
            heal["terrain_restoration_support"] = {"state": "confirmed", "reason": reason,
                                                   "evidence": proof}
            heal["cause"] = "Grassy Terrain corroborado por HUD y mensaje"
            self.issues.remove(issue)
            self.resolved_issues.append({**issue, "resolution": {
                "state": "resolved", "event_seq": heal["seq"], "actor_id": item["actor_id"],
                "slot": item["slot"], "reason": reason, "evidence": proof}})

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

    def _reconcile_repeated_faint_text(self) -> None:
        """A lingering faint sentence must not faint the living partner.

        The detector may assign a second reading of one announcement to the
        other slot after the first actor has left. Only discard that reading
        when the first faint has confirmed zero HP and the text repeats while
        the other actor still has positive, confirmed HP.
        """
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "faint_text_unconfirmed":
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            if rejected["status"] != "review" or not rejected.get("text_support"):
                unresolved.append(issue)
                continue
            previous = next((e for e in reversed(self.events[:rejected["seq"] - 1])
                             if e["actor_id"] == rejected["actor_id"] and e["status"] == "consistent" and
                             (e["kind"] in HP_KINDS | {"switch", "drag"})), None)
            previous_health = (previous["after"] or previous["health"]) if previous else None
            if (not previous or previous.get("hp_state") != "confirmed" or
                not 0 < (health_ratio(previous_health) or 0) <= 1 or
                self._hp_support(rejected["slot"], "0/100", rejected["frame"])["state"] == "confirmed"):
                unresolved.append(issue)
                continue
            matches = []
            for faint in self.events[:rejected["seq"] - 1]:
                if (faint["kind"] != "faint" or faint["status"] != "consistent" or
                    faint["slot"] == rejected["slot"] or faint["turn"] != rejected["turn"] or
                    faint["slot"][:2] != rejected["slot"][:2] or
                    not 0 < rejected["observed_ms"] - faint["observed_ms"] <= 3_000):
                    continue
                signature = narration_signature(rejected["value"] or "")
                if not signature or signature[0] != "faint" or signature[1] != faint["slot"][:2]:
                    continue
                named = signature[2].casefold()
                named_species = self.nickname_species[signature[1]].get(named, named)
                expected_species = identity_species(faint["species"]).casefold()
                clean_names = {faint["species"].casefold()} | {
                    name for name, species in self.nickname_species[signature[1]].items()
                    if identity_species(species).casefold() == expected_species}

                def inserted_ascii_noise(clean: str) -> bool:
                    if (len(clean) < 4 or len(named) != len(clean) + 1 or
                        not any(c.isalpha() and not c.isascii() for c in clean)):
                        return False
                    return any(named[:i] + named[i + 1:] == clean and named[i].isascii() and
                               named[i].isalnum() for i in range(len(named)))

                def missing_ascii_letters(clean: str) -> bool:
                    return (len(clean) >= 6 and clean.isascii() and named.isascii() and
                            1 <= len(clean) - len(named) <= 2 and
                            any(clean[:i] + clean[i + len(clean) - len(named):] == named and
                                clean[i:i + len(clean) - len(named)].isalpha()
                                for i in range(len(named) + 1)))

                noisy_name = (identity_species(named_species).casefold() != expected_species and
                              named not in self.nickname_species[signature[1]] and
                              sum(inserted_ascii_noise(clean) or missing_ascii_letters(clean)
                                  for clean in clean_names) == 1)
                if identity_species(named_species).casefold() != expected_species and not noisy_name:
                    continue
                hp = next((e for e in reversed(self.events[:faint["seq"] - 1])
                           if e["actor_id"] == faint["actor_id"] and e["status"] != "suppressed" and
                           e["kind"] in HP_KINDS | {"switch", "drag"}), None)
                if (not hp or hp["kind"] != "damage" or hp["status"] != "consistent" or
                    hp.get("hp_state") != "confirmed" or health_ratio(hp["after"]) != 0):
                    continue
                if any(e["status"] != "suppressed" and e["kind"] in ACTIVITY | {"turn", "faint"}
                       for e in self.events[faint["seq"]:rejected["seq"] - 1]):
                    continue
                evidence = []
                rows = [row for row in self.frames
                        if faint["observed_ms"] <= row["timestamp_ms"] <= rejected["observed_ms"]]
                if any(b["timestamp_ms"] - a["timestamp_ms"] > 1_000 for a, b in zip(rows, rows[1:])):
                    continue
                blocked = False
                for row in rows:
                    for line in row.get("ocr", ()):
                        text = line.get("text", "").strip()
                        if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                            continue
                        if ANNOUNCED_ENTRY.search(text) or re.search(r"\b(come back|went back|withdrew|used)\b", text, re.I):
                            blocked = True
                        match = FAINT_NARRATION.fullmatch(text)
                        if not match or ("p2" if match[1] else "p1") != faint["slot"][:2]:
                            continue
                        name = match[2].casefold()
                        species = self.nickname_species[faint["slot"][:2]].get(name, name)
                        if identity_species(species).casefold() == identity_species(faint["species"]).casefold():
                            evidence.append({"frame": row["frame"], "text": text,
                                             "confidence": line["confidence"], "kind": "faint_narration"})
                preceding = sorted({e["frame"] for e in evidence})
                supported_noise = (noisy_name and len(preceding) >= 2 and
                                   preceding[-2:] == [rejected["frame"] - 2, rejected["frame"] - 1] and
                                   any(e["frame"] == rejected["frame"] for e in
                                       rejected["text_support"]["evidence"]))
                if (not blocked and len(preceding) >= 2 and
                    (any(e["frame"] == rejected["frame"] for e in evidence) or supported_noise)):
                    if supported_noise:
                        evidence += [{**e, "kind": "suspect_faint_ocr"} for e in
                                     rejected["text_support"]["evidence"] if e["frame"] == rejected["frame"]]
                    matches.append((faint, hp, evidence))
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            faint, hp, evidence = matches[0]
            resolution = {"state": "resolved", "event_seq": faint["seq"], "hp_event_seq": hp["seq"],
                          "actor_id": faint["actor_id"], "slot": faint["slot"],
                          "reason": "Segundo OCR del mismo debilitamiento: PS cero del actor, anuncio repetido y compañero con PS positivos confirmados.",
                          "evidence": hp["hp_support"]["evidence"] + evidence}
            rejected["status"] = "suppressed"
            rejected["text_support"]["state"] = "rejected"
            rejected["resolution"] = resolution
            faint["observations"].append({"frame": rejected["frame"], "text": rejected["value"],
                                          "reason": "Lectura repetida atribuida al otro slot por el detector"})
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_partner_hud_entries(self) -> None:
        """A simultaneous partner switch can resurface an unchanged HUD."""
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "reentry_without_exit":
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            slot, health = rejected["slot"], rejected["health"]
            if (rejected["status"] != "suppressed" or not health or
                not 0 < (health_ratio(health) or 0) <= 1 or
                not any(e["kind"] in {"switch", "drag"} and e["status"] == "consistent" and
                        e["slot"] != slot and e["slot"][:2] == slot[:2] and
                        e["frame"] == rejected["frame"] for e in self.events)):
                unresolved.append(issue)
                continue
            expected = identity_species(rejected["species"]).casefold()
            label = health.split("/", 1)[0] + "%" if slot.startswith("p2") else health
            prior = next((e for e in reversed(self.events[:rejected["seq"] - 1])
                          if e["actor_id"] == rejected["actor_id"] and e["status"] == "consistent" and
                          e["kind"] in HP_KINDS | {"switch", "drag", "faint"}), None)
            if (not prior or prior["kind"] == "faint" or prior.get("hp_state") != "confirmed" or
                (prior["after"] or prior["health"]) != health):
                unresolved.append(issue)
                continue

            def hud_readings(row: dict[str, Any], kind: str) -> list[dict[str, Any]]:
                areas = NAME_HUD_AREAS if kind == "hud_name" else HP_HUD_AREAS
                left, right, top, bottom = areas[slot]
                return [line for line in row.get("ocr", ()) if line.get("confidence", 0) >= .95 and
                        left <= line.get("left", -1) <= right and top <= line.get("top", -1) <= bottom and
                        line.get("right", right) <= right + .02 and
                        line.get("bottom", bottom) <= bottom + .02 and
                        (re.search(r"[A-Za-z]{3}", line.get("text", "")) if kind == "hud_name"
                         else HP_TEXT.fullmatch(line.get("text", "").strip()))]

            def valid_reading(line: dict[str, Any], kind: str) -> bool:
                if kind == "hud_hp":
                    return re.sub(r"\s+", "", line["text"]) == label
                name = line["text"].casefold()
                return identity_species(self.nickname_species[slot[:2]].get(name, name)).casefold() == expected

            def hud_evidence(row: dict[str, Any]) -> list[dict[str, Any]] | None:
                proof = []
                for kind in ("hud_name", "hud_hp"):
                    readings = hud_readings(row, kind)
                    if len(readings) != 1 or not valid_reading(readings[0], kind):
                        return None
                    proof.append({"frame": row["frame"], "text": readings[0]["text"],
                                  "confidence": readings[0]["confidence"], "kind": kind})
                return proof

            window = [r for r in self.frames if prior["observed_ms"] <= r["timestamp_ms"] <=
                      rejected["observed_ms"] + 1_000]
            pre = [(r, hud_evidence(r)) for r in window if r["timestamp_ms"] < rejected["observed_ms"] and
                   rejected["observed_ms"] - r["timestamp_ms"] <= 40_000]
            pre = [(r, proof) for r, proof in pre if proof]
            post = [(r, hud_evidence(r)) for r in window if r["timestamp_ms"] >= rejected["observed_ms"]]
            if (len(pre) < 2 or pre[-1][0]["timestamp_ms"] - pre[-2][0]["timestamp_ms"] > 1_000 or
                len(post) < 2 or not all(proof for _, proof in post[:2]) or
                post[0][0]["frame"] != rejected["frame"] or
                post[1][0]["timestamp_ms"] - post[0][0]["timestamp_ms"] > 1_000):
                unresolved.append(issue)
                continue
            start_ms, end_ms = pre[-2][0]["timestamp_ms"], post[1][0]["timestamp_ms"]
            between = [r for r in window if start_ms <= r["timestamp_ms"] <= end_ms]
            conflicting_hud = any(any(not valid_reading(line, kind)
                                      for kind in ("hud_name", "hud_hp")
                                      for line in hud_readings(row, kind)) for row in between)
            outgoing_or_return = False
            for row in between:
                for line in row.get("ocr", ()):
                    text = line.get("text", "").strip()
                    if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                        continue
                    entry = ANNOUNCED_ENTRY.search(text)
                    if entry:
                        name = (entry[1] or entry[2]).casefold()
                        species = self.nickname_species[slot[:2]].get(name, name)
                        if identity_species(species).casefold() == expected:
                            outgoing_or_return = True
                    if re.search(r"\b(come back|went back|withdrew)\b", text, re.I):
                        outgoing_or_return = True
            if (any(e["status"] != "suppressed" and e["slot"] == slot and
                    e["kind"] in {"switch", "drag", "faint", "illusion_reveal"} and
                    start_ms < e["observed_ms"] <= end_ms
                    for e in self.events if e["seq"] != rejected["seq"]) or
                conflicting_hud or outgoing_or_return or
                any(e["kind"] in {"switch", "drag"} and e.get("slot") == slot
                    for r in between for e in r.get("detections", {}).get("events", ())
                    if r["frame"] != rejected["frame"])):
                unresolved.append(issue)
                continue
            evidence = [item for _, proof in (pre[-2:] + post[:2]) for item in proof]
            resolution = {"state": "resolved", "event_seq": prior["seq"],
                          "actor_id": rejected["actor_id"], "slot": slot,
                          "reason": "HUD del compañero reaparece junto a la entrada del otro slot: nombre y PS estables antes y después, sin salida observada.",
                          "evidence": evidence}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_opponent_menu_hud_entries(self) -> None:
        """Reject entries inferred from a stationary two-slot HUD after a menu redraw.

        A provisional identity can carry the *other* slot's HP after the menu
        closes. A named, unchanged occupant can also look like a fresh entry.
        Both cases need repeated, located name/HP evidence; a real entry or
        withdrawal announcement leaves the warning open.
        """
        def occupant(slot: str, seq: int) -> dict[str, Any] | None:
            last = next((e for e in reversed(self.events[:seq - 1])
                         if e["slot"] == slot and e["status"] == "consistent" and
                         e["kind"] in {"switch", "drag", "faint"}), None)
            return last if last and last["kind"] != "faint" else None

        def health_event(slot: str, actor_id: str, seq: int) -> dict[str, Any] | None:
            return next((e for e in reversed(self.events[:seq - 1])
                         if e["slot"] == slot and e["actor_id"] == actor_id and
                         e["status"] == "consistent" and e["kind"] in HP_KINDS | {"switch", "drag"}), None)

        def named_hp(row: dict[str, Any], slot: str, species: str,
                     health: str) -> list[dict[str, Any]] | None:
            left, right, top, bottom = NAME_HUD_AREAS[slot]
            names = [line for line in row.get("ocr", ())
                     if line.get("confidence", 0) >= .95 and
                     left <= line.get("left", -1) <= right and
                     top <= line.get("top", -1) <= bottom and
                     line.get("right", right) <= right + .02 and
                     line.get("bottom", bottom) <= bottom + .02 and
                     re.search(r"[A-Za-z]{3}", line.get("text", ""))]
            values = [(value, line) for value, line in complete_hud_health(row, slot)
                      if line["confidence"] >= .95]
            expected = identity_species(species).casefold()
            if (len(names) != 1 or not values or
                identity_species(self.nickname_species[slot[:2]].get(
                    names[0]["text"].casefold(), names[0]["text"])).casefold() != expected or
                any(value != health for value, _ in values)):
                return None
            hp_line = max((line for _, line in values), key=lambda line: line["confidence"])
            return [{"frame": row["frame"], "text": line["text"],
                     "confidence": line["confidence"], "kind": kind}
                    for kind, line in (("hud_name", names[0]), ("hud_hp", hp_line))]

        def conflicting_hud(row: dict[str, Any], slot: str, species: str, health: str) -> bool:
            readings = [value for value, line in complete_hud_health(row, slot)
                        if line["confidence"] >= .95]
            name = hud_nickname(row, slot)
            if not readings or not name:
                return False
            actual = self.nickname_species[slot[:2]].get(name, name)
            return (identity_species(actual).casefold() != identity_species(species).casefold() or
                    any(value != health for value in readings))

        unresolved = []
        for issue in self.issues:
            if issue["code"] not in {"ghost_reentry_after_faint", "reentry_without_exit"}:
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            slot, seq, frame = rejected["slot"], rejected["seq"], rejected["frame"]
            if (rejected["status"] != "suppressed" or slot not in {"p2a", "p2b"} or
                not rejected["health"] or frame not in self.frame_lookup):
                unresolved.append(issue)
                continue
            current = self.frame_lookup[frame]
            raw = [e for e in current.get("detections", {}).get("events", ())
                   if e["kind"] == "switch" and e.get("slot") == slot]
            if len(raw) != 1:
                unresolved.append(issue)
                continue
            active = occupant(slot, seq)
            if not active:
                unresolved.append(issue)
                continue
            active_hp = health_event(slot, active["actor_id"], seq)
            active_health = (active_hp["after"] or active_hp["health"]) if active_hp else None
            if not active_health or not 0 < (health_ratio(active_health) or 0) <= 1:
                unresolved.append(issue)
                continue

            # The candidate and nearby samples must show the living occupant
            # in its slot. A false ghost copied the confirmed partner HP.
            partner_slot = "p2b" if slot == "p2a" else "p2a"
            partner = occupant(partner_slot, seq)
            partner_hp = health_event(partner_slot, partner["actor_id"], seq) if partner else None
            partner_health = (partner_hp["after"] or partner_hp["health"]) if partner_hp else None
            ghost = (issue["code"] == "ghost_reentry_after_faint" and
                     PLACEHOLDER.fullmatch(raw[0].get("species") or "") and
                     partner_hp is not None and partner_hp.get("hp_state") == "confirmed" and
                     partner_health == rejected["health"] and
                     partner["actor_id"] != rejected["actor_id"] and
                     active["actor_id"] != rejected["actor_id"])
            duplicate = (issue["code"] == "reentry_without_exit" and
                         active["actor_id"] == rejected["actor_id"] and
                         active_hp.get("hp_state") == "confirmed" and
                         active_health == rejected["health"] and
                         any(re.search(r"MOVE[ -]TIME|Battle Info", line.get("text", ""), re.I)
                             for line in current.get("ocr", ())))
            if not ghost and not duplicate:
                unresolved.append(issue)
                continue

            nearby = [row for row in self.frames if abs(row["timestamp_ms"] - rejected["observed_ms"]) <= 2_000]
            if any(line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                   (ANNOUNCED_ENTRY.search(line.get("text", "")) or
                    re.search(r"\b(come back|went back|withdrew)\b", line.get("text", ""), re.I))
                   for row in nearby for line in row.get("ocr", ())):
                unresolved.append(issue)
                continue

            evidence = []
            if ghost:
                for row in nearby:
                    if not frame <= row["frame"] <= frame + 4:
                        continue
                    first = named_hp(row, partner_slot, partner["species"], partner_health)
                    second = named_hp(row, slot, active["species"], active_health)
                    if first and second:
                        evidence.append((row, first + second))
                valid = (len(evidence) >= 2 and evidence[0][0]["frame"] <= frame + 2 and
                         evidence[1][0]["timestamp_ms"] - evidence[0][0]["timestamp_ms"] <= 1_000)
            else:
                for row in nearby:
                    proof = named_hp(row, slot, active["species"], active_health)
                    if proof:
                        evidence.append((row, proof))
                before = [row for row, _ in evidence if frame - 2 <= row["frame"] < frame]
                after = [row for row, _ in evidence if frame < row["frame"] <= frame + 4]
                valid = (len(before) >= 2 and len(after) >= 2 and
                         after[0]["frame"] <= frame + 1 and
                         any(row["frame"] == frame for row, _ in evidence))
            if not valid:
                unresolved.append(issue)
                continue

            start = evidence[0][0]["timestamp_ms"]
            end = evidence[-1][0]["timestamp_ms"]
            if any(conflicting_hud(row, slot, active["species"], active_health) or
                   (ghost and conflicting_hud(row, partner_slot, partner["species"], partner_health))
                   for row in nearby if start <= row["timestamp_ms"] <= end):
                unresolved.append(issue)
                continue

            proof = [item for _, pair in evidence for item in pair]
            previous = partner_hp if ghost else active_hp
            resolution = {"state": "resolved", "event_seq": previous["seq"],
                          "actor_id": previous["actor_id"], "slot": previous["slot"],
                          "reason": ("El PS pertenece al HUD nombrado del compañero; el ocupante del slot continúa."
                                     if ghost else "El HUD nombrado permanece estable durante el redibujado del menú."),
                          "evidence": proof}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_intermediate_hp_entries(self) -> None:
        """A changing HUD can be mistaken for an entry during an HP animation.

        Require the same named occupant, a literal intermediate HUD value, and
        a confirmed next HP event whose before/after values bracket that value.
        An actual withdrawal or announced entry keeps the warning open.
        """
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "reentry_without_exit":
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            slot, actor_id = rejected["slot"], rejected["actor_id"]
            following = self.events[rejected["seq"]:rejected["seq"] + 1]
            next_hp = following[0] if following else None
            prior = next((e for e in reversed(self.events[:rejected["seq"] - 1])
                          if e["actor_id"] == actor_id and e["slot"] == slot and
                          e["status"] == "consistent" and e["kind"] in HP_KINDS | {"switch", "drag"}), None)
            before = (prior["after"] or prior["health"]) if prior else None
            middle = rejected["health"]
            after = next_hp["after"] if next_hp else None
            values = [health_ratio(value) for value in (before, middle, after)]
            if (rejected["status"] != "suppressed" or rejected["kind"] != "switch" or
                not actor_id or not prior or prior.get("hp_state") != "confirmed" or
                not next_hp or next_hp["kind"] not in HP_KINDS or
                next_hp["status"] != "consistent" or next_hp.get("hp_state") != "confirmed" or
                next_hp["slot"] != slot or next_hp["actor_id"] != actor_id or
                next_hp["turn"] != rejected["turn"] or next_hp["before"] != before or
                not 0 < next_hp["observed_ms"] - rejected["observed_ms"] <= 1_000 or
                any(value is None for value in values) or not 0 < values[1] < 1 or
                not (values[0] < values[1] < values[2] if next_hp["kind"] == "heal"
                     else values[0] > values[1] > values[2])):
                unresolved.append(issue)
                continue
            if any(e["status"] == "consistent" and e["slot"] == slot and
                   e["kind"] in {"switch", "drag", "faint"} for e in
                   self.events[prior["seq"]:next_hp["seq"] - 1]):
                unresolved.append(issue)
                continue

            expected = identity_species(rejected["species"]).casefold()
            hp_label = middle.split("/", 1)[0] + "%" if slot.startswith("p2") else middle
            hp_box = HP_HUD_AREAS[slot]
            name_box = NAME_HUD_AREAS[slot]

            def hud_lines(row: dict[str, Any], box: tuple[float, ...], minimum: float) -> list[dict[str, Any]]:
                left, right, top, bottom = box
                return [line for line in row.get("ocr", ())
                        if line.get("confidence", 0) >= minimum and
                        left <= line.get("left", -1) <= right and
                        top <= line.get("top", -1) <= bottom and
                        line.get("right", right) <= right + .02 and
                        line.get("bottom", bottom) <= bottom + .02]

            current = self.frame_lookup[rejected["frame"]]
            next_row = self.frame_lookup[next_hp["frame"]]
            hp_readings = [line for line in hud_lines(current, hp_box, .85)
                           if HP_TEXT.fullmatch(line.get("text", "").strip())]
            names = [[line for line in hud_lines(row, name_box, .95)
                      if re.search(r"[A-Za-z]{3}", line.get("text", ""))]
                     for row in (current, next_row)]
            if (len(hp_readings) != 1 or
                re.sub(r"\s+", "", hp_readings[0]["text"]) != hp_label or
                any(len(readings) != 1 or
                    identity_species(self.nickname_species[slot[:2]].get(
                        readings[0]["text"].casefold(), readings[0]["text"])).casefold() != expected
                    for readings in names)):
                unresolved.append(issue)
                continue
            window = [row for row in self.frames
                      if rejected["observed_ms"] - 1_000 <= row["timestamp_ms"] <= next_hp["observed_ms"]]
            if any(line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                   (ANNOUNCED_ENTRY.search(line.get("text", "")) or
                    re.search(r"\b(come back|went back|withdrew)\b", line.get("text", ""), re.I))
                   for row in window for line in row.get("ocr", ())):
                unresolved.append(issue)
                continue
            evidence = (prior["hp_support"]["evidence"] +
                        [{"frame": current["frame"], "text": hp_readings[0]["text"],
                          "confidence": hp_readings[0]["confidence"], "kind": "intermediate_hp"}] +
                        [{"frame": row["frame"], "text": readings[0]["text"],
                          "confidence": readings[0]["confidence"], "kind": "hud_name"}
                         for row, readings in zip((current, next_row), names)] +
                        next_hp["hp_support"]["evidence"])
            resolution = {"state": "resolved", "event_seq": next_hp["seq"],
                          "actor_id": actor_id, "slot": slot,
                          "reason": "HUD intermedio durante un cambio de PS confirmado, con el mismo nombre antes y después y sin anuncio de entrada o salida.",
                          "evidence": evidence}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_hud_identity_entries(self) -> None:
        """Join a provisional HUD identity to its corroborated living occupant.

        Require the same name and complete HP before the HUD disappears and
        in two frames after it returns. Only a locally resolved placeholder
        can explain this duplicate; species equality alone is insufficient.
        """
        def hud_lines(row: dict[str, Any], slot: str, areas: dict) -> list[dict[str, Any]]:
            left, right, top, bottom = areas[slot]
            return [line for line in row.get("ocr", ())
                    if line.get("confidence", 0) >= .95 and
                    left <= line.get("left", -1) <= right and
                    top <= line.get("top", -1) <= bottom and
                    line.get("right", right) <= right + .02 and
                    line.get("bottom", bottom) <= bottom + .02]

        unresolved = []
        for issue in self.issues:
            if issue["code"] != "reentry_without_exit":
                unresolved.append(issue)
                continue
            rejected = self.events[issue["event_seq"] - 1]
            slot = rejected["slot"]
            row = self.frame_lookup[rejected["frame"]]
            raw_entries = [e for e in row.get("detections", {}).get("events", ())
                           if e["kind"] == "switch" and e.get("slot") == slot and
                           PLACEHOLDER.fullmatch(e.get("species") or "")]
            partial = hud_nickname(row, slot)
            hp = next((e for e in reversed(self.events[:rejected["seq"] - 1])
                       if e["actor_id"] == rejected["actor_id"] and e["status"] != "suppressed" and
                       (e["kind"] in HP_KINDS | {"switch", "drag", "faint", "illusion_reveal"} or
                        e["kind"].startswith("hp_"))), None)
            if (len(raw_entries) != 1 or not partial or not hp or
                hp["slot"] != slot or hp["status"] != "consistent" or
                hp.get("hp_state") != "confirmed" or hp["kind"] not in HP_KINDS | {"switch", "drag"}):
                unresolved.append(issue)
                continue
            health = hp["after"] or hp["health"]
            if (not 0 < (health_ratio(health) or 0) <= 1 or
                rejected["health"] not in {None, health} or
                not 0 < rejected["observed_ms"] - hp["observed_ms"] <= 8_000):
                unresolved.append(issue)
                continue
            raw = raw_entries[0]["species"]
            expected = identity_species(rejected["species"]).casefold()
            expected_hp = health.split("/", 1)[0] + "%" if slot.startswith("p2") else health
            before = self.frame_lookup[hp["frame"]]
            name = hud_nickname(before, slot)
            named_species = self.nickname_species[slot[:2]].get(name, name) if name else ""
            if identity_species(named_species).casefold() != expected:
                unresolved.append(issue)
                continue
            window = [r for r in self.frames
                      if hp["observed_ms"] <= r["timestamp_ms"] <= rejected["observed_ms"] + 1_500]
            proofs = []
            conflict = False
            for r in window:
                names = [line for line in hud_lines(r, slot, NAME_HUD_AREAS)
                         if re.search(r"[A-Za-z]{3}", line.get("text", ""))]
                values = [line for line in hud_lines(r, slot, HP_HUD_AREAS)
                          if HP_TEXT.fullmatch(line.get("text", "").strip())]
                if (any(line["text"].strip().casefold() != name for line in names) or
                    any(re.sub(r"\s+", "", line["text"]) != expected_hp for line in values)):
                    conflict = True
                if not names or not values:
                    continue
                if r["timestamp_ms"] > rejected["observed_ms"]:
                    local = r.get("resolved_identities", {}).get(raw, "")
                    alias = r.get("resolved_aliases", {}).get(slot[:2], {}).get(partial, "")
                    if (identity_species(local).casefold() != expected or
                        identity_species(alias).casefold() != expected):
                        continue
                elif r["frame"] != hp["frame"]:
                    continue
                proofs.append((r, [{"frame": r["frame"], "text": line["text"],
                                    "confidence": line["confidence"], "kind": kind}
                                   for kind, lines in (("hud_name", names), ("hud_hp", values))
                                   for line in lines]))
            following = [p for p in proofs if p[0]["timestamp_ms"] > rejected["observed_ms"]]
            if (conflict or not any(p[0]["frame"] == hp["frame"] for p in proofs) or
                len({p[0]["frame"] for p in following}) < 2):
                unresolved.append(issue)
                continue
            end_ms = following[1][0]["timestamp_ms"]
            blocked = any(e["seq"] != rejected["seq"] and e["seq"] > hp["seq"] and
                          e["observed_ms"] <= end_ms and e["status"] != "suppressed" and
                          (e["kind"] in ACTIVITY | HP_KINDS | {"faint", "illusion_reveal"} or
                           e["kind"].startswith("hp_") or
                           (e["kind"] == "turn" and e["seq"] < rejected["seq"]))
                          for e in self.events)
            for r in window:
                if r["timestamp_ms"] > end_ms:
                    continue
                for line in r.get("ocr", ()):
                    text = line.get("text", "").strip()
                    if line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and (
                        ANNOUNCED_ENTRY.search(text) or RAW_ACTION.search(text) or
                        re.search(r"\b(come back|went back|withdrew)\b", text, re.I)):
                        blocked = True
            if blocked:
                unresolved.append(issue)
                continue
            resolution = {"state": "resolved", "event_seq": hp["seq"],
                          "actor_id": rejected["actor_id"], "slot": slot,
                          "provisional_identity": raw, "observed_name": partial,
                          "reason": "Identidad provisional al regresar el HUD: mismo nombre y PS antes y en dos frames posteriores, sin salida ni nueva acción.",
                          "evidence": [e for r, evidence in proofs if r["timestamp_ms"] <= end_ms for e in evidence]}
            rejected["resolution"] = resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_transient_text(self) -> None:
        """Discard a malformed reading only when its legible event is known."""
        normalized = lambda text: re.sub(r"\W+", "", text.casefold())
        names = {identity_species(a["species"]).casefold() for a in self.actors.values()}
        names.update(name for aliases in self.nickname_species.values() for name in aliases)
        unresolved = []
        for issue in self.issues:
            if issue["code"] not in {"unclassified_text", "faint_text_unconfirmed"}:
                unresolved.append(issue)
                continue
            transient = self.events[issue["event_seq"] - 1]
            text = transient["value"] or ""
            pending_faint = issue["code"] == "faint_text_unconfirmed"
            # An already meaningful but unhandled sentence may describe a
            # distinct event, even when it resembles a later announcement.
            if narration_signature(text) and not pending_faint:
                unresolved.append(issue)
                continue
            if pending_faint:
                signature = narration_signature(text)
                _, side, named, _ = signature
                known = self.nickname_species[side].get(named.casefold())
                expected = identity_species(transient["species"] or "").casefold()
                if (side != transient["slot"][:2] or
                    (known and identity_species(known).casefold() not in {expected, expected.split("-", 1)[0]})):
                    unresolved.append(issue)
                    continue
            matches: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
            for row in self._confirmation_rows(transient["frame"], include_boundary=not pending_faint,
                                                faint_slot=transient["slot"] if pending_faint else None):
                if row["frame"] == transient["frame"]:
                    continue
                for line in row.get("ocr", ()):
                    clean = line.get("text", "").strip()
                    signature = narration_signature(clean)
                    if not signature or line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                        continue
                    kind, side, named, detail = signature
                    if difflib.SequenceMatcher(a=normalized(text), b=normalized(clean)).ratio() < .78:
                        continue
                    if "the opposing" in text.casefold() and side != "p2":
                        continue
                    if any(len(name) >= 4 and name in text.casefold() and name not in clean.casefold()
                           for name in names):
                        continue
                    if kind == "mega":
                        stone = re.search(r"[’']s (\S+) is\b", text, re.I)
                        if stone and stone[1].casefold() != detail.casefold():
                            continue
                    species = self.nickname_species[side].get(named.casefold(), named).casefold()
                    for accepted in self.events:
                        if (accepted["status"] != "consistent" or accepted["turn"] != transient["turn"] or
                            accepted["kind"] != kind or not (accepted["slot"] or "").startswith(side) or
                            (not pending_faint and row["frame"] not in {accepted["frame"], accepted["logical_frame"]})):
                            continue
                        if pending_faint and (kind != "faint" or accepted["slot"] != transient["slot"] or
                            accepted["actor_id"] != transient["actor_id"] or
                            not transient["frame"] < accepted["frame"] <= row["frame"]):
                            continue
                        expected = identity_species(accepted["species"] or "").casefold()
                        if species != expected and not (kind == "switch" and species.startswith(expected + " ")):
                            continue
                        value = accepted["move"] if kind == "move" else accepted["value"]
                        if detail and detail.casefold() != (value or "").casefold():
                            continue
                        matches[accepted["seq"]].append({"frame": row["frame"], "text": clean,
                                                         "confidence": line["confidence"]})
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            seq, evidence = next(iter(matches.items()))
            accepted = self.events[seq - 1]
            if pending_faint:
                hp = next((e for e in reversed(self.events[:transient["seq"] - 1])
                           if e["actor_id"] == transient["actor_id"] and e["status"] != "suppressed" and
                           (e["kind"] in HP_KINDS | {"switch", "drag", "faint"} or e["kind"].startswith("hp_"))), None)
                if (len({e["frame"] for e in evidence}) < 2 or not hp or hp["kind"] != "damage" or
                    hp["status"] != "consistent" or hp.get("hp_state") != "confirmed" or
                    health_ratio(hp["after"]) != 0):
                    unresolved.append(issue)
                    continue
            resolution = {"state": "resolved", "event_seq": seq,
                          "slot": accepted["slot"], "actor_id": accepted["actor_id"],
                          "from": "provisional", "to": "discarded",
                          "reason": "Texto transitorio sustituido por una lectura legible del mismo anuncio y un evento aceptado.",
                          "evidence": evidence}
            transient["status"] = "suppressed"
            transient["resolution"] = resolution
            if pending_faint:
                transient["text_support"]["state"] = "rejected"
                resolution["hp_event_seq"] = hp["seq"]
                resolution["reason"] = ("Candidato de texto incompleto sin efecto sobre el actor; "
                                        "PS cero confirmados y anuncio legible repetido del único debilitamiento aceptado.")
                accepted["observations"].append({"frame": transient["frame"], "text": text,
                                                  "reason": "Lectura provisional anterior sin aplicar al estado"})
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_voicing_faint_text(self) -> None:
        """Confirm a repeated Japanese faint despite one b/p kana OCR error.

        The faint's actor and zero HP already exist in Ledger. A named zero
        HUD immediately before it and a unique alias with one voicing mark
        difference are required; similarity by itself never resolves a faint.
        """
        voiced = dict(zip("ばびぶべぼバビブベボ", "ぱぴぷぺぽパピプペポ"))
        voiced.update({right: left for left, right in list(voiced.items())})
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "faint_text_unconfirmed":
                unresolved.append(issue)
                continue
            faint = self.events[issue["event_seq"] - 1]
            slot, actor_id = faint.get("slot"), faint.get("actor_id")
            if (faint["kind"] != "faint" or faint["status"] != "review" or
                slot not in {"p2a", "p2b"} or not actor_id or self.active.get(slot) != actor_id or
                any(e["slot"] == slot and e["status"] == "consistent" and
                    e["kind"] in ACTIVITY | {"faint"} for e in self.events[faint["seq"]:])):
                unresolved.append(issue)
                continue
            hp = next((e for e in reversed(self.events[:faint["seq"] - 1]) if
                       e["actor_id"] == actor_id and e["kind"] in HP_KINDS and
                       e["status"] == "consistent"), None)
            if (not hp or hp["kind"] != "damage" or hp.get("hp_state") != "confirmed" or
                health_ratio(hp["after"]) != 0 or faint["frame"] - hp["frame"] > 12):
                unresolved.append(issue)
                continue
            match = FAINT_NARRATION.fullmatch(faint.get("value") or "")
            name = match[2].casefold() if match and match[1] else ""
            aliases = [alias for alias, species in self.nickname_species["p2"].items() if
                       len(alias) == len(name) and len(name) >= 4 and
                       identity_species(species).casefold() ==
                       identity_species(self.actors[actor_id]["species"]).casefold() and
                       sum(a != b for a, b in zip(alias, name)) == 1 and
                       all(a == b or voiced.get(a) == b for a, b in zip(alias, name))]
            if not aliases or any(
                len(alias) == len(name) and sum(a != b for a, b in zip(alias, name)) == 1 and
                all(a == b or voiced.get(a) == b for a, b in zip(alias, name)) and
                identity_species(species).casefold() != identity_species(
                    self.actors[actor_id]["species"]).casefold()
                for alias, species in self.nickname_species["p2"].items()):
                unresolved.append(issue)
                continue
            hud = []
            for number in range(hp["frame"], faint["frame"]):
                row = self.frame_lookup.get(number, {})
                nickname = hud_nickname(row, slot)
                if nickname in aliases and any(value == "0/100" for value, _ in complete_hud_health(row, slot)):
                    hud.append({"frame": number, "text": nickname, "confidence": .9,
                                "kind": "named_zero_hud"})
            narration = []
            for number in range(faint["frame"], faint["frame"] + 3):
                for line in self.frame_lookup.get(number, {}).get("ocr", ()):
                    if (line.get("text", "").strip() == faint["value"] and
                        line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95):
                        narration.append({"frame": number, "text": line["text"],
                                          "confidence": line["confidence"], "kind": "faint_narration"})
            if not hud or len({e["frame"] for e in narration}) < 2:
                unresolved.append(issue)
                continue
            resolution = {"state": "resolved", "event_seq": faint["seq"], "slot": slot,
                          "actor_id": actor_id, "hp_event_seq": hp["seq"],
                          "reason": "Mote japonés con una marca de sonoridad OCR distinta; HUD a cero y anuncio repetido del mismo actor.",
                          "evidence": hud + narration}
            faint["status"] = "consistent"
            faint["resolution"] = resolution
            faint["text_support"]["state"] = "confirmed"
            self.actors[actor_id]["fainted"] = True
            del self.active[slot]
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_spread_damage_hud(self) -> None:
        """Fold a delayed partner HP endpoint into a single spread impact.

        A 0% from a fainted partner can be assigned to the other slot as 0/1.
        Resolve it only when the live actor has a named, positive final HUD,
        the opponent's second slot has independently confirmed zero HP, and
        narration names the live actor as hit by the same spread attack.
        """
        resolved = set()
        for issue in self.issues:
            if issue["code"] != "hp_unconfirmed":
                continue
            bad = self.events[issue["event_seq"] - 1]
            slot, actor_id = bad.get("slot"), bad.get("actor_id")
            if (bad["kind"] != "hp_unconfirmed" or bad["health"] != "0/1" or
                bad["status"] != "review" or slot not in {"p2a", "p2b"} or not actor_id):
                continue
            partner = "p2b" if slot == "p2a" else "p2a"
            prior = next((e for e in reversed(self.events[:bad["seq"] - 1]) if
                          e["actor_id"] == actor_id and e["status"] == "consistent" and
                          e["kind"] in HP_KINDS | {"switch", "drag"}), None)
            later = next((e for e in self.events[bad["seq"]:] if
                          e["actor_id"] == actor_id and e["kind"] in HP_KINDS), None)
            partner_zero = next((e for e in reversed(self.events[:bad["seq"] - 1]) if
                                 e["slot"] == partner and e["kind"] == "damage" and
                                 e["status"] == "consistent" and e.get("hp_state") == "confirmed" and
                                 health_ratio(e.get("after")) == 0), None)
            if (not prior or prior["kind"] != "damage" or prior.get("hp_state") != "confirmed" or
                not later or later["kind"] != "heal" or later["status"] != "review" or
                later.get("hp_state") != "confirmed" or not partner_zero or
                prior["turn"] != bad["turn"] or bad["turn"] != later["turn"] or
                prior["cause"] != partner_zero["cause"] or not isinstance(prior["cause"], int) or
                not 0 < later["frame"] - bad["frame"] <= 6 or
                not 0 < bad["frame"] - prior["frame"] <= 6 or
                not 0 < later["frame"] - partner_zero["frame"] <= 8 or
                not (0 < (health_ratio(later["after"]) or 0) < (health_ratio(prior["after"]) or 0))):
                continue
            move = self.events[prior["cause"] - 1]
            if (move["kind"] != "move" or not (move.get("slot") or "").startswith("p1") or
                not 0 < prior["frame"] - move["frame"] <= 10 or
                any(e["kind"] in ACTIVITY | {"mega", "faint"} and e["slot"] == slot
                    for e in self.events[prior["seq"]:later["seq"] - 1])):
                continue
            name = identity_species(self.actors[actor_id]["species"]).casefold()
            final_row = self.frame_lookup.get(later["frame"], {})
            final = [line for value, line in complete_hud_health(final_row, slot)
                     if value == later["after"] and line.get("confidence", 0) >= .97]
            if not final or hud_nickname(final_row, slot) != name:
                continue
            wrong_slot = [{"frame": n, "text": line["text"], "confidence": line["confidence"]}
                          for n in range(partner_zero["frame"], later["frame"])
                          for value, line in complete_hud_health(self.frame_lookup.get(n, {}), partner)
                          if value == "0/100" and line.get("confidence", 0) >= .97]
            if (len({p["frame"] for p in wrong_slot}) < 2 or
                any(value == "0/100" for n in range(partner_zero["frame"], later["frame"])
                    for value, _ in complete_hud_health(self.frame_lookup.get(n, {}), slot))):
                continue
            narration = [{"frame": n, "text": line["text"], "confidence": line["confidence"]}
                         for n in range(bad["frame"], later["frame"])
                         for line in self.frame_lookup.get(n, {}).get("ocr", ())
                         if line.get("confidence", 0) >= .95 and line.get("top", 0) >= .55 and
                         re.fullmatch(r"It's (?:not very|super) effective on the opposing (.+)\.",
                                      line.get("text", ""), re.I) and
                         line["text"].casefold().endswith("on the opposing " + name + ".")]
            if len({p["frame"] for p in narration}) < 2:
                continue
            final_proof = {"frame": later["frame"], "text": final[0]["text"],
                           "confidence": final[0]["confidence"], "kind": "named_final_hud"}
            evidence = (prior["hp_support"]["evidence"] + wrong_slot + narration + [final_proof])
            resolution = {"state": "resolved", "event_seq": prior["seq"],
                          "actor_id": actor_id, "slot": slot,
                          "reason": "El 0 % pertenece al compañero debilitado; el HUD propio y la narración confirman el daño final del mismo ataque.",
                          "evidence": evidence}
            prior["after"] = prior["health"] = later["after"]
            prior["observations"].extend(later["observations"])
            prior["hp_support"] = {"state": "confirmed", "reason": resolution["reason"],
                                   "evidence": evidence}
            prior["note"] = "PS finales confirmados en el HUD tras completar el impacto sobre ambos rivales."
            for rejected in (bad, later):
                rejected["status"] = "suppressed"
                rejected["resolution"] = resolution
            bad["hp_state"] = "rejected"
            later["hp_state"] = "merged"
            self.actors[actor_id]["health"] = prior["after"]
            self.actors[actor_id]["health_state"] = "confirmed"
            for pending in self.issues:
                if pending["event_seq"] in {bad["seq"], later["seq"]} and pending["code"] in {
                    "hp_unconfirmed", "hp_transition"}:
                    self.resolved_issues.append({**pending, "resolution": resolution})
                    resolved.add(pending["event_seq"])
        self.issues = [issue for issue in self.issues if issue.get("event_seq") not in resolved]

    def _reconcile_partner_hp_label(self) -> None:
        """Reject a partner's low percentage copied onto the other target."""
        resolved = set()
        for issue in self.issues:
            if issue["code"] != "hp_unconfirmed":
                continue
            bad = self.events[issue["event_seq"] - 1]
            slot, actor_id = bad.get("slot"), bad.get("actor_id")
            if (bad["kind"] != "hp_unconfirmed" or bad["status"] != "review" or
                slot not in {"p2a", "p2b"} or not actor_id or
                not re.fullmatch(r"\d+/100", str(bad.get("health") or ""))):
                continue
            partner = "p2b" if slot == "p2a" else "p2a"
            last = next((e for e in reversed(self.events[:bad["seq"] - 1]) if
                         e["actor_id"] == actor_id and e["status"] == "consistent" and
                         e["kind"] in HP_KINDS | {"switch", "drag"}), None)
            other = next((e for e in self.events[bad["seq"]:] if e["kind"] in HP_KINDS and
                          e["actor_id"] == actor_id), None)
            partner_damage = next((e for e in self.events if
                                   e["kind"] == "damage" and e["slot"] == partner and
                                   e["status"] == "consistent" and e.get("hp_state") == "confirmed" and
                                   e.get("after") == bad["health"] and e["turn"] == bad["turn"] and
                                   0 <= bad["frame"] - e["frame"] <= 4), None)
            if (not last or not last.get("health") and not last.get("after") or
                not other or other["kind"] != "heal" or other["status"] != "review" or
                other.get("hp_state") != "confirmed" or not partner_damage or
                not 0 < other["frame"] - bad["frame"] <= 4 or
                not 0 <= bad["frame"] - partner_damage["frame"] <= 4 or
                last["turn"] > bad["turn"] or bad["turn"] != other["turn"] or
                not (0 < (health_ratio(bad["health"]) or 0) <
                     (health_ratio(other["after"]) or 0) <
                     (health_ratio(last.get("after") or last.get("health")) or 0))):
                continue
            move = self.events[partner_damage["cause"] - 1] if isinstance(partner_damage["cause"], int) else None
            if (not move or move["kind"] != "move" or not (move.get("slot") or "").startswith("p1") or
                not 0 < bad["frame"] - move["frame"] <= 15 or
                any(e["kind"] in ACTIVITY | {"mega", "faint"} and e["slot"] == slot
                    for e in self.events[move["seq"]:other["seq"] - 1])):
                continue
            name = hud_nickname(self.frame_lookup.get(bad["frame"], {}), slot)
            species = identity_species(self.actors[actor_id]["species"]).casefold()
            if not name or not (species == name or species.startswith(name + "-")):
                continue
            own, wrong = [], []
            for n in range(partner_damage["frame"], other["frame"] + 3):
                row = self.frame_lookup.get(n, {})
                if hud_nickname(row, slot) == name:
                    own.extend({"frame": n, "text": line["text"], "confidence": line["confidence"]}
                               for value, line in complete_hud_health(row, slot)
                               if value == other["after"] and line["confidence"] >= .97)
                wrong.extend({"frame": n, "text": line["text"], "confidence": line["confidence"]}
                             for value, line in complete_hud_health(row, partner)
                             if value == bad["health"] and line["confidence"] >= .95)
            if (len({x["frame"] for x in own}) < 2 or len({x["frame"] for x in wrong}) < 2 or
                not any(observation.get("health") == other["after"] for observation in bad["observations"])):
                continue
            narrated = [{"frame": n, "text": line["text"], "confidence": line["confidence"]}
                        for n in range(other["frame"], other["frame"] + 3)
                        for line in self.frame_lookup.get(n, {}).get("ocr", ())
                        if line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95 and
                        re.fullmatch(r"It's (?:not very|super) effective on the opposing (.+)\.",
                                     line.get("text", ""), re.I) and
                        line["text"].casefold().endswith("on the opposing " + name + ".")]
            if len({x["frame"] for x in narrated}) < 2:
                continue
            evidence = own + wrong + narrated
            resolution = {"state": "resolved", "event_seq": bad["seq"],
                          "actor_id": actor_id, "slot": slot,
                          "reason": "El porcentaje bajo pertenece al HUD del compañero; el HUD nombrado acredita el daño de este objetivo.",
                          "evidence": evidence}
            bad.update(kind="damage", status="consistent", health=other["after"],
                       after=other["after"], cause=move["seq"], hp_state="confirmed",
                       hp_support={"state": "confirmed", "reason": resolution["reason"],
                                   "evidence": own}, note=resolution["reason"], resolution=resolution)
            other.update(status="suppressed", hp_state="merged", resolution=resolution)
            self.actors[actor_id]["health"] = bad["after"]
            self.actors[actor_id]["health_state"] = "confirmed"
            for pending in self.issues:
                if pending["event_seq"] in {bad["seq"], other["seq"]} and pending["code"] in {
                    "hp_unconfirmed", "hp_transition"}:
                    self.resolved_issues.append({**pending, "resolution": resolution})
                    resolved.add(pending["event_seq"])
        self.issues = [issue for issue in self.issues if issue.get("event_seq") not in resolved]

    def _reconcile_late_lethal_endpoint(self) -> None:
        """Complete one impact when its final zero appears with the faint HUD."""
        for faint in self.events:
            if faint["kind"] != "faint" or faint["status"] != "consistent" or not faint.get("actor_id"):
                continue
            damage = next((e for e in reversed(self.events[:faint["seq"] - 1]) if
                           e["actor_id"] == faint["actor_id"] and e["status"] == "consistent" and
                           e["kind"] in HP_KINDS | {"switch", "drag"}), None)
            if (not damage or damage["kind"] != "damage" or damage.get("hp_state") != "confirmed" or
                not 0 < (health_ratio(damage.get("after")) or 0) < 1 or
                not isinstance(damage.get("cause"), int) or
                faint["slot"] != damage["slot"] or faint["turn"] != damage["turn"] or
                not 0 < faint["frame"] - damage["frame"] <= 6 or
                any(e["status"] == "consistent" and e["kind"] in ACTIVITY | {"mega", "faint"} and
                    e["slot"] == faint["slot"] for e in self.events[damage["seq"]:faint["seq"] - 1])):
                continue
            move = self.events[damage["cause"] - 1]
            if move["kind"] != "move" or not 0 < damage["frame"] - move["frame"] <= 10:
                continue
            row = self.frame_lookup.get(faint["frame"], {})
            name = hud_nickname(row, faint["slot"])
            species = identity_species(self.actors[faint["actor_id"]]["species"]).casefold()
            if (not name or identity_species(self.nickname_species[faint["slot"][:2]].get(
                    name, name)).casefold() != species):
                continue
            support = self._hp_support(faint["slot"], "0/100", faint["frame"])
            if support["state"] != "confirmed" or not any(
                e["frame"] == faint["frame"] and e["text"] == "0%" for e in support["evidence"]):
                continue
            narration = []
            for number in range(faint["frame"], faint["frame"] + 3):
                for line in self.frame_lookup.get(number, {}).get("ocr", ()):
                    match = FAINT_NARRATION.fullmatch(line.get("text", "").strip())
                    if (match and match[1] and match[2].casefold() == name and
                        line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95):
                        narration.append({"frame": number, "text": line["text"],
                                          "confidence": line["confidence"]})
            if len({x["frame"] for x in narration}) < 2:
                continue
            proof = support["evidence"] + narration
            damage["observations"].append({"frame": faint["frame"], "health": "0/100",
                                           "reason": "Extremo final del mismo impacto acreditado por HUD y desmayo"})
            damage["after"] = damage["health"] = "0/100"
            damage["hp_support"] = {"state": "confirmed", "reason": "HUD cero junto al desmayo",
                                    "evidence": damage["hp_support"]["evidence"] + proof}
            damage["note"] = "PS cero observados al finalizar la animación y repetirse el desmayo."
            damage["endpoint_reconstruction"] = {"state": "confirmed", "faint_seq": faint["seq"],
                                                 "move_seq": move["seq"], "evidence": proof}
            self.actors[faint["actor_id"]]["health"] = "0/100"

    def _reconcile_switch_and_mega_context(self) -> None:
        """Link repeated withdrawal/stone text to the actual entry or Mega.

        These lines explain an action, but are not additional battle actions.
        A mere resemblance to an event does not resolve the warning: both the
        subject and the later accepted action must agree with repeated OCR.
        """
        def opponent_species(name: str) -> str:
            nickname = strip_pokemon_title(name).casefold()
            return identity_species(self.nickname_species["p2"].get(nickname, nickname)).casefold()

        def same_species(mentioned: str, actual: str) -> bool:
            base = opponent_species(mentioned)
            current = identity_species(actual).casefold()
            return base == current or current.startswith(base + "-")

        unresolved = []
        for issue in self.issues:
            if issue["code"] != "unclassified_text":
                unresolved.append(issue)
                continue
            message = self.events[issue["event_seq"] - 1]
            value = message.get("value") or ""
            withdrawal = re.fullmatch(r"(.+?) withdrew (.+)!", value, re.I)
            stone = re.fullmatch(r"The opposing (.+?)'s .+?reacting to Trainer's Omni Ring!", value)
            matches = []
            for slot in ("p2a", "p2b"):
                previous = next((e for e in reversed(self.events[:message["seq"] - 1])
                                 if e.get("slot") == slot and e["kind"] in {"switch", "drag"} and
                                 e["status"] == "consistent"), None)
                if not previous or any(e.get("actor_id") == previous["actor_id"] and e["kind"] == "faint"
                                       for e in self.events[previous["seq"]:message["seq"] - 1]):
                    continue
                actor = self.actors[previous["actor_id"]]
                if withdrawal and same_species(withdrawal[2], actor["species"]):
                    upcoming = [e for e in self.events[message["seq"]:] if
                                e["kind"] in {"switch", "drag"} and e["status"] == "consistent" and
                                e.get("slot") == slot and e["actor_id"] != previous["actor_id"] and
                                message["frame"] < e["logical_frame"] <= message["frame"] + 20]
                    if len(upcoming) != 1:
                        continue
                    action = upcoming[0]
                    withdraw_proof, entry_proof = [], []
                    for row in self.frames:
                        if not message["frame"] <= row["frame"] <= action["logical_frame"] + 3:
                            continue
                        for line in row.get("ocr", ()):
                            text = line.get("text", "").strip()
                            if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                                continue
                            old = re.fullmatch(r"(.+?) withdrew (.+)!", text, re.I)
                            new = re.fullmatch(r"(.+?) sent out (.+)!", text, re.I)
                            proof = {"frame": row["frame"], "text": text, "confidence": line["confidence"]}
                            if (old and row["frame"] <= message["frame"] + 3 and
                                    old[1].casefold() == withdrawal[1].casefold() and
                                    same_species(old[2], actor["species"])):
                                withdraw_proof.append(proof)
                            if (new and action["logical_frame"] - 3 <= row["frame"] <=
                                    action["logical_frame"] + 3 and
                                    new[1].casefold() == withdrawal[1].casefold() and
                                    same_species(new[2], self.actors[action["actor_id"]]["species"])):
                                entry_proof.append(proof)
                    if (len({x["frame"] for x in withdraw_proof}) >= 2 and
                            len({x["frame"] for x in entry_proof}) >= 2):
                        matches.append((action, withdraw_proof + entry_proof, "Retirada y nueva entrada del mismo slot corroboradas por OCR repetido."))
                elif stone and actor["species"].casefold() == stone[1].casefold():
                    upcoming = [e for e in self.events[message["seq"]:] if
                                e["kind"] == "mega" and e["status"] == "consistent" and
                                e.get("actor_id") == previous["actor_id"] and e.get("slot") == slot and
                                0 < e["frame"] - message["frame"] <= 40]
                    if len(upcoming) != 1:
                        continue
                    action = upcoming[0]
                    reaction_proof, mega_proof = [], []
                    for row in self.frames:
                        if not message["frame"] <= row["frame"] <= action["frame"] + 3:
                            continue
                        for line in row.get("ocr", ()):
                            text = line.get("text", "").strip()
                            if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                                continue
                            name = re.match(r"The opposing (.+?)'s ", text)
                            evolved = re.fullmatch(r"The opposing (.+?) has Mega Evolved into Mega (.+)!", text)
                            proof = {"frame": row["frame"], "text": text, "confidence": line["confidence"]}
                            if (name and row["frame"] <= message["frame"] + 3 and
                                    "reacting to Trainer's Omni Ring!" in text and
                                    self.nickname_species["p2"].get(name[1].casefold()) == actor["species"]):
                                reaction_proof.append(proof)
                            if (evolved and action["frame"] <= row["frame"] <= action["frame"] + 3 and
                                    self.nickname_species["p2"].get(evolved[1].casefold()) == actor["species"] and
                                    action["species"].casefold() == (evolved[2] + "-Mega").casefold()):
                                mega_proof.append(proof)
                    if (len({x["frame"] for x in reaction_proof}) >= 2 and
                            len({x["frame"] for x in mega_proof}) >= 2):
                        matches.append((action, reaction_proof + mega_proof, "Preparación y megaevolución del mismo actor corroboradas por OCR repetido."))
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            action, proof, reason = matches[0]
            resolution = {"state": "resolved", "event_seq": action["seq"],
                          "actor_id": action["actor_id"], "slot": action["slot"],
                          "reason": reason, "evidence": proof}
            message["status"], message["resolution"] = "suppressed", resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _reconcile_malformed_mega_text(self) -> None:
        """Link a missing space in the stone announcement to a confirmed Mega."""
        unresolved = []
        for issue in self.issues:
            if issue["code"] != "unclassified_text":
                unresolved.append(issue)
                continue
            message = self.events[issue["event_seq"] - 1]
            malformed = re.fullmatch(r"(.+?)['’]s (\S+?)is reacting to (.+?)['’]s Omni Ring!",
                                     str(message.get("value") or ""), re.I)
            if not malformed:
                unresolved.append(issue)
                continue
            species, stone, trainer = malformed.groups()
            matches = []
            for action in self.events[message["seq"]:]:
                if (action["kind"] != "mega" or action["status"] != "consistent" or
                    not (action.get("slot") or "").startswith("p1") or
                    action["value"].casefold() != stone.casefold() or
                    identity_species(action["species"]).casefold() != species.casefold() or
                    not 0 < action["frame"] - message["frame"] <= 30):
                    continue
                clean, evolved = [], []
                for number in range(message["frame"], action["frame"] + 4):
                    row = self.frame_lookup.get(number, {})
                    for line in row.get("ocr", ()):
                        if line.get("confidence", 0) < .95 or line.get("top", 0) < .55:
                            continue
                        text = line.get("text", "").strip()
                        fixed = MEGA_NARRATION.fullmatch(text)
                        later = re.fullmatch(r"(.+?) has Mega Evolved into Mega (.+)!", text, re.I)
                        proof = {"frame": number, "text": text, "confidence": line["confidence"]}
                        if (fixed and not fixed[1] and number <= message["frame"] + 4 and
                            fixed[3].casefold() == stone.casefold() and
                            text.casefold().endswith("reacting to " + trainer.casefold() + "'s omni ring!") and
                            identity_species(self.nickname_species["p1"].get(
                                fixed[2].casefold(), fixed[2])).casefold() == species.casefold()):
                            clean.append(proof)
                        if (later and action["frame"] <= number <= action["frame"] + 3 and
                            later[2].casefold() == species.casefold() and
                            identity_species(self.nickname_species["p1"].get(
                                later[1].casefold(), later[1])).casefold() == species.casefold()):
                            evolved.append(proof)
                if len({e["frame"] for e in clean}) >= 2 and len({e["frame"] for e in evolved}) >= 2:
                    matches.append((action, clean + evolved))
            if len(matches) != 1:
                unresolved.append(issue)
                continue
            action, evidence = matches[0]
            resolution = {"state": "resolved", "event_seq": action["seq"],
                          "actor_id": action["actor_id"], "slot": action["slot"],
                          "reason": "Espacio perdido en la lectura de la piedra; anuncio correcto repetido y megaevolución del mismo actor.",
                          "evidence": evidence}
            message["status"], message["resolution"] = "suppressed", resolution
            self.resolved_issues.append({**issue, "resolution": resolution})
        self.issues = unresolved

    def _ghost_placeholder_after_faint(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Identify a transient clipped HUD as the old fainted occupant."""
        event = candidate["event"]
        slot, number = event.get("slot"), candidate["observed_frame"]
        if (event["kind"] != "switch" or slot not in {"p2a", "p2b"} or event.get("health") or
            not PLACEHOLDER.match(event.get("species") or "") or slot in self.active):
            return None
        faint = next((e for e in reversed(self.events) if e["kind"] == "faint" and
                      e["slot"] == slot and e["status"] == "consistent"), None)
        if not faint or not 0 < number - faint["frame"] <= 3:
            return None
        hp = next((e for e in reversed(self.events[:faint["seq"] - 1]) if
                   e["actor_id"] == faint["actor_id"] and e["kind"] in HP_KINDS and
                   e["status"] == "consistent"), None)
        if not hp or hp.get("hp_state") != "confirmed" or health_ratio(hp.get("after")) != 0:
            return None
        fragment = hud_nickname(self.frame_lookup.get(number, {}), slot)
        old = identity_species(self.actors[faint["actor_id"]]["species"]).casefold()
        if (not fragment or len(fragment) < 3 or not old.endswith(fragment) or
            complete_hud_health(self.frame_lookup[number], slot)):
            return None
        narration = []
        for n in range(faint["frame"], number + 3):
            for line in self.frame_lookup.get(n, {}).get("ocr", ()):
                if line.get("top", 0) < .55 or line.get("confidence", 0) < .95:
                    continue
                text = line.get("text", "").strip()
                match = FAINT_NARRATION.fullmatch(text)
                if match and match[1] and identity_species(self.nickname_species["p2"].get(
                        match[2].casefold(), match[2])).casefold() == old:
                    narration.append({"frame": n, "text": text, "confidence": line["confidence"]})
                if ANNOUNCED_ENTRY.search(text):
                    return None
        if len({e["frame"] for e in narration}) < 2 or not any(e["frame"] >= number for e in narration):
            return None
        return {"state": "resolved", "event_seq": faint["seq"],
                "actor_id": faint["actor_id"], "slot": slot,
                "reason": "HUD recortado del rival debilitado durante el anuncio repetido; no entró un actor nuevo.",
                "evidence": (hp["hp_support"]["evidence"] + narration +
                             [{"frame": number, "text": fragment, "kind": "clipped_hud"}])}

    def _recover_withdrawn_entries(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recover a missed voluntary replacement from independent evidence.

        Require repeated withdrawal + single entry announcement, the outgoing
        HUD's unique slot, a repeated incoming HUD, and a known alias or a
        repeated ability panel unique within the archived team. No nickname,
        species or ability is hardcoded. Missing/ambiguous evidence stays open.
        """
        recovered = []
        for candidate in candidates:
            event, start = candidate["event"], candidate["observed_frame"]
            match = re.fullmatch(r"Go! (.+?)!", str(event.get("value", ""))) if event["kind"] == "message" else None
            if not match or " and " in match[1]:
                continue
            name = strip_pokemon_title(match[1]).casefold()
            announcements = [{"frame": n, **line} for n in range(start, start + 5)
                             for line in self.frame_lookup.get(n, {}).get("ocr", ())
                             if line.get("text", "").strip() == event["value"] and
                             line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95]
            if len({e["frame"] for e in announcements}) < 2:
                continue
            withdrawals = [(n, m[1].casefold(), line) for n in range(start - 20, start)
                           for line in self.frame_lookup.get(n, {}).get("ocr", ())
                           if line.get("top", 0) >= .55 and line.get("confidence", 0) >= .95
                           and (m := re.fullmatch(r"(.+?), come back!", line.get("text", ""), re.I))]
            if not withdrawals or len({x[1] for x in withdrawals}) != 1:
                continue
            withdrawal_frame, old_name, _ = withdrawals[0]
            if len({n for n, _, _ in withdrawals}) < 2:
                continue
            slot_reads = [(n, s) for n in range(withdrawal_frame - 3, withdrawal_frame)
                          for s in ("p1a", "p1b") if hud_nickname(self.frame_lookup.get(n, {}), s) == old_name]
            if len({n for n, _ in slot_reads}) < 2 or len({s for _, s in slot_reads}) != 1:
                continue
            slot = slot_reads[0][1]
            previous = [c for c in candidates if c["logical_frame"] < withdrawal_frame and
                        c["event"].get("slot") == slot and c["event"]["kind"] in {"switch", "drag", "faint"}]
            if not previous or previous[-1]["event"]["kind"] == "faint":
                continue
            # Search only this appearance, with continuous sampled time and
            # no other entry/withdrawal in this slot or another Go! episode.
            appearance = []
            last_ms = self.frame_lookup[withdrawal_frame]["timestamp_ms"]
            blocked = False
            for n in range(withdrawal_frame, min(start + 121, self.frames[-1]["frame"] + 1)):
                row = self.frame_lookup.get(n)
                if row is None or not 0 <= row["timestamp_ms"] - last_ms <= 1_000:
                    break
                last_ms = row["timestamp_ms"]
                events = row.get("detections", {}).get("events", ())
                if row.get("detections", {}).get("battle_complete") or any(
                    e["kind"] in {"switch", "drag", "faint"} and e.get("slot") == slot for e in events):
                    break
                text_lines = [l["text"] for l in row.get("ocr", ()) if l.get("top", 0) >= .55 and l.get("confidence", 0) >= .95]
                if any((ANNOUNCED_ENTRY.search(t) and t != event["value"]) or
                       (n >= start and re.search(r"\b(come back|went back|withdrew)\b", t, re.I)) for t in text_lines):
                    break
                if n < start and (any(e["kind"] in ACTIVITY | HP_KINDS | {"mega", "faint"} for e in events) or
                                  any(RAW_ACTION.search(t) for t in text_lines)):
                    blocked = True
                    break
                if n >= start:
                    appearance.append(row)
            if blocked or len(appearance) < 2:
                continue
            species = self.nickname_species["p1"].get(name)
            ability_evidence = []
            roster = self.context.get("teams", {}).get("p1", [])
            catalog = species_abilities() if not species and roster else {}
            for row in appearance:
                if row["timestamp_ms"] - candidate["observed_ms"] > 10_000 or any(
                    e["kind"] in {"move", "cant", "mega", "damage", "heal"} for e in row["detections"]["events"]) or any(
                    RAW_ACTION.search(l.get("text", "")) for l in row.get("ocr", ()) if l.get("top", 0) >= .55):
                    break
                panel = [l for l in row.get("ocr", ()) if .02 <= l.get("left", -1) <= .30 and
                         .30 <= l.get("top", -1) <= .55 and l.get("confidence", 0) >= .95]
                owners = [l for l in panel if re.sub(r"[’']s$", "", l.get("text", "").casefold()) == name]
                if any(l["text"] in {"Trace", "Imposter", "Illusion"} for l in panel):
                    ability_evidence = []
                    break
                if len(owners) == 1:
                    for line in panel:
                        matches = [s for s in roster if line["text"] in catalog.get(s, set())]
                        if len(matches) == 1:
                            ability_evidence.append({"frame": row["frame"], "species": matches[0],
                                                     "ability": line["text"], "owner": owners[0], "reading": line})
            if not species:
                if (len({e["frame"] for e in ability_evidence}) < 2 or
                    not any(b["frame"] == a["frame"] + 1 for a, b in zip(ability_evidence, ability_evidence[1:])) or
                    len({(e["species"], e["ability"]) for e in ability_evidence}) != 1 or
                    any("Illusion" in catalog.get(s, set()) for s in roster)):
                    continue
                species = ability_evidence[0]["species"]
            if any(sum(hud_nickname(row, s) == name for s in ("p1a", "p1b")) > 1 for row in appearance):
                continue
            hud = [row for row in appearance if hud_nickname(row, slot) == name and
                   sum(hud_nickname(row, s) == name for s in ("p1a", "p1b")) == 1 and
                   len(complete_hud_health(row, slot)) == 1]
            pair = next(((a, b) for a, b in zip(hud, hud[1:]) if b["frame"] == a["frame"] + 1), None)
            if not pair or pair[0] is not hud[0]:
                continue
            if any(hud_nickname(row, slot) not in {None, name} for row in appearance
                   if row["frame"] >= pair[0]["frame"]):
                continue
            initial = complete_hud_health(pair[0], slot)[0][0]
            maximum = initial.split("/")[1]
            # First appearance may start with an impact already animating.
            # The standard machine labels that starting maximum as inferred.
            if any(c["event"]["kind"] in {"switch", "drag"} and c["event"].get("slot", "") and c["event"]["slot"].startswith("p1") and
                   identity_species(c.get("canonical_species") or c["event"].get("species") or "") == identity_species(species)
                   for c in candidates if c["logical_frame"] < start):
                continue
            observations = reconstruct_entry_health(slot, name, f"{maximum}/{maximum}", pair[0]["frame"],
                                                    appearance[-1]["frame"], self.frame_lookup)
            if observations is None:
                continue
            proof = {"state": "confirmed", "nickname": name, "species": species, "slot": slot,
                     "withdrawals": [{"frame": n, **line} for n, _, line in withdrawals],
                     "outgoing_hud": [{"frame": n, "slot": s, "nickname": old_name} for n, s in slot_reads],
                     "announcements": announcements, "team": roster, "ability_evidence": ability_evidence,
                     "hud_frames": [r["frame"] for r in pair], "reason": "Retirada y entrada corroboradas por HUD e identidad"}
            self.nickname_species["p1"][name] = species
            candidate["withdrawal_reconstruction"] = proof
            candidate["event"] = {**event, "kind": "switch", "slot": slot, "species": species, "health": None, "value": None}
            if ability_evidence:
                a = ability_evidence[0]
                recovered.append({"event": {"kind": "ability", "slot": slot, "species": species, "value": a["ability"],
                                             "source_frame": a["frame"], "confidence": a["reading"]["confidence"]},
                                  "observed_frame": a["frame"], "observed_ms": self.frame_lookup[a["frame"]]["timestamp_ms"],
                                  "logical_frame": a["frame"], "ordinal": -1})
            for observation in observations:
                n = observation["frame"]
                recovered.append({"event": {"kind": observation["kind"], "slot": slot, "species": species,
                                             "health": observation["health"], "source_frame": n,
                                             "confidence": observation["confidence"]},
                                  "observed_frame": n, "observed_ms": observation["observed_ms"],
                                  "logical_frame": n, "ordinal": -1,
                                  "hp_reconstruction": {**observation, "entry_frame": start}})
        return merge_recovered_candidates(candidates, recovered)

    def _complete_hp_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recover a later literal reading omitted by the detector's deduplication.

        The malformed observation stays rejected. Only the independent, valid
        reading becomes a candidate, at its own time and before any new action.
        """
        recovered = []
        for candidate in candidates:
            event = candidate["event"]
            if event["kind"] not in HP_KINDS or not self._invalid_own_hp_source(candidate):
                continue
            slot, health = event["slot"], event["health"]
            rows = self._confirmation_rows(candidate["observed_frame"],
                                           faint_slot=slot if health_ratio(health) == 0 else None)
            first_name = hud_nickname(self.frame_lookup[candidate["observed_frame"]], slot)
            for row in rows[1:]:
                name = hud_nickname(row, slot)
                if first_name and name and name != first_name:
                    break
                readings = complete_hud_health(row, slot)
                if readings and (len(readings) != 1 or readings[0][0] != health):
                    break
                if len(readings) != 1:
                    continue
                if not any(c["observed_frame"] == row["frame"] and c["event"].get("slot") == slot and
                           c["event"].get("health") == health for c in candidates + recovered):
                    recovered.append({**candidate, "observed_frame": row["frame"],
                                      "logical_frame": row["frame"], "observed_ms": row["timestamp_ms"],
                                      "event": {**event, "source_frame": row["frame"],
                                                "timestamp_ms": row["timestamp_ms"],
                                                "confidence": readings[0][1]["confidence"]},
                                      "hp_reconstruction": {"rejected_frame": candidate["observed_frame"],
                                                            "frame": row["frame"], "text": readings[0][1]["text"],
                                                            "reason": "lectura literal posterior, sin reparar el separador"}})
                break
        return merge_recovered_candidates(candidates, recovered)

    def _recover_late_lethal_hp(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recover a zero HP observation hidden inside a delayed switch event.

        The detector sometimes reports only the late HUD switch, even when an
        intervening attack has already taken that new occupant to zero. A
        repeated named zero HUD and repeated faint text establish the endpoint;
        one opposing attack establishes the cause. Otherwise leave it alone.
        """
        recovered = []
        for entry in candidates:
            event = entry["event"]
            slot = event.get("slot")
            anchor = entry.get("anchor")
            if (event["kind"] != "switch" or slot not in {"p2a", "p2b"} or
                not entry.get("late_health") or event.get("health") != "0/100" or not anchor):
                continue
            start, end = anchor["frame"], entry["observed_frame"]
            species = identity_species(entry.get("canonical_species") or event.get("species") or "").casefold()
            if not species or not start < end:
                continue
            zero = []
            nickname = None
            for number in (end, end + 1, end + 2):
                row = self.frame_lookup.get(number)
                if not row:
                    break
                name = hud_nickname(row, slot)
                readings = complete_hud_health(row, slot)
                actual = identity_species(self.nickname_species["p2"].get(name or "", name or "")).casefold()
                if (not name or actual != species or len(readings) != 1 or
                    readings[0][0] != "0/100" or readings[0][1]["confidence"] < .95 or
                    (nickname and nickname != name)):
                    break
                nickname = name
                zero.append({"frame": number, "text": readings[0][1]["text"],
                             "nickname": name, "confidence": readings[0][1]["confidence"]})
            if len(zero) < 2:
                continue
            opposing_moves = [c for c in candidates if c["event"]["kind"] == "move" and
                              c["event"].get("slot", "").startswith("p1") and
                              start < c["logical_frame"] < end and
                              end - c["observed_frame"] <= 20]
            if len(opposing_moves) != 1 or any(
                c is not entry and c["event"].get("slot") == slot and
                c["event"]["kind"] in HP_KINDS | {"switch", "drag", "faint"} and
                start < c["logical_frame"] <= end for c in candidates):
                continue
            faint = [c for c in candidates if c["event"]["kind"] == "faint" and
                     c["event"].get("slot") == slot and
                     end < c["observed_frame"] <= end + 12]
            if len(faint) != 1:
                continue
            faint_text = []
            for number in range(faint[0]["observed_frame"], faint[0]["observed_frame"] + 3):
                row = self.frame_lookup.get(number, {})
                for line in row.get("ocr", ()):
                    match = FAINT_NARRATION.fullmatch(line.get("text", "").strip())
                    if (match and match[1] and line.get("top", 0) >= .55 and
                        line.get("confidence", 0) >= .95 and
                        strip_pokemon_title(match[2]).casefold() == nickname):
                        faint_text.append({"frame": number, "text": line["text"],
                                           "confidence": line["confidence"]})
            if len({proof["frame"] for proof in faint_text}) < 2:
                continue
            row = self.frame_lookup[end]
            recovered.append({"event": {"kind": "damage", "slot": slot,
                                        "species": event.get("species"), "health": "0/100",
                                        "source_frame": end, "timestamp_ms": row["timestamp_ms"],
                                        "confidence": zero[0]["confidence"]},
                              "observed_frame": end, "observed_ms": row["timestamp_ms"],
                              "logical_frame": end, "ordinal": 1,
                              "hp_reconstruction": {"reason": "HUD cero tras el único ataque rival, antes del debilitamiento repetido",
                                                    "entry_frame": start, "move_frame": opposing_moves[0]["observed_frame"],
                                                    "zero_hud": zero, "faint_text": faint_text}})
        return merge_recovered_candidates(candidates, recovered)

    def run(self) -> dict[str, Any]:
        candidates = self._recover_withdrawn_entries(ordered_candidates(self.frames, self.alias_reconstructions))
        candidates = self._recover_late_lethal_hp(candidates)
        for candidate in self._complete_hp_candidates(candidates):
            self._handle(candidate)
        self._flush_hp()
        self._reconcile_hp_narration()
        self._reconcile_terrain_hp_fragments()
        self._reconcile_mega_candidates()
        self._reconcile_faint_hud_entries()
        self._reconcile_hud_identity_entries()
        self._reconcile_repeated_faint_text()
        self._reconcile_partner_hud_entries()
        self._reconcile_opponent_menu_hud_entries()
        self._reconcile_intermediate_hp_entries()
        self._reconcile_transient_text()
        self._reconcile_voicing_faint_text()
        self._reconcile_spread_damage_hud()
        self._reconcile_partner_hp_label()
        self._reconcile_late_lethal_endpoint()
        self._reconcile_switch_and_mega_context()
        self._reconcile_malformed_mega_text()
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
                    len(row["detections"]["events"]) for row in self.raw_frames),
                "events": self.events, "issues": self.issues, "resolved_issues": self.resolved_issues,
                "narration_links": self.narration_links,
                "counts": dict(counts), "actors": self.actors,
                **({"transition_prefix": self.transition_prefix} if self.transition_prefix else {}),
                **({"alias_reconstructions": self.alias_reconstructions} if self.alias_reconstructions else {}),
                **({"ignored_ui_frames": self.ignored_ui_frames} if self.ignored_ui_frames else {})}


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
    side = "propio" if slot.startswith("p1") else "rival" if slot.startswith("p2") else ""
    actor = f"{species} ({side}, {slot})" if side else species
    if kind == "turn":
        return f"Inicio del turno {item['turn']}"
    if kind in {"switch", "drag"}:
        health = item.get("health")
        if item.get("hp_state") == "inferred":
            health = f"{health or 'completos'} (inferidos; sin lectura del HUD)"
        elif not health:
            health = f"sin lectura (última lectura: {item['last_confirmed_health']})" if item.get(
                "last_confirmed_health") else "sin lectura"
        return f"Entra {actor} · PS {health}"
    if kind == "illusion_reveal":
        return f"Se rompe la Ilusión: {item['species']} aparece en {slot}; PS {item['after'] or '?'}"
    if kind == "move":
        target = item.get("target_slot")
        suffix = " sobre sí mismo" if target == slot else f" → {target}" if target else ""
        return f"{actor} usa {item['move']}{suffix}"
    if kind in HP_KINDS:
        verb = "pierde" if kind == "damage" else "recupera"
        return f"{actor} {verb} PS: {item['before'] or '?'} → {item['after'] or '?'}"
    if kind == "hp_checkpoint":
        return f"HUD confirma PS actuales de {actor}: {item['health']}"
    if kind == "hp_unconfirmed":
        return (f"PS de {actor} sin confirmar: lectura {item['health'] or '?'}; "
                f"última lectura {item['before'] or '?'}")
    if kind in {"hp_ocr_conflict", "hp_zero_rebound"}:
        return f"Lecturas de PS en conflicto para {actor}; se conserva {item['before'] or '?'}"
    if kind == "faint":
        return f"Se debilita {actor}"
    if kind == "status":
        return f"{actor} queda {STATUS_NAMES.get(str(item['value']), item['value'])}"
    if kind == "ability":
        return f"Habilidad de {actor}: {item['value']}"
    if kind == "mega":
        return f"{actor} megaevoluciona con {item['value']}"
    if kind == "enditem":
        return f"Se activa {item['value']} de {actor}"
    effect = str(item.get("value") or "").removeprefix("move: ")
    if kind == "fieldstart":
        return f"Comienza el efecto de campo: {effect}"
    if kind == "fieldend":
        return f"Termina el efecto de campo: {effect}"
    if kind == "weather":
        weather = {"RainDance": "lluvia", "SunnyDay": "sol", "Sandstorm": "tormenta de arena"}
        return f"Cambia el clima: {weather.get(effect, effect)}"
    if kind == "sidestart":
        return f"Se activa {effect} en el lado {side or 'desconocido'}"
    if kind == "cant":
        reason = {"flinch": "retroceso", "par": "parálisis"}
        return f"{actor} no puede actuar ({reason.get(effect, effect)})"
    if kind == "miss":
        return f"El ataque falla sobre {item.get('target_slot') or 'el objetivo'}"
    if kind == "battle_end":
        return "Termina la batalla por abandono" if "forfeit" in effect.lower() else f"Termina la batalla: {effect}"
    if kind in {"ui_text", "unclassified_text"}:
        return f"Mensaje de pantalla: «{effect}»"
    return f"Suceso {kind} de {actor}: {effect}".strip()


def render_markdown(ledger: dict[str, Any], comparison: dict[str, Any] | None) -> str:
    # The archived comparison remains in report.json for audits. The readable
    # battle log describes only what this automaton retained from the OCR trace.
    visible = [item for item in ledger["events"] if item["status"] != "suppressed" and item["kind"] != "turn"]
    hp_count = sum(item["kind"] in HP_KINDS for item in visible)
    lines = [f"# Batalla {ledger['battle_index'] + 1} — log de Champions Ledger", "",
             f"{len(visible)} sucesos · {hp_count} cambios de PS · {len(ledger['issues'])} avisos abiertos "
             f"· fotogramas {ledger['first_frame']}–{ledger['last_frame']}.", "",
             "p1 = equipo propio; p2 = rival. Los PS del rival son porcentajes normalizados del HUD "
             "(100/100 = 100 %), no puntos absolutos. Un aviso abierto indica que el autómata necesita revisión; "
             "cero avisos no equivale a validar visualmente el vídeo.", ""]
    if ledger.get("transition_prefix"):
        prefix = ledger["transition_prefix"]
        lines += [f"Transición de la partida anterior excluida (fotogramas {prefix['first_frame']}–"
                  f"{prefix['last_frame']}); selección corroborada en {prefix['preview_frames']} "
                  f"e inicio nuevo en {prefix['battle_start_frame']}. "
                  "El resultado anterior y sus pruebas permanecen en el JSON y la traza OCR.", ""]
    if ledger.get("ignored_ui_frames"):
        ignored = ledger["ignored_ui_frames"]
        excluded = sum(len(row["detections"].get("events", ())) for row in ignored)
        frames = ", ".join(str(row["frame"]) for row in ignored)
        lines += [f"Panel de estados excluido del combate (fotogramas {frames}); "
                  f"{excluded} lecturas informativas disponibles en el JSON.", ""]
    links = ledger.get("narration_links", [])
    if links:
        linked = sum(link["status"] == "linked" for link in links)
        lines += [f"Mensajes de PS asociados a un cambio: {linked}/{len(links)}; "
                  f"{len(links) - linked} sin asociar.", ""]
    by_seq = {item["seq"]: item for item in ledger["events"]}
    event_positions = {item["seq"]: index for index, item in enumerate(ledger["events"])}
    section = None
    # Entries with late HUD identification are listed together as the opening
    # lineup. The remaining events are ordered by their observed time.
    ordered = sorted(visible, key=lambda item: (item["turn"],
                     0 if item["turn"] == 0 and item["kind"] in {"switch", "drag"} else 1,
                     item["logical_ms"], item["seq"]))
    displayed: list[list[dict[str, Any]]] = []
    for item in ordered:
        if (displayed and item["kind"] == displayed[-1][-1]["kind"] and
            item["kind"] in {"fieldstart", "battle_end"} and
            item["turn"] == displayed[-1][-1]["turn"] and
            item["value"] == displayed[-1][-1]["value"] and
            item["logical_ms"] - displayed[-1][-1]["logical_ms"] <= (
                10_000 if item["kind"] == "battle_end" else 3_000)):
            displayed[-1].append(item)
        else:
            displayed.append([item])
    for repetition in displayed:
        item = repetition[0]
        if item["turn"] != section:
            section = item["turn"]
            if lines[-1]:
                lines.append("")
            lines += [f"## {'Apertura' if section == 0 else 'Turno ' + str(section)}", ""]
        marker = "⚠️ " if item["status"] == "review" else ""
        mm, ss = divmod(item["logical_ms"] // 1000, 60)
        when = "Inicial" if section == 0 and item["kind"] in {"switch", "drag"} else f"{mm:02d}:{ss:02d}"
        detail = f"{marker}{when} · {event_description(item)} · fotograma {item['source_frame']}"
        if len(repetition) > 1:
            frames = ", ".join(str(e["source_frame"]) for e in repetition)
            detail += f" · {len(repetition)} lecturas seguidas (fotogramas {frames})"
        if item["kind"] in {"fieldstart", "fieldend"} and item["observations"]:
            frames = ", ".join(str(e["source_frame"]) for e in [item, *item["observations"]])
            detail += f" · {1 + len(item['observations'])} lecturas del mismo estado (fotogramas {frames})"
        cause = item.get("cause")
        if isinstance(cause, int) and item["kind"] == "damage":
            action = by_seq.get(cause)
            if action and action["kind"] == "move":
                detail += f" · acción asociada: {action['move']}"
        elif isinstance(cause, str):
            detail += f" · {cause}"
        elif item["kind"] == "heal":
            item_index = event_positions[item["seq"]]
            previous = ledger["events"][max(0, item_index - 3):item_index]
            activated = next((e for e in reversed(previous) if e["kind"] == "enditem" and
                              e["actor_id"] == item["actor_id"] and e["turn"] == item["turn"]), None)
            if activated:
                detail += f" · tras {activated['value']}"
        lines += ["- " + detail]
        if item["note"] and item["status"] == "review":
            lines += ["  - " + item["note"]]
        reconstruction = item.get("entry_reconstruction")
        if reconstruction:
            proof = reconstruction["evidence"]
            lines += [f"  - Entrada corroborada por HUD: PS {reconstruction['health']} "
                      f"(fotogramas {proof[0]['frame']} y {proof[1]['frame']})."]
        delayed = item.get("delayed_voluntary_entry")
        if delayed:
            lines += [f"  - Entrada anunciada en fotograma {delayed['anchor']['frame']}; "
                      "retirada anterior y HUD corroboran el slot."]
        action = item.get("action_reconstruction")
        if action:
            lines += [f"  - Acción corroborada por reloj y narración (fotograma {item['frame']})."]
        identity = item.get("identity_support")
        if identity and identity["state"] == "confirmed":
            proof = identity["evidence"]
            lines += [f"  - Identidad corroborada tras estabilizarse el HUD: {proof[0]['nickname']} "
                      f"→ {identity['species']} (fotogramas {proof[0]['frame']} y {proof[1]['frame']})."]
        if item["kind"] in HP_KINDS and len(item["observations"]) > 1:
            seen = " → ".join(str(x["health"]) for x in item["observations"])
            lines += ["  - Lecturas durante la animación: " + seen]
        if item.get("hp_baseline"):
            if item["hp_baseline"]["state"] == "inferred":
                lines += ["  - PS previos inferidos al máximo por primer avistamiento; no confirmados por OCR."]
            else:
                prior = item["hp_baseline"]["evidence"][0]
                lines += [f"  - PS anteriores en el HUD de {prior['nickname']}: "
                          f"{prior['text']} (fotograma {prior['frame']})."]
        screen = item["evidence"][0] if item["evidence"] else None
        if item["kind"] in HP_KINDS and item.get("after"):
            expected = (item["after"].split("/", 1)[0] + "%" if item["slot"].startswith("p2")
                        else item["after"])
            readings = item.get("hp_support", {}).get("evidence", []) + item["evidence"]
            screen = next((line for line in readings if
                           line.get("text", "").replace(" ", "") == expected), None)
        if screen:
            lines += [f"  - Pantalla: «{screen['text']}» "
                      f"(fotograma {screen['frame']})."]
        elif item["kind"] in HP_KINDS and item.get("after"):
            support = item.get("hp_support", {})
            split = next((line for line in support.get("evidence", ()) if
                          line.get("percent") and line.get("text") == item["after"].split("/", 1)[0]), None)
            if split:
                lines += [f"  - HUD: «{split['text']}» y «%» separados "
                          f"(fotograma {split['frame']})."]
        for evidence in item.get("causal_evidence", []):
            delta = evidence["delta_ms"] / 1000
            lines += [f"  - Narración asociada: «{evidence['text']}» (fotograma {evidence['frame']}; "
                      f"{delta:+g} s respecto al inicio del cambio de PS)."]
    lines += ["", "## Avisos abiertos", ""]
    if ledger["issues"]:
        for issue in ledger["issues"]:
            lines += [f"- Fotograma {issue['frame']}: {issue['message']} (`{issue['code']}`)"]
    else:
        lines += ["- Ninguno."]
    for link in links:
        if link["status"] != "linked":
            lines += [f"- Mensaje sin asociar, fotograma {link['frame']}: «{link['text']}»; "
                      f"{link['reason']}"]
    if ledger.get("resolved_issues"):
        lines += ["", "## Avisos resueltos con evidencia", ""]
        for issue in sorted(ledger["resolved_issues"], key=lambda row: row["frame"]):
            resolution = issue["resolution"]
            frames = ", ".join(str(f) for f in sorted({e["frame"] for e in resolution["evidence"]}))
            lines += [f"- Fotograma {issue['frame']}: {resolution['reason']} "
                      f"Evidencia en fotogramas {frames}."]
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
        context = read_diagnostic_context(args.diagnostic)
    else:
        frames, baselines = read_trace(args.trace), {}
        context = {}
    args.out.mkdir(parents=True, exist_ok=True)
    grouped: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for frame in frames:
        grouped[int(frame["battle_index"])].append(frame)
    report = {"source": args.diagnostic.name if args.diagnostic else args.trace.name,
              "sampled_frames": len(frames), "battles": []}
    for battle_index, group in sorted(grouped.items()):
        if not any(row["detections"].get("events") for row in group):
            continue
        ledger = BattleAutomaton(battle_index, group, context).run()
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
        print(f"Batalla {row['battle_index'] + 1}: {row['candidate_events']} candidatos -> "
              f"{row['ledger_events']} sucesos; PS {row['hp_episodes']}; "
              f"avisos abiertos {sum(row['issue_codes'].values())}")


if __name__ == "__main__":
    main()
