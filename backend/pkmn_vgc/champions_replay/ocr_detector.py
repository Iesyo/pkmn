from __future__ import annotations

import gzip
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, Sequence

from .detector import DetectionError, DetectorContext
from .models import BattleEvent, FrameDetections
from .sources import FramePacket


def _text_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _clean_ocr_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("|", " ").strip().split())


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @classmethod
    def from_mapping(cls, value: object) -> OcrLine:
        if not isinstance(value, dict):
            raise ValueError("Cada línea OCR debe ser un objeto JSON.")
        return cls(
            text=_clean_ocr_text(value.get("text")),
            confidence=max(0.0, min(1.0, float(value.get("confidence", 0)))),
            left=max(0.0, min(1.0, float(value.get("left", 0)))),
            top=max(0.0, min(1.0, float(value.get("top", 0)))),
            right=max(0.0, min(1.0, float(value.get("right", 0)))),
            bottom=max(0.0, min(1.0, float(value.get("bottom", 0)))),
        )


class OcrEngine(Protocol):
    def read(self, image: bytes) -> tuple[OcrLine, ...]: ...


class RapidOcrEngine:
    """OCR local y rápido. Los imports pesados se mantienen opcionales."""

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        if not 0 <= min_confidence <= 1:
            raise ValueError("La confianza mínima de OCR debe estar entre 0 y 1.")
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            from rapidocr import RapidOCR  # type: ignore[import-not-found]
        except ImportError as error:
            raise DetectionError(
                "Falta el detector OCR local. Instálalo con: "
                'python -m pip install -e ".\\backend[champions]"'
            ) from error

        self._cv2 = cv2
        self._np = np
        self._engine = RapidOCR(params={"Global.log_level": "ERROR"})
        self.min_confidence = min_confidence

    def read(self, image: bytes) -> tuple[OcrLine, ...]:
        encoded = self._np.frombuffer(image, dtype=self._np.uint8)
        decoded = self._cv2.imdecode(encoded, self._cv2.IMREAD_COLOR)
        if decoded is None:
            raise DetectionError("OCR no pudo decodificar el frame recibido de FFmpeg.")

        height, width = decoded.shape[:2]
        try:
            result = self._engine(
                decoded,
                use_cls=False,
                text_score=self.min_confidence,
            )
        except Exception as error:  # pragma: no cover - depende del runtime ONNX
            raise DetectionError(f"RapidOCR no pudo analizar el frame: {error}") from error

        texts = result.txts if result.txts is not None else ()
        scores = result.scores if result.scores is not None else ()
        boxes = result.boxes
        if boxes is None:
            return ()

        lines: list[OcrLine] = []
        for text, score, box in zip(texts, scores, boxes, strict=False):
            cleaned = _clean_ocr_text(text)
            confidence = float(score)
            if not cleaned or confidence < self.min_confidence:
                continue
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            lines.append(
                OcrLine(
                    text=cleaned,
                    confidence=max(0.0, min(1.0, confidence)),
                    left=max(0.0, min(1.0, min(xs) / width)),
                    top=max(0.0, min(1.0, min(ys) / height)),
                    right=max(0.0, min(1.0, max(xs) / width)),
                    bottom=max(0.0, min(1.0, max(ys) / height)),
                )
            )
        return tuple(sorted(lines, key=lambda line: (line.top, line.left)))


@dataclass(frozen=True, slots=True)
class ChampionsCatalog:
    species: tuple[str, ...] = ()
    moves: tuple[str, ...] = ()


def _dex_candidates() -> tuple[Path, ...]:
    module = Path(__file__).resolve()
    return (
        module.parents[3] / "public" / "data" / "showdown-dex.json.gz",
        module.parent / "data" / "showdown-dex.json.gz",
    )


@lru_cache(maxsize=1)
def load_champions_catalog() -> ChampionsCatalog:
    """Carga los nombres canónicos del snapshot ya incluido en el proyecto."""

    for path in _dex_candidates():
        if not path.is_file():
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            species_values = payload.get("species", {})
            champion_ids = payload.get("formats", {}).get("champions", ())
            moves_values = payload.get("moves", {})
            species = tuple(
                entry["name"]
                for species_id in champion_ids
                if isinstance((entry := species_values.get(species_id)), dict)
                and isinstance(entry.get("name"), str)
            )
            moves = tuple(
                entry["name"]
                for entry in moves_values.values()
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            )
            return ChampionsCatalog(species=species, moves=moves)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return ChampionsCatalog()


class _NameMatcher:
    def __init__(self, values: Sequence[str]) -> None:
        unique: dict[str, str] = {}
        for value in values:
            key = _text_key(value)
            if key and key not in unique:
                unique[key] = value
        self._values = unique

    def resolve(
        self,
        value: str,
        *,
        threshold: float = 0.78,
        allow_fuzzy: bool = True,
    ) -> str | None:
        key = _text_key(value)
        if len(key) < 3:
            return None
        exact = self._values.get(key)
        if exact:
            return exact
        if not allow_fuzzy:
            return None

        best_name: str | None = None
        best_score = threshold
        for candidate_key, candidate_name in self._values.items():
            if abs(len(candidate_key) - len(key)) > max(3, len(key) // 2):
                continue
            score = SequenceMatcher(None, key, candidate_key).ratio()
            if score > best_score:
                best_score = score
                best_name = candidate_name
        return best_name


_UI_TEXT = {
    "battleinfo",
    "fight",
    "movetime",
    "moveinfo",
    "pokemon",
    "communicating",
    "check",
}


def _health_value(value: str) -> str | None:
    compact = value.replace(" ", "").replace("O", "0").replace("o", "0")
    if re.fullmatch(r"\d{1,2}:\d{2}", compact):
        return None
    percentage = re.fullmatch(r"(\d{1,3})\s*%", compact)
    if percentage:
        current = min(100, int(percentage.group(1)))
        return f"{current}/100"

    fraction = re.fullmatch(r"(\d{1,4})\D+(\d{1,4})", compact)
    if fraction:
        current, maximum = map(int, fraction.groups())
        if 0 <= current <= maximum and maximum > 0:
            return f"{current}/{maximum}"

    # RapidOCR puede leer la barra inclinada como un 1: 187/187 -> 1871187.
    digits = "".join(character for character in compact if character.isdigit())
    for size in range(4, 1, -1):
        if len(digits) in {size * 2, size * 2 + 1} and digits[:size] == digits[-size:]:
            current = int(digits[:size])
            if current > 0:
                return f"{current}/{current}"
    return None


def _health_ratio(value: str) -> float:
    current, maximum = value.split("/", 1)
    return int(current) / max(1, int(maximum))


def _health_readings(lines: Sequence[OcrLine]) -> list[tuple[OcrLine, str]]:
    """Une porcentajes que RapidOCR separa como `33` + `%`."""

    percent_signs = [line for line in lines if line.text.strip() == "%"]
    readings: list[tuple[OcrLine, str]] = []
    for line in lines:
        health = _health_value(line.text)
        compact = line.text.replace(" ", "").replace("O", "0").replace("o", "0")
        if health is None and re.fullmatch(r"\d{1,3}", compact):
            suffix = min(
                (
                    candidate
                    for candidate in percent_signs
                    if candidate.left >= line.left
                    and -0.02 <= candidate.left - line.right <= 0.04
                    and abs(candidate.center_y - line.center_y) <= 0.035
                ),
                key=lambda candidate: abs(candidate.left - line.right),
                default=None,
            )
            if suffix is not None:
                health = _health_value(f"{compact}%")
        if health:
            readings.append((line, health))
    return readings


class ChampionsTextParser:
    """Convierte texto y posiciones OCR en observaciones de batalla con estado."""

    def __init__(
        self,
        *,
        context: DetectorContext | None = None,
        catalog: ChampionsCatalog | None = None,
    ) -> None:
        self.context = context or DetectorContext()
        self.catalog = catalog or load_champions_catalog()
        context_species = (*self.context.p1_team, *self.context.p2_team)
        self._species = _NameMatcher((*context_species, *self.catalog.species))
        self._side_species = {
            "p1": _NameMatcher(self.context.p1_team or self.catalog.species),
            "p2": _NameMatcher(self.context.p2_team or self.catalog.species),
        }
        self._known_teams = {
            "p1": bool(self.context.p1_team),
            "p2": bool(self.context.p2_team),
        }
        self._aliases = {
            "p1": self._canonical_aliases(self.context.p1_aliases),
            "p2": self._canonical_aliases(self.context.p2_aliases),
        }
        self._moves = _NameMatcher(self.catalog.moves)
        self._active: dict[str, str] = {}
        self._health: dict[str, str] = {}
        self._visible_messages: set[str] = set()
        self._turn = 0
        self._command_visible = False
        self._turn_has_activity = False
        self._battle_open = False
        self._pending_end = False

    def _canonical_aliases(
        self,
        aliases: Sequence[tuple[str, str]],
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        for alias, species in aliases:
            alias_key = _text_key(alias)
            canonical = self._species.resolve(species, threshold=0.9) or species.strip()
            if alias_key and canonical:
                result[alias_key] = canonical
        return result

    def _resolve_species(self, value: str, side: str | None = None) -> str | None:
        if side in self._side_species:
            alias = self._aliases[side].get(_text_key(value))
            if alias:
                return alias
            resolved = self._side_species[side].resolve(
                value,
                allow_fuzzy=self._known_teams[side],
            )
            if resolved:
                return resolved
            return None
        return self._species.resolve(value, allow_fuzzy=False)

    def _side_for_species(self, species: str, *, opposing: bool = False) -> str:
        if opposing:
            return "p2"
        for slot, active_species in self._active.items():
            if _text_key(active_species) == _text_key(species):
                return slot[:2]
        p1 = {_text_key(value) for value in self.context.p1_team}
        p2 = {_text_key(value) for value in self.context.p2_team}
        key = _text_key(species)
        if key in p2 and key not in p1:
            return "p2"
        return "p1"

    def _slot_for_species(self, species: str, side: str) -> str:
        for slot, active_species in self._active.items():
            if slot.startswith(side) and _text_key(active_species) == _text_key(species):
                return slot
        return f"{side}a"

    def _hud_species(self, lines: Sequence[OcrLine], side: str) -> list[tuple[str, OcrLine]]:
        if side == "p1":
            candidates = [line for line in lines if line.center_y >= 0.82 and line.center_x <= 0.72]
        else:
            candidates = [line for line in lines if line.center_y <= 0.18 and line.center_x >= 0.43]

        found: list[tuple[str, OcrLine]] = []
        seen: set[str] = set()
        for line in sorted(candidates, key=lambda item: item.center_x):
            if _health_value(line.text) or _text_key(line.text) in _UI_TEXT:
                continue
            species = self._resolve_species(line.text, side)
            key = _text_key(species or "")
            if not species or key in seen:
                continue
            seen.add(key)
            found.append((species, line))
        return found[:2]

    def _hud_observations(self, lines: Sequence[OcrLine]) -> dict[str, tuple[str, str | None]]:
        text_keys = {_text_key(line.text) for line in lines}
        if text_keys.intersection({"close", "hidesummary", "helditem", "movesmore"}):
            return {}

        observations: dict[str, tuple[str, str | None]] = {}
        readings = _health_readings(lines)
        for side in ("p1", "p2"):
            species_lines = self._hud_species(lines, side)
            if not species_lines:
                continue

            slot_lines: list[tuple[str, str, OcrLine]] = []
            if len(species_lines) == 1:
                species, line = species_lines[0]
                existing = next(
                    (
                        slot
                        for slot, active_species in self._active.items()
                        if slot.startswith(side) and _text_key(active_species) == _text_key(species)
                    ),
                    f"{side}a",
                )
                slot_lines.append((existing, species, line))
            else:
                for index, (species, line) in enumerate(species_lines):
                    slot_lines.append((f"{side}{'ab'[index]}", species, line))

            if side == "p1":
                health_lines = [
                    (line, health)
                    for line, health in readings
                    if line.center_y >= 0.9
                    and line.center_x <= 0.55
                ]
            else:
                health_lines = [
                    (line, health)
                    for line, health in readings
                    if 0.08 <= line.center_y <= 0.145
                    and line.center_x >= 0.43
                ]

            for slot, species, species_line in slot_lines:
                nearby = min(
                    health_lines,
                    key=lambda item: abs(item[0].center_x - species_line.center_x),
                    default=None,
                )
                health = nearby[1] if nearby and abs(nearby[0].center_x - species_line.center_x) < 0.16 else None
                observations[slot] = (species, health)
        return observations

    def _message_lines(self, lines: Sequence[OcrLine]) -> list[OcrLine]:
        messages: list[OcrLine] = []
        keywords = (
            " used ",
            " fainted",
            " battle ",
            "poison",
            "burn",
            "paraly",
            "asleep",
            "woke up",
            "frozen",
            "sent out",
            "go!",
            "tailwind",
            "trick room",
            "rain",
            "sunlight",
            "sandstorm",
            "snow",
        )
        for line in lines:
            if not (0.56 <= line.center_y <= 0.86 and line.left <= 0.86):
                continue
            key = _text_key(line.text)
            lowered = f" {line.text.casefold()} "
            if key in _UI_TEXT or _health_value(line.text):
                continue
            if line.center_y >= 0.82 and self._resolve_species(line.text):
                continue
            looks_like_battle_text = any(keyword in lowered for keyword in keywords) or (
                len(line.text) >= 8 and line.text.rstrip().endswith(("!", ".", "?"))
            )
            opens_battle = any(
                keyword in lowered
                for keyword in (" sent out ", "sent out ", " go! ", " used ", " fainted")
            )
            if looks_like_battle_text and (self._battle_open or opens_battle):
                messages.append(line)
        return sorted(messages, key=lambda line: (line.top, line.left))

    def _message_event(
        self,
        message: str,
        *,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        cleaned = message.strip()
        opposing_prefix = "the opposing "
        lowered = cleaned.casefold()

        move_match = re.match(r"^(The opposing )?(.+?) used (.+?)[!.]?$", cleaned, re.IGNORECASE)
        if move_match:
            opposing = bool(move_match.group(1))
            actor = self._resolve_species(move_match.group(2), "p2" if opposing else "p1")
            if actor:
                side = self._side_for_species(actor, opposing=opposing)
                move_raw = move_match.group(3).strip()
                move = self._moves.resolve(move_raw, threshold=0.72)
                if not move:
                    return ()
                if len(_text_key(move_raw)) < len(_text_key(move)) * 0.75:
                    return ()
                return (
                    BattleEvent(
                        kind="move",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
                        species=actor,
                        move=move,
                        source_frame=source_frame,
                    ),
                )

        faint_match = re.match(r"^(The opposing )?(.+?) fainted[!.]?$", cleaned, re.IGNORECASE)
        if faint_match:
            opposing = bool(faint_match.group(1))
            species = self._resolve_species(faint_match.group(2), "p2" if opposing else "p1")
            if species:
                side = self._side_for_species(species, opposing=opposing)
                return (
                    BattleEvent(
                        kind="faint",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(species, side),  # type: ignore[arg-type]
                        species=species,
                        source_frame=source_frame,
                    ),
                )

        status_patterns = (
            (r"^(The opposing )?(.+?) was badly poisoned[!.]?$", "tox"),
            (r"^(The opposing )?(.+?) was poisoned[!.]?$", "psn"),
            (r"^(The opposing )?(.+?) was burned[!.]?$", "brn"),
            (r"^(The opposing )?(.+?) (?:was paralyzed|is paralyzed)[!.]?$", "par"),
            (r"^(The opposing )?(.+?) fell asleep[!.]?$", "slp"),
            (r"^(The opposing )?(.+?) was frozen solid[!.]?$", "frz"),
        )
        for pattern, status in status_patterns:
            status_match = re.match(pattern, cleaned, re.IGNORECASE)
            if not status_match:
                continue
            opposing = bool(status_match.group(1))
            species = self._resolve_species(status_match.group(2), "p2" if opposing else "p1")
            if species:
                side = self._side_for_species(species, opposing=opposing)
                return (
                    BattleEvent(
                        kind="status",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(species, side),  # type: ignore[arg-type]
                        species=species,
                        value=status,
                        source_frame=source_frame,
                    ),
                )

        cure_patterns = (
            r"^(The opposing )?(.+?) woke up[!.]?$",
            r"^(The opposing )?(.+?) thawed out[!.]?$",
            r"^(The opposing )?(.+?) (?:was cured|is no longer (?:poisoned|burned|paralyzed))[!.]?$",
        )
        for pattern in cure_patterns:
            cure_match = re.match(pattern, cleaned, re.IGNORECASE)
            if not cure_match:
                continue
            opposing = bool(cure_match.group(1))
            species = self._resolve_species(cure_match.group(2), "p2" if opposing else "p1")
            if species:
                side = self._side_for_species(species, opposing=opposing)
                return (
                    BattleEvent(
                        kind="curestatus",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(species, side),  # type: ignore[arg-type]
                        species=species,
                        value="status",
                        source_frame=source_frame,
                    ),
                )

        weather = None
        if "started to rain" in lowered or "rain began" in lowered:
            weather = "RainDance"
        elif "sunlight turned harsh" in lowered or "harsh sunlight" in lowered:
            weather = "SunnyDay"
        elif "sandstorm" in lowered and any(word in lowered for word in ("kicked", "started", "brewing")):
            weather = "Sandstorm"
        elif "started to snow" in lowered or "snow began" in lowered:
            weather = "Snow"
        if weather:
            return (
                BattleEvent(
                    kind="weather",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    value=weather,
                    source_frame=source_frame,
                ),
            )

        direct_turn = re.fullmatch(r"Turn\s+(\d+)", cleaned, re.IGNORECASE)
        if direct_turn:
            return (
                BattleEvent(
                    kind="turn",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    turn=max(1, int(direct_turn.group(1))),
                    source_frame=source_frame,
                ),
            )

        return (
            BattleEvent(
                kind="message",
                timestamp_ms=timestamp_ms,
                confidence=confidence,
                value=cleaned,
                source_frame=source_frame,
            ),
        )

    def parse(
        self,
        lines: Sequence[OcrLine],
        *,
        timestamp_ms: int,
        source_frame: int,
    ) -> FrameDetections:
        events: list[BattleEvent] = []
        observations = self._hud_observations(lines)
        changed_slots: set[str] = set()

        for slot, (species, health) in observations.items():
            previous_species = self._active.get(slot)
            if previous_species != species:
                self._active[slot] = species
                changed_slots.add(slot)
                if health:
                    self._health[slot] = health
                events.append(
                    BattleEvent(
                        kind="switch",
                        timestamp_ms=timestamp_ms,
                        confidence=0.92,
                        slot=slot,  # type: ignore[arg-type]
                        species=species,
                        health=health,
                        source_frame=source_frame,
                    )
                )

        message_lines = self._message_lines(lines)
        current_messages = {_text_key(line.text) for line in message_lines}
        for line in message_lines:
            key = _text_key(line.text)
            if not key or key in self._visible_messages:
                continue
            parsed = self._message_event(
                line.text,
                confidence=line.confidence,
                timestamp_ms=timestamp_ms,
                source_frame=source_frame,
            )
            events.extend(parsed)
            if any(event.kind not in {"message", "turn"} for event in parsed):
                self._turn_has_activity = True
        self._visible_messages = current_messages

        for slot, (species, health) in observations.items():
            if not health or slot in changed_slots:
                continue
            previous_health = self._health.get(slot)
            self._health[slot] = health
            if not previous_health or previous_health == health:
                continue
            kind = "damage" if _health_ratio(health) < _health_ratio(previous_health) else "heal"
            events.append(
                BattleEvent(
                    kind=kind,
                    timestamp_ms=timestamp_ms,
                    confidence=0.9,
                    slot=slot,  # type: ignore[arg-type]
                    species=species,
                    health=health,
                    source_frame=source_frame,
                )
            )
            self._turn_has_activity = True

        text_keys = {_text_key(line.text) for line in lines}
        command_visible = "fight" in text_keys and (
            "pokemon" in text_keys or "movetime" in text_keys
        )
        if command_visible and not self._command_visible:
            if self._turn == 0:
                self._turn = 1
                events.append(
                    BattleEvent(
                        kind="turn",
                        timestamp_ms=timestamp_ms,
                        confidence=0.95,
                        turn=1,
                        source_frame=source_frame,
                    )
                )
            elif self._turn_has_activity:
                self._turn += 1
                self._turn_has_activity = False
                events.append(
                    BattleEvent(
                        kind="turn",
                        timestamp_ms=timestamp_ms,
                        confidence=0.9,
                        turn=self._turn,
                        source_frame=source_frame,
                    )
                )
        self._command_visible = command_visible

        visible_text = " ".join(line.text.casefold() for line in lines)
        if "battle has ended" in visible_text or "battle is over" in visible_text:
            self._pending_end = True

        winner = None
        if any(
            phrase in visible_text
            for phrase in ("you won the battle", "you won against", "you defeated", "you beat ")
        ):
            winner = "p1"
        elif "you lost" in visible_text or "you were defeated" in visible_text:
            winner = "p2"
        else:
            win_words = {"win", "won", "victory"}
            loss_words = {"lose", "lost", "defeat", "defeated"}
            wins = [line for line in lines if _text_key(line.text) in win_words]
            losses = [line for line in lines if _text_key(line.text) in loss_words]
            if wins and not losses:
                winner = "p1"
            elif losses and not wins:
                winner = "p2"
            elif wins and losses:
                local_won = any(line.center_x < 0.5 for line in wins)
                local_lost = any(line.center_x < 0.5 for line in losses)
                if local_won != local_lost:
                    winner = "p1" if local_won else "p2"

        if observations or events:
            self._battle_open = True
        battle_complete = self._pending_end or winner is not None
        battle_started = self._battle_open and winner is None
        if winner:
            self._battle_open = False

        return FrameDetections(
            events=tuple(events),
            winner=winner,  # type: ignore[arg-type]
            battle_started=battle_started,
            battle_complete=battle_complete,
        )


class ChampionsOcrDetector:
    """Detector principal para vídeo/OBS: OCR local y parser determinista."""

    def __init__(
        self,
        *,
        context: DetectorContext | None = None,
        engine: OcrEngine | None = None,
        trace_path: Path | None = None,
        min_confidence: float = 0.5,
    ) -> None:
        self.engine = engine or RapidOcrEngine(min_confidence=min_confidence)
        self.parser = ChampionsTextParser(context=context)
        self.trace_path = trace_path

    def detect(self, frame: FramePacket) -> FrameDetections:
        started = time.monotonic()
        lines = self.engine.read(frame.image)
        detections = self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
        if self.trace_path:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "frame": frame.index + 1,
                "timestamp_ms": frame.timestamp_ms,
                "elapsed_ms": round((time.monotonic() - started) * 1_000),
                "ocr": [asdict(line) for line in lines],
                "detections": {
                    "battle_started": detections.battle_started,
                    "battle_complete": detections.battle_complete,
                    "winner": detections.winner,
                    "events": [asdict(event) for event in detections.events],
                },
            }
            with self.trace_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return detections


class OcrTraceDetector:
    """Reaplica el parser a una traza sin repetir FFmpeg ni RapidOCR."""

    def __init__(self, *, context: DetectorContext | None = None) -> None:
        self.parser = ChampionsTextParser(context=context)

    def detect(self, frame: FramePacket) -> FrameDetections:
        try:
            payload = json.loads(frame.image.decode("utf-8-sig"))
            values = payload.get("ocr")
            if not isinstance(values, list):
                raise ValueError("falta la lista ocr")
            lines = tuple(
                line
                for item in values
                if (line := OcrLine.from_mapping(item)).text
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise DetectionError(f"La traza OCR contiene un frame inválido: {error}") from error
        return self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
