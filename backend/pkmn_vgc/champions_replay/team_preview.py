from __future__ import annotations

import io
import math
import os
import re
import threading
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .detector import DetectionError
from .sources import FramePacket


class TeamPreviewResolver(Protocol):
    def resolve(
        self,
        frame: FramePacket,
        *,
        rotation_degrees: int = 0,
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _SpriteShape:
    mask: object
    aspect_ratio: float


# Pokemon Champions usa estos colores para las placas de tipo. La comparación
# se hace con cromaticidad, por lo que sigue funcionando con capturas oscuras o
# con el degradado del reproductor encima.
_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "Normal": (146, 157, 163),
    "Fire": (255, 157, 85),
    "Water": (45, 116, 227),
    "Electric": (244, 210, 60),
    "Grass": (99, 188, 90),
    "Ice": (115, 206, 192),
    "Fighting": (206, 65, 107),
    "Poison": (171, 106, 200),
    "Ground": (139, 78, 38),
    "Flying": (121, 169, 221),
    "Psychic": (231, 56, 107),
    "Bug": (145, 193, 47),
    "Rock": (197, 183, 140),
    # Estas dos placas son bastante más oscuras/rojizas en Champions que en
    # la paleta web habitual de Pokémon.
    "Ghost": (72, 40, 72),
    "Dragon": (72, 88, 209),
    "Dark": (56, 40, 40),
    "Steel": (92, 151, 166),
    "Fairy": (236, 143, 230),
}

_SPRITE_ALIASES = {
    "urshifurapidstrike": "urshifu-rapidstrike",
    "urshifurapid": "urshifu-rapidstrike",
    "ogerponwellspring": "ogerpon-wellspring",
    "calyrexshadow": "calyrex-shadow",
    "calyrexice": "calyrex-ice",
    "landorustherian": "landorus-therian",
    "indeedeef": "indeedee-f",
    "ursalunabloodmoon": "ursaluna-bloodmoon",
    "zamazentacrowned": "zamazenta-crowned",
}

_HYPHENATED_BASE_IDS = {
    "nidoranf",
    "nidoranm",
    "hooh",
    "porygonz",
    "jangmoo",
    "hakamoo",
    "kommoo",
    "wochien",
    "chienpao",
    "tinglu",
    "chiyu",
}


def _text_id(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _sprite_slug(species: str) -> str:
    species_id = _text_id(species)
    explicit = _SPRITE_ALIASES.get(species_id)
    if explicit:
        return explicit
    if species_id in _HYPHENATED_BASE_IDS or "-" not in species:
        return species_id
    base, *forme = (part.strip() for part in species.split("-") if part.strip())
    return f"{_text_id(base)}-{_text_id('-'.join(forme))}"


def _chromaticity(color: Sequence[float]) -> tuple[float, float, float]:
    total = max(1.0, float(sum(color)))
    return tuple(float(channel) / total for channel in color)  # type: ignore[return-value]


def _color_distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second, strict=True)))


class ChampionsTeamPreviewResolver:
    """Lee el roster rival desde sprites y placas de tipo del Team Preview.

    Los nombres propios están disponibles por OCR, pero el selector rival sólo
    muestra imágenes. Primero reducimos cada fila por su par de tipos y sólo
    descargamos los sprites animados de Showdown cuando quedan dos o más
    candidatos. Todo ocurre en el mismo frame que ya recorrió el OCR.
    """

    _ROW_CENTERS = (0.188, 0.292, 0.396, 0.500, 0.606, 0.710)
    _SPRITE_URL = "https://play.pokemonshowdown.com/sprites/ani/{slug}.gif"

    def __init__(
        self,
        species_types: Sequence[tuple[str, Sequence[str]]],
        *,
        cache_directory: Path,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.species_types = tuple(
            (species, tuple(types))
            for species, types in species_types
            if species and types and "-Mega" not in species
        )
        self.cache_directory = cache_directory
        self.timeout_seconds = timeout_seconds
        self._template_lock = threading.Lock()
        self._templates: dict[str, tuple[_SpriteShape, ...]] = {}

    @staticmethod
    def _decode_frame(frame: FramePacket, rotation_degrees: int) -> object:
        try:
            from PIL import Image, ImageOps  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as error:
            raise DetectionError(
                "Falta Pillow para leer los sprites del Team Preview. Instala de nuevo "
                'con: python -m pip install -e ".\\backend[champions]"'
            ) from error
        try:
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(frame.image))).convert("RGB")
        except Exception as error:
            raise DetectionError(f"No se pudo decodificar el Team Preview: {error}") from error
        quarter_turns = (rotation_degrees // 90) % 4
        if quarter_turns:
            image = image.rotate(-90 * quarter_turns, expand=True)
        return np.asarray(image)

    @staticmethod
    def _bounds(length: int, start: float, end: float) -> tuple[int, int]:
        left = max(0, min(length - 1, round(length * start)))
        right = max(left + 1, min(length, round(length * end)))
        return left, right

    @classmethod
    def _type_from_tile(
        cls,
        image: object,
        *,
        row_center: float,
        left: float,
        right: float,
    ) -> str | None:
        import numpy as np  # type: ignore[import-not-found]

        height, width = image.shape[:2]  # type: ignore[union-attr]
        x1, x2 = cls._bounds(width, left, right)
        y1, y2 = cls._bounds(height, row_center - 0.039, row_center + 0.008)
        crop = image[y1:y2, x1:x2]  # type: ignore[index]
        if not crop.size:
            return None

        margin = max(2, round(width * 0.005))
        surrounding = np.concatenate(
            (
                image[y1:y2, max(0, x1 - margin) : x1].reshape(-1, 3),  # type: ignore[index]
                image[y1:y2, x2 : min(width, x2 + margin)].reshape(-1, 3),  # type: ignore[index]
            ),
            axis=0,
        )
        if not surrounding.size:
            return None
        background_bins = (surrounding // 16) * 16
        background_value, _count = Counter(map(tuple, background_bins)).most_common(1)[0]
        background = np.asarray(background_value, dtype=float) + 8

        ranked: list[tuple[float, tuple[int, int, int]]] = []
        for raw_color, count in Counter(map(tuple, ((crop.reshape(-1, 3) // 16) * 16))).items():
            color = np.asarray(raw_color, dtype=float) + 8
            maximum = float(color.max())
            minimum = float(color.min())
            saturation = (maximum - minimum) / max(1.0, maximum)
            separation = float(np.linalg.norm(color - background))
            # Descarta fondo de tarjeta y el glifo blanco/gris de la placa.
            if maximum < 24 or separation < 28 or (saturation < 0.20 and maximum > 90):
                continue
            score = (
                count
                * (0.30 + 0.70 * saturation)
                * separation
                * math.sqrt(maximum / max(20.0, float(background.max())))
            )
            ranked.append((score, tuple(int(value) for value in color)))
        if not ranked:
            return None

        _score, dominant = max(ranked)
        observed = _chromaticity(dominant)
        distances = sorted(
            (
                _color_distance(observed, _chromaticity(reference)),
                pokemon_type,
            )
            for pokemon_type, reference in _TYPE_COLORS.items()
        )
        distance, pokemon_type = distances[0]
        margin = distances[1][0] - distance
        if distance > 0.125 or margin < 0.012:
            return None
        return pokemon_type

    def _candidates_for_types(self, types: Sequence[str]) -> tuple[str, ...]:
        expected = tuple(types)
        return tuple(
            species
            for species, candidate_types in self.species_types
            if len(candidate_types) == len(expected)
            and set(candidate_types) == set(expected)
        )

    @classmethod
    def _gender(cls, image: object, row_center: float) -> str | None:
        import numpy as np  # type: ignore[import-not-found]

        height, width = image.shape[:2]  # type: ignore[union-attr]
        x1, x2 = cls._bounds(width, 0.842, 0.864)
        y1, y2 = cls._bounds(height, row_center + 0.004, row_center + 0.039)
        pixels = image[y1:y2, x1:x2].reshape(-1, 3).astype(float)  # type: ignore[index]
        if not len(pixels):
            return None
        red, green, blue = pixels[:, 0], pixels[:, 1], pixels[:, 2]
        blue_pixels = (blue > 45) & (blue > red * 1.30) & (blue > green * 1.08)
        if int(blue_pixels.sum()) >= max(5, round(len(pixels) * 0.025)):
            return "M"
        local_red = float(np.median(red))
        red_pixels = (red > local_red + 28) & (red > green * 1.45) & (red > blue * 1.35)
        if int(red_pixels.sum()) >= max(5, round(len(pixels) * 0.02)):
            return "F"
        return None

    @staticmethod
    def _components(mask: object) -> tuple[tuple[tuple[int, int], ...], ...]:
        import numpy as np  # type: ignore[import-not-found]

        height, width = mask.shape  # type: ignore[union-attr]
        visited = np.zeros((height, width), dtype=bool)
        components: list[tuple[tuple[int, int], ...]] = []
        for start_y in range(height):
            for start_x in range(width):
                if visited[start_y, start_x] or not mask[start_y, start_x]:  # type: ignore[index]
                    continue
                visited[start_y, start_x] = True
                pending = deque(((start_y, start_x),))
                points: list[tuple[int, int]] = []
                while pending:
                    y, x = pending.popleft()
                    points.append((y, x))
                    for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                        if (
                            0 <= next_y < height
                            and 0 <= next_x < width
                            and not visited[next_y, next_x]
                            and mask[next_y, next_x]  # type: ignore[index]
                        ):
                            visited[next_y, next_x] = True
                            pending.append((next_y, next_x))
                components.append(tuple(points))
        return tuple(components)

    @classmethod
    def _normalize_mask(cls, mask: object) -> _SpriteShape | None:
        try:
            from PIL import Image  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - validado al decodificar
            raise DetectionError(str(error)) from error

        ys, xs = np.where(mask)
        if len(xs) < 20:
            return None
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        cropped = (mask[y1:y2, x1:x2].astype("uint8") * 255)  # type: ignore[index]
        source_height, source_width = cropped.shape
        scale = min(56 / max(1, source_width), 56 / max(1, source_height))
        target_width = max(1, round(source_width * scale))
        target_height = max(1, round(source_height * scale))
        resized = np.asarray(
            Image.fromarray(cropped).resize(
                (target_width, target_height),
                Image.Resampling.NEAREST,
            )
        ) > 127
        normalized = np.zeros((64, 64), dtype=bool)
        offset_x = (64 - target_width) // 2
        offset_y = (64 - target_height) // 2
        normalized[
            offset_y : offset_y + target_height,
            offset_x : offset_x + target_width,
        ] = resized
        return _SpriteShape(normalized, source_width / max(1, source_height))

    @classmethod
    def _observed_shape(cls, image: object, row_center: float) -> _SpriteShape | None:
        import numpy as np  # type: ignore[import-not-found]

        height, width = image.shape[:2]  # type: ignore[union-attr]
        x1, x2 = cls._bounds(width, 0.775, 0.841)
        y1, y2 = cls._bounds(height, row_center - 0.053, row_center + 0.053)
        crop = image[y1:y2, x1:x2]  # type: ignore[index]
        if not crop.size:
            return None
        edge_width = max(2, round(crop.shape[1] * 0.07))
        background = np.median(
            np.concatenate((crop[:, :edge_width], crop[:, -edge_width:]), axis=1),
            axis=1,
        )[:, None, :]
        difference = np.linalg.norm(crop.astype(float) - background, axis=2)
        mask = difference > 35

        best: tuple[float, tuple[tuple[int, int], ...]] | None = None
        crop_height, crop_width = mask.shape
        for points in cls._components(mask):
            if len(points) < max(12, round(mask.size * 0.004)):
                continue
            ys = [point[0] for point in points]
            xs = [point[1] for point in points]
            component_width = max(xs) - min(xs) + 1
            component_height = max(ys) - min(ys) + 1
            center_x = sum(xs) / len(xs)
            if not 0.12 * crop_width <= center_x <= 0.90 * crop_width:
                continue
            if component_height < 0.12 * crop_height:
                continue
            if component_width > 0.70 * crop_width and component_height < 0.14 * crop_height:
                continue
            score = len(points) * (1 - abs(center_x - crop_width / 2) / max(1, crop_width * 1.25))
            if best is None or score > best[0]:
                best = (score, points)
        if best is None:
            return None
        selected = np.zeros_like(mask)
        for y, x in best[1]:
            selected[y, x] = True
        return cls._normalize_mask(selected)

    def _sprite_path(self, species: str) -> Path:
        slug = _sprite_slug(species)
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise DetectionError(f"Nombre de sprite inválido para {species}.")
        path = self.cache_directory / f"{slug}.gif"
        if path.is_file() and path.stat().st_size:
            return path
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        request = Request(
            self._SPRITE_URL.format(slug=slug),
            headers={"User-Agent": "PKMN-VGC-Champions/1.0"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise DetectionError(f"No se pudo descargar el sprite de {species}: {error}") from error
        if not payload:
            raise DetectionError(f"Showdown devolvió un sprite vacío para {species}.")
        temporary = path.with_suffix(f".{os.getpid()}-{threading.get_ident()}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        return path

    def _sprite_templates(self, species: str) -> tuple[_SpriteShape, ...]:
        try:
            from PIL import Image  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - validado al decodificar
            raise DetectionError(str(error)) from error

        with self._template_lock:
            cached = self._templates.get(species)
        if cached is not None:
            return cached
        path = self._sprite_path(species)
        try:
            sprite = Image.open(path)
            frame_count = max(1, int(getattr(sprite, "n_frames", 1)))
            step = max(1, frame_count // 16)
            templates: list[_SpriteShape] = []
            for frame_index in range(0, frame_count, step):
                sprite.seek(frame_index)
                rgba = np.asarray(sprite.convert("RGBA"))
                shape = self._normalize_mask(rgba[:, :, 3] > 40)
                if shape is not None:
                    templates.append(shape)
        except Exception as error:
            path.unlink(missing_ok=True)
            raise DetectionError(f"El sprite en caché de {species} no es válido: {error}") from error
        result = tuple(templates)
        if not result:
            raise DetectionError(f"El sprite de {species} no contiene siluetas utilizables.")
        with self._template_lock:
            self._templates[species] = result
        return result

    @staticmethod
    def _shift(mask: object, delta_y: int, delta_x: int) -> object:
        import numpy as np  # type: ignore[import-not-found]

        shifted = np.zeros_like(mask)
        height, width = mask.shape  # type: ignore[union-attr]
        source_y1 = max(0, -delta_y)
        source_y2 = min(height, height - delta_y)
        target_y1 = max(0, delta_y)
        source_x1 = max(0, -delta_x)
        source_x2 = min(width, width - delta_x)
        target_x1 = max(0, delta_x)
        shifted[
            target_y1 : target_y1 + source_y2 - source_y1,
            target_x1 : target_x1 + source_x2 - source_x1,
        ] = mask[source_y1:source_y2, source_x1:source_x2]  # type: ignore[index]
        return shifted

    @classmethod
    def _shape_score(cls, observed: _SpriteShape, template: _SpriteShape) -> float:
        best = 0.0
        for delta_y in range(-3, 4):
            for delta_x in range(-3, 4):
                shifted = cls._shift(template.mask, delta_y, delta_x)
                intersection = int((observed.mask & shifted).sum())  # type: ignore[operator]
                union = int((observed.mask | shifted).sum())  # type: ignore[operator]
                overlap = intersection / max(1, union)
                aspect = max(0.0, 1 - abs(observed.aspect_ratio - template.aspect_ratio))
                best = max(best, 0.90 * overlap + 0.10 * aspect)
        return best

    @staticmethod
    def _gendered_candidate(candidates: Sequence[str], gender: str | None) -> str | None:
        if gender not in {"M", "F"} or len(candidates) < 2:
            return None
        by_base: dict[str, dict[str, str]] = {}
        for species in candidates:
            if species.endswith(("-M", "-F")):
                base, suffix = species[:-2], species[-1]
            else:
                base, suffix = species, "M"
            by_base.setdefault(_text_id(base), {})[suffix] = species
        matches = [values[gender] for values in by_base.values() if gender in values and {"M", "F"} <= values.keys()]
        return matches[0] if len(matches) == 1 else None

    def resolve(
        self,
        frame: FramePacket,
        *,
        rotation_degrees: int = 0,
    ) -> tuple[str, ...]:
        image = self._decode_frame(frame, rotation_degrees)
        candidates_by_row: list[tuple[str, ...]] = []
        genders: list[str | None] = []
        for row_number, row_center in enumerate(self._ROW_CENTERS, start=1):
            primary = self._type_from_tile(
                image,
                row_center=row_center,
                left=0.844,
                right=0.865,
            )
            secondary = self._type_from_tile(
                image,
                row_center=row_center,
                left=0.868,
                right=0.891,
            )
            types = tuple(value for value in (primary, secondary) if value)
            candidates = self._candidates_for_types(types)
            if not types or not candidates:
                raise DetectionError(
                    f"no se pudo leer un par de tipos válido en la fila rival {row_number}"
                )
            candidates_by_row.append(candidates)
            genders.append(self._gender(image, row_center))

        required_templates = {
            species
            for candidates, gender in zip(candidates_by_row, genders, strict=True)
            if len(candidates) > 1 and self._gendered_candidate(candidates, gender) is None
            for species in candidates
        }
        if required_templates:
            with ThreadPoolExecutor(max_workers=min(4, len(required_templates))) as executor:
                loaded = dict(
                    zip(
                        required_templates,
                        executor.map(self._sprite_templates, required_templates),
                        strict=True,
                    )
                )
        else:
            loaded = {}

        roster: list[str] = []
        for row_number, (row_center, candidates, gender) in enumerate(
            zip(self._ROW_CENTERS, candidates_by_row, genders, strict=True),
            start=1,
        ):
            if len(candidates) == 1:
                roster.append(candidates[0])
                continue
            gendered = self._gendered_candidate(candidates, gender)
            if gendered:
                roster.append(gendered)
                continue
            observed = self._observed_shape(image, row_center)
            if observed is None:
                raise DetectionError(f"no se pudo aislar el sprite rival de la fila {row_number}")
            scores = sorted(
                (
                    max(self._shape_score(observed, template) for template in loaded[species]),
                    species,
                )
                for species in candidates
            )
            best_score, best_species = scores[-1]
            runner_up = scores[-2][0] if len(scores) > 1 else 0.0
            if best_score < 0.45 or best_score - runner_up < 0.04:
                raise DetectionError(
                    f"el sprite rival de la fila {row_number} quedó ambiguo "
                    f"({best_species} {best_score:.2f})"
                )
            roster.append(best_species)

        if len(roster) != 6 or len({_text_id(species) for species in roster}) != 6:
            raise DetectionError("el Team Preview rival no produjo seis especies distintas")
        return tuple(roster)
