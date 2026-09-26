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
from typing import Any, Callable, Hashable, Mapping, Protocol, Sequence

from .detector import DetectionError, DetectorContext, HudAlias, HudAliasResolver
from .models import ACTOR_IDENTITY_PREFIX, BattleEvent, FrameDetections, is_actor_identity
from .armado import BattleView, HudFrame
from .notices import notice_readings
from .sources import FramePacket, OcrTraceFrameSource
from .team_preview import TeamPreviewResolver, looks_like_a_nickname


def _text_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


_PHASE_LABEL_MATCH_RATIO = 0.85


def _phase_label_seen(keys: Sequence[str], label: str) -> bool:
    """¿Aparece esta etiqueta de fase (menú, Team Preview...) en el frame?

    COL-102, reapertura estructural del 25 sep, job `331e6e783c3e45a4`: los
    gates de fase de `ChampionsTextParser` (Team Preview, `selection_visible`,
    `move_menu_visible`) comparaban la clave de cada línea contra una
    etiqueta EXACTA ("select4pokemon", "battleinfo"...). Bastó que un frame
    leyera "Select" como "Seleet" para que el gate de Team Preview fallara en
    ese único frame -no en los vecinos- y todo lo que hubiera en pantalla en
    ese instante (el roster visible, un contador de selección) se escribiera
    como si fuera de la batalla real, con salud inventada. Ya se había vuelto
    a topar con la misma forma del problema dos veces esta ronda (con
    "Battle Info" y con "Move Info"), cada vez ampliando a mano el conjunto
    de textos exactos aceptados; el vídeo siguiente puede corromper una
    etiqueta distinta de cualquiera de los tres gates, así que la
    comparación en sí -no la lista de qué etiqueta- es lo que necesitaba
    tolerar el ruido de OCR.

    Sólo se compara con una línea corta, del mismo orden de tamaño que la
    etiqueta: una oración de mensaje que la nombre de paso ("The opposing
    Pokémon fainted!" contiene "pokemon") no es el botón "POKÉMON" del menú,
    y admitirla por substring rompería esa distinción. Dentro de ese margen
    de tamaño, la clave se compara entera contra `label` -exacta primero, y
    si no calza, con el mismo umbral que ya usa esta clase para nicknames y
    motes (`_unplaced_entry_named`).
    """

    for key in keys:
        if not key or len(key) > len(label) + 4:
            continue
        if key == label:
            return True
        if SequenceMatcher(None, key, label).ratio() >= _PHASE_LABEL_MATCH_RATIO:
            return True
    return False


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


_JAPANESE_TEXT = re.compile(r"[぀-ヿ一-鿿ｦ-ﾟ]")
# El segundo lector sólo se usa si ya está en disco: nunca se descarga solo.
_SECOND_READER_MODEL = "PP-OCRv6_rec_medium.onnx"


def _box_overlap(first: OcrLine, second: OcrLine) -> float:
    width = max(0.0, min(first.right, second.right) - max(first.left, second.left))
    height = max(0.0, min(first.bottom, second.bottom) - max(first.top, second.top))
    intersection = width * height
    union = (
        (first.right - first.left) * (first.bottom - first.top)
        + (second.right - second.left) * (second.bottom - second.top)
        - intersection
    )
    return intersection / union if union > 0 else 0.0


def _is_japanese_sentence(text: str) -> bool:
    """Una frase del juego con japonés dentro: un mote narrado en un mensaje.

    Las marcas del HUD que el OCR confunde con caracteres ("二川", "ニニニ")
    y las placas de nombre son una sola palabra corta; los mensajes no.
    """

    return bool(_JAPANESE_TEXT.search(text)) and " " in text.strip() and len(text) >= 12


def _with_japanese_second_opinion(
    lines: Sequence[OcrLine],
    second: Sequence[OcrLine],
) -> tuple[OcrLine, ...]:
    """Toma del segundo lector el texto de las frases con japonés.

    COL-102, job 18241f89f82c4e83: el modelo PP-OCRv6 `small` lee mal los
    motes en hiragana dentro de los mensajes ("せんせL)" por "せんせい",
    "The opposingしごでき" sin el espacio), y el `medium` los lee bien casi
    siempre, pero en algunas frases largas en inglés se queda en dos letras.
    Así que el `medium` sólo decide en las frases donde el `small` ya vio
    japonés, y sólo si en su lectura no se pierde nada: a veces se come medio
    mote ("しごでき" → "でき") o el espacio que separa el mote de "used", y
    entonces se queda la del `small`.
    """

    replaced: list[OcrLine] = []
    for line in lines:
        if _is_japanese_sentence(line.text):
            match = max(second, key=lambda candidate: _box_overlap(line, candidate), default=None)
            if (
                match is not None
                and _box_overlap(line, match) >= 0.5
                and len(_JAPANESE_TEXT.findall(match.text)) >= len(_JAPANESE_TEXT.findall(line.text))
                and len(match.text.split()) >= len(line.text.split())
            ):
                line = replace(line, text=match.text, confidence=match.confidence)
        replaced.append(line)
    return tuple(replaced)


@dataclass(frozen=True, slots=True)
class PreparedOcrFrame:
    frame: FramePacket
    lines: tuple[OcrLine, ...]
    elapsed_ms: int
    rotation_degrees: int


@dataclass(frozen=True, slots=True)
class _HudPlate:
    """Una placa de nombre del HUD tal como se ve en el frame.

    `order` es su puesto de izquierda a derecha entre las placas leídas de su
    lado. `health` es la barra emparejada con ella, si la hay; `legacy_band`
    dice si está en la franja donde el HUD clásico pone el nombre aunque la
    barra no se haya leído.
    """

    side: str
    order: int
    label: str
    species: str | None
    line: OcrLine
    health: str | None
    legacy_band: bool


@dataclass(frozen=True, slots=True)
class _Banner:
    """Un banner lateral leído en un frame: de quién es y qué dice."""

    side: str
    owner: OcrLine
    label: OcrLine


@dataclass(slots=True)
class _UnplacedEntry:
    """Una entrada anunciada cuyo slot todavía no confirma el HUD.

    `held` guarda, en orden, todo lo que la pantalla narró desde el anuncio;
    se suelta detrás del switch en cuanto el HUD dice en qué slot cayó.
    """

    side: str
    species: str
    name_key: str
    timestamp_ms: int
    held: list[BattleEvent | _HeldAbility]
    slot: str | None = None
    switch: BattleEvent | None = None


@dataclass(frozen=True, slots=True)
class _HeldAbility:
    """Habilidad de una entrada aún sin slot; se escribe al confirmarlo."""

    entry: _UnplacedEntry
    ability: str
    confidence: float
    timestamp_ms: int
    source_frame: int


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
            self._params = accelerated
        except Exception:  # pragma: no cover - depende del runtime instalado
            # Que el proveedor figure no garantiza que arranque: sin GPU
            # utilizable se sigue en CPU en vez de quedarse sin OCR.
            if accelerated == base:
                raise
            self._engine = RapidOCR(params=base)
            self._params = base
        self.min_confidence = min_confidence
        self.rotation_quarter_turns = 0
        self._orientation_locked = False
        self._second_reader: Any = None
        self._second_reader_loaded = False

    def _second_reader_engine(self) -> Any:
        """El lector `medium` para frases con japonés, si está instalado.

        Se carga la primera vez que un frame trae una, así que un vídeo sin
        motes japoneses no paga nada. Si el modelo no está en disco no se
        descarga: se sigue sólo con el `small`, como antes.
        """

        if self._second_reader_loaded:
            return self._second_reader
        self._second_reader_loaded = True
        try:
            import rapidocr  # type: ignore[import-not-found]
            from rapidocr import ModelType, RapidOCR  # type: ignore[import-not-found]

            if not (Path(rapidocr.__file__).parent / "models" / _SECOND_READER_MODEL).is_file():
                return None
            self._second_reader = RapidOCR(params={**self._params, "Rec.model_type": ModelType.MEDIUM})
        except Exception:  # pragma: no cover - depende del runtime instalado
            self._second_reader = None
        return self._second_reader

    def _with_second_opinion(self, oriented: Any, lines: tuple[OcrLine, ...]) -> tuple[OcrLine, ...]:
        # Sólo los frames con una frase en japonés: en el job 18241f89f82c4e83
        # son el 4 %, frente al 41 % que tiene algún carácter japonés suelto.
        if not any(_is_japanese_sentence(line.text) for line in lines):
            return lines
        reader = self._second_reader_engine()
        if reader is None:
            return lines
        return _with_japanese_second_opinion(lines, self._read_decoded(oriented, reader))

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

    def _read_decoded(self, decoded: Any, engine: Any = None) -> tuple[OcrLine, ...]:
        height, width = decoded.shape[:2]
        try:
            result = (engine or self._engine)(
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
            oriented = self._rotate(decoded, self.rotation_quarter_turns)
            return self._with_second_opinion(oriented, self._read_decoded(oriented))

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
            candidates.append((battle_signals, score, quarter_turns, lines, oriented))
        battle_signals, _score, quarter_turns, lines, oriented = max(
            candidates,
            key=lambda candidate: (candidate[0], candidate[1]),
        )
        # Una notificación de WhatsApp puede ser el texto más legible del
        # primer frame vertical. Sólo fijamos la rotación cuando aparecen
        # señales propias del HUD; hasta entonces volvemos a evaluar.
        if lines and battle_signals and (quarter_turns != 0 or battle_signals >= 2):
            self.rotation_quarter_turns = quarter_turns
            self._orientation_locked = True
        return self._with_second_opinion(oriented, lines)

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
    move_targets: tuple[tuple[str, str], ...] = ()
    abilities: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
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
            move_targets = tuple(
                (entry["name"], entry["target"])
                for entry in moves_values.values()
                if isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and isinstance(entry.get("target"), str)
            )
            abilities = tuple(
                entry["name"]
                for entry in ability_values.values()
                if isinstance(entry, dict) and isinstance(entry.get("name"), str)
            )
            items = tuple(
                entry["name"]
                for entry in items_values.values()
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
                move_targets=move_targets,
                abilities=abilities,
                items=items,
                species_moves=species_moves,
                species_abilities=species_abilities,
                species_teammates=_historical_teammates(species),
                mega_stones=mega_stones,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return ChampionsCatalog()


def _similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, _text_key(first), _text_key(second)).ratio()


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

_MOVE_INFO_HEADERS = {"category", "power", "accuracy", "range"}


def _move_info_description(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """El texto descriptivo de la tarjeta Move Info del menú de movimientos.

    La descripción de Protect termina en «1/3 of what it was before.», una
    línea que por sí sola tiene forma de aviso de combate. Cada vez que un
    Pokémon abría la tarjeta durante su selección, esa línea entraba al replay
    como mensaje. La tarjeta se reconoce por sus cabeceras; la descripción es
    el párrafo que baja justo debajo de ellas, alineado a su columna.
    """

    headers = [line for line in lines if _text_key(line.text) in _MOVE_INFO_HEADERS]
    if len(headers) < 2:
        return []
    column = min(header.left for header in headers)
    edge = max(header.bottom for header in headers)
    description: list[OcrLine] = []
    for line in sorted(lines, key=lambda item: item.top):
        if line in headers or line.top < edge - 0.01 or abs(line.left - column) > 0.04:
            continue
        if line.top - edge > 0.06:
            break
        description.append(line)
        edge = line.bottom
    return description


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
    # COL-102, job 347da1c2ff16491b, frame 380: la barra de Torkoal dice
    # 63 % y el OCR devolvió "63" y, encima de su cola, "3%". El 3 se leyó
    # dos veces y el "3%" pasaba por una barra del 3 %: daño y, al frame
    # siguiente, una cura que nunca ocurrió. Un "N%" que empieza dentro de un
    # número de la misma fila y repite sus últimas cifras es ese número.
    tails: dict[int, OcrLine] = {}
    for line in lines:
        compact = line.text.replace(" ", "").replace("O", "0").replace("o", "0")
        if not re.fullmatch(r"\d{1,3}", compact):
            continue
        for candidate in lines:
            tail = re.fullmatch(r"(\d{1,2})%", candidate.text.replace(" ", ""))
            if (
                tail
                and line.left < candidate.left < line.right
                and abs(candidate.center_y - line.center_y) <= 0.035
                and compact.endswith(tail.group(1))
                and len(compact) > len(tail.group(1))
            ):
                tails[id(line)] = candidate
    readings: list[tuple[OcrLine, str]] = []
    for line in lines:
        if any(tail is line for tail in tails.values()):
            continue
        health = _health_value(line.text)
        compact = line.text.replace(" ", "").replace("O", "0").replace("o", "0")
        if health is None and id(line) in tails:
            health = _health_value(f"{compact}%")
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
        self._items = _NameMatcher(self.catalog.items)
        self._move_targets: dict[str, str] = {
            _text_key(name): target for name, target in self.catalog.move_targets
        }
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
        # Capa de armado: lo que el HUD confirmó en toda la batalla. Sólo la
        # tiene la segunda fase; sin ella el parser espera como en vivo.
        self.battle_view: BattleView | None = None
        self.reset_battle_state()

    def reset_battle_state(self) -> None:
        """Descarta el estado efímero antes de analizar otra batalla."""

        self._teams = {
            "p1": tuple(self.context.p1_team),
            "p2": tuple(self.context.p2_team),
        }
        # Vuelve el orden en que se guardó el equipo, no el de la pantalla.
        self._preview_rows_confirmed = False
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
        # Fila del panel propio que ocupa cada pick (1-4). La especie de esa
        # fila sólo se sabe cuando el propio Team Preview confirma el orden.
        self._preview_ranks: dict[int, int] = {}
        self._preview_count = 0
        # El equipo propio del job viene en el orden en que se guardó, no en el
        # de la pantalla. Hasta que la lectura del Team Preview no confirme el
        # orden de sus filas (bind_preview_team), fila i no es roster[i].
        self._preview_rows_confirmed = False
        # Sólo para trazas antiguas, que no guardan esa confirmación (ver
        # OcrTraceDetector._parser_for_battle).
        self.trust_saved_team_order = False
        self._open_slots: dict[str, list[str]] = {"p1": [], "p2": []}
        self._pending_switch_timestamps: dict[str, int] = {}
        self._visible_messages: set[str] = set()
        self.last_message_texts: tuple[str, ...] = ()
        self.last_hud_observations: dict[str, tuple[str, str | None]] = {}
        self._visible_abilities: set[tuple[str, str, str]] = set()
        self._pending_abilities: dict[tuple[str, str, str], tuple[float, int]] = {}
        self._pending_fieldstarts: set[str] = set()
        # Un clima o una habilidad puede volverse legible mientras los leads
        # todavía están entrando, varios segundos antes de que el HUD
        # estabilice lo suficiente para confirmar sus switches. Lo que la
        # pantalla ya mostró en ese hueco se represa aquí y se suelta recién
        # detrás del primer switch de la batalla, para que nunca narre el
        # efecto de un Pokémon antes de que el replay lo haya sacado a pelear.
        self._pending_pre_switch_events: list[BattleEvent] = []
        self._pending_ability_positions: dict[tuple[str, str, str], tuple[int, int, int]] = {}
        # Lo mismo a mitad de batalla. COL-102, job 82923f56ce264a92: un
        # Parting Shot y un debilitado dejaron abiertos los dos slots del
        # rival, así que "sent out Pelipper!" no podía decir en cuál entraba
        # y el switch esperó 21 s a que el HUD lo leyera; mientras tanto el
        # "It started to rain!" de su Drizzle ya se había escrito. Cada anuncio
        # sin slot abre un tramo que represa lo narrado detrás de él hasta que
        # el HUD lo coloca.
        self._unplaced_entries: list[_UnplacedEntry] = []
        self._recent_field_sources: dict[str, tuple[str, str, str, int]] = {}
        self._turn = 0
        self._command_visible = False
        self._turn_has_activity = False
        self._battle_open = False
        self._pending_end = False
        self._mega_seen: set[str] = set()
        # Un Pokémon sólo puede perder su objeto una vez por batalla; sin esto,
        # la misma lectura de OCR con el nombre del objeto ligeramente distinto
        # entre frames ("Sitrus Berry", "Strus Berry") escribía el mismo
        # -enditem dos o tres veces.
        self._items_removed: set[str] = set()
        # Slot -> (objeto, dueño, último frame con su banner, primera cura).
        self._item_banners: dict[str, tuple[str, str, int, int | None]] = {}
        # Idem para "X is buffeted by the sandstorm!": como mucho una vez
        # por slot y por turno, no por texto exacto -el mismo aviso se
        # relee con el mote un poco distinto en cada frame.
        self._weather_buffeted_seen: set[tuple[str, int]] = set()
        # Mismo patrón para "X's perish count fell to N!": el conteo sólo
        # baja una vez por turno y por slot, pero "fell" sale relaído como
        # "fll" o "fel t" en frames distintos del mismo aviso.
        self._perish_count_seen: set[tuple[str, int]] = set()
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
        if side == "p1":
            # El roster viene de las filas del Team Preview, en su orden.
            self._preview_rows_confirmed = True
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
            if self._known_teams[side]:
                # COL-102, reapertura estructural del 25 sep, job
                # `90403f16712d4d41`: "Warrior96 sent out Zoroark!" nunca
                # resolvió a la Zoroark-Hisui del roster confirmado -el
                # juego omite la forma regional en el texto de batalla, el
                # Team Preview no. "zoroark" (7) contra "zoroarkhisui" (12)
                # ni siquiera llegaba a puntuarse: la guarda de longitud del
                # comparador difuso (pensada para no emparejar especies sin
                # relación) descartaba la comparación antes de calcularla, y
                # aunque la calculara el ratio (0,74) queda bajo el umbral
                # (0,78). Esa identidad nunca resuelta debilitó al final de
                # esta misma batalla y descartó la partida completa -no fue
                # sólo un mote perdido. Se compara además contra el nombre
                # base de cada especie del roster ya confirmado -antes de su
                # guion, el mismo criterio que ya usa `verify.py` para el
                # roster- sin pasar por la guarda de longitud ni el umbral
                # difuso: no es una lectura ruidosa que tolerar, es la forma
                # exacta en que el juego siempre nombra a un Pokémon con
                # forma regional en un mensaje de batalla.
                for species in self._teams[side]:
                    if _text_key(species.split("-", 1)[0]) == value_key:
                        return species
            if not self._known_teams[side]:
                # COL-102, job 82923f56ce264a92: sin equipo rival conocido,
                # el matcher cae al catálogo completo (~1000 especies) y sólo
                # acepta coincidencia exacta -adivinar entre todas ellas es
                # demasiado arriesgado. Pero un Pokémon que ya está
                # confirmado en el campo de ese lado ("Sheasler" releído de
                # "Sneasler", ya activo en p2b) es una base seria y acotada:
                # comparar sólo contra quien ya ocupa un slot, no contra el
                # catálogo entero.
                return self._fuzzy_match_active_species(value, side)
            return None
        return self._species.resolve(value, allow_fuzzy=False)

    def _fuzzy_match_active_species(self, value: str, side: str) -> str | None:
        value_key = _text_key(value)
        if len(value_key) < 4:
            return None
        best_name: str | None = None
        best_score = 0.78
        for slot, active_species in self._active.items():
            if not slot.startswith(side) or not active_species or is_actor_identity(active_species):
                continue
            candidate_key = _text_key(self._canonical_actor(active_species))
            if abs(len(candidate_key) - len(value_key)) > max(3, len(value_key) // 2):
                continue
            score = SequenceMatcher(None, value_key, candidate_key).ratio()
            if score > best_score:
                best_score = score
                best_name = active_species
        return best_name

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

    def _slot_for_species(
        self, species: str, side: str, *, exclude_open: bool = False
    ) -> str:
        open_slots = self._open_slots.get(side, ()) if exclude_open else ()
        for slot, active_species in self._active.items():
            if slot in open_slots:
                continue
            if slot.startswith(side) and (
                active_species == species
                or _text_key(self._canonical_actor(active_species))
                == _text_key(self._canonical_actor(species))
            ):
                return slot
        return f"{side}a"

    # Movimientos con "target": "self" en el propio movimiento apuntan a
    # quien lo usa; con "allies"/"adjacentAlly" apuntan al compañero. En
    # ninguno de los dos casos hay que buscar qué rival recibió daño cerca
    # -Protect y Life Dew no le pegan a nadie- así que resolverlo aquí evita
    # que la heurística de proximidad de showdown.py adivine mal.
    _SELF_TARGET_CLASSES = frozenset({"self"})
    _ALLY_TARGET_CLASSES = frozenset({"allies", "adjacentAlly"})

    def _target_slot_for_move(self, move: str, actor_slot: str) -> str | None:
        target_class = self._move_targets.get(_text_key(move))
        if target_class in self._SELF_TARGET_CLASSES:
            return actor_slot
        if target_class in self._ALLY_TARGET_CLASSES:
            return f"{actor_slot[:2]}{'b' if actor_slot.endswith('a') else 'a'}"
        return None

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
        announced_as: str = "",
    ) -> tuple[BattleEvent, ...]:
        slots = self._open_slots[side]
        if not slots:
            # Initial leads are placed from HUD geometry. A text announcement
            # has no reliable doubles slot until a withdrawal or faint opens it.
            return ()
        if len(slots) > 1:
            # COL-102, job f53bd34897b84f86: a faint and a Parting Shot
            # recall opened both doubles slots in the same stretch of turn
            # 2, and "the earliest-opened slot gets the next announcement"
            # guessed backwards -Charizard's "Go!" claimed the slot Sinistcha
            # had just vacated, when the HUD later confirmed Charizard
            # actually took Incineroar's. Announcement order doesn't track
            # which physical slot a "Go!"/"sent out" refers to once more
            # than one is open; only the HUD, reading both names together,
            # does. With the whole battle in view (second phase), look up
            # where the HUD confirms it; otherwise wait for it instead of
            # guessing -but remember that the entry happened here, so what
            # follows it waits too.
            confirmed = (
                self.battle_view.slot_confirmed(
                    side,
                    lambda pokemon: self._same_species(pokemon, species),
                    from_frame=source_frame,
                )
                if self.battle_view is not None
                else None
            )
            if confirmed not in slots:
                if not any(
                    entry.side == side and self._same_species(entry.species, species)
                    for entry in self._unplaced_entries
                ):
                    self._unplaced_entries.append(
                        _UnplacedEntry(
                            side=side,
                            species=species,
                            name_key=_text_key(announced_as or species),
                            timestamp_ms=timestamp_ms,
                            held=[],
                        )
                    )
                return ()
            slots.remove(confirmed)
            slots.insert(0, confirmed)
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

    def _same_species(self, left: str, right: str) -> bool:
        return _text_key(self._canonical_actor(left)) == _text_key(self._canonical_actor(right))

    def _unplaced_entry_named(self, side: str, value: str) -> _UnplacedEntry | None:
        """La entrada aún sin slot de ese lado a la que se refiere un texto."""

        value_key = _text_key(re.sub(r"[\'’]s$", "", value.strip(), flags=re.IGNORECASE))
        if len(value_key) < 3:
            return None
        matches = [
            entry
            for entry in self._unplaced_entries
            if entry.side == side
            and entry.slot is None
            and max(
                SequenceMatcher(None, value_key, entry.name_key).ratio(),
                SequenceMatcher(None, value_key, _text_key(entry.species)).ratio(),
            )
            >= 0.85
        ]
        return matches[0] if len(matches) == 1 else None

    def _place_unplaced_entries(self, switch_events: Sequence[BattleEvent]) -> list[BattleEvent]:
        """Coloca las entradas que el HUD acaba de confirmar y suelta sus tramos.

        El switch toma el momento del anuncio y sale en el orden en que el
        juego anunció las entradas, cada uno seguido de lo que se narró detrás
        de él. Un tramo sólo se suelta cuando todos los anteriores ya tienen
        slot: soltar uno más reciente primero sí sería reordenar.
        """

        if not self._unplaced_entries:
            return list(switch_events)
        direct: list[BattleEvent] = []
        for event in switch_events:
            entry = next(
                (
                    entry
                    for entry in self._unplaced_entries
                    if entry.slot is None
                    and entry.side == (event.slot or "")[:2]
                    and self._same_species(entry.species, event.species or "")
                ),
                None,
            )
            if entry is None:
                direct.append(event)
                continue
            entry.slot = event.slot
            entry.switch = replace(event, timestamp_ms=entry.timestamp_ms)
        released: list[BattleEvent] = []
        placed: dict[tuple[str, str], str] = {}
        while self._unplaced_entries and self._unplaced_entries[0].switch is not None:
            entry = self._unplaced_entries.pop(0)
            placed[(entry.side, _text_key(self._canonical_actor(entry.species)))] = entry.slot or ""
            released.append(entry.switch)  # type: ignore[arg-type]
            released.extend(self._settle_held(entry.held, placed))
        return direct + released

    def _settle_held(
        self,
        held: Sequence[BattleEvent | _HeldAbility],
        placed: Mapping[tuple[str, str], str],
    ) -> list[BattleEvent]:
        """Pone en su slot confirmado lo represado a nombre de una entrada.

        Lo que no tenía slot seguro se había escrito en el primero del lado;
        con el HUD ya leído, cada evento de una especie que entró (`placed`,
        sólo las entradas ya soltadas hasta este tramo) pasa al slot donde de
        verdad está. Una habilidad cuyo dueño nunca se confirmó se descarta:
        escribirla sería adivinar su slot.
        """

        settled: list[BattleEvent] = []
        for item in held:
            if isinstance(item, _HeldAbility):
                if not item.entry.slot:
                    continue
                settled.append(
                    BattleEvent(
                        kind="ability",
                        timestamp_ms=item.timestamp_ms,
                        confidence=item.confidence,
                        slot=item.entry.slot,  # type: ignore[arg-type]
                        species=item.entry.species,
                        value=item.ability,
                        source_frame=item.source_frame,
                    )
                )
                continue
            if item.slot and item.species:
                slot = placed.get((item.slot[:2], _text_key(self._canonical_actor(item.species))))
                if slot and slot != item.slot:
                    item = replace(item, slot=slot)  # type: ignore[arg-type]
            settled.append(item)
        return settled

    def _hold_behind_unplaced_entries(
        self,
        events: list[BattleEvent],
        marks: Sequence[tuple[int, _UnplacedEntry]],
        *,
        give_up: bool,
    ) -> list[BattleEvent]:
        """Represa lo que el frame narró detrás de cada anuncio aún sin slot.

        `marks` dice desde qué posición del frame empieza el tramo de cada
        entrada. Con `give_up` (llega el turno siguiente o se acaba la batalla
        sin que el HUD las haya colocado) se deja de esperar: todo lo represado
        sale tal como se leyó, sin switch, igual que antes de esta espera.
        """

        if not marks:
            return events
        start = marks[0][0]
        if give_up:
            waiting: list[BattleEvent] = []
            placed: dict[tuple[str, str], str] = {}
            for entry in self._unplaced_entries:
                if entry.switch is not None:
                    placed[(entry.side, _text_key(self._canonical_actor(entry.species)))] = (
                        entry.slot or ""
                    )
                    waiting.append(entry.switch)
                waiting.extend(self._settle_held(entry.held, placed))
            self._unplaced_entries.clear()
            return events[:start] + waiting + events[start:]
        bounds = [index for index, _entry in marks[1:]] + [len(events)]
        for (index, entry), end in zip(marks, bounds):
            entry.held.extend(events[index:end])
        return events[:start]

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

    @staticmethod
    def _banner_observations(lines: Sequence[OcrLine]) -> list[_Banner]:
        """Los banners laterales del frame: "<Pokémon>'s" y debajo su etiqueta.

        El juego usa el mismo banner para una habilidad ("Incineroar's /
        Intimidate") y para un objeto que se activa ("Incineroar's / Sitrus
        Berry"). Ancla al lado de quien lo activa: el rival, pegado al borde
        derecho; el propio, al izquierdo. Sólo mirar la derecha dejaba sin
        -ability ninguna habilidad del propio equipo (Intimidate, Sand Stream).
        """

        overlay = [
            line
            for line in lines
            if (line.left >= 0.68 or line.right <= 0.32) and 0.28 <= line.center_y <= 0.52
        ]
        banners: list[_Banner] = []
        for owner in overlay:
            if not re.search(r"[\'’]s$", owner.text, re.IGNORECASE):
                continue
            label = min(
                (
                    candidate
                    for candidate in overlay
                    if candidate.top >= owner.bottom - 0.015
                    and 0 <= candidate.center_y - owner.center_y <= 0.12
                    and abs(candidate.center_x - owner.center_x) <= 0.12
                ),
                key=lambda candidate: candidate.center_y - owner.center_y,
                default=None,
            )
            if label is not None:
                banners.append(_Banner(side="p2" if owner.left >= 0.68 else "p1", owner=owner, label=label))
        return banners

    def _note_item_banner(self, banner: _Banner, item: str, *, timestamp_ms: int) -> None:
        """Apunta que el objeto de un Pokémon en el campo acaba de activarse.

        El juego no narra en el cuadro de texto la cura de una Sitrus Berry
        ni la de Leftovers: sólo muestra el banner y la barra sube. Sin esto
        el replay escribía una cura sin causa (COL-102: Incineroar 28 → 52
        en el job 82923f56ce264a92, Garchomp +1/16 en el 18241f89f82c4e83).
        """

        side = banner.side
        actor = self._actor_for_value(side, re.sub(r"[\'’]s$", "", banner.owner.text.strip()))
        if not actor:
            return
        slot = self._slot_for_species(actor, side, exclude_open=True)
        occupant = self._active.get(slot)
        if occupant is None or not self._same_species(occupant, actor):
            return
        current = self._item_banners.get(slot)
        if current and self._same_species(current[1], occupant) and current[0] == item:
            self._item_banners[slot] = (item, occupant, timestamp_ms, current[3])
        else:
            self._item_banners[slot] = (item, occupant, timestamp_ms, None)

    def _item_heal(
        self,
        slot: str,
        species: str,
        *,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[tuple[str, ...], list[BattleEvent]]:
        """Etiqueta y eventos previos de una cura que viene de un objeto.

        Sólo curas: tras una baya que reduce daño (Occa, Chople) lo que llega
        es el golpe, y ése no lo causa el objeto. La cura puede leerse en dos
        o tres frames que el pipeline funde en uno; todas llevan la etiqueta,
        y una baya sólo se come una vez.
        """

        banner = self._item_banners.get(slot)
        if banner is None:
            return (), []
        item, owner, seen_ms, first_heal_ms = banner
        if not self._same_species(owner, species) or timestamp_ms - seen_ms > 5_000:
            return (), []
        if first_heal_ms is not None and timestamp_ms - first_heal_ms > 1_500:
            return (), []
        before: list[BattleEvent] = []
        owner_key = f"{slot[:2]}:{_text_key(self._canonical_actor(owner))}"
        if first_heal_ms is None:
            self._item_banners[slot] = (item, owner, seen_ms, timestamp_ms)
            if item.casefold().endswith("berry") and owner_key not in self._items_removed:
                self._items_removed.add(owner_key)
                before.append(
                    BattleEvent(
                        kind="enditem",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=slot,  # type: ignore[arg-type]
                        species=owner,
                        value=item,
                        tags=("[eat]",),
                        source_frame=source_frame,
                    )
                )
        return (f"[from] item: {item}",), before

    def _ability_events(
        self,
        lines: Sequence[OcrLine],
        *,
        timestamp_ms: int,
        source_frame: int,
    ) -> tuple[BattleEvent, ...]:
        visible: set[tuple[str, str, str]] = set()
        learned_switches: list[BattleEvent] = []
        for banner in self._banner_observations(lines):
            actor_line, ability_line = banner.owner, banner.label
            ability = self._abilities.resolve(ability_line.text, threshold=0.78)
            item = self._items.resolve(ability_line.text, threshold=0.78)
            # COL-102, job 347da1c2ff16491b, frame 181: "Silveria's / Psychic
            # Seed" es el objeto de Sneasler, pero se parece a "Psychic Surge"
            # por encima del umbral y se escribía como su habilidad. Si el
            # rótulo encaja con las dos listas, gana la más parecida.
            if ability and item and _similarity(ability_line.text, item) > _similarity(
                ability_line.text, ability
            ):
                ability = None
            if not ability:
                if item:
                    self._note_item_banner(banner, item, timestamp_ms=timestamp_ms)
                continue
            # Un banner a nombre de quien acaba de anunciarse sin slot es su
            # habilidad de entrada ("Pelipper's Drizzle" tras "sent out
            # Pelipper!"). El lado lo da el borde del banner; el slot, el HUD
            # cuando lo coloque. Hasta entonces queda en su tramo, en su sitio.
            entry = self._unplaced_entry_named(banner.side, actor_line.text)
            if entry is not None:
                key = (entry.side, entry.species, ability)
                visible.add(key)
                if key not in self._visible_abilities:
                    self._unplaced_entries[-1].held.append(
                        _HeldAbility(
                            entry=entry,
                            ability=ability,
                            confidence=min(actor_line.confidence, ability_line.confidence),
                            timestamp_ms=timestamp_ms,
                            source_frame=source_frame,
                        )
                    )
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
                if not self._active:
                    # Antes de los switches de los leads todo se represa en
                    # orden; la habilidad guarda aquí su sitio en ese tramo.
                    self._pending_ability_positions[key] = (
                        len(self._pending_pre_switch_events),
                        timestamp_ms,
                        source_frame,
                    )
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
            events.extend(
                self._placed_ability_events(
                    key,
                    confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )
            )
        return tuple(events)

    def _placed_ability_events(
        self,
        key: tuple[str, str, str],
        confidence: float,
        *,
        timestamp_ms: int,
        source_frame: int,
    ) -> list[BattleEvent]:
        """La habilidad pendiente, si su Pokémon ya está en el campo."""

        side, species, ability = key
        # COL-102, job f53bd34897b84f86: Incineroar left p1b (Parting
        # Shot) and came back through p1a; `_active["p1b"]` still named
        # it until the HUD confirmed the swap, so the lookup kept
        # matching that stale, now-open slot instead of waiting. Slots
        # marked open (`_mark_slot_open`) are excluded here so a
        # departed Pokémon's old spot can't stand in for its real one.
        slot = self._slot_for_species(species, side, exclude_open=True)
        # El rótulo de habilidad no lleva el prefijo del rival, así que el
        # lado se deduce y con la misma especie en los dos equipos puede
        # caer en el que no juega. Buscar el slot devuelve el primero del
        # lado cuando no encuentra a nadie, y eso le colgaba la habilidad
        # al ocupante que hubiera. Se espera a que esté en el campo.
        occupant = self._active.get(slot) if slot else None
        if occupant is None or _text_key(self._canonical_actor(occupant)) != _text_key(
            self._canonical_actor(species)
        ):
            return []
        events = [
            BattleEvent(
                kind="ability",
                timestamp_ms=timestamp_ms,
                confidence=confidence,
                slot=slot,  # type: ignore[arg-type]
                species=species,
                value=ability,
                source_frame=source_frame,
            )
        ]
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
        return events

    def _release_pre_switch_events(self) -> list[BattleEvent]:
        """Suelta lo represado en la apertura, cada habilidad en su sitio.

        COL-102, job 82923f56ce264a92: con el roster rival ya conocido, el
        Intimidate del Incineroar líder sí encuentra dueño, pero esperaba
        aparte y salía detrás de su propio "Attack fell!", que se había leído
        medio segundo después. Cada habilidad leída mientras nadie estaba en
        el campo guarda su posición en ese tramo y entra ahí en cuanto el HUD
        coloca a su Pokémon.
        """

        held = list(self._pending_pre_switch_events)
        self._pending_pre_switch_events = []
        positions = sorted(self._pending_ability_positions.items(), key=lambda item: item[1][0])
        self._pending_ability_positions = {}
        offset = 0
        for key, (position, timestamp_ms, source_frame) in positions:
            pending = self._pending_abilities.get(key)
            if pending is None:
                continue
            placed = self._placed_ability_events(
                key,
                pending[0],
                timestamp_ms=timestamp_ms,
                source_frame=source_frame,
            )
            held[position + offset:position + offset] = placed
            offset += len(placed)
        return held

    # Efectos que ocupan todo el campo. Showdown los escribe como
    # -fieldstart/-fieldend y el visor los pinta como estado del campo
    # (addPseudoWeather/removePseudoWeather), no como un mensaje suelto. El
    # juego narra cada inicio y cada fin con un texto fijo; sólo entran aquí
    # los que ya se vieron en un job real.
    _FIELD_STARTS = (
        ("battlefield got weird", "Psychic Terrain"),
        ("electric current ran across the battlefield", "Electric Terrain"),
        ("grass grew to cover the battlefield", "Grassy Terrain"),
        ("mist swirled around the battlefield", "Misty Terrain"),
        # COL-102, job 82923f56ce264a92, Partida 2: "Mate twisted the
        # dimensions!" (frame 1587).
        ("twisted the dimensions", "Trick Room"),
    )
    _FIELD_ENDS = (
        ("weirdness disappeared from the battlefield", "Psychic Terrain"),
        ("electricity disappeared from the battlefield", "Electric Terrain"),
        ("grass disappeared from the battlefield", "Grassy Terrain"),
        ("mist disappeared from the battlefield", "Misty Terrain"),
        # Mismo job, frame 2084. También sale cuando se reusa Trick Room con
        # el campo ya invertido, que lo deshace.
        ("twisted dimensions returned to normal", "Trick Room"),
    )
    # El aviso de inicio nombra a quien lo activó ("[POKEMON] twisted the
    # dimensions!"); Showdown lo lleva en [of] y sin él el visor deja el
    # hueco del nombre vacío.
    _FIELD_START_ACTOR = re.compile(
        r"^(The opposing )?(.+?)\s+twisted the dimensions", re.IGNORECASE
    )

    def _fieldstart_event(
        self,
        effect: str,
        *,
        confidence: float,
        timestamp_ms: int,
        source_frame: int,
        of: tuple[str, str] | None = None,
    ) -> BattleEvent:
        tags: tuple[str, ...] = ()
        source = self._recent_field_sources.get(effect)
        if source and timestamp_ms - source[3] <= 15_000:
            ability, slot, species, _ = source
            tags = (f"[from] ability: {ability}", f"[of] {slot}: {species}")
        elif of:
            tags = (f"[of] {of[0]}: {of[1]}",)
        return BattleEvent(
            kind="fieldstart",
            timestamp_ms=timestamp_ms,
            confidence=confidence,
            value=f"move: {effect}",
            tags=tags,
            source_frame=source_frame,
        )

    def _field_start_actor(self, message: str) -> tuple[str, str] | None:
        """Slot y especie de quien activó el efecto, sólo si está en el campo."""

        match = self._FIELD_START_ACTOR.match(message.strip())
        if not match:
            return None
        side = "p2" if match.group(1) else "p1"
        actor = self._actor_for_value(side, match.group(2))
        if not actor:
            return None
        slot = self._slot_for_species(actor, side, exclude_open=True)
        occupant = self._active.get(slot)
        if occupant is None or not self._same_species(occupant, actor):
            return None
        return slot, actor

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
        keys = [_text_key(line.text) for line in lines]
        return _phase_label_seen(keys, "select4pokemon") and _phase_label_seen(
            keys, "sendintobattle"
        )

    def _preview_detections(self, lines: Sequence[OcrLine]) -> FrameDetections:
        """Lee nicknames, jugadores y orden de picks del selector 4/6.

        Champions coloca las seis filas propias siempre en las mismas bandas.
        Asociarlas con el Team conocido evita depender de que el HUD muestre la
        especie en lugar del nickname durante la batalla, pero sólo cuando la
        lectura del propio Team Preview ya confirmó qué especie hay en cada
        fila. COL-102, job 8b7488cb5914449f: el equipo del job guardaba
        Rillaboom antes que Blaziken y el juego los muestra al revés; atar la
        fila 4 ("Tonatiuh", con Blazikenite) a roster[3] la hizo Rillaboom
        durante toda la batalla, con prioridad sobre la lectura buena.
        """

        row_centers = (0.145, 0.26, 0.38, 0.495, 0.61, 0.73)
        trusted = self._preview_rows_confirmed or self.trust_saved_team_order
        roster = self._teams["p1"][:6] if trusted else ()
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
            if abs(line.center_y - row_centers[row_index]) > 0.05:
                continue
            self._preview_ranks[int(rank_match.group(1))] = row_index

        # Los picks se guardan por fila; la especie de cada fila, sólo si ya
        # está confirmada.
        selected = tuple(
            roster[self._preview_ranks[rank]]
            for rank in sorted(self._preview_ranks)
            if rank <= count and self._preview_ranks[rank] < len(roster)
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
        return tuple((side, nickname) for side, nickname, _line in self.visual_alias_labels(lines))

    def visual_alias_labels(
        self, lines: Sequence[OcrLine]
    ) -> tuple[tuple[str, str, OcrLine], ...]:
        """Encuentra nicknames desconocidos colocados junto a una barra de HP.

        El icono queda inmediatamente a la izquierda del texto. La geometría
        evita enviar mensajes, temporizadores o notificaciones al modelo visual.
        """

        text_keys = {_text_key(line.text) for line in lines}
        if text_keys.intersection({"close", "hidesummary", "helditem", "movesmore"}):
            return ()

        candidates: list[tuple[str, str, OcrLine]] = []
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
            candidates.append((side, label.text, label))
        return tuple(sorted(candidates, key=lambda value: (value[0], value[2].center_x)))

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

    def _read_hud_side(
        self,
        lines: Sequence[OcrLine],
        side: str,
        readings: Sequence[tuple[OcrLine, str]],
    ) -> tuple[list[_HudPlate], list[tuple[OcrLine, str]]]:
        """Capa de lectura del HUD: las placas de un lado y sus barras.

        Sólo mira la pantalla y lo que el parser ya sabe (motes, anuncios) para
        reconocer cada placa; no cambia nada. Qué slot ocupa una placa cuando
        se lee sola, y a qué identidad corresponde, lo decide
        `_hud_observations`.
        """

        side_readings = sorted(
            (
                (line, health)
                for line, health in readings
                if (side == "p2" and line.center_y <= 0.24)
                or (side == "p1" and line.center_y >= 0.76)
            ),
            key=lambda item: item[0].center_x,
        )
        species_lines = self._hud_species(lines, side)
        paired = _paired_health([line for _label, _species, line in species_lines], readings)
        plates = [
            _HudPlate(
                side=side,
                order=index,
                label=label,
                species=species,
                line=line,
                health=paired.get(index),
                legacy_band=(side == "p1" and line.center_y >= 0.82)
                or (side == "p2" and line.center_y <= 0.18),
            )
            for index, (label, species, line) in enumerate(species_lines)
        ]
        return plates, side_readings

    def _hud_plate_slot(
        self,
        plate: _HudPlate,
        plates_on_side: int,
        side_readings: Sequence[tuple[OcrLine, str]],
    ) -> str:
        """El slot de una placa: su orden si se leen las dos, si no, deducido."""

        side = plate.side
        if plates_on_side > 1:
            return f"{side}{'ab'[plate.order]}"
        slot = next(
            (
                slot
                for slot, active_species in self._active.items()
                if plate.species
                and slot.startswith(side)
                and _text_key(self._canonical_actor(active_species)) == _text_key(plate.species)
            ),
            None,
        )
        if slot is None and len(side_readings) >= 2:
            nearest_index = min(
                range(len(side_readings)),
                key=lambda index: (
                    abs(side_readings[index][0].center_x - plate.line.center_x)
                    + abs(side_readings[index][0].center_y - plate.line.center_y)
                ),
            )
            slot = f"{side}{'a' if nearest_index == 0 else 'b'}"
        if slot is None:
            midpoint = 0.24 if side == "p1" else 0.75
            slot = f"{side}{'a' if plate.line.center_x < midpoint else 'b'}"
        return slot

    def _hud_observations(self, lines: Sequence[OcrLine]) -> dict[str, tuple[str, str | None]]:
        """Decide, a partir de lo que se lee en el HUD, quién está en cada slot."""

        text_keys = {_text_key(line.text) for line in lines}
        if text_keys.intersection({"close", "hidesummary", "helditem", "movesmore"}):
            return {}

        observations: dict[str, tuple[str, str | None]] = {}
        readings = _health_readings(lines)
        # Lado a lado: lo que se decide con las placas propias ya cuenta al
        # leer las del rival.
        for side in ("p1", "p2"):
            plates, side_readings = self._read_hud_side(lines, side, readings)
            if not plates:
                continue
            for plate in plates:
                slot = self._hud_plate_slot(plate, len(plates), side_readings)
                if plate.health is None and not plate.legacy_band:
                    continue
                identity = self._identity_for_value(side, plate.label)
                if identity:
                    actor = identity
                    if plate.species:
                        self._set_identity_species(identity, plate.species, evidence="inferred")
                elif plate.species:
                    actor = plate.species
                else:
                    actor = self._ensure_identity(side, plate.label, slot)
                observations[slot] = (actor, plate.health)
                announced_key = self._matching_announced_lead_key(side, plate.line.text)
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
        # COL-102, job c5010e62d19e4663, partida 2, frame 1150 (574,5 s): el
        # panel de movimientos de Dee Dee ya mostraba su lista -"Terrain
        # Pulse", "Helping Hand", "Follow Me", "Trick Room"- un frame antes
        # de que su propio rótulo "Move Info" se leyera; a "moveinfo" solo
        # le llegaba tarde para excluir esa lista como menú. Ese único
        # frame, el "1" inicial de "Terrain Pulse" se pierde -"rrain
        # Pulse"- y la palabra suelta contiene "rain", una de las palabras
        # clave de clima de abajo: se leía como si fuera un mensaje real de
        # batalla, en vez de una etiqueta de menú, y sobrevivía al turno
        # siguiente como un "-message" sin dueño. "MOVE TIME"/"Battle Info"
        # ya estaban en pantalla varios frames antes de "Move Info": se
        # agregan al mismo conjunto que abre el menú, para no depender de
        # cuál de sus rótulos se lea primero.
        move_menu_keys = [_text_key(line.text) for line in lines]
        move_menu_visible = any(
            _phase_label_seen(move_menu_keys, label)
            for label in ("battleinfo", "fight", "pokemon", "movetime", "moveinfo", "movesmore")
        )
        move_description = _move_info_description(lines)
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
            if line in move_description:
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

        started = next(
            (effect for phrase, effect in self._FIELD_STARTS if phrase in lowered),
            None,
        )
        if started:
            # Un terreno que viene de una habilidad (Psychic Surge) espera a
            # que esa habilidad encuentre a su Pokémon, para llevar su [from].
            pending_source = any(
                self._terrain_for_ability(ability) == started
                for _side, _species, ability in self._pending_abilities
            )
            if pending_source:
                self._pending_fieldstarts.add(started)
                return ()
            return (
                self._fieldstart_event(
                    started,
                    confidence=confidence,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                    of=self._field_start_actor(cleaned),
                ),
            )

        ended = next(
            (effect for phrase, effect in self._FIELD_ENDS if phrase in lowered),
            None,
        )
        if ended:
            return (
                BattleEvent(
                    kind="fieldend",
                    timestamp_ms=timestamp_ms,
                    confidence=confidence,
                    value=f"move: {ended}",
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

        # Volt Switch, U-turn y Parting Shot sacan al Pokémon sin que el juego
        # diga "withdrew", así que este relevo no apuntaba su momento. El HUD tarda
        # en dejar leer a quien entra, y sin esa marca su entrada se escribía
        # después del ataque que ya había recibido. El mensaje sigue su camino.
        went_back = re.match(
            r"^(The opposing )?(.+?)\s+went back to\s+(.+?)[!.]?$", cleaned, re.IGNORECASE
        )
        if went_back:
            side = "p2" if went_back.group(1) else self._side_for_player(went_back.group(3))
            actor = self._actor_for_value(side, went_back.group(2)) if side else None
            if side and actor:
                slot = self._slot_for_species(actor, side)
                self._mark_slot_open(slot)
                self._pending_switch_timestamps[slot] = timestamp_ms

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
                        announced_as=sent_out.group(2),
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
                    announced_as=go.group(1),
                )

        # COL-102, job 4eb88ad277cf4546, frame 357: "The opposing Kratos hung
        # on using its Focus Sash!" es el texto que Showdown escribe para un
        # -enditem de Focus Sash: el objeto se gasta y el golpe deja 1 PS (ver
        # el acumulador). Sólo el Sash se consume; otro objeto que "aguante"
        # queda como mensaje.
        hung_on = re.match(
            r"^(The opposing )?(.+?) hung on using its (.+?)[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if hung_on and _text_key(hung_on.group(3)) == "focussash":
            side = "p2" if hung_on.group(1) else "p1"
            actor = self._actor_for_value(side, hung_on.group(2))
            if actor:
                return (
                    BattleEvent(
                        kind="enditem",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
                        species=actor,
                        value="Focus Sash",
                        source_frame=source_frame,
                    ),
                )

        knocked_off = re.match(
            r"^(The opposing )?(.+?)\s+knocked of?f\s+(the opposing )?(.+?)[\'’]s\s+(.+?)[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if knocked_off:
            actor_side = "p2" if knocked_off.group(1) else "p1"
            owner_side = "p2" if knocked_off.group(3) else "p1"
            owner_species = self._actor_for_value(owner_side, knocked_off.group(4))
            owner_key = f"{owner_side}:{_text_key(self._canonical_actor(owner_species))}" if owner_species else None
            if owner_species and owner_key not in self._items_removed:
                self._items_removed.add(owner_key)
                tags: tuple[str, ...] = ()
                actor_species = self._actor_for_value(actor_side, knocked_off.group(2))
                if actor_species:
                    tags = (
                        "[from] move: Knock Off",
                        f"[of] {self._slot_for_species(actor_species, actor_side)}: {actor_species}",
                    )
                item_raw = knocked_off.group(5).strip()
                return (
                    BattleEvent(
                        kind="enditem",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(owner_species, owner_side),  # type: ignore[arg-type]
                        value=self._items.resolve(item_raw) or item_raw,
                        tags=tags,
                        source_frame=source_frame,
                    ),
                )
            return ()

        mega_reaction = re.match(
            r"^(The opposing )?(.+?)[\'’]s (.+?) (?:is|i) reacting\s*to .+?[\'’]s (?:Omni|Omi|Mega) Ring[!.]?$",
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
                        target_slot=self._target_slot_for_move(move, slot),  # type: ignore[arg-type]
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
                            target_slot=self._target_slot_for_move(pending_move, slot),  # type: ignore[arg-type]
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
            slot: str | None = self._slot_for_species(actor, side) if actor else None
            if not actor:
                # COL-102, reapertura estructural del 25 sep: una identidad
                # sin resolver (por HUD, sin mote ni especie todavía atados)
                # puede debilitarse antes de que nada la confirme por texto.
                # Si es la única ocupante sin especie de ese lado, el
                # debilitado sólo puede ser suya -no hay otro candidato con
                # quien confundirla.
                #
                # No cubre el caso de `10a7fba6fda04585`, partida 5
                # ("MineMine"): ahí el mote nunca llegó a atarse a ninguna
                # identidad al entrar (species resolution falló y el switch
                # de un solo Pokémon no reserva identidad de reserva), y
                # para cuando llega su "fainted!" los dos slots de ese lado
                # ya muestran especies reales distintas -el mensaje llegó
                # tarde, después de que el bookkeeping ya hubiera pasado a
                # la siguiente entrada. Reservar una identidad nueva ahí
                # arriesgaba dejarla sin resolver para siempre y descartar
                # la partida entera (la falla de Zoroark, más grave que el
                # mensaje suelto actual); se dejó sin corregir a propósito.
                unresolved = [
                    (occupant_slot, occupant)
                    for occupant_slot, occupant in self._active.items()
                    if occupant_slot.startswith(side) and is_actor_identity(occupant)
                ]
                if len(unresolved) == 1:
                    slot, actor = unresolved[0]
            if actor and slot:
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
            # Champions lo anuncia como "…is paralyzed, so it may be unable to
            # move!" (COL-102, job 5748b289aa5b445b: Dragonite en el turno 3 y
            # Sableye en el 5 quedaban sin -status, sólo con el mensaje).
            (
                r"^(The opposing )?(.+?) (?:was paralyzed|is paralyzed)"
                r"(?:,? so it may be unable to move)?[!.]?$",
                "par",
            ),
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

        buffeted = re.match(
            r"^(The opposing )?(.+?) is buffeted by the sandstorm!$", cleaned, re.IGNORECASE
        )
        if buffeted:
            side = "p2" if buffeted.group(1) else "p1"
            actor = self._actor_for_value(side, buffeted.group(2))
            if actor:
                # A diferencia del objeto (una vez por batalla), la tormenta
                # de arena puede volver a golpear al mismo Pokémon en cada
                # turno; el límite seguro es una vez por turno y por slot,
                # no por texto. Distintos Pokémon narran su propio golpe sin
                # ningún frame vacío entre uno y otro (medido en el job
                # ciego), así que separarlos por hueco de pantalla no sirve.
                key = (self._slot_for_species(actor, side), self._turn)
                if key in self._weather_buffeted_seen:
                    return ()
                self._weather_buffeted_seen.add(key)
                prefix = "The opposing " if side == "p2" else ""
                return (
                    BattleEvent(
                        kind="message",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        value=f"{prefix}{actor} is buffeted by the sandstorm!",
                        source_frame=source_frame,
                    ),
                )

        perish = re.match(
            r"^(The opposing )?(.+?)[\'’]s perish count \w+(?:\s+\w+)?\s*(\d+|O)[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if perish:
            side = "p2" if perish.group(1) else "p1"
            actor = self._actor_for_value(side, perish.group(2))
            if actor:
                # Mismo criterio que buffeted: una vez por turno y por slot,
                # no por texto -el conteo real sólo cambia una vez por turno.
                key = (self._slot_for_species(actor, side), self._turn)
                if key in self._perish_count_seen:
                    return ()
                self._perish_count_seen.add(key)
                prefix = "The opposing " if side == "p2" else ""
                count = perish.group(3)
                count = "0" if count.upper() == "O" else count
                return (
                    BattleEvent(
                        kind="message",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        value=f"{prefix}{actor}'s perish count fell to {count}!",
                        source_frame=source_frame,
                    ),
                )

        # COL-102, job 8b7488cb5914449f, partida 3: el flinch quedaba como
        # -message y el visor sólo lo escribía en el log. En Showdown es un
        # "cant": el Pokémon pierde esa acción, sin estado que dure.
        flinched = re.match(
            r"^(The opposing )?(.+?) flinched and couldn['’]?t move[!.]?$",
            cleaned,
            re.IGNORECASE,
        )
        if flinched:
            side = "p2" if flinched.group(1) else "p1"
            actor = self._actor_for_value(side, flinched.group(2))
            if actor:
                return (
                    BattleEvent(
                        kind="cant",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
                        species=actor,
                        value="flinch",
                        source_frame=source_frame,
                    ),
                )

        avoided = re.match(
            r"^(The opposing )?(.+?) avoided the attack!$", cleaned, re.IGNORECASE
        )
        if avoided:
            side = "p2" if avoided.group(1) else "p1"
            actor = self._actor_for_value(side, avoided.group(2))
            if actor:
                # El aviso nombra a quien esquivó, no a quien atacó -eso lo
                # completa showdown.py buscando el movimiento más reciente
                # de la misma acción, igual que ya hace con el crítico.
                return (
                    BattleEvent(
                        kind="miss",
                        timestamp_ms=timestamp_ms,
                        confidence=confidence,
                        target_slot=self._slot_for_species(actor, side),  # type: ignore[arg-type]
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
        self.last_hud_observations = observations
        text_keys = [_text_key(line.text) for line in lines]
        selection_visible = any(
            _phase_label_seen(text_keys, label)
            for label in ("fight", "pokemon", "movetime", "moveinfo", "battleinfo")
        )
        changed_slots: set[str] = set()
        switch_events: list[BattleEvent] = []
        had_active_pokemon = bool(self._active)

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
                switch_events.append(
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

        events.extend(self._place_unplaced_entries(switch_events))
        if switch_events and not had_active_pokemon:
            events.extend(self._release_pre_switch_events())
        # Desde aquí, lo que produzca el frame va detrás de cualquier entrada
        # que siga sin slot, y de cada una que se anuncie en este mismo frame.
        hold_marks: list[tuple[int, _UnplacedEntry]] = (
            [(len(events), self._unplaced_entries[-1])] if self._unplaced_entries else []
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
        # Lo que este frame tomó como mensajes; la segunda fase lo recoge en una
        # pasada previa para agrupar las relecturas de cada aviso.
        self.last_message_texts = tuple(line.text for line in message_lines)
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
            if self._unplaced_entries and (
                not hold_marks or hold_marks[-1][1] is not self._unplaced_entries[-1]
            ):
                hold_marks.append((len(events), self._unplaced_entries[-1]))
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
            tags: tuple[str, ...] = ()
            if kind == "heal":
                tags, before = self._item_heal(
                    slot,
                    species,
                    confidence=0.9,
                    timestamp_ms=timestamp_ms,
                    source_frame=source_frame,
                )
                events.extend(before)
            events.append(
                BattleEvent(
                    kind=kind,
                    timestamp_ms=timestamp_ms,
                    confidence=0.9,
                    slot=slot,  # type: ignore[arg-type]
                    species=species,
                    health=health,
                    tags=tags,
                    source_frame=source_frame,
                )
            )
            self._turn_has_activity = True

        # Mismo riesgo que Team Preview/selection_visible/move_menu_visible:
        # este gate decide cuándo avanza el turno (más abajo), así que una
        # sola lectura corrupta de "FIGHT" no sólo perdería un evento, podría
        # dejar un turno sin abrir o abrirlo de más. Pasa por el mismo
        # comparador tolerante a ruido de OCR que los otros tres.
        command_visible = _phase_label_seen(text_keys, "fight") and (
            _phase_label_seen(text_keys, "pokemon") or _phase_label_seen(text_keys, "movetime")
        )
        if command_visible and not self._command_visible:
            if self._turn == 0:
                self._turn = 1
                # Sin esto, actividad previa a la propia batalla (p. ej. el
                # mensaje de clima de una habilidad que se revela junto a los
                # leads) sobrevive como "actividad del turno" y el segundo
                # regreso al menú FIGHT/POKÉMON del mismo turno 1 -uno por
                # cada Pokémon en dobles- se lee como el cierre del turno 1,
                # dejándolo vacío y corriendo sus eventos reales al turno 2.
                self._turn_has_activity = False
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

        if hold_marks:
            # Si el turno siguiente llega (o la batalla termina) sin que el HUD
            # haya colocado la entrada, se deja de esperar en vez de arrastrar
            # lo represado por encima del cambio de turno.
            events = self._hold_behind_unplaced_entries(
                events,
                hold_marks,
                give_up=battle_complete
                or any(event.kind == "turn" for event in events[hold_marks[0][0]:]),
            )

        if not self._active and events:
            self._pending_pre_switch_events.extend(events)
            events = []

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
    # Respaldo para una fila que nunca sacó ni un solo voto fuerte en las 24
    # lecturas (COL-102, job 82923f56ce264a92: una placa de tipo ilegible en
    # la mayoría de los frames dejaba a Slowking fuera del margen calibrado
    # de _species_and_guesses_for_cards en 36 de 37 lecturas). Un margen bajo
    # no vale como voto fuerte, pero decenas de conjeturas independientes
    # coincidiendo siempre en la misma especie sí son evidencia real -exige
    # muchas más coincidencias y casi unanimidad, precisamente porque cada
    # una por separado no pasó el filtro fino.
    _PREVIEW_WEAK_MIN_VOTES = 10
    _PREVIEW_WEAK_MIN_RATIO = 0.85

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
        # Conjeturas del rival que nunca llegaron al margen calibrado por
        # frame. Ninguna sola vale como voto fuerte, pero si docenas de
        # lecturas independientes coinciden siempre en la misma especie, eso
        # sí es evidencia -y hoy se estaba tirando entera. Ver
        # _accept_preview_team.
        self._preview_weak_votes: dict[str, list[Counter[str]]] = {
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
                            if side == "p2":
                                # (especie segura, mejor conjetura) si el
                                # resolver soporta resolve_rows_with_guesses;
                                # si no, la especie sola, como antes.
                                confident, guess = row if isinstance(row, tuple) else (row, None)
                                if confident:
                                    self._preview_votes[side][index][confident] += 1
                                if guess:
                                    self._preview_weak_votes[side][index][guess] += 1
                                continue
                            # p1: (especie, mote) -el mote sí viene escrito
                            # junto al sprite, sin margen que discutir.
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
        for index, votes in enumerate(rows):
            ranked = votes.most_common()
            if not ranked:
                fallback = self._weak_preview_species(side, index) if final else None
                if fallback is None:
                    return ()
                team.append(fallback)
                continue
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

    def _weak_preview_species(self, side: str, index: int) -> str | None:
        """Conjetura de respaldo para una fila sin ni un voto fuerte.

        Sólo se llama cuando las 24 lecturas se agotaron y esa fila nunca
        cruzó el margen calibrado del resolver ni una vez -así que exige
        muchas más coincidencias y casi unanimidad entre ellas, precisamente
        porque cada una por separado no alcanzó ese margen.
        """

        weak_rows = self._preview_weak_votes.get(side)
        if weak_rows is None or index >= len(weak_rows):
            return None
        ranked = weak_rows[index].most_common()
        if not ranked:
            return None
        species, count = ranked[0]
        total = sum(votes for _species, votes in ranked)
        if count < self._PREVIEW_WEAK_MIN_VOTES or count / total < self._PREVIEW_WEAK_MIN_RATIO:
            return None
        return species

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

        # El rival trae sólo silueta y tipo, sin mote que la respalde; cuando
        # el resolver puede además entregar su mejor conjetura por debajo del
        # margen calibrado, la usamos como respaldo de la votación entre
        # frames en vez de perderla (ver _accept_preview_team).
        read_with_guesses = getattr(self._preview_resolver, "resolve_rows_with_guesses", None)
        p2_read = read_with_guesses or read
        rosters: dict[str, tuple] = {
            "p2": p2_read(frame, rotation_degrees=rotation_degrees, side="p2")
        }
        # El panel del jugador sí trae el mote escrito junto al sprite.
        labelled = getattr(self._preview_resolver, "resolve_labelled_rows", None)
        try:
            if labelled is None:
                rosters["p1"] = read(frame, rotation_degrees=rotation_degrees, side="p1")
            else:
                # El equipo propio del job, como conjunto de candidatas: su
                # orden guardado no dice nada de las filas (ver
                # ChampionsTextParser._preview_detections).
                rosters["p1"] = labelled(
                    frame,
                    lines,
                    rotation_degrees=rotation_degrees,
                    side="p1",
                    team=tuple(self._base_context.p1_team),
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
        labels = self.parser.visual_alias_labels(lines)
        candidates = tuple((side, nickname) for side, nickname, _line in labels)
        if not candidates:
            return ()
        signature = tuple(candidates)
        count = self._visual_candidate_counts.get(signature, 0) + 1
        self._visual_candidate_counts[signature] = count
        if count < 2 or signature in self._visual_attempted:
            return signature
        self._visual_attempted.add(signature)
        resolve_icons = getattr(self._alias_resolver, "resolve_icons", None)
        if callable(resolve_icons):
            # Comparación local del icono: el frame tal cual y la posición de
            # cada mote, contra el roster que ya se leyó del Team Preview.
            self._alias_source = prepared
            self._alias_future_generation = self._alias_generation
            self._alias_future = self._alias_executor.submit(
                resolve_icons,
                frame,
                labels,
                {side: tuple(self.parser._teams[side]) for side in ("p1", "p2")},
                rotation_degrees=prepared.rotation_degrees,
            )
            return signature
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
        for table in (self._preview_votes, self._preview_weak_votes, self._preview_labels):
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


def load_trace_rosters(path: Path) -> dict[int, tuple[str, ...]]:
    """El roster rival final de cada batalla, tal como quedó en la traza.

    Sale del Team Preview: de su lectura durante la batalla o, si sólo se
    resolvió al cerrarla, del registro `preview_team_flush` que la traza
    guarda detrás de su último frame. Ése manda, porque es la última palabra
    del resolvedor para esa batalla.
    """

    read: dict[int, tuple[str, ...]] = {}
    closing: dict[int, tuple[str, ...]] = {}
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
            detections = payload.get("detections")
            teams = detections.get("teams") if isinstance(detections, dict) else None
            team = teams.get("p2") if isinstance(teams, dict) else None
            if not isinstance(team, list) or not team:
                continue
            roster = tuple(str(species) for species in team if species)
            if payload.get("phase") == "preview_team_flush":
                closing[battle_index] = roster
            else:
                read.setdefault(battle_index, roster)
    return {**read, **closing}


class OcrTraceDetector:
    """Decide el replay desde una traza ya completa, sin repetir FFmpeg ni RapidOCR.

    Es la segunda fase de un job: cuando se lee, la traza ya contiene todo lo
    que el recorrido del vídeo llegó a saber de cada batalla, así que su
    roster rival y sus motes finales se conocen desde su primer frame, y no
    desde el momento en que el vídeo los reveló.
    """

    def __init__(
        self,
        *,
        context: DetectorContext | None = None,
        aliases_by_battle: Mapping[
            int,
            Mapping[str, Sequence[tuple[str, str]]],
        ] | None = None,
        rosters_by_battle: Mapping[int, Sequence[str]] | None = None,
    ) -> None:
        self._base_context = context or DetectorContext()
        self._aliases_by_battle = dict(aliases_by_battle or {})
        self._rosters_by_battle = {
            battle_index: tuple(team)
            for battle_index, team in (rosters_by_battle or {}).items()
        }
        self._battle_index = 0
        self._notice_readings: dict[tuple[Hashable, str], str] = {}
        self._views_by_battle: dict[int, BattleView] = {}
        self.parser = self._parser_for_battle(0)

    def _parser_for_battle(self, battle_index: int) -> ChampionsTextParser:
        parser = ChampionsTextParser(context=self._context_for_battle(battle_index))
        # Una traza anterior a resolved_aliases no guarda qué especie leyó el
        # Team Preview en cada fila propia: el orden guardado del equipo es lo
        # único que tiene, como antes. Las trazas actuales traen los motes ya
        # confirmados por la primera fase.
        parser.trust_saved_team_order = not self._aliases_by_battle
        return parser

    @classmethod
    def from_trace(cls, path: Path, *, context: DetectorContext | None = None) -> OcrTraceDetector:
        def build() -> OcrTraceDetector:
            return cls(
                context=context,
                aliases_by_battle=load_trace_aliases(path),
                rosters_by_battle=load_trace_rosters(path),
            )

        # Una pasada de reconocimiento anota, frame a frame, qué tomó el parser
        # como mensaje y qué confirmó el HUD. Con eso la pasada real lee cada
        # aviso con su mejor lectura y mira el HUD por delante en vez de
        # esperarlo.
        detector = build()
        if not path.is_file():
            return detector
        scout = build()
        read: list[tuple[Hashable, Sequence[str]]] = []
        hud: dict[int, list[HudFrame]] = {}
        for frame in OcrTraceFrameSource(path=path):
            scout.parser.last_message_texts = ()
            scout.parser.last_hud_observations = {}
            detections = scout.detect(frame)
            read.append(((scout._battle_index, frame.index), scout.parser.last_message_texts))
            hud.setdefault(scout._battle_index, []).append(
                HudFrame(
                    frame=frame.index,
                    slots={slot: pokemon for slot, (pokemon, _health) in scout.parser.last_hud_observations.items()},
                    opens_turn=any(event.kind == "turn" for event in detections.events),
                )
            )
        detector._notice_readings = notice_readings(read)
        detector._views_by_battle = {battle: BattleView(frames) for battle, frames in hud.items()}
        detector.parser.battle_view = detector._views_by_battle.get(detector._battle_index)
        return detector

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
            # Un equipo rival escrito a mano en el job manda sobre el que se
            # reconoció en el vídeo; si no lo hay, el de esta batalla entra
            # como si se hubiera conocido desde el principio.
            p2_team=self._base_context.p2_team or self._rosters_by_battle.get(battle_index, ()),
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
        if payload.get("phase") in {"visual_alias_flush", "preview_team_flush"}:
            # Lo que el vídeo resolvió al cerrar una batalla ya está en su
            # roster desde el primer frame. Reaplicar aquí la pantalla de
            # selección de la que salió lo metía en la batalla siguiente.
            return FrameDetections()
        if battle_index != self._battle_index:
            self._battle_index = battle_index
            self.parser = self._parser_for_battle(battle_index)
            self.parser.battle_view = self._views_by_battle.get(battle_index)
        if recorded.p2_team:
            self.parser.bind_preview_team(recorded.p2_team, side="p2")
        self.parser.bind_visual_aliases(visual_aliases)
        if self._notice_readings:
            lines = tuple(
                replace(line, text=best)
                if (best := self._notice_readings.get(((battle_index, frame.index), _text_key(line.text))))
                else line
                for line in lines
            )
        detections = self.parser.parse(
            lines,
            timestamp_ms=frame.timestamp_ms,
            source_frame=frame.index,
        )
        p2_team = recorded.p2_team or self._rosters_by_battle.get(battle_index, ())
        if p2_team and not detections.p2_team:
            detections = replace(detections, p2_team=p2_team)
        return detections

    def reset_battle_state(self) -> None:
        self.parser.reset_battle_state()

    def resolved_identities(self) -> dict[str, str]:
        return self.parser.resolved_identities()
