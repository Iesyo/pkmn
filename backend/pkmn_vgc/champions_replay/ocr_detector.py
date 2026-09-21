from __future__ import annotations

import gzip
import json
import re
import threading
import time
import unicodedata
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .detector import DetectionError, DetectorContext, HudAlias, HudAliasResolver
from .models import ACTOR_IDENTITY_PREFIX, BattleEvent, FrameDetections, is_actor_identity
from .sources import FramePacket
from .team_preview import TeamPreviewResolver, looks_like_a_nickname


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


def _available_providers() -> tuple[str, ...]:
    try:
        import onnxruntime  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - depende del runtime instalado
        return ()
    return tuple(onnxruntime.get_available_providers())


def _accelerator_params(providers: Sequence[str]) -> dict[str, object]:
    """Corre los modelos en la GPU cuando el runtime trae DirectML.

    Son los mismos modelos y el mismo resultado; sólo cambia dónde se ejecutan.
    Medido en la ROG sobre doce frames de dos grabaciones, el frame baja de 554
    a 335 ms y el texto sale idéntico, motes japoneses incluidos.
    """

    if "DmlExecutionProvider" not in providers:
        return {}
    return {"EngineConfig.onnxruntime.use_dml": True}


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
        base: dict[str, object] = {"Global.log_level": "ERROR"}
        accelerated = {**base, **_accelerator_params(_available_providers())}
        try:
            self._engine = RapidOCR(params=accelerated)
        except Exception:  # pragma: no cover - depende del runtime instalado
            # Que el proveedor figure no garantiza que arranque: sin GPU
            # utilizable se sigue en CPU en vez de quedarse sin OCR.
            if accelerated == base:
                raise
            self._engine = RapidOCR(params=base)
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
        if abs(scale - 1.0) > 0.05:
            cropped = self._cv2.resize(
                cropped,
                None,
                fx=scale,
                fy=scale,
                interpolation=self._cv2.INTER_CUBIC,
            )
        # El icono ampliado sirve para asociar nickname/slot, mientras que el
        # frame completo conserva los modelos 3D que facilitan reconocer la
        # especie. Una sola composición funciona con todas las versiones de la
        # API local de Ollama y evita una segunda inferencia.
        preview_scale = min(1.0, 1600 / max(1, width))
        preview = oriented
        if preview_scale < 0.99:
            preview = self._cv2.resize(
                oriented,
                None,
                fx=preview_scale,
                fy=preview_scale,
                interpolation=self._cv2.INTER_AREA,
            )
        preview = preview.copy()
        cropped = cropped.copy()
        self._cv2.putText(
            preview,
            "FULL BATTLE FRAME",
            (24, 48),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            self._cv2.LINE_AA,
        )
        self._cv2.putText(
            cropped,
            "HUD ZOOM - ICON IS LEFT OF NICKNAME",
            (24, 48),
            self._cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            self._cv2.LINE_AA,
        )
        canvas_width = max(preview.shape[1], cropped.shape[1])
        separator = 12
        canvas_height = preview.shape[0] + separator + cropped.shape[0]
        composed = self._np.zeros((canvas_height, canvas_width, 3), dtype=self._np.uint8)
        preview_left = (canvas_width - preview.shape[1]) // 2
        crop_left = (canvas_width - cropped.shape[1]) // 2
        composed[: preview.shape[0], preview_left : preview_left + preview.shape[1]] = preview
        composed[preview.shape[0] + separator :, crop_left : crop_left + cropped.shape[1]] = cropped

        ok, jpeg = self._cv2.imencode(".jpg", composed, [self._cv2.IMWRITE_JPEG_QUALITY, 92])
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
    species_types: tuple[tuple[str, tuple[str, ...]], ...] = ()
    moves: tuple[str, ...] = ()
    abilities: tuple[str, ...] = ()
    species_moves: tuple[tuple[str, tuple[str, ...]], ...] = ()
    species_abilities: tuple[tuple[str, tuple[str, ...]], ...] = ()
    species_teammates: tuple[tuple[str, str, int], ...] = ()
    mega_stones: tuple[tuple[str, str, str], ...] = ()


def _dex_candidates() -> tuple[Path, ...]:
    module = Path(__file__).resolve()
    return (
        module.parents[3] / "public" / "data" / "showdown-dex.json.gz",
        module.parent / "data" / "showdown-dex.json.gz",
    )


def _tournament_candidates() -> tuple[Path, ...]:
    module = Path(__file__).resolve()
    return tuple(sorted((module.parents[3] / "data").glob("tournament-teams-*.json")))


def _historical_teammates(species: Sequence[str]) -> tuple[tuple[str, str, int], ...]:
    """Cuenta parejas vistas en los equipos reales incluidos en la app."""

    canonical = {_text_key(value): value for value in species}
    pair_counts: Counter[tuple[str, str]] = Counter()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            names = value.get("pokemonNames")
            if isinstance(names, list):
                team = sorted(
                    {
                        resolved
                        for name in names
                        if isinstance(name, str)
                        and (resolved := canonical.get(_text_key(name))) is not None
                    }
                )
                for index, first in enumerate(team):
                    for second in team[index + 1 :]:
                        pair_counts[(first, second)] += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in _tournament_candidates():
        try:
            visit(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(
        (first, second, count)
        for (first, second), count in sorted(pair_counts.items())
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
            species_types = tuple(
                (
                    entry["name"],
                    tuple(
                        pokemon_type
                        for pokemon_type in entry.get("types", ())
                        if isinstance(pokemon_type, str)
                    ),
                )
                for species_id in champion_ids
                if isinstance((entry := species_values.get(species_id)), dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("types"), list)
                and entry["types"]
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
            species_moves = tuple(
                (
                    entry["name"],
                    tuple(
                        move
                        for move in entry.get("championsMoves", ())
                        if isinstance(move, str)
                    ),
                )
                for species_id in champion_ids
                if isinstance((entry := species_values.get(species_id)), dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("championsMoves"), list)
                and entry["championsMoves"]
            )
            species_abilities = tuple(
                (
                    entry["name"],
                    tuple(
                        ability
                        for ability in entry.get("abilities", ())
                        if isinstance(ability, str)
                    ),
                )
                for species_id in champion_ids
                if isinstance((entry := species_values.get(species_id)), dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("abilities"), list)
                and entry["abilities"]
                and isinstance(entry.get("championsMoves"), list)
                and entry["championsMoves"]
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
                species_types=species_types,
                moves=moves,
                abilities=abilities,
                species_moves=species_moves,
                species_abilities=species_abilities,
                species_teammates=_historical_teammates(species),
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
        current = int(percentage.group(1))
        # Una barra nunca pasa del 100%. Recortar la lectura ahí convertía un
        # dígito pegado por el OCR ("77" leído "779") en una curación a tope.
        if current > 100:
            return None
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


def _paired_health(
    species_lines: Sequence[OcrLine],
    readings: Sequence[tuple[OcrLine, str]],
) -> dict[int, str]:
    """Da cada barra de vida a un solo nombre del HUD.

    La ventana de búsqueda alcanza a los dos nombres del lado. Cuando el OCR
    sólo leía una de las dos barras, el mismo valor acababa escrito en los dos
    slots y la vida del compañero salía como daño o curación del otro.
    """

    candidates = sorted(
        (
            (
                abs(reading.center_x - line.center_x)
                + abs(reading.center_y - line.center_y),
                index,
                position,
                health,
            )
            for index, line in enumerate(species_lines)
            for position, (reading, health) in enumerate(readings)
            if -0.02 <= reading.center_y - line.center_y <= 0.18
            and abs(reading.center_x - line.center_x) < 0.2
        )
    )
    paired: dict[int, str] = {}
    taken: set[int] = set()
    for _distance, index, position, health in candidates:
        if index in paired or position in taken:
            continue
        paired[index] = health
        taken.add(position)
    return paired


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
        self._teams = {
            "p1": tuple(self.context.p1_team),
            "p2": tuple(self.context.p2_team),
        }
        self._side_species = {
            "p1": _NameMatcher(self._teams["p1"] or self.catalog.species),
            "p2": _NameMatcher(self._teams["p2"] or self.catalog.species),
        }
        self._known_teams = {
            "p1": bool(self._teams["p1"]),
            "p2": bool(self._teams["p2"]),
        }
        self._aliases = {
            "p1": self._team_form_aliases(self.context.p1_team),
            "p2": self._team_form_aliases(self.context.p2_team),
        }
        self._aliases["p1"].update(self._canonical_aliases(self.context.p1_aliases))
        self._aliases["p2"].update(self._canonical_aliases(self.context.p2_aliases))
        self._message_aliases: dict[str, dict[str, str]] = {
            "p1": {
                alias.casefold(): species
                for alias, species in self.context.p1_aliases
                if alias.strip() and _text_key(alias) != _text_key(species)
            },
            "p2": {
                alias.casefold(): species
                for alias, species in self.context.p2_aliases
                if alias.strip() and _text_key(alias) != _text_key(species)
            },
        }
        self._configured_aliases = {
            side: dict(values)
            for side, values in self._aliases.items()
        }
        self._configured_bound_alias_keys = {
            "p1": {_text_key(alias) for alias, _species in self.context.p1_aliases},
            "p2": {_text_key(alias) for alias, _species in self.context.p2_aliases},
        }
        self._configured_message_aliases = {
            side: dict(values)
            for side, values in self._message_aliases.items()
        }
        self._bound_alias_keys = {
            side: set(values)
            for side, values in self._configured_bound_alias_keys.items()
        }
        self._moves = _NameMatcher(self.catalog.moves)
        self._abilities = _NameMatcher(self.catalog.abilities)
        self._species_by_move: dict[str, set[str]] = {}
        for species, moves in self.catalog.species_moves:
            for move in moves:
                self._species_by_move.setdefault(_text_key(move), set()).add(species)
        self._species_by_ability: dict[str, set[str]] = {}
        for species, abilities in self.catalog.species_abilities:
            for ability in abilities:
                self._species_by_ability.setdefault(_text_key(ability), set()).add(species)
        self._teammate_counts = {
            tuple(sorted((first, second))): count
            for first, second, count in self.catalog.species_teammates
        }
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

        self._teams = {
            "p1": tuple(self.context.p1_team),
            "p2": tuple(self.context.p2_team),
        }
        self._side_species = {
            side: _NameMatcher(team or self.catalog.species)
            for side, team in self._teams.items()
        }
        self._known_teams = {
            side: bool(team)
            for side, team in self._teams.items()
        }
        # Los aliases aprendidos pertenecen a una sola batalla. Reutilizarlos
        # en la siguiente mezcla identidades cuando dos rivales usan el mismo
        # mote o cuando cambia la pareja de leads.
        self._aliases = {
            side: dict(values)
            for side, values in self._configured_aliases.items()
        }
        self._bound_alias_keys = {
            side: set(values)
            for side, values in self._configured_bound_alias_keys.items()
        }
        self._message_aliases = {
            side: dict(values)
            for side, values in self._configured_message_aliases.items()
        }
        self._active: dict[str, str] = {}
        self._health: dict[str, str] = {}
        self._player_names = {
            "p1": self.context.p1_name,
            "p2": self.context.p2_name,
        }
        self._announced_slots: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
        self._announced_leads: dict[str, tuple[str, ...]] = {"p1": (), "p2": ()}
        self._hud_alias_slots: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
        self._preview_ranks: dict[int, str] = {}
        self._preview_count = 0
        self._open_slots: dict[str, list[str]] = {"p1": [], "p2": []}
        self._pending_switch_timestamps: dict[str, int] = {}
        self._visible_messages: set[str] = set()
        self._visible_abilities: set[tuple[str, str, str]] = set()
        self._pending_abilities: dict[tuple[str, str, str], tuple[float, int]] = {}
        self._pending_fieldstarts: set[str] = set()
        self._recent_field_sources: dict[str, tuple[str, str, str, int]] = {}
        self._turn = 0
        self._command_visible = False
        self._turn_has_activity = False
        self._battle_open = False
        self._pending_end = False
        self._mega_seen: set[str] = set()
        self._alias_evidence: dict[tuple[str, str], set[str]] = {}
        self._alias_evidence_labels: dict[tuple[str, str], set[str]] = {}
        self._pending_alias_moves: dict[
            tuple[str, str],
            list[tuple[str, int, float, int]],
        ] = {}
        self._identity_counter = 0
        self._identity_by_alias: dict[str, dict[str, str]] = {"p1": {}, "p2": {}}
        self._identity_species: dict[str, str] = {}
        self._identity_evidence: dict[str, int] = {}
        self._identity_slots: dict[str, str] = {}

    def _infer_alias(
        self,
        side: str,
        nickname: str,
        candidates: set[str],
        evidence: str,
    ) -> tuple[str | None, str | None, bool]:
        """Reduce candidatos con evidencia de batalla y aprende al quedar uno.

        No adivina entre formas que comparten exactamente los mismos datos. En
        esos casos espera otra pista (Mega, habilidad, alias configurado, etc.).
        """

        nickname_key = _text_key(nickname)
        if not nickname_key or not candidates:
            return None, None, False
        known_team = set(self._teams[side])
        if known_team:
            candidates &= known_team
        assigned = {
            species
            for alias_key, species in self._aliases[side].items()
            if alias_key != nickname_key and alias_key in self._bound_alias_keys[side]
        }
        candidates -= assigned
        evidence_key = (side, nickname_key)
        self._alias_evidence_labels.setdefault(evidence_key, set()).add(evidence)
        previous = self._alias_evidence.get(evidence_key)
        narrowed = candidates if previous is None else previous & candidates
        if not narrowed:
            return None, None, False
        self._alias_evidence[evidence_key] = narrowed
        if (
            len(narrowed) > 1
            and not self._known_teams[side]
            and len(self._alias_evidence_labels[evidence_key]) >= 2
        ):
            observed_teammates = {
                species
                for alias_key, species in self._aliases[side].items()
                if alias_key != nickname_key and alias_key in self._bound_alias_keys[side]
            }
            scores = {
                species: sum(
                    self._teammate_counts.get(tuple(sorted((species, teammate))), 0)
                    for teammate in observed_teammates
                    if teammate != species
                )
                for species in narrowed
            }
            ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
            if ranked:
                top_species, top_score = ranked[0]
                runner_up = ranked[1][1] if len(ranked) > 1 else 0
                if top_score >= 3 and top_score >= max(1, runner_up * 2):
                    narrowed = {top_species}
                    self._alias_evidence[evidence_key] = narrowed
        if len(narrowed) != 1:
            return None, None, False
        species = next(iter(narrowed))
        slot, changed = self._bind_alias(side, nickname, species, evidence="inferred")
        return species, slot, changed

    def _infer_alias_from_move(
        self,
        side: str,
        nickname: str,
        move: str,
    ) -> tuple[str | None, str | None, bool]:
        return self._infer_alias(
            side,
            nickname,
            set(self._species_by_move.get(_text_key(move), ())),
            f"move:{_text_key(move)}",
        )

    def _infer_alias_from_ability(
        self,
        side: str,
        nickname: str,
        ability: str,
    ) -> tuple[str | None, str | None, bool]:
        return self._infer_alias(
            side,
            nickname,
            set(self._species_by_ability.get(_text_key(ability), ())),
            f"ability:{_text_key(ability)}",
        )

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

    def bind_preview_labels(self, labels: Sequence[tuple[str, str]], *, side: str) -> int:
        """Ata cada mote con la especie de su misma tarjeta del Team Preview.

        Es la única fuente que empareja las dos cosas sin deducir nada. Sin
        esto, cuando los dos entrenadores llevan la misma especie el texto de
        batalla se atribuye al lado equivocado.
        """

        bound = 0
        for nickname, species in labels:
            if not nickname or not species:
                continue
            canonical = self._species.resolve(species, threshold=0.9) or species
            self._bind_alias(side, nickname, canonical, evidence="preview")
            bound += 1
        return bound

    def bind_preview_team(
        self,
        team: Sequence[str],
        *,
        side: str = "p2",
    ) -> tuple[str, ...]:
        """Fija el roster visual antes de resolver motes por evidencia.

        Si una inferencia temprana apuntó fuera del Team Preview, se descarta.
        Los eventos conservan su identidad estable y reciben la especie correcta
        únicamente al serializar el replay.
        """

        if side not in {"p1", "p2"}:
            return ()
        canonical: list[str] = []
        seen: set[str] = set()
        for raw_species in team:
            species = self._species.resolve(str(raw_species), threshold=0.9)
            key = _text_key(species or "")
            if not species or not key or key in seen:
                continue
            seen.add(key)
            canonical.append(species)
        if not canonical:
            return ()

        roster = tuple(canonical[:6])
        allowed = {_text_key(species) for species in roster}
        self._teams[side] = roster
        self._side_species[side] = _NameMatcher(roster)
        self._known_teams[side] = True

        configured_keys = self._configured_bound_alias_keys[side]
        for alias_key, species in tuple(self._aliases[side].items()):
            if alias_key in configured_keys and _text_key(species) in allowed:
                continue
            if _text_key(species) not in allowed:
                self._aliases[side].pop(alias_key, None)
                self._bound_alias_keys[side].discard(alias_key)
        self._aliases[side].update(self._team_form_aliases(roster))

        configured_messages = self._configured_message_aliases[side]
        for alias, species in tuple(self._message_aliases[side].items()):
            if alias in configured_messages and _text_key(species) in allowed:
                continue
            if _text_key(species) not in allowed:
                self._message_aliases[side].pop(alias, None)

        side_identities = set(self._identity_by_alias[side].values())
        for identity in side_identities:
            species = self._identity_species.get(identity)
            if species and _text_key(species) not in allowed:
                self._identity_species.pop(identity, None)
                self._identity_evidence.pop(identity, None)

        for evidence_key, candidates in tuple(self._alias_evidence.items()):
            evidence_side, alias_key = evidence_key
            if evidence_side != side:
                continue
            narrowed = {species for species in candidates if _text_key(species) in allowed}
            if narrowed:
                self._alias_evidence[evidence_key] = narrowed
            else:
                self._alias_evidence.pop(evidence_key, None)
            if len(narrowed) == 1:
                species = next(iter(narrowed))
                self._aliases[side][alias_key] = species
                self._bound_alias_keys[side].add(alias_key)
                identity = self._identity_by_alias[side].get(alias_key)
                if identity:
                    self._set_identity_species(identity, species, evidence="preview")
        return roster

    def _resolve_species(self, value: str, side: str | None = None) -> str | None:
        if side in self._side_species:
            value_key = _text_key(value)
            # Tres caracteres es el suelo que ya usan el resto de resolutores.
            # Sin él, la búsqueda exacta de alias dejaba que un fragmento de dos
            # —el símbolo de género del panel, leído ’07’ o ’37’— nombrara a un
            # Pokémon y ocupara un slot del HUD.
            if len(value_key) < 3:
                return None
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

    def _new_identity(self, side: str, value: str, slot: str | None = None) -> str:
        self._identity_counter += 1
        identity = f"{ACTOR_IDENTITY_PREFIX}{side}_{self._identity_counter:04d}__"
        key = _text_key(value)
        if key:
            self._identity_by_alias[side][key] = identity
        if slot:
            self._identity_slots[slot] = identity
        return identity

    def _remember_identity_alias(self, side: str, value: str, identity: str) -> None:
        key = _text_key(value)
        if key:
            self._identity_by_alias[side][key] = identity

    def _identity_for_value(self, side: str, value: str) -> str | None:
        """Resuelve un mote a la identidad estable observada, no a su especie."""

        value_key = _text_key(value)
        identity = self._identity_by_alias[side].get(value_key)
        if identity:
            return identity
        if len(value_key) >= 3:
            # El desempate va por identidad, no por lectura. El OCR escribe el
            # mismo mote de muchas formas; comparando lecturas, dos variantes
            # del mismo Pokémon empataban y tumbaban su propia identidad.
            best: dict[str, float] = {}
            for alias_key, candidate in self._identity_by_alias[side].items():
                if abs(len(alias_key) - len(value_key)) > max(3, len(value_key) // 2):
                    continue
                score = SequenceMatcher(None, value_key, alias_key).ratio()
                if score > best.get(candidate, 0.0):
                    best[candidate] = score
            ranked = sorted(
                ((score, candidate) for candidate, score in best.items()),
                reverse=True,
            )
            if ranked:
                score, identity = ranked[0]
                runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
                threshold = 0.55 if len(value_key) <= 3 else 0.64
                if score >= threshold and score - runner_up >= 0.12:
                    self._remember_identity_alias(side, value, identity)
                    return identity
        slot = self._announced_slot(side, value)
        active = self._active.get(slot or "")
        if active and is_actor_identity(active):
            self._remember_identity_alias(side, value, active)
            return active
        return None

    def _ensure_identity(self, side: str, value: str, slot: str) -> str:
        identity = self._identity_for_value(side, value)
        active = self._active.get(slot)
        if identity is None and active and is_actor_identity(active):
            identity = active
        if identity is None:
            identity = self._new_identity(side, value, slot)
        self._remember_identity_alias(side, value, identity)
        self._identity_slots[slot] = identity
        return identity

    def _canonical_actor(self, actor: str) -> str:
        return self._identity_species.get(actor, actor)

    def _actor_for_value(self, side: str, value: str) -> str | None:
        identity = self._identity_for_value(side, value)
        if identity:
            return identity
        species = self._resolve_species(value, side)
        if not species:
            return None
        species_key = _text_key(species)
        for slot, active in self._active.items():
            if slot.startswith(side) and _text_key(self._canonical_actor(active)) == species_key:
                return active
        return species

    def _side_for_species(self, species: str, *, opposing: bool = False) -> str:
        if opposing:
            return "p2"
        # El mensaje sin "The opposing" es del jugador. Si p1 tiene esa especie,
        # se respeta: cuando los dos entrenadores llevan la misma, buscar por
        # slot activo devolvía el primero insertado y cruzaba los ataques.
        own = _text_key(self._canonical_actor(species))
        for slot, active_species in self._active.items():
            if slot.startswith("p1") and _text_key(self._canonical_actor(active_species)) == own:
                return "p1"
        if _text_key(species) in {_text_key(value) for value in self._teams["p1"]}:
            return "p1"
        for slot, active_species in self._active.items():
            if _text_key(self._canonical_actor(active_species)) == _text_key(self._canonical_actor(species)):
                return slot[:2]
        p1 = {_text_key(value) for value in self._teams["p1"]}
        p2 = {_text_key(value) for value in self._teams["p2"]}
        key = _text_key(species)
        if key in p2 and key not in p1:
            return "p2"
        return "p1"

    def _slot_for_species(self, species: str, side: str) -> str:
        for slot, active_species in self._active.items():
            if slot.startswith(side) and (
                active_species == species
                or _text_key(self._canonical_actor(active_species))
                == _text_key(self._canonical_actor(species))
            ):
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
        slots = {
            **self._announced_slots[side],
            **self._hud_alias_slots[side],
        }
        exact = slots.get(value_key)
        if exact:
            return exact
        if len(value_key) < 3:
            return None
        ranked = sorted(
            (
                (SequenceMatcher(None, value_key, alias_key).ratio(), candidate_slot)
                for alias_key, candidate_slot in slots.items()
            ),
            reverse=True,
        )
        if not ranked:
            return None
        score, slot = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        threshold = 0.55 if len(value_key) <= 3 else 0.64
        return slot if score >= threshold and score - runner_up >= 0.15 else None

    # Qué tan firme es la prueba de que una identidad es cierta especie. El
    # juego escribiéndola en pantalla pesa más que el Team Preview leído por
    # imagen, y cualquiera de los dos pesa más que deducirla de un movimiento.
    _EVIDENCE_RANK = {"inferred": 1, "preview": 2, "explicit": 3}

    def _set_identity_species(
        self,
        identity: str,
        species: str,
        *,
        evidence: str = "explicit",
    ) -> bool:
        """Fija la especie de una identidad sin dejar que la pise algo más flojo."""

        rank = self._EVIDENCE_RANK[evidence]
        if self._identity_evidence.get(identity, 0) > rank:
            return False
        self._identity_species[identity] = species
        self._identity_evidence[identity] = rank
        return True

    def _bind_alias(
        self,
        side: str,
        value: str,
        species: str,
        *,
        evidence: str = "explicit",
    ) -> tuple[str | None, bool]:
        """Aprende un nickname cuando el juego revela después su especie."""

        identity = self._identity_for_value(side, value)
        # El OCR lee el mismo mote de varias formas. Si esta lectura es una
        # variante de un mote ya identificado con mejor evidencia, hereda su
        # especie en vez de abrir una entrada que la contradiga.
        established = self._identity_species.get(identity) if identity else None
        if (
            established
            and self._identity_evidence.get(identity, 0) > self._EVIDENCE_RANK[evidence]
        ):
            species = established
        key = _text_key(value)
        if key:
            self._aliases[side][key] = species
            self._bound_alias_keys[side].add(key)
            if _text_key(value) != _text_key(species):
                self._message_aliases[side][value.casefold()] = species
        slot = self._announced_slot(side, value)
        if identity:
            self._set_identity_species(identity, species, evidence=evidence)
            if slot:
                self._identity_slots[slot] = identity
            return slot or self._slot_for_species(identity, side), False
        changed = bool(slot and self._active.get(slot) != species)
        if slot:
            self._active[slot] = species
            self._health.pop(slot, None)
        return slot, changed

    def _canonical_message_aliases(self, value: str) -> str:
        """Mantiene el log visible en especies canónicas, no nicknames."""

        normalized = value
        for alias, species in sorted(
            (
                item
                for aliases in self._message_aliases.values()
                for item in aliases.items()
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            normalized = re.sub(
                rf"(?<!\w){re.escape(alias)}(?!\w)",
                species,
                normalized,
                flags=re.IGNORECASE,
            )
        return normalized

    def resolved_aliases(self) -> dict[str, dict[str, str]]:
        """Devuelve el mapa final mote -> especie, separado por lado."""

        return {
            side: dict(values)
            for side, values in self._message_aliases.items()
        }

    def resolved_identities(self) -> dict[str, str]:
        """Mapa final de actor estable a especie, aplicado sólo al serializar."""

        resolved = dict(self._identity_species)
        for side, aliases in self._identity_by_alias.items():
            for alias_key, identity in aliases.items():
                species = self._aliases[side].get(alias_key)
                if species:
                    resolved[identity] = species
            side_identities = set(aliases.values())
            unresolved = side_identities - resolved.keys()
            used = {
                self._canonical_actor(actor)
                for slot, actor in self._active.items()
                if slot.startswith(side) and not is_actor_identity(self._canonical_actor(actor))
            }
            used.update(
                species
                for identity, species in resolved.items()
                if identity in side_identities
            )
            candidates = set(self._teams[side])
            candidates.update(self._aliases[side].values())
            remaining = {
                species
                for species in candidates
                if _text_key(species) not in {_text_key(value) for value in used}
            }
            # Sólo se completa por descarte cuando ambos lados son inequívocos;
            # nunca se asignan dos identidades pendientes por orden o por slot.
            if len(unresolved) == 1 and len(remaining) == 1:
                resolved[next(iter(unresolved))] = next(iter(remaining))
        return resolved

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
            **{_text_key(species): species for species in self._teams[side]},
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
        self._pending_switch_timestamps.pop(slot, None)
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

    def _ability_relief(self, side: str, value: str, ability: str) -> tuple[str | None, str | None]:
        """Especie, y el slot sólo cuando el Pokémon relevó a otro.

        Atar el mote por la habilidad mete al Pokémon en su slot. Si ese slot
        estaba ocupado, hubo un relevo que el replay tiene que contar. Si estaba
        vacío no lo hubo: son los leads, y el HUD los anuncia unos frames después
        sabiendo quién está en cada sitio.
        """

        occupied = self._active.get(self._announced_slot(side, value) or "")
        species, slot, changed = self._infer_alias_from_ability(side, value, ability)
        if not changed or not slot:
            return species, None
        if occupied is None:
            # Slot vacío: no hubo relevo, y el orden en que el juego anuncia las
            # salidas no siempre coincide con el del HUD, así que colocar aquí al
            # Pokémon es adivinar. Se deshace y la habilidad espera a que el HUD
            # lo lea. Escribirla antes de que nadie haya entrado deja el replay
            # con una habilidad sin dueño, y el visor oficial se cae al cargarlo.
            self._active.pop(slot, None)
            return species, None
        return species, slot

    def _ability_actor(
        self, value: str, ability: str | None = None
    ) -> tuple[str, str, str | None] | None:
        """Lado, Pokémon y, si al atarlo cambia de ocupante, el slot que estrenó."""

        raw = re.sub(r"[\'’]s$", "", value.strip(), flags=re.IGNORECASE)
        candidates: list[tuple[str, str]] = []
        has_known_team = any(self._known_teams.values())
        for side in ("p1", "p2"):
            if has_known_team and not self._known_teams[side]:
                continue
            actor = self._actor_for_value(side, raw)
            if actor:
                candidates.append((side, actor))
        active = [
            candidate
            for candidate in candidates
            if any(
                slot.startswith(candidate[0])
                and (
                    active_species == candidate[1]
                    or _text_key(self._canonical_actor(active_species))
                    == _text_key(self._canonical_actor(candidate[1]))
                )
                for slot, active_species in self._active.items()
            )
        ]
        for shortlist in (active, candidates):
            if len(shortlist) != 1:
                continue
            side, actor = shortlist[0]
            learned = None
            if ability and is_actor_identity(actor):
                _species, learned = self._ability_relief(side, raw, ability)
            return side, actor, learned
        if ability:
            announced_sides = [
                side
                for side in ("p1", "p2")
                if self._announced_slot(side, raw) is not None
            ]
            if len(announced_sides) == 1:
                side = announced_sides[0]
                species, learned = self._ability_relief(side, raw, ability)
                if species:
                    actor = self._actor_for_value(side, raw) or species
                    return side, actor, learned
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
        learned_switches: list[BattleEvent] = []
        for actor_line in overlay:
            if not re.search(r"[\'’]s$", actor_line.text, re.IGNORECASE):
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
            resolved = self._ability_actor(actor_line.text, ability)
            if not resolved:
                continue
            side, species, learned_slot = resolved
            confidence = min(actor_line.confidence, ability_line.confidence)
            if learned_slot:
                # Atar el mote por la habilidad mete al Pokémon en su slot. Si esa
                # entrada no se anuncia, el HUD ya no ve cambio cuando por fin lo
                # lee y el replay se queda sin ella: el daño, el estado y los
                # movimientos del recién entrado salen a nombre del que estaba
                # antes. La vía de los movimientos ya reconstruye este cambio.
                learned_switches.append(
                    BattleEvent(
                        kind="switch",
                        timestamp_ms=max(0, timestamp_ms - 1),
                        confidence=confidence,
                        slot=learned_slot,  # type: ignore[arg-type]
                        species=species,
                        source_frame=source_frame,
                    )
                )
            key = (side, species, ability)
            visible.add(key)
            if key not in self._visible_abilities:
                self._pending_abilities[key] = (confidence, self._turn)
        self._visible_abilities = visible
        return tuple(learned_switches) + self._flush_pending_abilities(
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
        for key, (confidence, turn) in tuple(self._pending_abilities.items()):
            side, species, ability = key
            # Una habilidad pertenece al turno en que el juego la anunció. Si al
            # cambiar de turno sigue sin encontrar a su Pokémon en el campo, la
            # lectura fue de otro y esperar más sólo la coloca en el momento
            # equivocado.
            if turn != self._turn:
                del self._pending_abilities[key]
                continue
            slot = self._slot_for_species(species, side)
            # El rótulo de habilidad no lleva el prefijo del rival, así que el
            # lado se deduce y con la misma especie en los dos equipos puede
            # caer en el que no juega. Buscar el slot devuelve el primero del
            # lado cuando no encuentra a nadie, y eso le colgaba la habilidad
            # al ocupante que hubiera. Se espera a que esté en el campo.
            occupant = self._active.get(slot) if slot else None
            if occupant is None or _text_key(self._canonical_actor(occupant)) != _text_key(
                self._canonical_actor(species)
            ):
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
        if _text_key(base_species) != _text_key(self._canonical_actor(actor)):
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
        roster = self._teams["p1"][:6]
        for index, center_y in enumerate(row_centers[: len(roster)]):
            label = min(
                (
                    line
                    for line in lines
                    if 0.09 <= line.center_x <= 0.25
                    and abs(line.center_y - center_y) <= 0.04
                    and looks_like_a_nickname(line.text)
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
            self._aliases["p2"].update(self._team_form_aliases(self._teams["p2"]))
            self._bound_alias_keys["p2"] = set(self._configured_bound_alias_keys["p2"])
            self._message_aliases["p2"] = dict(self._configured_message_aliases["p2"])
        self._player_names.update({"p1": p1_name, "p2": p2_name})

        return FrameDetections(
            p1_name=p1_name,
            p2_name=p2_name,
            p1_team=tuple(roster),
            p2_team=tuple(self._teams["p2"]),
            p1_selected=selected,
            team_preview=True,
        )

    def _hud_species(
        self,
        lines: Sequence[OcrLine],
        side: str,
    ) -> list[tuple[str, str | None, OcrLine]]:
        if side == "p1":
            candidates = [line for line in lines if line.center_y >= 0.55]
        else:
            candidates = [line for line in lines if line.center_y <= 0.45]

        readings = _health_readings(lines)
        found: list[tuple[str, str | None, OcrLine]] = []
        seen: set[str] = set()
        for line in sorted(candidates, key=lambda item: item.center_x):
            if _health_value(line.text) or _text_key(line.text) in _UI_TEXT:
                continue
            species = self._resolve_species(line.text, side)
            announced = self._announced_slot(side, line.text) is not None
            identity = self._identity_for_value(side, line.text)
            key = _text_key(species or identity or line.text)
            if (not species and not announced and not identity) or key in seen:
                continue
            # El slot sale del orden de esta lista, así que un fragmento suelto
            # que luego se descarta por no tener barra propia empujaba al
            # compañero al slot equivocado y lo duplicaba en los dos.
            if not _paired_health([line], readings) and not (
                (side == "p1" and line.center_y >= 0.82)
                or (side == "p2" and line.center_y <= 0.18)
            ):
                continue
            seen.add(key)
            found.append((line.text, species, line))
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
            self._bound_alias_keys[alias.side].add(nickname_key)
            identity = self._identity_for_value(alias.side, alias.nickname)
            if identity:
                self._set_identity_species(identity, canonical, evidence="preview")
            if _text_key(alias.nickname) != _text_key(canonical):
                self._message_aliases[alias.side][alias.nickname.casefold()] = canonical
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

            side_readings = sorted(
                (
                    (line, health)
                    for line, health in readings
                    if (side == "p2" and line.center_y <= 0.24)
                    or (side == "p1" and line.center_y >= 0.76)
                ),
                key=lambda item: item[0].center_x,
            )

            slot_lines: list[tuple[str, str, str | None, OcrLine]] = []
            if len(species_lines) == 1:
                label, species, line = species_lines[0]
                slot = next(
                    (
                        slot
                        for slot, active_species in self._active.items()
                        if species
                        and slot.startswith(side)
                        and _text_key(self._canonical_actor(active_species)) == _text_key(species)
                    ),
                    None,
                )
                if slot is None and len(side_readings) >= 2:
                    nearest_index = min(
                        range(len(side_readings)),
                        key=lambda index: (
                            abs(side_readings[index][0].center_x - line.center_x)
                            + abs(side_readings[index][0].center_y - line.center_y)
                        ),
                    )
                    slot = f"{side}{'a' if nearest_index == 0 else 'b'}"
                if slot is None:
                    midpoint = 0.24 if side == "p1" else 0.75
                    slot = f"{side}{'a' if line.center_x < midpoint else 'b'}"
                slot_lines.append((slot, label, species, line))
            else:
                for index, (label, species, line) in enumerate(species_lines):
                    slot_lines.append((f"{side}{'ab'[index]}", label, species, line))

            paired = _paired_health(
                [species_line for _slot, _label, _species, species_line in slot_lines],
                readings,
            )
            for index, (slot, label, species, species_line) in enumerate(slot_lines):
                health = paired.get(index)
                legacy_hud_band = (
                    side == "p1" and species_line.center_y >= 0.82
                ) or (
                    side == "p2" and species_line.center_y <= 0.18
                )
                if health is None and not legacy_hud_band:
                    continue
                identity = self._identity_for_value(side, label)
                if identity:
                    actor = identity
                    if species:
                        self._set_identity_species(identity, species, evidence="inferred")
                elif species:
                    actor = species
                else:
                    actor = self._ensure_identity(side, label, slot)
                observations[slot] = (actor, health)
                announced_key = self._matching_announced_lead_key(side, species_line.text)
                if announced_key:
                    self._hud_alias_slots[side][announced_key] = slot
                    if is_actor_identity(actor):
                        self._remember_identity_alias(side, announced_key, actor)

            self._complete_announced_lead_observations(
                side,
                observations,
                side_readings,
            )
        return dict(sorted(observations.items()))

    def _matching_announced_lead_key(self, side: str, value: str) -> str | None:
        value_key = _text_key(value)
        leads = self._announced_leads[side]
        if value_key in leads:
            return value_key
        if len(value_key) < 3:
            return None
        ranked = sorted(
            (
                (SequenceMatcher(None, value_key, lead_key).ratio(), lead_key)
                for lead_key in leads
            ),
            reverse=True,
        )
        if not ranked:
            return None
        score, lead_key = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        return lead_key if score >= 0.64 and score - runner_up >= 0.15 else None

    def _complete_announced_lead_observations(
        self,
        side: str,
        observations: dict[str, tuple[str, str | None]],
        side_readings: Sequence[tuple[OcrLine, str]],
    ) -> None:
        """Recupera el lead que OCR omitió usando el slot geométrico restante."""

        leads = self._announced_leads[side]
        if self._turn or len(leads) != 2:
            return
        mapped = self._hud_alias_slots[side]
        known = [(lead_key, mapped[lead_key]) for lead_key in leads if lead_key in mapped]
        if len(known) == 1:
            known_slot = known[0][1]
            remaining_slot = f"{side}{'b' if known_slot.endswith('a') else 'a'}"
            remaining_key = next(lead_key for lead_key in leads if lead_key != known[0][0])
            mapped[remaining_key] = remaining_slot

        health_by_slot = {
            f"{side}{'a' if index == 0 else 'b'}": health
            for index, (_line, health) in enumerate(side_readings[:2])
        }
        observed_actors = {
            actor
            for slot, (actor, _health) in observations.items()
            if slot.startswith(side)
        }
        for lead_key in leads:
            slot = mapped.get(lead_key)
            species = self._aliases[side].get(lead_key)
            if not slot or slot in observations:
                continue
            identity = self._identity_for_value(side, lead_key)
            actor = identity or species or self._ensure_identity(side, lead_key, slot)
            if actor in observed_actors:
                continue
            if identity and species:
                self._set_identity_species(identity, species, evidence="inferred")
            observations[slot] = (actor, health_by_slot.get(slot))
            observed_actors.add(actor)

    def _message_lines(self, lines: Sequence[OcrLine]) -> list[OcrLine]:
        messages: list[OcrLine] = []
        move_menu_visible = any(
            _text_key(line.text) in {"moveinfo", "movesmore"}
            for line in lines
        )
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
            # The phone recording keeps the move-selection sidebar visible on
            # the right. Its move names are UI labels, not battle messages;
            # exclude the whole menu even if OCR invents punctuation that
            # would otherwise make a label look like a dialogue sentence.
            if move_menu_visible and line.left >= 0.68:
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
        self._announced_leads[side] = tuple(
            actor_key
            for actor in actors
            if (actor_key := _text_key(actor))
        )
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

        if "tailwind started blowing" in lowered:
            side = "p2" if "opposing" in lowered else "p1"
            return (
                BattleEvent(
                    kind="sidestart",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    slot=f"{side}a",  # type: ignore[arg-type]
                    value="move: Tailwind",
                    source_frame=source_frame,
                ),
            )

        if "tailwind petered out" in lowered:
            side = "p2" if "opposing" in lowered else "p1"
            return (
                BattleEvent(
                    kind="sideend",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    slot=f"{side}a",  # type: ignore[arg-type]
                    value="move: Tailwind",
                    source_frame=source_frame,
                ),
            )

        withdrew = re.match(r"^(.*?)\s*withdrew\s+(.+?)[!.]?$", cleaned, re.IGNORECASE)
        if withdrew:
            side = self._side_for_player(withdrew.group(1))
            species = self._announced_species(withdrew.group(2), side) if side else None
            if side and species:
                slot = self._slot_for_species(species, side)
                self._mark_slot_open(slot)
                self._pending_switch_timestamps[slot] = timestamp_ms
                return ()

        come_back = re.match(r"^(.+?),\s*come back[!.]?$", cleaned, re.IGNORECASE)
        if come_back:
            species = self._resolve_species(come_back.group(1), "p1")
            if species:
                slot = self._slot_for_species(species, "p1")
                self._mark_slot_open(slot)
                self._pending_switch_timestamps[slot] = timestamp_ms
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
                elif not any(slot.startswith(side) for slot in self._active):
                    self._announced_slots[side][_text_key(sent_out.group(2))] = f"{side}a"
                return ()

        go = re.match(r"^Go[!lI]\s*(.+?)[!.]?$", cleaned, re.IGNORECASE)
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
            actor = self._actor_for_value(side, actor_raw)
            mega = self._mega_stones.get(_text_key(mega_reaction.group(3)))
            learned_switch: tuple[BattleEvent, ...] = ()
            if mega is not None and (
                actor is None
                or is_actor_identity(actor)
                or _text_key(self._canonical_actor(actor)) != _text_key(mega[1])
            ):
                slot, changed = self._bind_alias(side, actor_raw, mega[1])
                actor = self._actor_for_value(side, actor_raw) or mega[1]
                if slot and changed:
                    learned_switch = (
                        BattleEvent(
                            kind="switch",
                            timestamp_ms=timestamp_ms,
                            confidence=confidence,
                            slot=slot,  # type: ignore[arg-type]
                            species=mega[1],
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
            actor = self._actor_for_value(side, actor_raw)
            announced_forme = mega_evolved.group(3)
            learned_switch: tuple[BattleEvent, ...] = ()
            if actor is None or is_actor_identity(actor):
                normalized_forme = announced_forme.strip()
                if not normalized_forme.casefold().startswith("mega "):
                    normalized_forme = f"Mega {normalized_forme}"
                words = normalized_forme.split()
                suffix = words[-1] if words and words[-1] in {"X", "Y", "Z"} else None
                base_words = words[1:-1] if suffix else words[1:]
                candidate = f"{' '.join(base_words)}-Mega{f'-{suffix}' if suffix else ''}"
                mega = self._mega_formes.get(_text_key(candidate))
                if mega is not None:
                    slot, changed = self._bind_alias(side, actor_raw, mega[1])
                    actor = self._actor_for_value(side, actor_raw) or mega[1]
                    if slot and changed:
                        learned_switch = (
                            BattleEvent(
                                kind="switch",
                                timestamp_ms=timestamp_ms,
                                confidence=confidence,
                                slot=slot,  # type: ignore[arg-type]
                                species=mega[1],
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

        # RapidOCR occasionally joins a Japanese nickname to ``used``. The
        # move name at the end still gives us an unambiguous split point.
        move_match = re.match(r"^(The opposing )?(.+?)\s*used\s*(.+?)[!.]?$", cleaned, re.IGNORECASE)
        if move_match:
            opposing = bool(move_match.group(1))
            side = "p2" if opposing else "p1"
            actor_raw = move_match.group(2).strip()
            move_raw = move_match.group(3).strip()
            move = self._moves.resolve(move_raw, threshold=0.72)
            if not move or len(_text_key(move_raw)) < len(_text_key(move)) * 0.75:
                return ()
            actor = self._actor_for_value(side, actor_raw)
            if actor is not None and is_actor_identity(actor):
                # El evento se congela ahora, en su posición real. La evidencia
                # del movimiento sólo completa identidad -> especie; nunca
                # reinyecta ni reordena el movimiento después.
                self._infer_alias_from_move(side, actor_raw, move)
                slot = self._slot_for_species(actor, side)
                return (
                    BattleEvent(
                        kind="move",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=slot,  # type: ignore[arg-type]
                        species=actor,
                        move=move,
                        source_frame=source_frame,
                    ),
                )
            if actor is None:
                announced_slot = self._announced_slot(side, actor_raw)
                actor = self._active.get(announced_slot or "")
            learned_slot: str | None = None
            learned_switch = False
            evidence_key = (side, _text_key(actor_raw))
            if actor is None:
                pending = self._pending_alias_moves.setdefault(evidence_key, [])
                observation = (move, timestamp_ms, confidence, source_frame)
                if observation not in pending:
                    pending.append(observation)
                actor, learned_slot, learned_switch = self._infer_alias_from_move(
                    side,
                    actor_raw,
                    move,
                )
            if actor:
                actor_side = self._side_for_species(actor, opposing=opposing)
                pending = self._pending_alias_moves.pop(evidence_key, [])
                if not pending:
                    pending = [(move, timestamp_ms, confidence, source_frame)]
                events: list[BattleEvent] = []
                slot = learned_slot or self._slot_for_species(actor, actor_side)
                if learned_switch and slot:
                    events.append(
                        BattleEvent(
                            kind="switch",
                            # Alias inference can finish several frames after
                            # the first stored move. Place the reconstructed
                            # send-out immediately before that move.
                            timestamp_ms=max(0, min(item[1] for item in pending) - 1),
                            confidence=confidence,
                            slot=slot,  # type: ignore[arg-type]
                            species=actor,
                            source_frame=source_frame,
                        )
                    )
                events.extend(
                    [
                        BattleEvent(
                            kind="move",
                            timestamp_ms=move_timestamp,
                            confidence=move_confidence,
                            slot=slot,  # type: ignore[arg-type]
                            species=actor,
                            move=pending_move,
                            source_frame=move_frame,
                        )
                        for pending_move, move_timestamp, move_confidence, move_frame in pending
                    ]
                )
                return tuple(events)
            return ()

        faint_match = re.match(r"^(The opposing )?(.+?) fainted[!.]?$", cleaned, re.IGNORECASE)
        if faint_match:
            opposing = bool(faint_match.group(1))
            side = "p2" if opposing else "p1"
            actor = self._actor_for_value(side, faint_match.group(2))
            if actor:
                slot = self._slot_for_species(actor, side)
                self._mark_slot_open(slot)
                return (
                    BattleEvent(
                        kind="faint",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=slot,  # type: ignore[arg-type]
                        species=actor,
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
            side = "p2" if opposing else "p1"
            actor = self._actor_for_value(side, status_match.group(2))
            if actor:
                return (
                    BattleEvent(
                        kind="status",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
                        species=actor,
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
            side = "p2" if opposing else "p1"
            actor = self._actor_for_value(side, cure_match.group(2))
            if actor:
                return (
                    BattleEvent(
                        kind="curestatus",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
                        species=actor,
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

        # A move name without an actor is an incomplete OCR fragment, not a
        # protocol message. A later frame normally contains the complete line.
        if self._moves.resolve(cleaned, allow_fuzzy=False):
            return ()

        cleaned = self._canonical_message_aliases(cleaned)

        # A free-form OCR sentence containing an unresolved non-Latin nickname
        # is not safe Showdown protocol. Core events above are retained after
        # alias/slot resolution; only the unverifiable flavor line is omitted.
        if any(ord(character) > 127 and character.isalnum() for character in cleaned):
            return ()

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
        text_keys = {_text_key(line.text) for line in lines}
        selection_visible = bool(
            text_keys.intersection({"fight", "pokemon", "movetime", "moveinfo"})
        )
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
                        timestamp_ms=max(
                            0,
                            self._pending_switch_timestamps.pop(slot, timestamp_ms) - 1,
                        ),
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
            # En MOVE TIME / Move Info, RapidOCR puede unir el contador con un
            # signo `%` cercano y fabricar lecturas como 32%. Nunca hay daño o
            # curación reales mientras el jugador está eligiendo una acción.
            if selection_visible:
                if previous_health is None:
                    self._health[slot] = health
                continue
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

    # El Team Preview sigue en pantalla decenas de frames: leerlo varias veces y
    # quedarnos con el roster que se repite evita que un frame en transición
    # (o una silueta ambigua) decida el equipo rival de toda la batalla.
    _PREVIEW_MAX_ATTEMPTS = 24
    _PREVIEW_MIN_VOTES = 3
    _PREVIEW_MIN_LEAD = 2

    def __init__(
        self,
        *,
        context: DetectorContext | None = None,
        engine: OcrEngine | None = None,
        trace_path: Path | None = None,
        min_confidence: float = 0.5,
        engine_factory: Callable[[], OcrEngine] | None = None,
        alias_resolver: HudAliasResolver | None = None,
        team_preview_resolver: TeamPreviewResolver | None = None,
    ) -> None:
        self._base_context = context or DetectorContext()
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
        self.parser = ChampionsTextParser(context=self._base_context)
        self.trace_path = trace_path
        self._trace_battle_index = 0
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
        self._preview_resolver = team_preview_resolver
        self._preview_executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="champions-team-preview")
            if team_preview_resolver is not None
            else None
        )
        self._preview_future: Future[tuple[str, ...]] | None = None
        self._preview_future_generation = 0
        self._preview_generation = 0
        self._preview_stable_frames = 0
        self._preview_attempts = 0
        # Un voto por fila, no por roster entero: basta con que una fila baile
        # para que ninguna lectura completa llegue a repetirse.
        self._preview_votes: dict[str, list[Counter[str]]] = {
            "p1": [Counter() for _ in range(6)],
            "p2": [Counter() for _ in range(6)],
        }
        self._preview_labels: dict[str, list[Counter[str]]] = {
            "p1": [Counter() for _ in range(6)],
            "p2": [Counter() for _ in range(6)],
        }
        self._preview_errors: Counter[str] = Counter()
        self._preview_team: dict[str, tuple[str, ...]] = {"p1": (), "p2": ()}
        self._preview_source: PreparedOcrFrame | None = None

    def _poll_preview_team(self, *, wait: bool = False) -> tuple[str, ...]:
        future = self._preview_future
        if future is not None and (wait or future.done()):
            generation = self._preview_future_generation
            self._preview_future = None
            try:
                rosters = future.result()
            except Exception as error:  # una lectura mala no descarta el Team Preview
                self._preview_errors[str(error)] += 1
            else:
                if generation == self._preview_generation:
                    for side, roster in rosters.items():
                        for index, row in enumerate(roster[:6]):
                            species, label = row if isinstance(row, tuple) else (row, None)
                            if species:
                                self._preview_votes[side][index][species] += 1
                            if label:
                                self._preview_labels[side][index][label] += 1
        elif not wait:
            return ()
        applied = ()
        for side in ("p2", "p1"):
            if self._preview_team[side]:
                continue
            accepted = self._accept_preview_team(side, final=wait)
            if side == "p2":
                applied = accepted
        return applied

    def _accept_preview_team(self, side: str, *, final: bool) -> tuple[str, ...]:
        """Se queda con el roster que repiten varios frames del Team Preview."""

        rows = self._preview_votes[side]
        if not any(rows):
            if final and side == "p2" and self._preview_errors:
                error, _count = self._preview_errors.most_common(1)[0]
                self._visual_warnings.append(
                    "Team Preview detectado, pero no se pudieron identificar los seis "
                    f"sprites rivales en {self._preview_attempts} lecturas: {error}"
                )
            return ()
        team: list[str] = []
        for votes in rows:
            ranked = votes.most_common()
            if not ranked:
                return ()
            species, count = ranked[0]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0
            if not final and (
                count < self._PREVIEW_MIN_VOTES or count - runner_up < self._PREVIEW_MIN_LEAD
            ):
                return ()
            team.append(species)
        if len(set(team)) != len(team):
            # Dos filas con la misma especie significa que alguna se leyó mal.
            return ()
        applied = self.parser.bind_preview_team(team, side=side)
        if len(applied) != 6:
            if side == "p2":
                self._visual_warnings.append(
                    "Team Preview detectado, pero la lectura visual no produjo seis "
                    "especies rivales."
                )
            return ()
        self._preview_team[side] = applied
        labels = [
            (votes.most_common(1)[0][0], species)
            for votes, species in zip(self._preview_labels[side], applied, strict=False)
            if votes
        ]
        if labels:
            self.parser.bind_preview_labels(labels, side=side)
        return applied

    def _schedule_preview_team(
        self,
        prepared: PreparedOcrFrame,
        detections: FrameDetections,
    ) -> None:
        if (
            self._preview_resolver is None
            or self._preview_executor is None
            or self._preview_future is not None
            or all(self._preview_team.values())
            or not detections.team_preview
        ):
            return
        self._preview_stable_frames += 1
        if self._preview_stable_frames < 2:
            return
        if self._preview_attempts >= self._PREVIEW_MAX_ATTEMPTS:
            return
        self._preview_attempts += 1
        self._preview_source = prepared
        self._preview_future_generation = self._preview_generation
        self._preview_future = self._preview_executor.submit(
            self._read_preview,
            prepared.frame,
            prepared.rotation_degrees,
            prepared.lines,
        )

    def _read_preview(
        self,
        frame: FramePacket,
        rotation_degrees: int,
        lines: Sequence[OcrLine] = (),
    ) -> dict[str, tuple]:
        """Lee ambos paneles del Team Preview en la misma pasada.

        El rival es el que importa y su fallo se propaga; el del jugador es un
        extra que ahorra pedir el equipo por fuera, así que si no sale se deja
        vacío en vez de tumbar la lectura.
        """

        assert self._preview_resolver is not None
        read = getattr(self._preview_resolver, "resolve_rows", None)
        if read is None:  # un resolver que sólo sepa devolver el roster entero
            def read(frame, *, rotation_degrees, side):  # type: ignore[misc]
                assert self._preview_resolver is not None
                return self._preview_resolver.resolve(
                    frame, rotation_degrees=rotation_degrees, side=side
                )

        rosters: dict[str, tuple] = {
            "p2": read(frame, rotation_degrees=rotation_degrees, side="p2")
        }
        # El panel del jugador sí trae el mote escrito junto al sprite.
        labelled = getattr(self._preview_resolver, "resolve_labelled_rows", None)
        try:
            if labelled is None:
                rosters["p1"] = read(frame, rotation_degrees=rotation_degrees, side="p1")
            else:
                rosters["p1"] = labelled(
                    frame, lines, rotation_degrees=rotation_degrees, side="p1"
                )
        except Exception:  # noqa: BLE001 - el panel propio es opcional
            rosters["p1"] = ()
        return rosters

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
            "battle_index": self._trace_battle_index,
            "elapsed_ms": prepared.elapsed_ms,
            "rotation_degrees": prepared.rotation_degrees,
            "phase": phase,
            "ocr": [asdict(line) for line in prepared.lines],
            "visual_alias_candidates": [
                {"side": side, "nickname": nickname}
                for side, nickname in visual_candidates
            ],
            "visual_aliases": [asdict(alias) for alias in visual_aliases],
            "resolved_aliases": self.parser.resolved_aliases(),
            "resolved_identities": self.parser.resolved_identities(),
            "visual_alias_pending": self._alias_future is not None,
            "preview_team_pending": self._preview_future is not None,
            "detections": {
                "players": {"p1": detections.p1_name, "p2": detections.p2_name},
                "teams": {
                    "p1": list(detections.p1_team),
                    "p2": list(detections.p2_team),
                },
                "selected": {
                    "p1": list(detections.p1_selected),
                    "p2": list(detections.p2_selected),
                },
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
        preview_team = self._poll_preview_team()
        detections = self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
        if preview_team and not detections.p2_team:
            detections = replace(detections, p2_team=preview_team)
        self._schedule_preview_team(prepared, detections)
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
        self._preview_generation += 1
        self._trace_battle_index += 1
        self._visual_candidate_counts.clear()
        self._visual_attempted.clear()
        self._alias_source = None
        if self._preview_future is not None:
            self._preview_future.cancel()
        self._preview_future = None
        self._preview_stable_frames = 0
        self._preview_attempts = 0
        for table in (self._preview_votes, self._preview_labels):
            for rows in table.values():
                for votes in rows:
                    votes.clear()
        self._preview_errors.clear()
        self._preview_team = {"p1": (), "p2": ()}
        self._preview_source = None
        self.parser.reset_battle_state()

    def resolved_identities(self) -> dict[str, str]:
        return self.parser.resolved_identities()

    def flush_pending(self) -> FrameDetections:
        """Espera el refuerzo visual antes de cerrar y perder sus aliases."""

        preview_source = self._preview_source
        preview_team = self._poll_preview_team(wait=True)
        pending = FrameDetections(p2_team=preview_team)
        if preview_team and preview_source is not None:
            preview_detections = replace(
                pending,
                p1_name=self.parser._player_names["p1"],
                p2_name=self.parser._player_names["p2"],
                team_preview=True,
            )
            self._write_trace(
                preview_source,
                preview_detections,
                phase="preview_team_flush",
            )
        self._preview_source = None

        future = self._alias_future
        source = self._alias_source
        if future is None or source is None:
            return pending
        generation = self._alias_future_generation
        self._alias_future = None
        self._alias_source = None
        try:
            aliases = future.result()
        except Exception as error:  # el OCR determinista conserva el replay parcial
            self._visual_warnings.append(f"Lectura visual de nicknames omitida: {error}")
            return pending
        if generation != self._alias_generation:
            return pending
        applied = self.parser.bind_visual_aliases(aliases)
        if not applied:
            self._visual_warnings.append(
                "La lectura visual terminó, pero no produjo asociaciones válidas para el HUD."
            )
            return pending
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
        return replace(detections, p2_team=preview_team or detections.p2_team)

    def pop_warnings(self) -> tuple[str, ...]:
        warnings = tuple(self._visual_warnings)
        self._visual_warnings.clear()
        return warnings

    def close(self) -> None:
        if self._alias_executor is not None:
            self._alias_executor.shutdown(wait=False, cancel_futures=True)
            self._alias_executor = None
        if self._preview_executor is not None:
            self._preview_executor.shutdown(wait=False, cancel_futures=True)
            self._preview_executor = None


def load_trace_aliases(path: Path) -> dict[int, dict[str, tuple[tuple[str, str], ...]]]:
    """Carga el mapa final de motes de cada batalla sin alterar la traza."""

    aliases: dict[int, dict[str, dict[str, str]]] = {}
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
                battle_index = max(0, int(payload.get("battle_index", 0)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            resolved = payload.get("resolved_aliases")
            if not isinstance(resolved, dict):
                continue
            battle_aliases = aliases.setdefault(battle_index, {"p1": {}, "p2": {}})
            for side in ("p1", "p2"):
                values = resolved.get(side)
                if not isinstance(values, dict):
                    continue
                for raw_alias, raw_species in values.items():
                    alias = str(raw_alias).strip()
                    species = str(raw_species).strip()
                    if alias and species:
                        battle_aliases[side][alias] = species
    return {
        battle_index: {
            side: tuple(values.items())
            for side, values in battle_aliases.items()
        }
        for battle_index, battle_aliases in aliases.items()
    }


class OcrTraceDetector:
    """Reaplica el parser a una traza sin repetir FFmpeg ni RapidOCR."""

    def __init__(
        self,
        *,
        context: DetectorContext | None = None,
        aliases_by_battle: Mapping[
            int,
            Mapping[str, Sequence[tuple[str, str]]],
        ] | None = None,
    ) -> None:
        self._base_context = context or DetectorContext()
        self._aliases_by_battle = dict(aliases_by_battle or {})
        self._battle_index = 0
        self.parser = ChampionsTextParser(context=self._context_for_battle(0))

    @staticmethod
    def _merged_aliases(
        configured: Sequence[tuple[str, str]],
        learned: Sequence[tuple[str, str]],
    ) -> tuple[tuple[str, str], ...]:
        merged = {alias.casefold(): (alias, species) for alias, species in configured}
        for alias, species in learned:
            merged[alias.casefold()] = (alias, species)
        return tuple(merged.values())

    def _context_for_battle(self, battle_index: int) -> DetectorContext:
        learned = self._aliases_by_battle.get(battle_index, {})
        return replace(
            self._base_context,
            p1_aliases=self._merged_aliases(
                self._base_context.p1_aliases,
                learned.get("p1", ()),
            ),
            p2_aliases=self._merged_aliases(
                self._base_context.p2_aliases,
                learned.get("p2", ()),
            ),
        )

    def detect(self, frame: FramePacket) -> FrameDetections:
        try:
            payload = json.loads(frame.image.decode("utf-8-sig"))
            battle_index = max(0, int(payload.get("battle_index", 0)))
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
            detection_values = payload.get("detections")
            recorded = FrameDetections.from_mapping(
                detection_values,
                timestamp_ms=frame.timestamp_ms,
            ) if isinstance(detection_values, Mapping) else FrameDetections()
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise DetectionError(f"La traza OCR contiene un frame inválido: {error}") from error
        if payload.get("phase") == "visual_alias_flush":
            return FrameDetections()
        if battle_index != self._battle_index:
            self._battle_index = battle_index
            self.parser = ChampionsTextParser(
                context=self._context_for_battle(battle_index)
            )
        if recorded.p2_team:
            self.parser.bind_preview_team(recorded.p2_team, side="p2")
        self.parser.bind_visual_aliases(visual_aliases)
        detections = self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
        if recorded.p2_team and not detections.p2_team:
            detections = replace(detections, p2_team=recorded.p2_team)
        return detections

    def reset_battle_state(self) -> None:
        self.parser.reset_battle_state()

    def resolved_identities(self) -> dict[str, str]:
        return self.parser.resolved_identities()
