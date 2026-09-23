"""Capa de armado: decidir con la batalla entera a la vista.

La capa de lectura convierte cada frame en observaciones (avisos del cuadro de
texto, banners laterales, placas del HUD). Leídas frame a frame, algunas
decisiones tenían que esperar: el juego anuncia una entrada ("sent out
Pelipper!") muchos segundos antes de que el HUD diga en qué slot cayó. En la
segunda fase del job la traza ya está completa, así que en vez de esperar se
puede mirar: esta vista guarda, por batalla, lo que el HUD confirmó en cada
frame y dónde empieza cada turno.

Sólo existe en la segunda fase. En vivo no hay futuro que mirar y el parser
sigue esperando como antes.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class HudFrame:
    """Lo que el HUD confirmó en un frame: slot -> Pokémon, y si abrió turno."""

    frame: int
    slots: Mapping[str, str]
    opens_turn: bool


class BattleView:
    """Lo que el HUD confirmó a lo largo de una batalla, en orden."""

    def __init__(self, frames: Sequence[HudFrame]) -> None:
        self._frames = sorted(frames, key=lambda item: item.frame)
        self._indexes = [item.frame for item in self._frames]

    def slot_confirmed(
        self,
        side: str,
        is_pokemon: Callable[[str], bool],
        *,
        from_frame: int,
    ) -> str | None:
        """El slot donde el HUD confirma a ese Pokémon, sin pasar del turno.

        Un Pokémon que entra a mitad de turno (un relevo) o al final (tras un
        debilitado) está en el campo, como tarde, cuando vuelve el menú del
        turno siguiente, que es cuando el HUD se deja leer entero. Más allá
        podría ser otra entrada del mismo Pokémon, así que ahí se deja de
        mirar. Si en ese tramo no aparece en un único slot, no hay respuesta.
        """

        for hud in self._frames[bisect_left(self._indexes, from_frame):]:
            matches = [
                slot
                for slot, pokemon in hud.slots.items()
                if slot.startswith(side) and is_pokemon(pokemon)
            ]
            if len(matches) == 1:
                return matches[0]
            if hud.opens_turn:
                return None
        return None
