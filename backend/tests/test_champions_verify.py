from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pkmn_vgc.champions_replay.models import BattleEvent
from pkmn_vgc.champions_replay.showdown import _event_lines, _named_species
from pkmn_vgc.champions_replay.verify import verify_replay


def _trace(directory: Path, records: list[dict]) -> Path:
    path = directory / "ocr.trace.jsonl"
    path.write_text(
        "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
        encoding="utf-8",
    )
    return path


def _frame(number: int, texts: list[str], aliases: dict[str, str] | None = None) -> dict:
    return {
        "frame": number,
        "battle_index": 0,
        "ocr": [{"text": text, "confidence": 0.99} for text in texts],
        "resolved_aliases": {"p2": aliases or {}},
    }


def _log(directory: Path, lines: list[str]) -> Path:
    path = directory / "replay-001.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class VerifyReplayTests(unittest.TestCase):
    def test_a_faithful_replay_reports_no_differences(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(
                directory,
                [
                    _frame(1, ["The opposing Sensei used Psychic Fangs!"], {"sensei": "Metagross"}),
                    _frame(2, ["The opposing Sensei used Psychic Fangs!"], {"sensei": "Metagross"}),
                    _frame(9, ["Tonatiuh fainted!"], {"sensei": "Metagross"}),
                ],
            )
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Psychic Fangs|",
                    "|faint|p1a: Tonatiuh",
                ],
            )

            report = verify_replay(trace, log)

        self.assertTrue(report.faithful, report)
        self.assertEqual(report.matched, 2)

    def test_a_non_terminal_empty_turn_is_reported(self) -> None:
        # COL-102 (reabierta): un turno sin eventos antes del siguiente
        # |turn| significa que sus acciones reales quedaron mal etiquetadas
        # bajo el turno de al lado, no que el turno haya estado vacío de
        # verdad.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|turn|1",
                    "|turn|2",
                    "|move|p2a: Metagross|Psychic Fangs|",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertTrue(any("turno 1" in problem for problem in report.rosters), report.rosters)

    def test_a_terminal_empty_turn_is_not_reported(self) -> None:
        # El vídeo puede cortarse justo después del marcador del último
        # turno, antes de que el HUD llegue a mostrar ninguna acción; eso no
        # es el bug de COL-102.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Psychic Fangs|",
                    "|turn|2",
                ],
            )

            report = verify_replay(trace, log)

        self.assertEqual(report.rosters, ())

    def test_a_pokemon_that_keeps_acting_at_zero_hp_is_reported(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `331e6e783c3e45a4`,
        # partida 3: Salamence baja a 0/100 sin faint y se cura a 65/100 sin
        # ningún move/item que lo explique -un Pokémon vivo no puede estar
        # en 0 PS.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Psychic Fangs|",
                    "|-damage|p2b: Salamence|0/100",
                    "|-heal|p2b: Salamence|65/100",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertTrue(
            any("Salamence" in problem and "0 PS" in problem for problem in report.rosters),
            report.rosters,
        )

    def test_a_pokemon_that_faints_at_zero_hp_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Psychic Fangs|",
                    "|-damage|p2b: Salamence|0/100",
                    "|faint|p2b: Salamence",
                ],
            )

            report = verify_replay(trace, log)

        self.assertEqual(report.rosters, ())

    def test_a_pokemon_left_at_zero_hp_with_no_faint_at_all_is_reported(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `10a7fba6fda04585`,
        # partida 4: Milotic queda en 0 PS y sigue en campo hasta el final del
        # replay -nunca llega su `faint`.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Psychic Fangs|",
                    "|-damage|p2b: Milotic|0/100",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertTrue(
            any("Milotic" in problem and "final" in problem for problem in report.rosters),
            report.rosters,
        )

    def test_a_second_species_in_a_slot_without_a_switch_is_reported(self) -> None:
        # COL-102, reapertura estructural del 25 sep: invariante de
        # identidad del mandato original, sin construir hasta ahora. El
        # mismo tipo de fallo que ya causó el switch fantasma de Kingambit
        # y la identidad huérfana de Indeedee-F, visto desde el replay
        # final: dos especies distintas en un slot sin switch/drag de por
        # medio.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|switch|p2a: Kingambit|Kingambit, L50|100/100",
                    "|move|p2a: Kingambit|Kowtow Cleave|p1a: Blaziken",
                    "|-damage|p2a: Archaludon|50/100",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertTrue(
            any("Archaludon" in problem and "Kingambit" in problem for problem in report.rosters),
            report.rosters,
        )

    def test_a_mega_evolution_does_not_look_like_a_slot_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|switch|p1a: Gardevoir|Gardevoir, L50|171/171",
                    "|detailschange|p1a: Gardevoir|Gardevoir-Mega, L50",
                    "|-mega|p1a: Gardevoir|Gardevoir|Gardevoirite",
                    "|move|p1a: Gardevoir|Hyper Voice|",
                ],
            )

            report = verify_replay(trace, log)

        self.assertEqual(report.rosters, ())

    def test_a_status_change_without_curing_the_previous_one_is_reported(self) -> None:
        # COL-102, reapertura estructural del 25 sep: quemadura, veneno,
        # parálisis, sueño y congelación se excluyen entre sí -otro
        # invariante del mandato original sin construir hasta ahora.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|switch|p2a: Salamence|Salamence, L50|100/100",
                    "|-status|p2a: Salamence|brn",
                    "|-status|p2a: Salamence|par",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertTrue(
            any("brn" in problem and "par" in problem for problem in report.rosters),
            report.rosters,
        )

    def test_a_cured_status_followed_by_a_new_one_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|switch|p2a: Salamence|Salamence, L50|100/100",
                    "|-status|p2a: Salamence|brn",
                    "|-curestatus|p2a: Salamence|brn",
                    "|-status|p2a: Salamence|par",
                ],
            )

            report = verify_replay(trace, log)

        self.assertEqual(report.rosters, ())

    def test_the_same_status_read_twice_is_not_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Psychic Fangs!"])])
            log = _log(
                directory,
                [
                    "|switch|p2a: Salamence|Salamence, L50|100/100",
                    "|-status|p2a: Salamence|brn",
                    "|-status|p2a: Salamence|brn",
                ],
            )

            report = verify_replay(trace, log)

        self.assertEqual(report.rosters, ())

    def test_an_event_the_screen_never_showed_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(directory, [_frame(1, ["The opposing Sensei used Bullet Punch!"])])
            log = _log(
                directory,
                [
                    "|move|p2a: Metagross|Bullet Punch|",
                    "|move|p2a: Metagross|Earthquake|",
                ],
            )

            report = verify_replay(trace, log)

        self.assertFalse(report.faithful)
        self.assertEqual([item.value for item in report.invented], ["Earthquake"])

    def test_the_same_message_over_several_frames_counts_once(self) -> None:
        """El OCR repite el mensaje y varía el mote; es una sola vez."""

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(
                directory,
                [
                    _frame(1, ["The opposing せんせい used Psychic Fangs!"]),
                    _frame(2, ["The opposing せんtせL) used Psychic Fangs!"]),
                    _frame(3, ["The opposingせんせL) used Psychic Fangs!"]),
                ],
            )
            log = _log(directory, ["|move|p2a: Metagross|Psychic Fangs|"])

            report = verify_replay(trace, log)

        self.assertTrue(report.faithful, report)
        self.assertEqual(report.on_screen, 1)

    def test_a_name_the_ocr_cut_short_is_not_a_difference(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            trace = _trace(
                directory,
                [
                    _frame(1, ["The opposing Charizard used Weather Ball!"]),
                    _frame(2, ["The opposing Charizard used Weather Ba!"]),
                ],
            )
            log = _log(directory, ["|move|p2a: Charizard|Weather Ball|"])

            report = verify_replay(trace, log)

        self.assertTrue(report.faithful, report)


class RosterTests(unittest.TestCase):
    """El roster del replay contra las especies que el juego escribió."""

    CATALOG = ("Charizard", "Aerodactyl", "Garchomp", "Hippowdon", "Basculegion")

    def _report(self, directory: Path, poke_lines: list[str], texts: list[str]):
        trace = _trace(
            directory,
            [
                {
                    "frame": index + 1,
                    "battle_index": 0,
                    "ocr": [{"text": text, "confidence": 0.99}],
                    "detections": {"players": {"p1": "Roku", "p2": "Latte"}},
                }
                for index, text in enumerate(texts)
            ],
        )
        log = _log(directory, poke_lines)
        return verify_replay(trace, log, species_names=self.CATALOG)

    def test_a_species_the_game_named_must_be_in_that_roster(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(
                Path(raw),
                ["|poke|p2|Hippowdon, L50|"],
                ["Latte sent out Garchomp!"],
            )

        self.assertFalse(report.faithful)
        self.assertTrue(any("Garchomp" in problem for problem in report.rosters), report.rosters)

    def test_a_roster_that_matches_the_screen_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(
                Path(raw),
                ["|poke|p2|Garchomp, L50|"],
                ["Latte sent out Garchomp!"],
            )

        self.assertEqual(report.rosters, ())

    def test_the_base_name_in_text_accepts_a_gender_forme(self) -> None:
        """El juego escribe "Basculegion" también para la hembra."""

        with tempfile.TemporaryDirectory() as raw:
            report = self._report(
                Path(raw),
                ["|poke|p1|Basculegion-F, L50|"],
                ["Go! Basculegion!"],
            )

        self.assertEqual(report.rosters, ())

    def test_a_message_that_cannot_be_attributed_is_ignored(self) -> None:
        """Sin saber de quién es la frase, callar antes que dar falsa alarma."""

        with tempfile.TemporaryDirectory() as raw:
            report = self._report(
                Path(raw),
                ["|poke|p1|Charizard, L50|"],
                ["It fainted because of Garchomp!"],
            )

        self.assertEqual(report.rosters, ())

    def test_a_repeated_species_in_one_roster_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._report(
                Path(raw),
                ["|poke|p1|Charizard, L50|", "|poke|p1|Charizard, L50|"],
                ["Go! Charizard!"],
            )

        self.assertTrue(any("repite" in problem for problem in report.rosters), report.rosters)


class NamedSpeciesTests(unittest.TestCase):
    """Quién sale escrito cuando el slot apuntado y el mensaje no coinciden."""

    def test_the_game_naming_another_pokemon_wins_over_the_tracked_slot(self) -> None:
        """El rival cambió sin que lo viéramos: manda lo que dijo la pantalla."""

        event = BattleEvent(kind="faint", timestamp_ms=1, slot="p2a", species="Ninetales-Alola")

        lines = _event_lines(event, {"p2a": "Armarouge"}, {}, {})

        self.assertEqual(lines, ["|faint|p2a: Ninetales-Alola"])

    def test_a_mega_keeps_the_forme_that_the_slot_already_tracks(self) -> None:
        event = BattleEvent(kind="faint", timestamp_ms=1, slot="p1a", species="Blaziken")

        lines = _event_lines(event, {"p1a": "Blaziken-Mega"}, {}, {})

        self.assertEqual(lines, ["|faint|p1a: Blaziken-Mega"])

    def test_without_a_tracked_slot_the_named_species_is_used(self) -> None:
        event = BattleEvent(kind="faint", timestamp_ms=1, slot="p2b", species="Sableye")

        self.assertEqual(_named_species(event, {}), "Sableye")


if __name__ == "__main__":
    unittest.main()
