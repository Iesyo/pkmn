from __future__ import annotations

import io
import json
import math
import threading
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .detector import DetectionError
from .sources import FramePacket


class TeamPreviewResolver(Protocol):
    def resolve(
        self,
        frame: FramePacket,
        *,
        rotation_degrees: int = 0,
        side: str = "p2",
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _SpriteShape:
    """Silueta normalizada más el histograma de color del sprite."""

    mask: object
    aspect_ratio: float
    colour: object


# Pokemon Champions usa estos colores para las placas de tipo. La comparación
# se hace con cromaticidad, por lo que sigue funcionando con capturas oscuras o
# con el degradado del reproductor encima.
_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "Normal": (146, 157, 163),
    # Fire en Champions es rojo, no el naranja de la paleta web; con el naranja
    # su cromaticidad coincidía con la del marrón de Ground y toda placa Ground
    # se leía como Fire.
    "Fire": (216, 24, 40),
    "Water": (45, 116, 227),
    "Electric": (244, 210, 60),
    "Grass": (99, 188, 90),
    "Ice": (115, 206, 192),
    "Fighting": (206, 65, 107),
    "Poison": (171, 106, 200),
    "Ground": (120, 72, 40),
    "Flying": (121, 169, 221),
    # Medido en dos capturas distintas del juego: (216,40,104) en grabación de
    # móvil y (224,0,112) en captura de PC, que satura más. El rosa de la paleta
    # web quedaba fuera de tolerancia en la segunda.
    "Psychic": (220, 20, 108),
    "Bug": (145, 193, 47),
    "Rock": (197, 183, 140),
    # Estas dos placas son bastante más oscuras/rojizas en Champions que en
    # la paleta web habitual de Pokémon.
    "Ghost": (72, 40, 72),
    "Dragon": (72, 88, 209),
    "Dark": (56, 40, 40),
    "Steel": (92, 151, 166),
    "Fairy": (224, 64, 224),
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


def looks_like_a_nickname(value: str) -> bool:
    """Si un texto de la tarjeta puede ser el mote o es otra cosa.

    La tarjeta dibuja el símbolo de género pegado al nombre, y el OCR lo
    devuelve como ’07’, ’37’ o ’f’. Los dos lectores del Team Preview se
    quedaban con él en cuanto el mote real no se leía en ese frame, y quedaba
    atado a la especie de esa fila: en batalla ese mismo símbolo volvía a
    aparecer en el HUD y ocupaba un slot.
    """

    stripped = value.strip()
    letters = sum(1 for character in stripped if character.isalpha())
    return letters >= 2 and len(stripped) >= 3


class ChampionsTeamPreviewResolver:
    """Lee el roster rival desde sprites y placas de tipo del Team Preview.

    Los nombres propios están disponibles por OCR, pero el selector rival sólo
    muestra imágenes. Primero reducimos cada fila por su par de tipos y sólo
    descargamos los sprites animados de Showdown cuando quedan dos o más
    candidatos. Todo ocurre en el mismo frame que ya recorrió el OCR.
    """

    # La rejilla se localiza en cada frame (ver _card_boxes), así que todos los
    # recortes van como proporción de la tarjeta detectada y no como fracción
    # del frame: así el lector no depende de la resolución ni del encuadre.
    _CARD_PRIMARY_TILE = (0.643, 0.791)
    _CARD_SECONDARY_TILE = (0.820, 0.961)
    _CARD_TYPE_BAND = (0.125, 0.512)
    _CARD_GENDER_BAND = (0.692, 0.842)
    _CARD_SPRITE_TILE = (0.100, 0.620)
    # El panel del jugador está al otro lado, es morado (verde lima cuando la
    # tarjeta está resaltada) y coloca el sprite pegado al borde derecho, con
    # el mote y el objeto como texto a la izquierda. No lleva placas de tipo.
    # El borde derecho se queda corto a propósito: el marco redondeado de la
    # tarjeta es más claro que el fondo y se colaba en la silueta.
    _PLAYER_SPRITE_TILE = (0.740, 0.970)
    # El mote va arriba a la izquierda de la tarjeta y el objeto justo debajo;
    # medidos en las dos capturas, el mote cae en y 0.24-0.30 y el objeto en
    # 0.70-0.74, así que la banda de arriba los separa sin ambigüedad.
    _PLAYER_NAME_TILE = (0.08, 0.60)
    _PLAYER_NAME_BAND = (0.10, 0.45)
    # El símbolo de género va entre el mote y el sprite. Medido en las dos
    # capturas: las hembras llenan un tercio del recuadro de rojo y los
    # machos nada, así que el rojo decide y el azul sólo confirma.
    _PLAYER_GENDER_TILE = (0.670, 0.745)
    _PLAYER_GENDER_BAND = (0.09, 0.40)
    _CARD_SPRITE_BAND = (0.010, 0.980)
    # El Team Preview dibuja el menu sprite del propio Champions: mismo arte,
    # misma pose, siempre igual. Comparar contra ese render identifica la especie
    # por sí solo; las placas de tipo quedan como verificación independiente.
    # Los sprites viajan con el repositorio (npm run data:champions-sprites), así
    # que la lectura es reproducible y no depende de la red.
    _SPRITE_MANIFEST_NAME = "champions-sprites.json"
    _COLOUR_BINS = 6
    _TILE_COVERAGE = 0.30
    # Devuelto cuando la casilla sí tiene placa pero su color no cuadra con
    # ningún tipo: distinto de no tener placa, porque significa que la lista
    # de tipos de esa fila está incompleta y no sirve para filtrar.
    UNKNOWN_TYPE = "?"
    # Lo que separa una tarjeta de la siguiente está vacío; el cuerpo de la
    # tarjeta puede estar tapado. Midiendo el cuerpo, un sprite grande partía
    # su propia franja en dos, y con el JPEG que el pipeline se fabrica para
    # analizar el relleno bajaba de 0,38 a 0,10 y ya no lo salvaba nada.
    _BAND_FILL = 0.05
    # Sin filtro por tipo hay cientos de candidatos. Se criban primero con
    # una silueta sin desplazamientos (barata) y sólo la lista corta pasa a
    # la comparación completa. El cribado no mira el color: un shiny tiene
    # que llegar igual a la comparación final.
    _SHORTLIST = 24
    # Cuánto adelgazar la máscara antes de elegir el trozo que es el sprite.
    # Con 0 los láseres de la arena se quedan pegados; con 2 se desprenden
    # pero un Pokémon estilizado se parte. No hay un valor bueno para todos,
    # así que se prueban los dos y decide la comparación.
    _THIN_STEPS = (0, 2)
    # La silueta manda y el color sólo desempata: un shiny conserva la forma
    # pero cambia los colores, y Champions no publica sprites shiny. Medido
    # sobre este vídeo, con estos pesos el acierto es 255/264 con colores
    # normales y 256/264 recoloreando el sprite para simular un shiny; con el
    # color pesando igual que la silueta, el shiny baja a 242/264.
    _SHAPE_WEIGHT = 0.75
    _COLOUR_WEIGHT = 0.15
    _ASPECT_WEIGHT = 0.10

    def __init__(
        self,
        species_types: Sequence[tuple[str, Sequence[str]]],
    ) -> None:
        self.species_types = tuple(
            (species, tuple(types))
            for species, types in species_types
            if species and types and "-Mega" not in species
        )
        self._template_lock = threading.Lock()
        self._templates: dict[str, tuple[_SpriteShape, ...]] = {}
        self._sprite_root, self._sprite_sources = self._load_manifest()
        self._shadowed = self._shadowed_species(self._sprite_sources)

    @staticmethod
    def _shadowed_species(sources: dict[str, tuple[str, ...]]) -> frozenset[str]:
        """Formas que Champions dibuja igual que otra: sólo compite una.

        Polteageist y su forma Antique comparten sprite, igual que Sinistcha y
        su Masterpiece. Tenerlas las dos entre los candidatos garantiza un
        empate y tira la fila, así que compite el nombre más corto, que es la
        forma base, y la otra se queda fuera.
        """

        by_files: dict[tuple[str, ...], list[str]] = {}
        for species, files in sources.items():
            by_files.setdefault(tuple(files), []).append(species)
        shadowed: set[str] = set()
        for names in by_files.values():
            if len(names) < 2:
                continue
            keeper = min(names, key=lambda name: (len(name), name))
            shadowed.update(name for name in names if name != keeper)
        return frozenset(shadowed)

    @classmethod
    def _manifest_candidates(cls) -> tuple[Path, ...]:
        module = Path(__file__).resolve()
        return (
            module.parents[3] / "public" / "data" / cls._SPRITE_MANIFEST_NAME,
            module.parent / "data" / cls._SPRITE_MANIFEST_NAME,
        )

    @classmethod
    def _load_manifest(cls) -> tuple[Path, dict[str, tuple[str, ...]]]:
        for candidate in cls._manifest_candidates():
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except ValueError as error:
                raise DetectionError(
                    f"El manifiesto de sprites de Champions no es válido: {error}"
                ) from error
            sprites = {
                species: tuple(names)
                for species, names in payload.get("sprites", {}).items()
                if names
            }
            if not sprites:
                raise DetectionError("El manifiesto de sprites de Champions está vacío.")
            root = candidate.with_name(payload.get("directory") or "champions-sprites")
            return root, sprites
        raise DetectionError(
            "Faltan los sprites de Champions. Genéralos con: npm run data:champions-sprites"
        )

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

    @classmethod
    def _card_boxes(
        cls, image: object, side: str = "p2"
    ) -> tuple[tuple[int, int, int, int], ...]:
        """Localiza las seis tarjetas rivales dentro del frame.

        El panel rival es un bloque carmesí de seis tarjetas idénticas en el
        lado derecho. Buscarlo en cada frame evita fijar fracciones que sólo
        valen para una resolución y un encuadre concretos, y hace que un frame
        en transición se descarte solo en vez de producir un roster inventado.
        """

        import numpy as np  # type: ignore[import-not-found]

        height, width = image.shape[:2]  # type: ignore[union-attr]
        channels = image.astype(float)  # type: ignore[union-attr]
        red, green, blue = channels[:, :, 0], channels[:, :, 1], channels[:, :, 2]
        if side == "p1":
            card = ((blue > 90) & (blue > green * 1.45) & (red > green * 1.10)) | (
                (green > 140) & (green > blue * 1.30) & (green > red * 1.05)
            )
            card[:, width // 2 :] = False
        else:
            card = (red > 55) & (red > green * 1.55) & (red > blue * 1.15)
            card[:, : width // 2] = False

        column = card.sum(axis=0)
        peak = float(column.max())
        if peak < height * 0.10:
            raise DetectionError(f"no se encontró el panel {side} del Team Preview")
        # Focos, láseres y reflejos también entran en el mapa de color, pero
        # son finos: el panel es el tramo contiguo de columnas más ancho.
        x1, x2 = cls._widest_run(column >= peak * 0.50)
        if x2 - x1 < width // 40:
            raise DetectionError(f"el panel {side} del Team Preview quedó demasiado estrecho")
        filled = card[:, x1:x2].sum(axis=1) / (x2 - x1)

        # Una fila cuenta como tarjeta mientras quede algo de carmesí. El umbral
        # mide el hueco entre tarjetas, que está vacío, no el cuerpo, que el
        # sprite tapa: medido sobre once frames de dos grabaciones, 0,05 es el
        # valor que más frames cierra en las dos.
        bands = cls._runs(filled >= cls._BAND_FILL, minimum=height // 40)
        if len(bands) < 6:
            raise DetectionError(
                f"el panel rival mostró {len(bands)} franjas en vez de seis tarjetas"
            )
        heights = sorted(bottom - top for top, bottom in bands)
        reference = heights[len(heights) // 2]
        if reference <= 0:
            raise DetectionError("no se pudo medir la altura de las tarjetas rivales")

        # Un láser sobre la separación funde dos tarjetas en una sola franja, y el
        # rótulo con el nombre del rival añade una franja más baja: repartimos las
        # fundidas y descartamos todo lo que no mida como una tarjeta.
        split: list[tuple[int, int]] = []
        for top, bottom in bands:
            pieces = max(1, round((bottom - top) / reference))
            step = (bottom - top) / pieces
            for index in range(pieces):
                split.append((round(top + index * step), round(top + (index + 1) * step)))
        cards = sorted(
            band
            for band in split
            if abs((band[1] - band[0]) - reference) <= reference * 0.25
        )
        if len(cards) < 6:
            raise DetectionError(
                f"sólo {len(cards)} tarjetas rivales miden lo mismo"
            )

        best: tuple[float, list[tuple[int, int]]] | None = None
        for index in range(len(cards) - 5):
            window = cards[index : index + 6]
            centers = [(top + bottom) / 2 for top, bottom in window]
            steps = [second - first for first, second in zip(centers, centers[1:])]
            spread = max(steps) - min(steps)
            if best is None or spread < best[0]:
                best = (spread, window)
        assert best is not None
        spread, window = best
        if spread > reference * 0.25:
            raise DetectionError("las tarjetas rivales no están equiespaciadas")

        # El mapa de color sólo cubre el fondo visible de la tarjeta: sprites,
        # placas y textos lo interrumpen, así que el ancho se mide aparte.
        if side == "p1":
            # La tarjeta del jugador lleva el sprite pegado al borde derecho, y
            # entre ella y el resto de la interfaz hay fondo oscuro: crecer
            # mientras siga iluminada llega hasta el borde real.
            lit = channels.max(axis=2) > 45
            profile = np.concatenate(
                [lit[top:bottom] for top, bottom in window], axis=0
            ).mean(axis=0)
        else:
            # En el panel rival no sirve "lo que esté iluminado": el juego pinta
            # una banda verde de ventaja pegada a la tarjeta y se la tragaba. Su
            # franja superior, en cambio, está limpia de punta a punta.
            strips = []
            for top, bottom in window:
                edge = max(1, round((bottom - top) * 0.12))
                strips.append(card[top : top + edge])
            profile = np.concatenate(strips, axis=0).mean(axis=0)
        left, right = x1, x2
        while left > 0 and profile[left - 1] >= 0.60:
            left -= 1
        while right < width and profile[right] >= 0.60:
            right += 1
        return tuple((left, right, top, bottom) for top, bottom in window)

    @staticmethod
    def _runs(flags: object, *, minimum: int = 1) -> list[tuple[int, int]]:
        """Tramos contiguos de True, descartando los más cortos que `minimum`."""

        runs: list[tuple[int, int]] = []
        start: int | None = None
        for index, value in enumerate(flags):  # type: ignore[call-overload]
            if value and start is None:
                start = index
            elif not value and start is not None:
                if index - start >= minimum:
                    runs.append((start, index))
                start = None
        if start is not None and len(flags) - start >= minimum:  # type: ignore[arg-type]
            runs.append((start, len(flags)))  # type: ignore[arg-type]
        return runs

    @classmethod
    def _widest_run(cls, flags: object) -> tuple[int, int]:
        runs = cls._runs(flags)
        if not runs:
            raise DetectionError("no se encontró el panel rival del Team Preview")
        return max(runs, key=lambda run: run[1] - run[0])

    @staticmethod
    def _box(
        card: tuple[int, int, int, int],
        horizontal: tuple[float, float],
        vertical: tuple[float, float],
    ) -> tuple[int, int, int, int]:
        """Traduce proporciones de la tarjeta a un recorte en píxeles."""

        x1, x2, y1, y2 = card
        card_width, card_height = x2 - x1, y2 - y1
        left = x1 + round(card_width * horizontal[0])
        right = max(left + 1, x1 + round(card_width * horizontal[1]))
        top = y1 + round(card_height * vertical[0])
        bottom = max(top + 1, y1 + round(card_height * vertical[1]))
        return left, right, top, bottom

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
        box: tuple[int, int, int, int],
    ) -> str | None:
        import numpy as np  # type: ignore[import-not-found]

        height, width = image.shape[:2]  # type: ignore[union-attr]
        x1, x2, y1, y2 = box
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

        coverage = float(
            (np.linalg.norm(crop.astype(float) - background, axis=2) > 30).mean()
        )
        if coverage < cls._TILE_COVERAGE:
            return None

        ranked: list[tuple[float, tuple[int, int, int]]] = []
        for raw_color, count in Counter(map(tuple, ((crop.reshape(-1, 3) // 16) * 16))).items():
            color = np.asarray(raw_color, dtype=float) + 8
            maximum = float(color.max())
            minimum = float(color.min())
            saturation = (maximum - minimum) / max(1.0, maximum)
            separation = float(np.linalg.norm(color - background))
            # Descarta el fondo de la tarjeta y el glifo blanco de la placa. El
            # umbral de brillo va alto a propósito: la placa de Normal es gris
            # medio y con un corte más bajo se descartaba junto con el glifo.
            if maximum < 24 or separation < 28 or (saturation < 0.20 and maximum > 200):
                continue
            score = (
                count
                * (0.30 + 0.70 * saturation)
                * separation
                * math.sqrt(maximum / max(20.0, float(background.max())))
            )
            ranked.append((score, tuple(int(value) for value in color)))
        if not ranked:
            return cls.UNKNOWN_TYPE

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
            # Hay placa, pero su color no cuadra: cada captura satura distinto y
            # preferimos decirlo a inventar un tipo o a fingir que no hay placa.
            return cls.UNKNOWN_TYPE
        return pokemon_type

    def _every_candidate(self) -> tuple[str, ...]:
        # Las formas macho/hembra son casi idénticas a este tamaño y sólo se
        # roban votos entre ellas. Compite la base y el símbolo de género de la
        # tarjeta decide la forma después (ver _gendered_variant).
        return tuple(
            species
            for species, _types in self.species_types
            if species in self._sprite_sources
            and species not in self._shadowed
            and not species.endswith(("-M", "-F"))
        )

    def _candidates_for_types(self, types: Sequence[str]) -> tuple[str, ...]:
        expected = tuple(types)
        return tuple(
            species
            for species, candidate_types in self.species_types
            if len(candidate_types) == len(expected)
            and set(candidate_types) == set(expected)
            and species not in self._shadowed
        )

    @classmethod
    def _player_gender(cls, image: object, box: tuple[int, int, int, int]) -> str | None:
        """Género en la tarjeta del jugador, cuyo fondo morado es azulado.

        No sirve el lector del panel rival: sobre morado, "predomina el azul"
        se cumple en toda la tarjeta.
        """

        x1, x2, y1, y2 = box
        crop = image[y1:y2, x1:x2]  # type: ignore[index]
        if not crop.size:
            return None
        red = crop[:, :, 0].astype(float)
        green = crop[:, :, 1].astype(float)
        blue = crop[:, :, 2].astype(float)
        if float(((red > 120) & (red > green * 2.0) & (red > blue * 2.0)).mean()) >= 0.15:
            return "F"
        if float(((blue > 120) & (blue > red * 2.0) & (green < blue * 0.8)).mean()) >= 0.20:
            return "M"
        return None

    def _gendered_variant(self, species: str, gender: str | None) -> str:
        """Ajusta la forma macho/hembra cuando el juego enseña el símbolo."""

        if gender not in {"M", "F"}:
            return species
        base = species[:-2] if species.endswith(("-M", "-F")) else species
        wanted = base if gender == "M" else f"{base}-F"
        if wanted == species:
            return species
        known = {name for name, _types in self.species_types}
        return wanted if wanted in known else species

    @classmethod
    def _gender(cls, image: object, box: tuple[int, int, int, int]) -> str | None:
        import numpy as np  # type: ignore[import-not-found]

        x1, x2, y1, y2 = box
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

    @classmethod
    def _colour_histogram(cls, image: object, mask: object) -> object:
        """Color del sprite, normalizado en brillo y sin depender de la pose."""

        import numpy as np  # type: ignore[import-not-found]

        pixels = image[mask]  # type: ignore[index]
        bins = cls._COLOUR_BINS
        if len(pixels) < 40:
            return np.zeros(bins ** 3, dtype=float)
        pixels = pixels.astype(float)
        # La iluminación de la arena apaga el render; igualamos el nivel alto.
        reference = max(1.0, float(np.percentile(pixels, 85)))
        pixels = np.clip(pixels * (165.0 / reference), 0, 255)
        index = np.minimum((pixels / (256 / bins)).astype(int), bins - 1)
        flat = index[:, 0] * bins * bins + index[:, 1] * bins + index[:, 2]
        histogram = np.bincount(flat, minlength=bins ** 3).astype(float)
        total = histogram.sum()
        return histogram / total if total else histogram

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
    def _normalize_mask(
        cls, mask: object, image: object | None = None
    ) -> _SpriteShape | None:
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
        # Estiramos a un cuadro fijo: Champions encuadra el modelo a su manera,
        # así que la forma interna distingue mejor que el tamaño relativo. La
        # proporción original se conserva aparte y pesa en la puntuación.
        target_width = target_height = 56
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
        colour = (
            cls._colour_histogram(image, mask)
            if image is not None
            else np.zeros(cls._COLOUR_BINS ** 3, dtype=float)
        )
        return _SpriteShape(normalized, source_width / max(1, source_height), colour)

    @staticmethod
    def _card_background(image: object, card: tuple[int, int, int, int]) -> object:
        """Color de la tarjeta, medido en su franja superior sin sprite ni placas."""

        import numpy as np  # type: ignore[import-not-found]

        # Color más repetido del recorte, no su mediana ni sus bordes: el fondo
        # de la tarjeta tiene degradado, el sprite ocupa una minoría y por los
        # bordes se cuelan la flecha de ventaja o el texto del mote.
        x1, x2, y1, y2 = card
        binned = ((image[y1:y2, x1:x2].reshape(-1, 3) // 16) * 16)  # type: ignore[index]
        dominant, _count = Counter(map(tuple, binned)).most_common(1)[0]
        return np.asarray(dominant, dtype=float) + 8

    @classmethod
    def _observed_shapes(
        cls,
        image: object,
        box: tuple[int, int, int, int],
        background: object,
    ) -> tuple[_SpriteShape, ...]:
        """Lecturas del sprite con cada nivel de adelgazado, sin repetidas."""

        shapes = []
        for steps in cls._THIN_STEPS:
            shape = cls._observed_shape(image, box, background, steps)
            if shape is not None:
                shapes.append(shape)
        return tuple(shapes)

    @classmethod
    def _observed_shape(
        cls,
        image: object,
        box: tuple[int, int, int, int],
        background: object,
        steps: int = 0,
    ) -> _SpriteShape | None:
        import numpy as np  # type: ignore[import-not-found]

        x1, x2, y1, y2 = box
        crop = image[y1:y2, x1:x2]  # type: ignore[index]
        if not crop.size:
            return None
        # El carmesí de la tarjeta es un velo: la arquitectura de la arena se ve
        # por detrás. Eso cambia el brillo del fondo, no su color, pero medir la
        # distancia a secas metía esa arquitectura en la silueta y el sprite se
        # emparejaba con otra especie. Proyectar sobre el fondo y quedarse con lo
        # que sobra la deja fuera sin tocar al sprite, que sí cambia de color.
        pixels = crop.astype(float)
        scale = (pixels @ background) / float(background @ background)
        residual = pixels - scale[..., None] * background
        mask = np.linalg.norm(residual, axis=2) > 40

        best: tuple[float, tuple[tuple[int, int], ...]] | None = None
        crop_height, crop_width = mask.shape
        # Los láseres de la arena llegan a tocar al sprite y lo convierten en un
        # único borrón. Adelgazar antes de etiquetar los desprende.
        core = cls._thin(mask, steps) if steps else mask
        if not core.any():  # type: ignore[union-attr]
            core = mask
        for points in cls._components(core):
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
            # Los láseres de la arena cruzan la tarjeta y pueden ser el trozo más
            # grande del recorte. Un sprite llena buena parte de su caja; una
            # raya diagonal, casi nada.
            fill = len(points) / max(1, component_width * component_height)
            if fill < 0.22:
                continue
            score = (
                len(points)
                * fill
                * (1 - abs(center_x - crop_width / 2) / max(1, crop_width * 1.25))
            )
            if best is None or score > best[0]:
                best = (score, points)
        if best is None:
            return None
        selected = np.zeros_like(mask)
        for y, x in best[1]:
            selected[y, x] = True
        if steps:
            selected = cls._thicken(selected, steps) & mask
        return cls._normalize_mask(selected, crop)

    def _sprite_paths(self, species: str) -> tuple[Path, ...]:
        """Archivos del repositorio con los sprites de una especie."""

        names = self._sprite_sources.get(species)
        if not names:
            raise DetectionError(f"Champions no tiene sprite para {species}.")
        paths = []
        for name in names:
            if "/" in name or "\\" in name or name.startswith("."):
                raise DetectionError(f"Nombre de sprite inválido para {species}: {name}")
            path = self._sprite_root / name
            if not (path.is_file() and path.stat().st_size):
                raise DetectionError(
                    f"Falta el sprite {name} de {species}; "
                    "regenéralos con: npm run data:champions-sprites"
                )
            paths.append(path)
        return tuple(paths)

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
        shapes: list[_SpriteShape] = []
        for path in self._sprite_paths(species):
            try:
                rgba = np.asarray(Image.open(path).convert("RGBA"))
                shape = self._normalize_mask(rgba[:, :, 3] > 40, rgba[:, :, :3])
            except Exception as error:
                raise DetectionError(
                    f"El sprite {path.name} de {species} no es válido: {error}"
                ) from error
            if shape is not None:
                shapes.append(shape)
        if not shapes:
            raise DetectionError(f"El sprite de {species} no contiene siluetas utilizables.")
        result = tuple(shapes)
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

    @staticmethod
    def _coarse_score(observed: _SpriteShape, template: _SpriteShape) -> float:
        """Solapamiento sin buscar desplazamientos, para cribar rápido."""

        intersection = int((observed.mask & template.mask).sum())  # type: ignore[operator]
        union = int((observed.mask | template.mask).sum())  # type: ignore[operator]
        return intersection / max(1, union)

    @classmethod
    def _thin(cls, mask: object, steps: int) -> object:
        """Erosiona la máscara para cortar las rayas finas pegadas al sprite."""

        thinned = mask
        for _ in range(steps):
            eroded = thinned
            for delta_y, delta_x in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                eroded = eroded & cls._shift(thinned, delta_y, delta_x)  # type: ignore[operator]
            thinned = eroded
        return thinned

    @classmethod
    def _thicken(cls, mask: object, steps: int) -> object:
        thickened = mask
        for _ in range(steps):
            grown = thickened
            for delta_y, delta_x in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                grown = grown | cls._shift(thickened, delta_y, delta_x)  # type: ignore[operator]
            thickened = grown
        return thickened

    @classmethod
    def _shape_score(cls, observed: _SpriteShape, template: _SpriteShape) -> float:
        """Parecido entre el sprite leído y el render de referencia.

        Champions encuadra el modelo a su manera y a menudo lo muestra girado
        respecto a HOME, así que probamos también la referencia espejada. El
        color desempata lo que la silueta no puede, como Sableye y Spiritomb.
        """

        import numpy as np  # type: ignore[import-not-found]

        overlap = 0.0
        for delta_y in range(-2, 3):
            for delta_x in range(-2, 3):
                shifted = cls._shift(template.mask, delta_y, delta_x)
                intersection = int((observed.mask & shifted).sum())  # type: ignore[operator]
                union = int((observed.mask | shifted).sum())  # type: ignore[operator]
                overlap = max(overlap, intersection / max(1, union))
        colour = float(np.minimum(observed.colour, template.colour).sum())
        aspect = max(
            0.0,
            1
            - abs(observed.aspect_ratio - template.aspect_ratio)
            / max(observed.aspect_ratio, template.aspect_ratio),
        )
        return (
            cls._SHAPE_WEIGHT * overlap
            + cls._COLOUR_WEIGHT * colour
            + cls._ASPECT_WEIGHT * aspect
        )

    def _templates_for(
        self,
        species_names: Sequence[str] | set[str],
        loaded: dict[str, tuple[_SpriteShape, ...]],
    ) -> dict[str, tuple[_SpriteShape, ...]]:
        """Carga las referencias que falten, sin tumbar la fila si alguna no está."""

        pending = tuple(name for name in species_names if name not in loaded)
        if not pending:
            return loaded

        def safe(species: str) -> tuple[_SpriteShape, ...]:
            try:
                return self._sprite_templates(species)
            except DetectionError:
                return ()

        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
            for species, templates in zip(pending, executor.map(safe, pending), strict=True):
                loaded[species] = templates
        return loaded

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
        # El género decide entre las dos formas de una especie, no entre
        # especies. Con más de un candidato en juego, que Indeedee sea el único
        # con pareja macho y hembra bastaba para quedarse la fila sin mirar el
        # sprite: Farigiraf, que la ganaba por silueta 0,79 contra 0,46, ni
        # llegaba a puntuarse. Manda la silueta y el género se aplica después.
        if len(by_base) != 1:
            return None
        matches = [values[gender] for values in by_base.values() if gender in values and {"M", "F"} <= values.keys()]
        return matches[0] if len(matches) == 1 else None

    def resolve(
        self,
        frame: FramePacket,
        *,
        rotation_degrees: int = 0,
        side: str = "p2",
    ) -> tuple[str, ...]:
        """Los seis Pokémon de un panel, o un error si alguna fila no se leyó."""

        rows = self.resolve_rows(frame, rotation_degrees=rotation_degrees, side=side)
        missing = [number for number, species in enumerate(rows, start=1) if not species]
        if missing:
            raise DetectionError(
                f"no se identificaron las filas {missing} del panel {side}"
            )
        return tuple(species for species in rows if species)

    def resolve_rows(
        self,
        frame: FramePacket,
        *,
        rotation_degrees: int = 0,
        side: str = "p2",
    ) -> tuple[str | None, ...]:
        """Lee fila a fila, dejando vacía la que no quede clara.

        El panel rival trae placas de tipo que sirven de verificación; el del
        jugador no, así que ahí decide el sprite contra el catálogo entero. Una
        fila dudosa se deja sin resolver en vez de arrastrar a las demás.
        """

        image = self._decode_frame(frame, rotation_degrees)
        cards = self._card_boxes(image, side)
        return self._species_for_cards(image, cards, side)

    def resolve_labelled_rows(
        self,
        frame: FramePacket,
        lines: Sequence[object] = (),
        *,
        rotation_degrees: int = 0,
        side: str = "p1",
    ) -> tuple[tuple[str | None, str | None], ...]:
        """Especie y mote de cada fila, leídos de la misma tarjeta.

        El panel del jugador escribe el mote junto al sprite, así que la propia
        pantalla dice qué mote es qué especie. Eso evita tener que deducirlo
        después, que es de donde salen las atribuciones cruzadas cuando los dos
        entrenadores llevan la misma especie.
        """

        image = self._decode_frame(frame, rotation_degrees)
        cards = self._card_boxes(image, side)
        species = self._species_for_cards(image, cards, side)
        names = self._labels_for_cards(image, cards, lines)
        return tuple(zip(species, names, strict=True))

    @classmethod
    def _labels_for_cards(
        cls,
        image: object,
        cards: Sequence[tuple[int, int, int, int]],
        lines: Sequence[object],
    ) -> tuple[str | None, ...]:
        height, width = image.shape[:2]  # type: ignore[union-attr]
        labels: list[str | None] = []
        for card in cards:
            x1, x2, y1, y2 = cls._box(card, cls._PLAYER_NAME_TILE, cls._PLAYER_NAME_BAND)
            best: str | None = None
            for line in lines:
                text = (getattr(line, "text", "") or "").strip()
                if not looks_like_a_nickname(text):
                    continue
                centre_x = (line.left + line.right) / 2 * width  # type: ignore[attr-defined]
                centre_y = (line.top + line.bottom) / 2 * height  # type: ignore[attr-defined]
                if not (x1 <= centre_x <= x2 and y1 <= centre_y <= y2):
                    continue
                if best is None or len(text) > len(best):
                    best = text
            labels.append(best)
        return tuple(labels)

    def _species_for_cards(
        self,
        image: object,
        cards: Sequence[tuple[int, int, int, int]],
        side: str,
    ) -> tuple[str | None, ...]:
        sprite_tile = self._PLAYER_SPRITE_TILE if side == "p1" else self._CARD_SPRITE_TILE
        candidates_by_row: list[tuple[str, ...]] = []
        genders: list[str | None] = []
        for row_number, card in enumerate(cards, start=1):
            if side == "p1":
                candidates_by_row.append(self._every_candidate())
                genders.append(
                    self._player_gender(
                        image,
                        self._box(
                            card, self._PLAYER_GENDER_TILE, self._PLAYER_GENDER_BAND
                        ),
                    )
                )
                continue
            primary = self._type_from_tile(
                image,
                box=self._box(card, self._CARD_PRIMARY_TILE, self._CARD_TYPE_BAND),
            )
            secondary = self._type_from_tile(
                image,
                box=self._box(card, self._CARD_SECONDARY_TILE, self._CARD_TYPE_BAND),
            )
            read = tuple(value for value in (primary, secondary) if value)
            if self.UNKNOWN_TYPE in read or not read:
                # El sprite identifica por sí solo; las placas sólo confirman. Si
                # alguna no se pudo leer, la lista de tipos estaría incompleta y
                # dejaría fuera a la especie correcta, así que no se filtra.
                candidates = self._every_candidate()
            else:
                candidates = self._candidates_for_types(read)
            if not candidates:
                raise DetectionError(
                    f"ningún Pokémon de Champions encaja con la fila {side} {row_number}"
                )
            candidates_by_row.append(candidates)
            genders.append(
                self._gender(
                    image,
                    self._box(card, self._CARD_PRIMARY_TILE, self._CARD_GENDER_BAND),
                )
            )

        required_templates = {
            species
            for candidates, gender in zip(candidates_by_row, genders, strict=True)
            if len(candidates) > 1 and self._gendered_candidate(candidates, gender) is None
            for species in candidates
        }
        loaded = self._templates_for(required_templates, {})

        roster: list[str | None] = []
        for row_number, (card, candidates, gender) in enumerate(
            zip(cards, candidates_by_row, genders, strict=True),
            start=1,
        ):
            if len(candidates) == 1:
                roster.append(candidates[0])
                continue
            gendered = self._gendered_candidate(candidates, gender)
            if gendered:
                roster.append(gendered)
                continue
            readings = self._observed_shapes(
                image,
                self._box(card, sprite_tile, self._CARD_SPRITE_BAND),
                self._card_background(
                    image, self._box(card, sprite_tile, self._CARD_SPRITE_BAND)
                ),
            )
            if not readings:
                # Una fila dudosa ya no tumba el panel entero: se deja vacía y
                # la votación entre frames del detector la resuelve aparte.
                roster.append(None)
                continue
            loaded = self._templates_for(candidates, loaded)
            comparable = [species for species in candidates if loaded.get(species)]
            if not comparable:
                roster.append(None)
                continue
            if len(comparable) > self._SHORTLIST:
                ranked_coarse = sorted(
                    (
                        max(
                            self._coarse_score(reading, template)
                            for reading in readings
                            for template in loaded[species]
                        ),
                        species,
                    )
                    for species in comparable
                )
                comparable = [species for _score, species in ranked_coarse[-self._SHORTLIST :]]
            # Cada lectura del sprite se puntúa por separado y nos quedamos con
            # la que decide con más holgura: comparar puntuaciones de recortes
            # distintos no dice nada, pero su margen sí mide cuánto se fía.
            decisions = []
            for reading in readings:
                scores = sorted(
                    (
                        max(self._shape_score(reading, template) for template in loaded[species]),
                        species,
                    )
                    for species in comparable
                )
                top = scores[-1]
                second = scores[-2][0] if len(scores) > 1 else 0.0
                decisions.append((top[0] - second, top[0], top[1]))
            margin_gap, best_score, best_species = max(decisions)
            runner_up = best_score - margin_gap
            # Medido sobre este vídeo: los aciertos tienen margen mediano 0.27 y
            # el p10 en 0.074; los errores nunca pasaron de 0.051. Se descarta la
            # duda en vez de inventar y la votación entre frames del detector
            # decide con las lecturas que sí pasan.
            if best_score < 0.33 or best_score - runner_up < 0.055:
                roster.append(None)
                continue
            roster.append(self._gendered_variant(best_species, gender))

        if len(roster) != 6:
            raise DetectionError(f"el Team Preview de {side} no tiene seis filas")
        named = [species for species in roster if species]
        if len({_text_id(species) for species in named}) != len(named):
            raise DetectionError(f"el Team Preview de {side} repitió alguna especie")
        return tuple(roster)
