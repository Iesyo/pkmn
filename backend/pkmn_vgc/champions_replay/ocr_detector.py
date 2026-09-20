from __future__ import annotations

import gzip
import json
import re
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .detector import DetectionError, DetectorContext, HudAlias, HudAliasResolver
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


@dataclass(frozen=True, slots=True)
class PreparedOcrFrame:
    frame: FramePacket
    lines: tuple[OcrLine, ...]
    elapsed_ms: int
    rotation_degrees: int


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
        self.rotation_quarter_turns = 0
        self._orientation_locked = False

    @staticmethod
    def _orientation_score(lines: Sequence[OcrLine], *, landscape: bool) -> tuple[float, int]:
        """Favorece texto legible del HUD para orientar grabaciones de celular."""

        battle_tokens = {
            "battle",
            "fight",
            "pokemon",
            "movetime",
            "battleinfo",
            "turn",
            "used",
            "fainted",
            "sentout",
            "lv50",
        }
        score = 2.0 if landscape else 0.0
        battle_signals = 0
        for line in lines:
            key = _text_key(line.text)
            score += min(0.5, line.confidence * len(key) / 20)
            if any(token in key for token in battle_tokens):
                battle_signals += 1
                score += 5.0
        return score, battle_signals

    def _read_decoded(self, decoded: Any) -> tuple[OcrLine, ...]:
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

    def _rotate(self, decoded: Any, quarter_turns: int) -> Any:
        if quarter_turns == 0:
            return decoded
        return self._np.ascontiguousarray(self._np.rot90(decoded, k=-quarter_turns))

    def read(self, image: bytes) -> tuple[OcrLine, ...]:
        encoded = self._np.frombuffer(image, dtype=self._np.uint8)
        decoded = self._cv2.imdecode(encoded, self._cv2.IMREAD_COLOR)
        if decoded is None:
            raise DetectionError("OCR no pudo decodificar el frame recibido de FFmpeg.")

        height, width = decoded.shape[:2]
        if self._orientation_locked or width >= height:
            if width >= height:
                self.rotation_quarter_turns = 0
                self._orientation_locked = True
            return self._read_decoded(self._rotate(decoded, self.rotation_quarter_turns))

        # Algunos screen recordings móviles conservan 1126x2436 aunque el
        # juego y su texto estén girados. Probamos ambas orientaciones una sola
        # vez y después reutilizamos la ganadora para no triplicar todo el OCR.
        candidates = []
        for quarter_turns in (0, 1, 3):
            oriented = self._rotate(decoded, quarter_turns)
            lines = self._read_decoded(oriented)
            oriented_height, oriented_width = oriented.shape[:2]
            score, battle_signals = self._orientation_score(
                lines,
                landscape=oriented_width >= oriented_height,
            )
            candidates.append((battle_signals, score, quarter_turns, lines))
        battle_signals, _score, quarter_turns, lines = max(
            candidates,
            key=lambda candidate: (candidate[0], candidate[1]),
        )
        # Una notificación de WhatsApp puede ser el texto más legible del
        # primer frame vertical. Sólo fijamos la rotación cuando aparecen
        # señales propias del HUD; hasta entonces volvemos a evaluar.
        if lines and battle_signals and (quarter_turns != 0 or battle_signals >= 2):
            self.rotation_quarter_turns = quarter_turns
            self._orientation_locked = True
        return lines

    def prepare_hud_frame(
        self,
        frame: FramePacket,
        lines: Sequence[OcrLine],
        candidates: Sequence[tuple[str, str]],
        *,
        rotation_degrees: int,
    ) -> FramePacket:
        """Entrega a visión el HUD orientado y ampliado, no el frame móvil crudo."""

        encoded = self._np.frombuffer(frame.image, dtype=self._np.uint8)
        decoded = self._cv2.imdecode(encoded, self._cv2.IMREAD_COLOR)
        if decoded is None:
            return frame
        oriented = self._rotate(decoded, (rotation_degrees // 90) % 4)
        height, width = oriented.shape[:2]
        sides = {side for side, _nickname in candidates}
        nickname_keys = {_text_key(nickname) for _side, nickname in candidates}
        labels = [line for line in lines if _text_key(line.text) in nickname_keys]

        if sides == {"p2"}:
            top, bottom = 0, max(1, round(height * 0.22))
        elif sides == {"p1"}:
            top, bottom = round(height * 0.68), height
        else:
            top, bottom = 0, height

        if labels and len(sides) == 1:
            left_ratio = max(0.0, min(line.left for line in labels) - 0.12)
            right_ratio = min(1.0, max(line.right for line in labels) + 0.16)
            left = round(width * left_ratio)
            right = max(left + 1, round(width * right_ratio))
        else:
            left, right = 0, width
        cropped = oriented[top:bottom, left:right]
        crop_height, crop_width = cropped.shape[:2]
        scale = min(max(1.0, 512 / max(1, crop_height)), 2000 / max(1, crop_width))
        if scale > 1.05:
            cropped = self._cv2.resize(
                cropped,
                None,
                fx=scale,
                fy=scale,
                interpolation=self._cv2.INTER_CUBIC,
            )
        ok, jpeg = self._cv2.imencode(".jpg", cropped, [self._cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return frame
        return FramePacket(
            index=frame.index,
            timestamp_ms=frame.timestamp_ms,
            image=jpeg.tobytes(),
            mime_type="image/jpeg",
        )


@dataclass(frozen=True, slots=True)
class ChampionsCatalog:
    species: tuple[str, ...] = ()
    moves: tuple[str, ...] = ()
    abilities: tuple[str, ...] = ()
    mega_stones: tuple[tuple[str, str, str], ...] = ()


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
            ability_values = payload.get("abilities", {})
            items_values = payload.get("items", {})
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
            abilities = tuple(
                entry["name"]
                for entry in ability_values.values()
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            )
            mega_stones = tuple(
                (entry["name"], base_species, mega_forme)
                for entry in items_values.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("details"), dict)
                and isinstance(entry["details"].get("megaStone"), dict)
                for base_species, mega_forme in entry["details"]["megaStone"].items()
                if isinstance(base_species, str) and isinstance(mega_forme, str)
            )
            return ChampionsCatalog(
                species=species,
                moves=moves,
                abilities=abilities,
                mega_stones=mega_stones,
            )
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
            "p1": self._team_form_aliases(self.context.p1_team),
            "p2": self._team_form_aliases(self.context.p2_team),
        }
        self._aliases["p1"].update(self._canonical_aliases(self.context.p1_aliases))
        self._aliases["p2"].update(self._canonical_aliases(self.context.p2_aliases))
        self._configured_aliases = {
            side: dict(values)
            for side, values in self._aliases.items()
        }
        self._moves = _NameMatcher(self.catalog.moves)
        self._abilities = _NameMatcher(self.catalog.abilities)
        self._mega_stones = {
            _text_key(item): (item, base_species, mega_forme)
            for item, base_species, mega_forme in self.catalog.mega_stones
        }
        self._mega_formes = {
            _text_key(mega_forme): (item, base_species, mega_forme)
            for item, base_species, mega_forme in self.catalog.mega_stones
        }
        self.reset_battle_state()

    def reset_battle_state(self) -> None:
        """Descarta el estado efímero antes de analizar otra batalla."""

        self._active: dict[str, str] = {}
        self._health: dict[str, str] = {}
        self._player_names = {
            "p1": self.context.p1_name,
            "p2": self.context.p2_name,
        }
        self._announced_slots: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
        self._preview_ranks: dict[int, str] = {}
        self._preview_count = 0
        self._open_slots: dict[str, list[str]] = {"p1": [], "p2": []}
        self._visible_messages: set[str] = set()
        self._visible_abilities: set[tuple[str, str, str]] = set()
        self._pending_abilities: dict[tuple[str, str, str], float] = {}
        self._pending_fieldstarts: set[str] = set()
        self._recent_field_sources: dict[str, tuple[str, str, str, int]] = {}
        self._turn = 0
        self._command_visible = False
        self._turn_has_activity = False
        self._battle_open = False
        self._pending_end = False
        self._mega_seen: set[str] = set()

    @staticmethod
    def _team_form_aliases(team: Sequence[str]) -> dict[str, str]:
        """Conserva formas de género que el HUD muestra sólo con el nombre base."""

        candidates: dict[str, list[str]] = {}
        for species in team:
            if not species.endswith(("-F", "-M")):
                continue
            base = species[:-2]
            candidates.setdefault(_text_key(base), []).append(species)
        return {
            base_key: formes[0]
            for base_key, formes in candidates.items()
            if len(formes) == 1
        }

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
            value_key = _text_key(value)
            alias = self._aliases[side].get(value_key)
            if alias:
                return alias
            if len(value_key) >= 4:
                fuzzy_alias = max(
                    (
                        (SequenceMatcher(None, value_key, alias_key).ratio(), species)
                        for alias_key, species in self._aliases[side].items()
                        if abs(len(alias_key) - len(value_key)) <= max(3, len(value_key) // 2)
                    ),
                    default=(0.0, ""),
                )
                if fuzzy_alias[0] >= 0.74:
                    return fuzzy_alias[1]
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

    def _side_for_player(self, value: str) -> str | None:
        player = _text_key(value)
        if player and player == _text_key(self._player_names["p1"]):
            return "p1"
        if player and player == _text_key(self._player_names["p2"]):
            return "p2"
        return None

    def _announced_slot(self, side: str, value: str) -> str | None:
        value_key = _text_key(value)
        exact = self._announced_slots[side].get(value_key)
        if exact:
            return exact
        if len(value_key) < 4:
            return None
        score, slot = max(
            (
                (SequenceMatcher(None, value_key, alias_key).ratio(), candidate_slot)
                for alias_key, candidate_slot in self._announced_slots[side].items()
            ),
            default=(0.0, ""),
        )
        return slot if score >= 0.64 else None

    def _bind_alias(self, side: str, value: str, species: str) -> tuple[str | None, bool]:
        """Aprende un nickname cuando el juego revela después su especie."""

        key = _text_key(value)
        if key:
            self._aliases[side][key] = species
        slot = self._announced_slot(side, value)
        changed = bool(slot and self._active.get(slot) != species)
        if slot:
            self._active[slot] = species
            self._health.pop(slot, None)
        return slot, changed

    def _mark_slot_open(self, slot: str) -> None:
        side = slot[:2]
        if side in self._open_slots and slot not in self._open_slots[side]:
            self._open_slots[side].append(slot)

    def _announced_species(self, value: str, side: str) -> str | None:
        # Champions sometimes appends a battle-only form in parentheses, e.g.
        # ``Basculegion (Basculegion-M)``. The team context owns the canonical
        # species/form that Showdown should receive.
        candidate = re.sub(r"\s+\([^()]+\)\s*$", "", value.strip())
        resolved = self._resolve_species(candidate, side)
        if resolved:
            return resolved
        candidate_key = _text_key(candidate)
        named_values = {
            **{_text_key(species): species for species in self.context.p1_team if side == "p1"},
            **{_text_key(species): species for species in self.context.p2_team if side == "p2"},
            **self._aliases[side],
        }
        for name_key, species in sorted(named_values.items(), key=lambda item: len(item[0]), reverse=True):
            if candidate_key.startswith(f"{name_key}the"):
                return species
        return None

    def _announced_switch(
        self,
        *,
        side: str,
        species: str,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        slots = self._open_slots[side]
        if not slots:
            # Initial leads are placed from HUD geometry. A text announcement
            # has no reliable doubles slot until a withdrawal or faint opens it.
            return ()
        slot = slots.pop(0)
        if self._active.get(slot) == species:
            return ()
        self._active[slot] = species
        self._health.pop(slot, None)
        return (
            BattleEvent(
                kind="switch",
                timestamp_ms=timestamp_ms,
                confidence=confidence,
                slot=slot,  # type: ignore[arg-type]
                species=species,
                source_frame=source_frame,
            ),
        )

    def _ability_actor(self, value: str) -> tuple[str, str] | None:
        raw = re.sub(r"[\'’]s$", "", value.strip(), flags=re.IGNORECASE)
        candidates: list[tuple[str, str]] = []
        has_known_team = any(self._known_teams.values())
        for side in ("p1", "p2"):
            if has_known_team and not self._known_teams[side]:
                continue
            species = self._resolve_species(raw, side)
            if species:
                candidates.append((side, species))
        active = [
            candidate
            for candidate in candidates
            if any(
                slot.startswith(candidate[0])
                and _text_key(active_species) == _text_key(candidate[1])
                for slot, active_species in self._active.items()
            )
        ]
        if len(active) == 1:
            return active[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _terrain_for_ability(ability: str) -> str | None:
        return {
            "electricsurge": "Electric Terrain",
            "grassysurge": "Grassy Terrain",
            "mistysurge": "Misty Terrain",
            "psychicsurge": "Psychic Terrain",
        }.get(_text_key(ability))

    def _ability_events(
        self,
        lines: Sequence[OcrLine],
        *,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        overlay = [
            line
            for line in lines
            if line.left >= 0.68 and 0.28 <= line.center_y <= 0.52
        ]
        visible: set[tuple[str, str, str]] = set()
        for actor_line in overlay:
            if not re.search(r"[\'’]s$", actor_line.text, re.IGNORECASE):
                continue
            actor = self._ability_actor(actor_line.text)
            if not actor:
                continue
            ability_line = min(
                (
                    candidate
                    for candidate in overlay
                    if candidate.top >= actor_line.bottom - 0.015
                    and 0 <= candidate.center_y - actor_line.center_y <= 0.12
                    and abs(candidate.center_x - actor_line.center_x) <= 0.12
                ),
                key=lambda candidate: candidate.center_y - actor_line.center_y,
                default=None,
            )
            if ability_line is None:
                continue
            ability = self._abilities.resolve(ability_line.text, threshold=0.78)
            if not ability:
                continue
            side, species = actor
            key = (side, species, ability)
            visible.add(key)
            if key not in self._visible_abilities:
                self._pending_abilities[key] = min(actor_line.confidence, ability_line.confidence)
        self._visible_abilities = visible
        return self._flush_pending_abilities(
            timestamp_ms=timestamp_ms,
            source_frame=source_frame,
        )

    def _flush_pending_abilities(
        self,
        *,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        events: list[BattleEvent] = []
        for key, confidence in tuple(self._pending_abilities.items()):
            side, species, ability = key
            slot = next(
                (
                    active_slot
                    for active_slot, active_species in self._active.items()
                    if active_slot.startswith(side)
                    and _text_key(active_species) == _text_key(species)
                ),
                None,
            )
            if slot is None:
                continue
            events.append(
                BattleEvent(
                    kind="ability",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    slot=slot,  # type: ignore[arg-type]
                    species=species,
                    value=ability,
                    source_frame=source_frame,
                )
            )
            terrain = self._terrain_for_ability(ability)
            if terrain:
                self._recent_field_sources[terrain] = (ability, slot, species, timestamp_ms)
                if terrain in self._pending_fieldstarts:
                    events.append(
                        self._fieldstart_event(
                            terrain,
                            confidence=confidence,
                            timestamp_ms=timestamp_ms,
                            source_frame=source_frame,
                        )
                    )
                    self._pending_fieldstarts.remove(terrain)
            del self._pending_abilities[key]
        return tuple(events)

    def _fieldstart_event(
        self,
        terrain: str,
        *,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> BattleEvent:
        tags: tuple[str, ...] = ()
        source = self._recent_field_sources.get(terrain)
        if source and timestamp_ms - source[3] <= 15_000:
            ability, slot, species, _ = source
            tags = (f"[from] ability: {ability}", f"[of] {slot}: {species}")
        return BattleEvent(
            kind="fieldstart",
            timestamp_ms=timestamp_ms,
            confidence=confidence,
            value=f"move: {terrain}",
            tags=tags,
            source_frame=source_frame,
        )

    def _mega_event(
        self,
        *,
        actor: str,
        side: str,
        item: str,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
        announced_forme: str | None = None,
    ) -> tuple[BattleEvent, ...]:
        slot = self._slot_for_species(actor, side)
        if slot in self._mega_seen:
            return ()

        mega = self._mega_stones.get(_text_key(item))
        if mega is None and announced_forme:
            normalized_forme = announced_forme.strip()
            if not normalized_forme.casefold().startswith("mega "):
                normalized_forme = f"Mega {normalized_forme}"
            words = normalized_forme.split()
            suffix = words[-1] if words and words[-1] in {"X", "Y", "Z"} else None
            base_words = words[1:-1] if suffix else words[1:]
            candidate = f"{' '.join(base_words)}-Mega{f'-{suffix}' if suffix else ''}"
            mega = self._mega_formes.get(_text_key(candidate))
        if mega is None:
            return ()

        canonical_item, base_species, mega_forme = mega
        if _text_key(base_species) != _text_key(actor):
            return ()
        self._mega_seen.add(slot)
        return (
            BattleEvent(
                kind="mega",
                timestamp_ms=timestamp_ms,
                confidence=confidence,
                slot=slot,  # type: ignore[arg-type]
                species=actor,
                forme=mega_forme,
                value=canonical_item,
                source_frame=source_frame,
            ),
        )

    @staticmethod
    def _is_team_preview(lines: Sequence[OcrLine]) -> bool:
        keys = {_text_key(line.text) for line in lines}
        return any(key.startswith("select4pokemon") for key in keys) and any(
            "sendintobattle" in key for key in keys
        )

    def _preview_detections(self, lines: Sequence[OcrLine]) -> FrameDetections:
        """Lee nicknames, jugadores y orden de picks del selector 4/6.

        Champions coloca las seis filas propias siempre en las mismas bandas.
        Asociarlas con el Team conocido evita depender de que el HUD muestre la
        especie en lugar del nickname durante la batalla.
        """

        row_centers = (0.145, 0.26, 0.38, 0.495, 0.61, 0.73)
        roster = self.context.p1_team[:6]
        for index, center_y in enumerate(row_centers[: len(roster)]):
            label = min(
                (
                    line
                    for line in lines
                    if 0.09 <= line.center_x <= 0.25
                    and abs(line.center_y - center_y) <= 0.04
                    and not re.fullmatch(r"[0-4]", line.text.strip())
                    and not re.fullmatch(r"[0-4]\s*/\s*4", line.text.strip())
                ),
                key=lambda line: abs(line.center_y - center_y),
                default=None,
            )
            if label is None:
                continue
            self._bind_alias("p1", label.text, roster[index])

        count = next(
            (
                int(match.group(1))
                for line in lines
                if (match := re.fullmatch(r"([0-4])\s*/\s*4", line.text.strip()))
            ),
            self._preview_count,
        )
        if count < self._preview_count or count == 0:
            self._preview_ranks.clear()
        self._preview_count = count

        for line in lines:
            rank_match = re.fullmatch(r"([1-4])", line.text.strip())
            if not rank_match or not 0.08 <= line.center_x <= 0.17:
                continue
            row_index = min(
                range(len(row_centers)),
                key=lambda index: abs(line.center_y - row_centers[index]),
            )
            if row_index >= len(roster) or abs(line.center_y - row_centers[row_index]) > 0.05:
                continue
            self._preview_ranks[int(rank_match.group(1))] = roster[row_index]

        selected = tuple(
            self._preview_ranks[rank]
            for rank in sorted(self._preview_ranks)
            if rank <= count and rank in self._preview_ranks
        )

        p1_name = min(
            (
                line.text
                for line in lines
                if 0.15 <= line.center_x <= 0.4 and 0.035 <= line.center_y <= 0.12
            ),
            key=len,
            default=self._player_names["p1"],
        )
        p2_name = min(
            (
                line.text
                for line in lines
                if 0.65 <= line.center_x <= 0.92 and 0.035 <= line.center_y <= 0.12
            ),
            key=len,
            default=self._player_names["p2"],
        )
        if _text_key(p2_name) != _text_key(self._player_names["p2"]):
            self._aliases["p2"] = dict(self._configured_aliases["p2"])
        self._player_names.update({"p1": p1_name, "p2": p2_name})

        return FrameDetections(
            p1_name=p1_name,
            p2_name=p2_name,
            p1_team=tuple(roster),
            p1_selected=selected,
            team_preview=True,
        )

    def _hud_species(self, lines: Sequence[OcrLine], side: str) -> list[tuple[str, OcrLine]]:
        if side == "p1":
            candidates = [line for line in lines if line.center_y >= 0.55]
        else:
            candidates = [line for line in lines if line.center_y <= 0.45]

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

    def visual_alias_candidates(self, lines: Sequence[OcrLine]) -> tuple[tuple[str, str], ...]:
        """Encuentra nicknames desconocidos colocados junto a una barra de HP.

        El icono queda inmediatamente a la izquierda del texto. La geometría
        evita enviar mensajes, temporizadores o notificaciones al modelo visual.
        """

        text_keys = {_text_key(line.text) for line in lines}
        if text_keys.intersection({"close", "hidesummary", "helditem", "movesmore"}):
            return ()

        candidates: list[tuple[str, str, float]] = []
        seen: set[tuple[str, str]] = set()
        for health_line, _health in _health_readings(lines):
            if health_line.center_y <= 0.24:
                side = "p2"
                label_band = (0.025, 0.11)
            elif health_line.center_y >= 0.76:
                side = "p1"
                label_band = (0.80, 0.90)
            else:
                continue
            label = min(
                (
                    line
                    for line in lines
                    if label_band[0] <= line.center_y <= label_band[1]
                    and 0.025 <= health_line.center_y - line.center_y <= 0.15
                    and abs(health_line.center_x - line.center_x) <= 0.14
                    and line.confidence >= 0.7
                    and not _health_value(line.text)
                    and _text_key(line.text) not in _UI_TEXT
                    and self._resolve_species(line.text, side) is None
                    and self._announced_slot(side, line.text) is not None
                ),
                key=lambda line: (
                    abs(health_line.center_x - line.center_x)
                    + abs(health_line.center_y - line.center_y)
                ),
                default=None,
            )
            if label is None:
                continue
            key = (side, _text_key(label.text))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            candidates.append((side, label.text, label.center_x))
        return tuple(
            (side, nickname)
            for side, nickname, _x in sorted(candidates, key=lambda value: (value[0], value[2]))
        )

    def bind_visual_aliases(self, aliases: Sequence[HudAlias]) -> tuple[HudAlias, ...]:
        """Valida especies visuales contra el catálogo y aprende sus aliases."""

        applied: list[HudAlias] = []
        for alias in aliases:
            if alias.side not in {"p1", "p2"} or alias.confidence < 0.7:
                continue
            raw_species = re.sub(
                r"(?:\s*\((?:female|male)\)|[-\s]+(?:female|male))\s*$",
                "",
                alias.species.strip(),
                flags=re.IGNORECASE,
            )
            canonical = None
            if alias.gender in {"F", "M"}:
                canonical = self._species.resolve(
                    f"{raw_species}-{alias.gender}",
                    allow_fuzzy=False,
                )
            canonical = canonical or self._species.resolve(raw_species, allow_fuzzy=False)
            canonical = canonical or self._species.resolve(raw_species, threshold=0.92)
            if not canonical:
                continue
            nickname_key = _text_key(alias.nickname)
            if not nickname_key:
                continue
            # La geometría del HUD decide p2a/p2b. El orden textual del anuncio
            # rival puede venir invertido, así que aquí aprendemos sólo el alias
            # y dejamos que ``parse`` emita los switches con sus slots visuales.
            self._aliases[alias.side][nickname_key] = canonical
            applied.append(
                HudAlias(
                    side=alias.side,
                    nickname=alias.nickname,
                    species=canonical,
                    gender=alias.gender,
                    confidence=alias.confidence,
                )
            )
        return tuple(applied)

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

            for slot, species, species_line in slot_lines:
                nearby = min(
                    (
                        (line, health)
                        for line, health in readings
                        if -0.02 <= line.center_y - species_line.center_y <= 0.18
                        and abs(line.center_x - species_line.center_x) < 0.2
                    ),
                    key=lambda item: (
                        abs(item[0].center_x - species_line.center_x)
                        + abs(item[0].center_y - species_line.center_y)
                    ),
                    default=None,
                )
                health = nearby[1] if nearby else None
                legacy_hud_band = (
                    side == "p1" and species_line.center_y >= 0.82
                ) or (
                    side == "p2" and species_line.center_y <= 0.18
                )
                if health is None and not legacy_hud_band:
                    continue
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
            "withdrew",
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
            if not (0.42 <= line.center_y <= 0.92 and line.left <= 0.92):
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

    def _lead_announcement_events(
        self,
        *,
        side: str,
        value: str,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        actors = [part.strip() for part in re.split(r"\s+and\s+", value, flags=re.IGNORECASE)]
        if len(actors) != 2:
            return ()
        for index, actor in enumerate(actors):
            slot = f"{side}{'ab'[index]}"
            actor_key = _text_key(actor)
            if actor_key:
                self._announced_slots[side][actor_key] = slot
        # El orden del anuncio de p2 no coincide siempre con la geometría del
        # HUD local. Guardamos los nicknames para inferencias posteriores y
        # dejamos que las barras de HP asignen los slots visibles.
        return ()

    def _message_event(
        self,
        message: str,
        *,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        cleaned = message.strip()
        lowered = cleaned.casefold()

        terrain = None
        if "battlefield got weird" in lowered:
            terrain = "Psychic Terrain"
        elif "electric current ran across the battlefield" in lowered:
            terrain = "Electric Terrain"
        elif "grass grew to cover the battlefield" in lowered:
            terrain = "Grassy Terrain"
        elif "mist swirled around the battlefield" in lowered:
            terrain = "Misty Terrain"
        if terrain:
            pending_source = any(
                self._terrain_for_ability(ability) == terrain
                for _side, _species, ability in self._pending_abilities
            )
            if pending_source:
                self._pending_fieldstarts.add(terrain)
                return ()
            return (
                self._fieldstart_event(
                    terrain,
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                ),
            )

        withdrew = re.match(r"^(.*?)\s*withdrew\s+(.+?)[!.]?$", cleaned, re.IGNORECASE)
        if withdrew:
            side = self._side_for_player(withdrew.group(1))
            species = self._announced_species(withdrew.group(2), side) if side else None
            if side and species:
                self._mark_slot_open(self._slot_for_species(species, side))
                return ()

        sent_out = re.match(r"^(.*?)\s*sent out\s+(.+?)[!.]?$", cleaned, re.IGNORECASE)
        if sent_out:
            side = self._side_for_player(sent_out.group(1))
            if side:
                self._battle_open = True
            # A doubles lead announcement contains two species but no dependable
            # slot mapping unless both names are kept in their displayed order.
            if side and " and " in sent_out.group(2).casefold():
                return self._lead_announcement_events(
                    side=side,
                    value=sent_out.group(2),
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )
            if side:
                species = self._announced_species(sent_out.group(2), side)
                if species:
                    return self._announced_switch(
                        side=side,
                        species=species,
                        confidence=confidence,
                        timestamp_ms=timestamp_ms,
                        source_frame=source_frame,
                    )
                slots = self._open_slots[side]
                if slots:
                    self._announced_slots[side][_text_key(sent_out.group(2))] = slots[0]
                return ()

        go = re.match(r"^Go!\s*(.+?)[!.]?$", cleaned, re.IGNORECASE)
        if go:
            self._battle_open = True
            if " and " in go.group(1).casefold():
                return self._lead_announcement_events(
                    side="p1",
                    value=go.group(1),
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )
            species = self._announced_species(go.group(1), "p1")
            if species:
                return self._announced_switch(
                    side="p1",
                    species=species,
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )

        mega_reaction = re.match(
            r"^(The opposing )?(.+?)[\'’]s (.+?) (?:is|i) reacting to .+?[\'’]s (?:Omni|Omi|Mega) Ring[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if mega_reaction:
            opposing = bool(mega_reaction.group(1))
            side = "p2" if opposing else "p1"
            actor_raw = mega_reaction.group(2)
            actor = self._resolve_species(actor_raw, side)
            mega = self._mega_stones.get(_text_key(mega_reaction.group(3)))
            learned_switch: tuple[BattleEvent, ...] = ()
            if actor is None and mega is not None:
                actor = mega[1]
                slot, changed = self._bind_alias(side, actor_raw, actor)
                if slot and changed:
                    learned_switch = (
                        BattleEvent(
                            kind="switch",
                            timestamp_ms=timestamp_ms,
                            confidence=confidence,
                            slot=slot,  # type: ignore[arg-type]
                            species=actor,
                            source_frame=source_frame,
                        ),
                    )
            if actor:
                return learned_switch + self._mega_event(
                    actor=actor,
                    side=side,
                    item=mega_reaction.group(3),
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )

        mega_evolved = re.match(
            r"^(The opposing )?(.+?) has Mega Evolved into (Mega .+?)[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if mega_evolved:
            opposing = bool(mega_evolved.group(1))
            side = "p2" if opposing else "p1"
            actor_raw = mega_evolved.group(2)
            actor = self._resolve_species(actor_raw, side)
            announced_forme = mega_evolved.group(3)
            learned_switch: tuple[BattleEvent, ...] = ()
            if actor is None:
                normalized_forme = announced_forme.strip()
                if not normalized_forme.casefold().startswith("mega "):
                    normalized_forme = f"Mega {normalized_forme}"
                words = normalized_forme.split()
                suffix = words[-1] if words and words[-1] in {"X", "Y", "Z"} else None
                base_words = words[1:-1] if suffix else words[1:]
                candidate = f"{' '.join(base_words)}-Mega{f'-{suffix}' if suffix else ''}"
                mega = self._mega_formes.get(_text_key(candidate))
                if mega is not None:
                    actor = mega[1]
                    slot, changed = self._bind_alias(side, actor_raw, actor)
                    if slot and changed:
                        learned_switch = (
                            BattleEvent(
                                kind="switch",
                                timestamp_ms=timestamp_ms,
                                confidence=confidence,
                                slot=slot,  # type: ignore[arg-type]
                                species=actor,
                                source_frame=source_frame,
                            ),
                        )
            if actor:
                return learned_switch + self._mega_event(
                    actor=actor,
                    side=side,
                    item="",
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                    announced_forme=announced_forme,
                )

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
                slot = self._slot_for_species(species, side)
                self._mark_slot_open(slot)
                return (
                    BattleEvent(
                        kind="faint",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=slot,  # type: ignore[arg-type]
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
        if self._is_team_preview(lines):
            return self._preview_detections(lines)

        events: list[BattleEvent] = []
        observations = self._hud_observations(lines)
        changed_slots: set[str] = set()

        for slot, (species, health) in observations.items():
            previous_species = self._active.get(slot)
            if previous_species != species:
                self._active[slot] = species
                if slot in self._open_slots[slot[:2]]:
                    self._open_slots[slot[:2]].remove(slot)
                self._health.pop(slot, None)
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

        ability_events = self._ability_events(
            lines,
            timestamp_ms=timestamp_ms,
            source_frame=source_frame,
        )
        events.extend(ability_events)
        if self._visible_abilities:
            self._battle_open = True

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

        result_lines = [
            line
            for line in lines
            if 0.15 <= line.center_y <= 0.85 and line.right - line.left >= 0.08
        ]
        result_text = " ".join(line.text.casefold() for line in result_lines)
        if "battle has ended" in result_text or "battle is over" in result_text:
            self._pending_end = True

        winner = None
        if any(
            phrase in result_text
            for phrase in ("you won the battle", "you won against", "you defeated", "you beat ")
        ):
            winner = "p1"
        elif "you lost" in result_text or "you were defeated" in result_text:
            winner = "p2"
        else:
            win_words = {"win", "won", "victory"}
            loss_words = {"lose", "lost", "defeat", "defeated"}
            wins = [line for line in result_lines if _text_key(line.text) in win_words]
            losses = [line for line in result_lines if _text_key(line.text) in loss_words]
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
        engine_factory: Callable[[], OcrEngine] | None = None,
        alias_resolver: HudAliasResolver | None = None,
    ) -> None:
        if engine is not None:
            self.engine = engine
        elif engine_factory is not None:
            self.engine = engine_factory()
        else:
            self.engine = RapidOcrEngine(min_confidence=min_confidence)
        self._engine_factory = engine_factory
        if engine is None and engine_factory is None:
            self._engine_factory = lambda: RapidOcrEngine(min_confidence=min_confidence)
        self.supports_parallel = callable(self._engine_factory)
        self._worker_engines = threading.local()
        self._engine_claim_lock = threading.Lock()
        self._primary_engine_claimed = False
        self.parser = ChampionsTextParser(context=context)
        self.trace_path = trace_path
        self._alias_resolver = alias_resolver
        self._alias_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="champions-hud-vision")
            if alias_resolver is not None
            else None
        )
        self._alias_future: Future[tuple[HudAlias, ...]] | None = None
        self._alias_future_generation = 0
        self._alias_generation = 0
        self._visual_candidate_counts: dict[tuple[tuple[str, str], ...], int] = {}
        self._visual_attempted: set[tuple[tuple[str, str], ...]] = set()
        self._visual_warnings: deque[str] = deque()
        self._alias_source: PreparedOcrFrame | None = None

    def _poll_visual_aliases(self) -> tuple[HudAlias, ...]:
        future = self._alias_future
        if future is None or not future.done():
            return ()
        generation = self._alias_future_generation
        self._alias_future = None
        try:
            aliases = future.result()
        except Exception as error:  # el refuerzo visual nunca debe abortar el OCR
            self._visual_warnings.append(f"Lectura visual de nicknames omitida: {error}")
            return ()
        if generation != self._alias_generation:
            return ()
        return self.parser.bind_visual_aliases(aliases)

    def _schedule_visual_aliases(
        self,
        prepared: PreparedOcrFrame,
    ) -> tuple[tuple[str, str], ...]:
        if self._alias_resolver is None or self._alias_executor is None or self._alias_future is not None:
            return ()
        frame = prepared.frame
        lines = prepared.lines
        candidates = self.parser.visual_alias_candidates(lines)
        if not candidates:
            return ()
        signature = tuple(candidates)
        count = self._visual_candidate_counts.get(signature, 0) + 1
        self._visual_candidate_counts[signature] = count
        if count < 2 or signature in self._visual_attempted:
            return signature
        self._visual_attempted.add(signature)
        vision_frame = frame
        prepare_hud_frame = getattr(self.engine, "prepare_hud_frame", None)
        if callable(prepare_hud_frame):
            try:
                vision_frame = prepare_hud_frame(
                    frame,
                    lines,
                    candidates,
                    rotation_degrees=prepared.rotation_degrees,
                )
            except Exception as error:  # el frame completo sigue siendo un fallback válido
                self._visual_warnings.append(f"No pudimos ampliar el HUD; se usará el frame completo: {error}")
        self._alias_source = prepared
        self._alias_future_generation = self._alias_generation
        self._alias_future = self._alias_executor.submit(
            self._alias_resolver.resolve,
            vision_frame,
            candidates,
        )
        return signature

    def _write_trace(
        self,
        prepared: PreparedOcrFrame,
        detections: FrameDetections,
        *,
        visual_aliases: Sequence[HudAlias] = (),
        visual_candidates: Sequence[tuple[str, str]] = (),
        phase: str = "frame",
    ) -> None:
        if not self.trace_path:
            return
        frame = prepared.frame
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "frame": frame.index + 1,
            "timestamp_ms": frame.timestamp_ms,
            "elapsed_ms": prepared.elapsed_ms,
            "rotation_degrees": prepared.rotation_degrees,
            "phase": phase,
            "ocr": [asdict(line) for line in prepared.lines],
            "visual_alias_candidates": [
                {"side": side, "nickname": nickname}
                for side, nickname in visual_candidates
            ],
            "visual_aliases": [asdict(alias) for alias in visual_aliases],
            "visual_alias_pending": self._alias_future is not None,
            "detections": {
                "team_preview": detections.team_preview,
                "battle_started": detections.battle_started,
                "battle_complete": detections.battle_complete,
                "winner": detections.winner,
                "events": [asdict(event) for event in detections.events],
            },
        }
        with self.trace_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_frame(engine: OcrEngine, frame: FramePacket) -> PreparedOcrFrame:
        started = time.monotonic()
        lines = engine.read(frame.image)
        return PreparedOcrFrame(
            frame=frame,
            lines=lines,
            elapsed_ms=round((time.monotonic() - started) * 1_000),
            rotation_degrees=int(getattr(engine, "rotation_quarter_turns", 0)) * 90,
        )

    def prepare(self, frame: FramePacket) -> PreparedOcrFrame:
        """Ejecuta sólo OCR; es seguro llamarlo desde workers independientes."""

        engine = getattr(self._worker_engines, "engine", None)
        if engine is None:
            with self._engine_claim_lock:
                if not self._primary_engine_claimed:
                    engine = self.engine
                    self._primary_engine_claimed = True
                else:
                    engine = self._engine_factory() if callable(self._engine_factory) else self.engine
            self._worker_engines.engine = engine
        return self._read_frame(engine, frame)

    def parse_prepared(self, prepared: PreparedOcrFrame) -> FrameDetections:
        frame = prepared.frame
        lines = prepared.lines
        visual_aliases = self._poll_visual_aliases()
        visual_candidates = self._schedule_visual_aliases(prepared)
        detections = self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
        self._write_trace(
            prepared,
            detections,
            visual_aliases=visual_aliases,
            visual_candidates=visual_candidates,
        )
        if visual_aliases:
            self._alias_source = None
        return detections

    def detect(self, frame: FramePacket) -> FrameDetections:
        return self.parse_prepared(self._read_frame(self.engine, frame))

    def reset_battle_state(self) -> None:
        self._alias_generation += 1
        self._visual_candidate_counts.clear()
        self._visual_attempted.clear()
        self._alias_source = None
        self.parser.reset_battle_state()

    def flush_pending(self) -> FrameDetections:
        """Espera el refuerzo visual antes de cerrar y perder sus aliases."""

        future = self._alias_future
        source = self._alias_source
        if future is None or source is None:
            return FrameDetections()
        generation = self._alias_future_generation
        self._alias_future = None
        self._alias_source = None
        try:
            aliases = future.result()
        except Exception as error:  # el OCR determinista conserva el replay parcial
            self._visual_warnings.append(f"Lectura visual de nicknames omitida: {error}")
            return FrameDetections()
        if generation != self._alias_generation:
            return FrameDetections()
        applied = self.parser.bind_visual_aliases(aliases)
        if not applied:
            self._visual_warnings.append(
                "La lectura visual terminó, pero no produjo asociaciones válidas para el HUD."
            )
            return FrameDetections()
        detections = self.parser.parse(
            source.lines,
            timestamp_ms=source.frame.timestamp_ms,
            source_frame=source.frame.index,
        )
        self._write_trace(
            source,
            detections,
            visual_aliases=applied,
            visual_candidates=tuple((alias.side, alias.nickname) for alias in applied),
            phase="visual_alias_flush",
        )
        return detections

    def pop_warnings(self) -> tuple[str, ...]:
        warnings = tuple(self._visual_warnings)
        self._visual_warnings.clear()
        return warnings

    def close(self) -> None:
        if self._alias_executor is not None:
            self._alias_executor.shutdown(wait=False, cancel_futures=True)
            self._alias_executor = None


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
            visual_values = payload.get("visual_aliases")
            visual_aliases = tuple(
                HudAlias(
                    side=str(item.get("side") or ""),
                    nickname=str(item.get("nickname") or ""),
                    species=str(item.get("species") or ""),
                    gender=item.get("gender") if item.get("gender") in {"M", "F"} else None,
                    confidence=float(item.get("confidence") or 0),
                )
                for item in visual_values
                if isinstance(item, dict)
            ) if isinstance(visual_values, list) else ()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise DetectionError(f"La traza OCR contiene un frame inválido: {error}") from error
        self.parser.bind_visual_aliases(visual_aliases)
        return self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )

    def reset_battle_state(self) -> None:
        self.parser.reset_battle_state()
