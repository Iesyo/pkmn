"""Avisos: cada texto del juego una sola vez, con su mejor lectura.

El cuadro de texto del juego muestra cada aviso durante varios frames y el OCR
lo relee en cada uno, a veces con una letra de más o de menos ("Speed fell!",
"Speed fel!"), cortado mientras se borra ("The battle has en") o sin su
comienzo mientras el cuadro entra o sale con un fundido ("used Zap Cannon!").
Leído frame a frame, cada variante parecía un aviso nuevo y el replay lo
repetía.

Con la traza completa se ve el aviso entero: los frames seguidos en que el
cuadro muestra lecturas casi iguales son el mismo aviso, y de todas sus
lecturas se elige la que mejor encaja con lo que se leyó en el resto de la
traza. El aviso sigue ocupando su lugar, el de su primer frame; sólo cambia qué
texto se lee en él.

Calibrado con tres trazas reales (COL-102): las relecturas de un mismo aviso
se parecen entre 0,94 y 0,99, y dos avisos distintos seguidos, como mucho 0,76.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from typing import Hashable, Iterable, Mapping, Sequence

SAME_NOTICE = 0.9
# Un corte más corto que esto no dice lo bastante como para atarlo a nada.
MIN_PREFIX = 8
_WORD = re.compile(r"[^\W_]+")


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _words(value: str) -> list[str]:
    return _WORD.findall(value.casefold())


def _evidence(words: Sequence[str], frequency: Mapping[str, int]) -> float:
    if not words:
        return 0.0
    return sum(math.log(frequency[word]) for word in words) / len(words)


@dataclass(slots=True)
class _Notice:
    readings: list[tuple[Hashable, str, str]] = field(default_factory=list)

    @property
    def last_key(self) -> str:
        return self.readings[-1][1]

    def best_text(self, frequency: Mapping[str, int]) -> str:
        """La lectura cuyas palabras más se repiten en el resto de la traza.

        Una palabra bien leída vuelve a salir en muchos avisos; la que el OCR
        estropea, casi nunca. En inglés el OCR suele perder letras ("fel",
        "itef") y en motes japoneses suele añadirlas ("せんtせL)"), así que ni
        la lectura más larga ni la más repetida en el propio aviso sirven para
        las dos. Cada par de lecturas se compara sólo en las palabras en que
        difieren: con la frase entera, "The battle has en" ganaba a la
        completa por tener menos palabras raras.
        """

        texts = Counter(text for _frame, _key_value, text in self.readings)
        candidates = list(texts)
        if len(candidates) == 1:
            return candidates[0]
        wins: Counter[str] = Counter()
        for first, second in combinations(candidates, 2):
            first_words, second_words = Counter(_words(first)), Counter(_words(second))
            first_only = _evidence(list((first_words - second_words).elements()), frequency)
            second_only = _evidence(list((second_words - first_words).elements()), frequency)
            if first_only > second_only:
                wins[first] += 1
            elif second_only > first_only:
                wins[second] += 1
        keys = Counter(_key(text) for text in texts.elements())
        return max(
            candidates,
            key=lambda text: (wins[text], len(_key(text)), keys[_key(text)], texts[text]),
        )


def _starts_cut(text: str) -> bool:
    """Una lectura a la que le falta el comienzo.

    Las frases del juego empiezan siempre en mayúscula: un mote, "The
    opposing…", "It's…". Mientras el cuadro entra o sale con un fundido, el
    comienzo puede quedar ilegible sobre el sprite de detrás y el OCR lee sólo
    el resto ("used Zap Cannon!", "and Sp. Def fell!").
    """

    first = next((character for character in text if character.isalpha()), "")
    return first.islower()


def _same_notice(key: str, text: str, notice: _Notice) -> bool:
    last = notice.last_key
    if key == last:
        return True
    shorter, longer = sorted((key, last), key=len)
    if len(shorter) >= MIN_PREFIX and longer.startswith(shorter):
        return True
    # Sólo se ata por el final una lectura que sin duda perdió su comienzo. Una
    # frase entera puede ser el final de otra distinta ("Raichu protected
    # itself!" y "The opposing Raichu protected itself!" en un espejo).
    shorter_text = text if shorter == key else notice.readings[-1][2]
    if len(shorter) >= MIN_PREFIX and longer.endswith(shorter) and _starts_cut(shorter_text):
        return True
    return SequenceMatcher(None, key, last).ratio() >= SAME_NOTICE


def notice_readings(
    frames: Iterable[tuple[Hashable, Sequence[str]]],
) -> dict[tuple[Hashable, str], str]:
    """Mapa (frame, lectura) -> mejor lectura de su aviso.

    `frames` va en orden y trae, por frame, las líneas que el parser tomó
    como mensajes. Un frame sin mensajes cierra los avisos abiertos, así que
    un mismo texto que vuelve a salir más tarde es un aviso nuevo. Sólo
    aparecen en el mapa las lecturas que no eran ya la mejor de su aviso.
    """

    frames = list(frames)
    frequency = Counter(
        word for _frame, texts in frames for text in texts for word in _words(text)
    )
    notices: list[_Notice] = []
    open_notices: list[_Notice] = []
    for frame, texts in frames:
        continued: list[_Notice] = []
        for text in texts:
            key = _key(text)
            if not key:
                continue
            notice = next(
                (
                    candidate
                    for candidate in open_notices
                    if candidate not in continued and _same_notice(key, text, candidate)
                ),
                None,
            )
            if notice is None:
                notice = _Notice()
                notices.append(notice)
            notice.readings.append((frame, key, text))
            continued.append(notice)
        open_notices = continued

    corrected: dict[tuple[Hashable, str], str] = {}
    for notice in notices:
        best = notice.best_text(frequency)
        for frame, key, text in notice.readings:
            if text != best:
                corrected[(frame, key)] = best
    return corrected
