from __future__ import annotations

import unittest

from pkmn_vgc.champions_replay.notices import notice_readings


def frames(*texts: str | None) -> list[tuple[int, tuple[str, ...]]]:
    """Un frame por texto; None es un frame sin mensaje en el cuadro."""

    return [(index, () if text is None else (text,)) for index, text in enumerate(texts)]


def corrected(read: list[tuple[int, tuple[str, ...]]]) -> dict[int, str]:
    """Frame -> lectura que pasa a leerse en él."""

    return {frame: best for (frame, _key), best in notice_readings(read).items()}


class ChampionsNoticeTests(unittest.TestCase):
    # Lecturas reales de ocr.trace.jsonl (COL-102). Cada caso lleva algo del
    # resto de la batalla alrededor, porque la mejor lectura se decide por
    # cuánto se repiten sus palabras en la traza entera.

    def test_a_reread_that_drops_a_letter_is_the_same_notice(self) -> None:
        # Job 82923f56ce264a92, Partida 2, frames 1571-1573: el OCR relee
        # el mismo aviso con "fel!" y el replay lo escribía dos veces.
        read = frames(
            "Charizard's Attack fell!",
            "Charizard's Attack fell!",
            None,
            "The opposing Whimsicott's Speed fell!",
            "The opposing Whimsicott's Speed fel!",
            "The opposing Whimsicott's Speed fel!",
            None,
            "The opposing Garchomp's Speed fell!",
        )

        self.assertEqual(
            corrected(read),
            {
                4: "The opposing Whimsicott's Speed fell!",
                5: "The opposing Whimsicott's Speed fell!",
            },
        )

    def test_a_notice_cut_while_it_fades_keeps_its_full_reading(self) -> None:
        # Job 82923f56ce264a92, Partida 1, frame 1226.
        read = frames(
            "The battle has ended due to a forfeit.",
            "The battle has ended due to a forfeit.",
            "The battle has en",
        )

        self.assertEqual(corrected(read), {2: "The battle has ended due to a forfeit."})

    def test_two_different_notices_in_a_row_stay_apart(self) -> None:
        # Job 82923f56ce264a92, Partida 2, frames 1901-1902 y 2396-2397: sin
        # ningún frame vacío entre medio, pero se parecen como mucho 0,76.
        read = frames(
            "The opposing Charizard used Solar Beam!",
            "The opposing Charizard absorbed light!",
            None,
            "Mate avoided the attack!",
            "Charizard avoided the attack!",
        )

        self.assertEqual(corrected(read), {})

    def test_a_blank_frame_ends_the_notice(self) -> None:
        # El mismo aviso que vuelve a salir tras un frame vacío es otro aviso
        # (el mismo texto en otro turno); ni se une ni se corrige.
        read = frames(
            "The opposing Whimsicott's Speed fel!",
            None,
            "The opposing Whimsicott's Speed fell!",
        )

        self.assertEqual(corrected(read), {})

    def test_a_nickname_the_ocr_pads_with_noise_keeps_its_clean_reading(self) -> None:
        # Job 18241f89f82c4e83: en motes japoneses el OCR añade caracteres
        # ("せんtせL)"), así que quedarse con la lectura más larga elegía la
        # peor. Gana la que coincide con cómo se leyó el mote en otros avisos.
        read = frames(
            "The opposing せんせL) used Bullet Punch!",
            None,
            "The opposing せんtせL) used Psychic Fangs!",
            "The opposing せんせL) used Psychic Fangs!",
            None,
            "The opposing せんせL) used Earthquake!",
        )

        self.assertEqual(corrected(read), {2: "The opposing せんせL) used Psychic Fangs!"})

    def test_a_reading_glued_to_its_prefix_loses_to_the_spaced_one(self) -> None:
        # Job 18241f89f82c4e83, frames 503-506: "The opposingしごでき" sin
        # espacio sale tres veces y el parser no reconocía al rival; la
        # lectura con espacio, aunque pierda una letra del mote, sí lo nombra.
        read = frames(
            "The opposing Metagross used Psychic Fangs!",
            None,
            "The opposing Metagross used Bullet Punch!",
            None,
            "The opposing しでき used Rain Dance!",
            None,
            "The opposingしごでき used Encore!",
            "The opposingしごでき used Encore!",
            "The opposingしごでき used Encore!",
            "The opposing しでき used Encore!",
            None,
            "The opposing しでき's Roseli Berry lessened the damage it took!",
        )

        self.assertEqual(
            corrected(read),
            {index: "The opposing しでき used Encore!" for index in (6, 7, 8)},
        )


if __name__ == "__main__":
    unittest.main()
