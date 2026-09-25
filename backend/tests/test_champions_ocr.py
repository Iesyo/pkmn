from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path

from pkmn_vgc.champions_replay.armado import BattleView, HudFrame
from pkmn_vgc.champions_replay.cli import _load_mapping, _seed_from_context, build_parser
from pkmn_vgc.champions_replay.detector import DetectionError, DetectorContext, HudAlias
from pkmn_vgc.champions_replay.ocr_detector import (
    ChampionsCatalog,
    _accelerator_params,
    ChampionsOcrDetector,
    ChampionsTextParser,
    OcrLine,
    OcrTraceDetector,
    RapidOcrEngine,
    _health_readings,
    _health_value,
    _with_japanese_second_opinion,
    load_champions_catalog,
    load_trace_aliases,
)
from pkmn_vgc.champions_replay.pipeline import CaptureSeed, ReplayCapturePipeline
from pkmn_vgc.champions_replay.showdown import build_replay_document
from pkmn_vgc.champions_replay.team_preview import (
    ChampionsHudIconResolver,
    ChampionsTeamPreviewResolver,
    looks_like_a_nickname,
)
from pkmn_vgc.champions_replay.sources import FramePacket, OcrTraceFrameSource


def line(
    text: str,
    *,
    x: float,
    y: float,
    confidence: float = 0.99,
    width: float = 0.1,
    height: float = 0.04,
) -> OcrLine:
    return OcrLine(
        text=text,
        confidence=confidence,
        left=x,
        top=y,
        right=min(1.0, x + width),
        bottom=min(1.0, y + height),
    )


class ChampionsOcrTests(unittest.TestCase):
    def parser(self) -> ChampionsTextParser:
        return ChampionsTextParser(
            context=DetectorContext(
                p1_name="IesYo",
                p2_name="Rival",
                p1_team=("Delphox", "Victreebel"),
                p2_team=("Steelix", "Drampa", "Umbreon"),
            ),
            catalog=ChampionsCatalog(
                species=("Delphox", "Victreebel", "Steelix", "Drampa", "Umbreon"),
                moves=("Rock Slide",),
            ),
        )

    @staticmethod
    def command_frame(*, p1_health: str = "152/152") -> tuple[OcrLine, ...]:
        return (
            line("Steelix", x=0.62, y=0.04),
            line("Drampa", x=0.83, y=0.04),
            line("100%", x=0.69, y=0.11),
            line("100%", x=0.90, y=0.11),
            line("Delphox", x=0.08, y=0.86),
            line("Victreebel", x=0.29, y=0.86),
            line(p1_health, x=0.13, y=0.93),
            line("1871187", x=0.34, y=0.93),
            line("MOVE TIME", x=0.82, y=0.32),
            line("FIGHT", x=0.86, y=0.70),
            line("POKÉMON", x=0.84, y=0.90),
        )

    def test_the_gpu_runs_the_models_when_the_runtime_brings_it(self) -> None:
        self.assertEqual(
            _accelerator_params(("DmlExecutionProvider", "CPUExecutionProvider")),
            {"EngineConfig.onnxruntime.use_dml": True},
        )
        # Sin DirectML no se pide nada: el mismo código sigue en CPU en otra
        # máquina, con los mismos modelos y el mismo resultado.
        self.assertEqual(_accelerator_params(("CPUExecutionProvider",)), {})
        self.assertEqual(_accelerator_params(()), {})

    def test_repairs_common_health_ocr_artifacts(self) -> None:
        self.assertEqual(_health_value("100%"), "100/100")
        self.assertEqual(_health_value("141/198"), "141/198")
        self.assertEqual(_health_value("1871187"), "187/187")
        self.assertIsNone(_health_value("33"))
        self.assertIsNone(_health_value("06:45"))
        self.assertIsNone(_health_value("Battle Info"))
        # RapidOCR pega el `%` al número y devuelve "779" por "77": recortar
        # eso al 100% inventaba una curación a tope que nadie vio en pantalla.
        self.assertIsNone(_health_value("779%"))

    def test_reads_active_slots_health_and_first_turn_from_hud(self) -> None:
        detections = self.parser().parse(
            self.command_frame(),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertTrue(detections.battle_started)
        switches = [event for event in detections.events if event.kind == "switch"]
        self.assertEqual(
            [(event.slot, event.species, event.health) for event in switches],
            [
                ("p1a", "Delphox", "152/152"),
                ("p1b", "Victreebel", "187/187"),
                ("p2a", "Steelix", "100/100"),
                ("p2b", "Drampa", "100/100"),
            ],
        )
        self.assertEqual(detections.events[-1].kind, "turn")
        self.assertEqual(detections.events[-1].turn, 1)

    def test_infers_an_unknown_opponent_nickname_from_move_evidence(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p1_name="IesYo", p2_name="Rival"),
            catalog=ChampionsCatalog(
                species=("Kingambit", "Sableye"),
                moves=("Protect", "Kowtow Cleave"),
                species_moves=(
                    ("Kingambit", ("protect", "kowtowcleave")),
                    ("Sableye", ("protect",)),
                ),
            ),
        )
        parser.parse(
            (line("Rival sent out せんせい and しごでき!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )

        ambiguous = parser.parse(
            (line("The opposing せんせい used Protect!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=1_000,
            source_frame=1,
        )
        resolved = parser.parse(
            (line("The opposing せんせい used Kowtow Cleave!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=2_000,
            source_frame=2,
        )

        self.assertEqual(ambiguous.events, ())
        self.assertEqual(
            [(event.kind, event.slot, event.species, event.move) for event in resolved.events],
            [
                ("switch", "p2a", "Kingambit", None),
                ("move", "p2a", "Kingambit", "Protect"),
                ("move", "p2a", "Kingambit", "Kowtow Cleave"),
            ],
        )

    def test_known_opponent_team_disambiguates_gendered_form_without_visual_model(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_name="Rival", p2_team=("Basculegion-F",)),
            catalog=ChampionsCatalog(
                species=("Basculegion", "Basculegion-F"),
                moves=("Wave Crash",),
                species_moves=(
                    ("Basculegion", ("wavecrash",)),
                    ("Basculegion-F", ("wavecrash",)),
                ),
            ),
        )
        parser.parse(
            (line("Rival sent out ニックネーム!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )

        resolved = parser.parse(
            (line("The opposing ニックネーム used Wave Crash!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.species) for event in resolved.events],
            [("switch", "Basculegion-F"), ("move", "Basculegion-F")],
        )

    def test_historical_teammates_break_a_move_evidence_tie(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_name="Rival"),
            catalog=ChampionsCatalog(
                species=("Metagross", "Sableye", "Grimmsnarl"),
                moves=("Light Screen", "Rain Dance"),
                species_moves=(
                    ("Sableye", ("lightscreen", "raindance")),
                    ("Grimmsnarl", ("lightscreen", "raindance")),
                ),
                species_teammates=(
                    ("Metagross", "Sableye", 20),
                    ("Grimmsnarl", "Metagross", 1),
                ),
            ),
        )
        parser.parse(
            (line("Rival sent out しこてき and せんせい!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        parser._bind_alias("p2", "せんせい", "Metagross")

        ambiguous = parser.parse(
            (line("The opposing しでき used Light Screen!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=1_000,
            source_frame=1,
        )
        resolved = parser.parse(
            (line("The opposing しでき used Rain Dance!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=2_000,
            source_frame=2,
        )

        self.assertEqual(ambiguous.events, ())
        self.assertEqual(
            [(event.kind, event.slot, event.species, event.move) for event in resolved.events],
            [
                ("switch", "p2a", "Sableye", None),
                ("move", "p2a", "Sableye", "Light Screen"),
                ("move", "p2a", "Sableye", "Rain Dance"),
            ],
        )

    def test_resolves_a_japanese_nickname_joined_to_used(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_name="Rival"),
            catalog=ChampionsCatalog(
                species=("Sableye",),
                moves=("Rain Dance",),
                species_moves=(("Sableye", ("raindance",)),),
            ),
        )
        parser.parse(
            (line("Rival sent out しごでき and Helper!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        parser._bind_alias("p2", "しごでき", "Sableye")

        detections = parser.parse(
            (line("The opposing しできused Rain Dance!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.species, event.move) for event in detections.events],
            [("move", "Sableye", "Rain Dance")],
        )

    def test_parses_a_move_when_ocr_removes_the_space_after_used(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Kingambit",)),
            catalog=ChampionsCatalog(
                species=("Kingambit",),
                moves=("Sucker Punch",),
            ),
        )
        parser._battle_open = True
        parser._active["p2a"] = "Kingambit"

        detections = parser.parse(
            (line("The opposing Kingambit usedSucker Punch!", x=0.2, y=0.7, width=0.5),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.species, event.move) for event in detections.events],
            [("move", "Kingambit", "Sucker Punch")],
        )

    def test_normalizes_known_nicknames_in_free_form_messages(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_team=("Sylveon",),
                p1_aliases=(("Nico", "Sylveon"),),
            ),
            catalog=ChampionsCatalog(species=("Sylveon",)),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Sylveon"

        detections = parser.parse(
            (line("Nico protected itself!", x=0.2, y=0.7, width=0.35),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(detections.events[0].value, "Sylveon protected itself!")

    def test_serializes_tailwind_messages_as_side_conditions(self) -> None:
        parser = self.parser()
        parser._battle_open = True
        parser._active["p1a"] = "Delphox"
        parser._active["p2a"] = "Steelix"

        opposing_start = parser.parse(
            (
                line(
                    "A tailwind started blowing on the opposing side!",
                    x=0.2,
                    y=0.7,
                    width=0.5,
                ),
            ),
            timestamp_ms=1_000,
            source_frame=1,
        )
        opposing_end = parser.parse(
            (line("The opposing side's tailwind petered out!", x=0.2, y=0.7, width=0.5),),
            timestamp_ms=2_000,
            source_frame=2,
        )
        local_start = parser.parse(
            (line("A tailwind started blowing behind your team!", x=0.2, y=0.7, width=0.5),),
            timestamp_ms=3_000,
            source_frame=3,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in opposing_start.events],
            [("sidestart", "p2a", "move: Tailwind")],
        )
        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in opposing_end.events],
            [("sideend", "p2a", "move: Tailwind")],
        )
        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in local_start.events],
            [("sidestart", "p1a", "move: Tailwind")],
        )

    def test_uses_the_recall_time_when_hud_confirms_a_switch_later(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p1_team=("Blaziken", "Torkoal"),
                p1_aliases=(("Tonatiuh", "Blaziken"),),
            ),
            catalog=ChampionsCatalog(species=("Blaziken", "Torkoal")),
        )
        parser.parse(
            (line("Blaziken", x=0.12, y=0.86),),
            timestamp_ms=0,
            source_frame=0,
        )
        recalled = parser.parse(
            (line("Tonatiuh, come back!", x=0.2, y=0.7, width=0.35),),
            timestamp_ms=1_000,
            source_frame=1,
        )
        switched = parser.parse(
            (line("Torkoal", x=0.12, y=0.86),),
            timestamp_ms=2_000,
            source_frame=2,
        )

        self.assertEqual(recalled.events, ())
        self.assertEqual(switched.events[0].kind, "switch")
        self.assertEqual(switched.events[0].species, "Torkoal")
        self.assertEqual(switched.events[0].timestamp_ms, 999)

    def test_ignores_an_orphan_move_name_until_its_actor_is_visible(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(moves=("Burning Jealousy",)),
        )
        parser._battle_open = True

        detections = parser.parse(
            (line("Burning Jealousy", x=0.2, y=0.7, width=0.3),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(detections.events, ())

    def test_ignores_phone_move_menu_labels_even_with_ocr_punctuation(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Venusaur",)),
            catalog=ChampionsCatalog(
                species=("Venusaur",),
                moves=("Giga Drain", "Sleep Powder"),
            ),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Venusaur"

        detections = parser.parse(
            (
                line("Move Info", x=0.84, y=0.34),
                line("Giga Drain!", x=0.76, y=0.44),
                line("Venusaur used Sleep Powder!", x=0.18, y=0.70, width=0.38),
            ),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.species, event.move) for event in detections.events],
            [("move", "Venusaur", "Sleep Powder")],
        )

    def test_move_info_description_is_not_a_battle_message(self) -> None:
        # COL-102, job 7cf4fc1532d04b7a (frame 421): con la tarjeta Move Info
        # abierta sobre Protect, la última línea de su descripción cae en la
        # franja de avisos y tiene forma de frase. Salía en el replay una vez
        # por cada Pokémon que elegía movimiento: dos veces por turno.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Venusaur",)),
            catalog=ChampionsCatalog(species=("Venusaur",), moves=("Sleep Powder",)),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Venusaur"
        card = (
            line("Category", x=0.146, y=0.175, width=0.05, height=0.03),
            line("Power", x=0.285, y=0.177, width=0.04, height=0.03),
            line("Accuracy", x=0.411, y=0.177, width=0.05, height=0.03),
            line("Range", x=0.153, y=0.272, width=0.04, height=0.03),
            line("Self", x=0.356, y=0.275, width=0.03, height=0.03),
            line(
                "The user protects itself from incoming moves for the turn. With",
                x=0.129,
                y=0.332,
                width=0.365,
                height=0.03,
            ),
            line(
                "each consecutive use, this move's chance of success becomes",
                x=0.128,
                y=0.374,
                width=0.357,
                height=0.03,
            ),
            line("1/3 of what it was before.", x=0.13, y=0.415, width=0.153, height=0.03),
        )

        only_card = parser.parse(card, timestamp_ms=1_000, source_frame=1)
        with_message = parser.parse(
            (*card, line("Venusaur used Sleep Powder!", x=0.213, y=0.675, width=0.3)),
            timestamp_ms=1_500,
            source_frame=2,
        )

        self.assertEqual(only_card.events, ())
        self.assertEqual(
            [(event.kind, event.species, event.move) for event in with_message.events],
            [("move", "Venusaur", "Sleep Powder")],
        )

    def test_one_readable_bar_does_not_feed_both_opponent_slots(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        action = tuple(
            item
            for item in self.command_frame()
            if item.text not in {"MOVE TIME", "FIGHT", "POKÉMON"}
        )
        # Sólo queda legible la barra de Steelix. La de Drampa cae dentro de la
        # ventana de búsqueda del otro nombre y acababa escrita en los dos.
        partial = tuple(item for item in action if not (item.text == "100%" and item.left > 0.85))
        partial = tuple(
            line("33%", x=0.69, y=0.11) if item.text == "100%" else item for item in partial
        )

        detections = parser.parse(partial, timestamp_ms=1_000, source_frame=1)

        self.assertEqual(
            [
                (event.kind, event.slot, event.health)
                for event in detections.events
                if event.kind in {"damage", "heal"}
            ],
            [("damage", "p2a", "33/100")],
        )

    def test_a_stray_fragment_does_not_push_the_partner_to_the_other_slot(self) -> None:
        parser = self.parser()
        parser.parse(
            (line("IesYo sent out Delphox and Victreebel!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        parser.parse(self.command_frame(), timestamp_ms=1_000, source_frame=1)
        # Un resto de interfaz fuera del HUD, sin barra propia, que se parece
        # lo justo a un lead anunciado. El slot sale del orden de la lista, así
        # que se llevaba p1a y empujaba a Delphox al slot de su compañero.
        noisy = self.command_frame() + (line("Victroeboi", x=0.02, y=0.58),)

        detections = parser.parse(noisy, timestamp_ms=2_000, source_frame=2)

        self.assertEqual([event for event in detections.events if event.kind == "switch"], [])

    def test_ocr_variants_of_one_nickname_keep_their_own_identity(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p1_name="IesYo", p2_name="Rival"),
            catalog=ChampionsCatalog(
                species=("Kingambit", "Sableye"),
                moves=("Kowtow Cleave",),
                species_moves=(
                    ("Kingambit", ("kowtowcleave",)),
                    ("Sableye", ("kowtowcleave",)),
                ),
            ),
        )
        identity = parser._new_identity("p2", "せんtせl", "p2a")
        parser._set_identity_species(identity, "Kingambit")
        for variant in ("せんtせl", "せんtl", "tんtl"):
            parser._bind_alias("p2", variant, "Kingambit")

        # Esta lectura empata con otras dos del mismo mote. Comparando lecturas
        # en vez de identidades, el empate tumbaba la identidad que las tres
        # señalaban; la especie correcta quedaba descartada por estar ya
        # asignada y el mote acababa en el compañero.
        garbled = "せtんttl"

        self.assertEqual(parser._identity_for_value("p2", garbled), identity)
        parser._infer_alias_from_move("p2", garbled, "Kowtow Cleave")
        self.assertEqual(parser.resolved_aliases()["p2"].get(garbled), "Kingambit")

    def test_a_two_character_fragment_cannot_name_a_pokemon(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=1_000, source_frame=1)
        # La tarjeta dibuja el símbolo de género junto al mote y el OCR lo lee
        # como un número corto. Aunque quede atado a una especie, con dos
        # caracteres no puede nombrar a nadie ni ocupar un slot del HUD.
        parser._bind_alias("p1", "07", "Victreebel")
        noisy = self.command_frame() + (line("07", x=0.02, y=0.86, width=0.02),)

        detections = parser.parse(noisy, timestamp_ms=2_000, source_frame=2)

        self.assertIsNone(parser._resolve_species("07", "p1"))
        self.assertEqual([event for event in detections.events if event.kind == "switch"], [])

    def test_the_gender_glyph_is_not_taken_for_a_nickname(self) -> None:
        for glyph in ("07", "37", "97", "f", "3", "♂"):
            self.assertFalse(looks_like_a_nickname(glyph), glyph)
        for nickname in ("Destroya", "Frida", "Rex"):
            self.assertTrue(looks_like_a_nickname(nickname), nickname)

        parser = self.parser()
        # Filas ya confirmadas por la lectura del Team Preview.
        parser.bind_preview_team(("Delphox", "Victreebel"), side="p1")
        # El símbolo cae en la misma banda que el mote y más cerca del centro de
        # la fila, así que ganaba el desempate por cercanía.
        parser._preview_detections(
            (
                line("Destroya", x=0.10, y=0.13),
                line("07", x=0.21, y=0.125, width=0.02),
                line("Frida", x=0.10, y=0.245),
            )
        )

        self.assertEqual(
            parser.resolved_aliases()["p1"],
            {"destroya": "Delphox", "frida": "Victreebel"},
        )

    def ability_parser(self) -> ChampionsTextParser:
        return ChampionsTextParser(
            context=DetectorContext(
                p1_name="IesYo",
                p2_name="Rival",
                p1_team=("Tyranitar", "Sinistcha"),
                p2_team=(),
            ),
            catalog=ChampionsCatalog(
                species=("Tyranitar", "Sinistcha"),
                abilities=("Sand Stream",),
                species_abilities=(("Tyranitar", ("sandstream",)),),
            ),
        )

    @staticmethod
    def ability_frame() -> tuple[OcrLine, ...]:
        return (
            line("Tyranitar's", x=0.70, y=0.30),
            line("Sand Stream", x=0.70, y=0.36),
        )

    def test_an_ability_is_not_pinned_on_whoever_holds_the_slot(self) -> None:
        parser = self.ability_parser()
        parser._turn = 3
        # El rótulo de habilidad no lleva el prefijo del rival, así que el lado
        # se deduce del roster. Con Tyranitar en los dos equipos caía en p1, y
        # buscar su slot devolvía p1a, que ocupaba otro Pokémon.
        parser._active["p1a"] = "Sinistcha"

        detections = parser.parse(self.ability_frame(), timestamp_ms=0, source_frame=0)

        self.assertEqual([event for event in detections.events if event.kind == "ability"], [])

    def test_an_ability_is_written_once_its_pokemon_is_on_the_field(self) -> None:
        parser = self.ability_parser()
        parser._turn = 3
        parser._active["p1a"] = "Tyranitar"

        detections = parser.parse(self.ability_frame(), timestamp_ms=0, source_frame=0)

        self.assertEqual(
            [(event.slot, event.species, event.value)
             for event in detections.events if event.kind == "ability"],
            [("p1a", "Tyranitar", "Sand Stream")],
        )

    def test_a_pending_ability_does_not_survive_its_turn(self) -> None:
        parser = self.ability_parser()
        parser._turn = 3
        parser._active["p1a"] = "Sinistcha"
        announced = parser.parse(self.ability_frame(), timestamp_ms=0, source_frame=0)
        self.assertEqual([event for event in announced.events if event.kind == "ability"], [])
        # Turnos después entra el Tyranitar del jugador. La habilidad era de otro
        # y esperar más sólo la coloca en el momento equivocado.
        parser._turn = 6
        parser._active["p1a"] = "Tyranitar"

        detections = parser.parse((), timestamp_ms=60_000, source_frame=120)

        self.assertEqual([event for event in detections.events if event.kind == "ability"], [])

    def relief_parser(self) -> ChampionsTextParser:
        """Tyranitar en los dos equipos, que es cuando el lado hay que deducirlo."""

        return ChampionsTextParser(
            context=DetectorContext(
                p1_name="IesYo",
                p2_name="Rival",
                p1_team=("Tyranitar", "Sinistcha"),
                p2_team=("Tyranitar", "Sylveon"),
            ),
            catalog=ChampionsCatalog(
                species=("Tyranitar", "Sinistcha", "Sylveon"),
                abilities=("Sand Stream",),
                species_abilities=(("Tyranitar", ("sandstream",)),),
            ),
        )

    def test_an_ability_announces_the_pokemon_it_relieves_into_the_slot(self) -> None:
        parser = self.relief_parser()
        parser._turn = 3
        parser._announced_slots["p2"] = {"tyranitar": "p2b"}
        # Sylveon está en el slot: lo que venga detrás es un relevo.
        parser._active["p2b"] = "Sylveon"

        detections = parser.parse(self.ability_frame(), timestamp_ms=4_000, source_frame=8)

        # Sin el cambio, el HUD ya no ve diferencia cuando por fin lee al recién
        # entrado, y su daño y su estado salen a nombre del que estaba antes.
        self.assertEqual(
            [(event.kind, event.slot, event.species)
             for event in detections.events if event.kind in {"switch", "ability"}],
            [("switch", "p2b", "Tyranitar"), ("ability", "p2b", "Tyranitar")],
        )

    def test_an_ability_waits_for_the_hud_to_place_its_pokemon(self) -> None:
        parser = self.relief_parser()
        # El juego anuncia las salidas en un orden que no siempre es el del HUD:
        # aquí dice p2a y Tyranitar acaba saliendo en p2b.
        parser._announced_slots["p2"] = {"tyranitar": "p2a"}

        announced = parser.parse(self.ability_frame(), timestamp_ms=0, source_frame=0)

        # Nadie a quien relevar, así que ni cambio ni habilidad: escribirla antes
        # de que nadie haya entrado deja el replay con una habilidad sin dueño, y
        # el visor oficial se cae al cargarlo.
        self.assertEqual(
            [event for event in announced.events if event.kind in {"switch", "ability"}], []
        )

        # El HUD lee los leads y coloca a cada uno donde está de verdad.
        parser._active["p2a"] = "Sylveon"
        parser._active["p2b"] = "Tyranitar"
        later = parser.parse((), timestamp_ms=2_000, source_frame=4)

        self.assertEqual(
            [(event.kind, event.slot, event.species)
             for event in later.events if event.kind == "ability"],
            [("ability", "p2b", "Tyranitar")],
        )

    def test_reads_the_own_sides_ability_banner_anchored_to_the_left(self) -> None:
        # COL-102 (reapertura): el banner de habilidad del propio equipo
        # aparece pegado al borde izquierdo, no al derecho como el del rival
        # (ability_frame). Sin mirar ese lado, Intimidate -y cualquier otra
        # habilidad propia al entrar- nunca llegaba a escribirse como
        # -ability, aunque su efecto narrado sí llegara por el camino de
        # mensajes.
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_team=("Incineroar",),
                p1_aliases=(("Antonio", "Incineroar"),),
            ),
            catalog=ChampionsCatalog(species=("Incineroar",), abilities=("Intimidate",)),
        )
        parser._active["p1a"] = "Incineroar"

        detections = parser.parse(
            (
                line("Antonio's", x=0.078, y=0.431, width=0.073),
                line("Intimidate", x=0.078, y=0.473, width=0.077),
            ),
            timestamp_ms=500,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in detections.events],
            [("ability", "p1a", "Intimidate")],
        )

    def test_an_ability_does_not_attach_to_a_departed_pokemons_stale_slot(self) -> None:
        # COL-102, job f53bd34897b84f86, auditoría focal del Turno 2. Al
        # marcar el slot de Incineroar (p1b) como abierto, `_active` seguía
        # nombrándolo ahí hasta que el HUD confirmara dónde había quedado de
        # verdad. Su propia Intimidate al reentrar resolvía contra esa
        # entrada obsoleta y se escribía en p1b, cuatro líneas antes de que
        # el HUD confirmara que en realidad había vuelto a p1a.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_name="Roku", p1_team=("Incineroar",)),
            catalog=ChampionsCatalog(species=("Incineroar",), abilities=("Intimidate",)),
        )
        parser._active["p1b"] = "Incineroar"
        parser._battle_open = True
        parser._turn = 2

        parser.parse(
            (line("Incineroar went back to Roku!", x=0.15, y=0.7, width=0.4),),
            timestamp_ms=0,
            source_frame=0,
        )
        pending = parser.parse(
            (
                line("Incineroar's", x=0.078, y=0.431, width=0.09),
                line("Intimidate", x=0.078, y=0.473, width=0.077),
            ),
            timestamp_ms=500,
            source_frame=1,
        )
        self.assertEqual(pending.events, ())

        # El HUD confirma que Incineroar volvió, pero a un slot distinto
        # -Charizard, no Incineroar, es quien de verdad ocupa p1b ahora.
        parser._active["p1a"] = "Incineroar"
        parser._active["p1b"] = "Charizard"
        parser._open_slots["p1"].remove("p1b")
        confirmed = parser.parse((), timestamp_ms=1_000, source_frame=2)

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in confirmed.events],
            [("ability", "p1a", "Intimidate")],
        )

    def test_an_announced_switch_does_not_guess_between_two_open_slots(self) -> None:
        # COL-102, job f53bd34897b84f86, auditoría focal del Turno 2. Un
        # debilitado (Sinistcha, p1a) y un relevo por Parting Shot
        # (Incineroar, p1b) abrieron los dos slots de dobles en el mismo
        # tramo del turno. "Go! Charizard!" reclamó el slot abierto más
        # temprano por orden de anuncio (p1a), pero el HUD confirmó después
        # que Charizard había tomado el slot de Incineroar (p1b) y que
        # Incineroar había tomado el otro (p1a): el orden del anuncio no
        # dice a qué slot físico se refiere un "Go!" en cuanto hay más de
        # uno abierto; sólo el HUD lo sabe.
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p1_team=("Sinistcha", "Incineroar", "Charizard"),
            ),
            catalog=ChampionsCatalog(species=("Sinistcha", "Incineroar", "Charizard")),
        )
        parser._active["p1a"] = "Sinistcha"
        parser._active["p1b"] = "Incineroar"
        parser._battle_open = True

        faint = parser.parse(
            (line("Sinistcha fainted!", x=0.2, y=0.7, width=0.3),),
            timestamp_ms=0,
            source_frame=0,
        )
        recall = parser.parse(
            (line("Incineroar went back to Roku!", x=0.15, y=0.7, width=0.4),),
            timestamp_ms=500,
            source_frame=1,
        )
        announced = parser.parse(
            (line("Go! Charizard!", x=0.15, y=0.73, width=0.25),),
            timestamp_ms=1_000,
            source_frame=2,
        )

        self.assertEqual([event.kind for event in faint.events], ["faint"])
        self.assertEqual([event.kind for event in recall.events], ["message"])
        self.assertEqual(announced.events, ())

        confirmed = parser.parse(
            (
                line("Incineroar", x=0.08, y=0.86),
                line("Charizard", x=0.29, y=0.86),
                line("202/202", x=0.13, y=0.93),
                line("167/167", x=0.34, y=0.93),
            ),
            timestamp_ms=15_000,
            source_frame=30,
        )
        self.assertEqual(
            [(event.kind, event.slot, event.species) for event in confirmed.events],
            [("switch", "p1a", "Incineroar"), ("switch", "p1b", "Charizard")],
        )

    def pelipper_parser(self) -> ChampionsTextParser:
        # COL-102, job 82923f56ce264a92, Partida 1, final del turno 4: el
        # rival sin equipo conocido (como en el job), Roku con el suyo.
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="ShotgunDave",
                p1_team=("Charizard", "Tyranitar", "Milotic", "Incineroar", "Sneasler", "Sinistcha"),
                p1_aliases=(("Frida", "Milotic"),),
            ),
            catalog=ChampionsCatalog(
                species=(
                    "Charizard", "Tyranitar", "Milotic", "Incineroar", "Sneasler",
                    "Sinistcha", "Pelipper",
                ),
                moves=("Parting Shot",),
                abilities=("Drizzle", "Intimidate", "Competitive"),
            ),
        )
        parser._active.update(
            {"p1a": "Charizard", "p1b": "Milotic", "p2a": "Incineroar", "p2b": "Sneasler"}
        )
        parser._battle_open = True
        parser._turn = 4
        return parser

    @staticmethod
    def pelipper_opening_frames() -> tuple[tuple[int, int, tuple[OcrLine, ...]], ...]:
        # Frames y posiciones reales de ocr.trace.jsonl: el debilitado y el
        # Parting Shot dejan abiertos p2a y p2b antes del primer anuncio.
        return (
            (610, 304_500, (line("The opposing Sneasler fainted!", x=0.152, y=0.725, width=0.269),)),
            (640, 319_500, (line("The opposing Incineroar used Parting Shot!", x=0.152, y=0.727, width=0.367),)),
            (650, 324_500, (line("The opposing Incineroar went back to ShotgunDave!", x=0.153, y=0.730, width=0.443),)),
            (669, 334_000, (line("ShotgunDave sent out Pelipper!", x=0.152, y=0.728, width=0.273),)),
            (
                679,
                339_000,
                (
                    line("Pelipper's", x=0.845, y=0.426, width=0.079, height=0.055),
                    line("Drizzle", x=0.866, y=0.468, width=0.059, height=0.047),
                    line("It started to rain!", x=0.154, y=0.731, width=0.149),
                ),
            ),
        )

    def test_an_entry_ability_waits_behind_its_switch_until_the_hud_places_it(self) -> None:
        # COL-102, job 82923f56ce264a92, Partida 1. Con los dos slots del
        # rival abiertos, "sent out Pelipper!" no puede decir en cuál entra y
        # el switch espera al HUD (21 s después); antes de esta corrección, la
        # lluvia de su Drizzle y el Intimidate de Incineroar se escribían en
        # cuanto se leían, por delante de los switches de sus dueños, y los
        # dos banners de habilidad se perdían.
        parser = self.pelipper_parser()
        frames = (
            *self.pelipper_opening_frames(),
            (695, 347_000, (line("ShotgunDave sent out Incineroar!", x=0.150, y=0.725, width=0.290),)),
            (
                702,
                350_500,
                (
                    line("Incineroar's", x=0.834, y=0.427, width=0.088, height=0.048),
                    line("Intimidate", x=0.844, y=0.468, width=0.080, height=0.049),
                ),
            ),
            (
                703,
                351_000,
                (
                    line("Incineroar's", x=0.834, y=0.430, width=0.088, height=0.043),
                    line("Intimidate", x=0.843, y=0.469, width=0.081, height=0.047),
                    line("Charizard and Frida's Attack fell!", x=0.153, y=0.728, width=0.280),
                ),
            ),
            (
                706,
                352_500,
                (
                    line("Fiida's", x=0.011, y=0.438, width=0.056, height=0.031),
                    line("Competitive", x=0.010, y=0.472, width=0.091, height=0.042),
                ),
            ),
            (
                708,
                353_500,
                (
                    line("Frida's", x=0.077, y=0.428, width=0.054, height=0.046),
                    line("Competitive", x=0.077, y=0.469, width=0.094, height=0.051),
                    line("Frida's Sp. Atk rose sharply!", x=0.149, y=0.720, width=0.245),
                ),
            ),
        )
        emitted = [
            (frame, [event.kind for event in parser.parse(lines, timestamp_ms=ts, source_frame=frame).events])
            for frame, ts, lines in frames
        ]
        # Hasta el anuncio todo sale al momento; desde ahí, nada se escribe
        # delante de un switch que todavía no existe.
        self.assertEqual(
            emitted,
            [
                (610, ["faint"]),
                (640, ["move"]),
                (650, ["message"]),
                (669, []),
                (679, []),
                (695, []),
                (702, []),
                (703, []),
                (706, []),
                (708, []),
            ],
        )

        confirmed = parser.parse(
            (
                line("Pelipper", x=0.622, y=0.046, width=0.064),
                line("Incineroar", x=0.832, y=0.052, width=0.070),
                line("100%", x=0.690, y=0.110, width=0.064),
                line("52%", x=0.907, y=0.108, width=0.052),
                line("MOVE TIME", x=0.828, y=0.332, width=0.069),
                line("FIGHT", x=0.902, y=0.706, width=0.058),
                line("Charizard", x=0.083, y=0.867, width=0.066),
                line("Frida", x=0.285, y=0.866, width=0.041),
                line("POKÉMON", x=0.838, y=0.906, width=0.094),
                line("94/167", x=0.141, y=0.930, width=0.070),
                line("202/202", x=0.333, y=0.927, width=0.085),
            ),
            timestamp_ms=355_500,
            source_frame=712,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.species, event.value) for event in confirmed.events],
            [
                ("switch", "p2a", "Pelipper", None),
                ("ability", "p2a", "Pelipper", "Drizzle"),
                ("weather", None, None, "RainDance"),
                ("switch", "p2b", "Incineroar", None),
                ("ability", "p2b", "Incineroar", "Intimidate"),
                ("message", None, None, "Charizard and Milotic's Attack fell!"),
                ("ability", "p1b", "Milotic", "Competitive"),
                ("message", None, None, "Milotic's Sp. Atk rose sharply!"),
                ("turn", None, None, None),
            ],
        )
        # Cada switch conserva el momento en que el juego lo anunció.
        self.assertEqual(
            [event.timestamp_ms for event in confirmed.events if event.kind == "switch"],
            [334_000, 347_000],
        )

    def test_an_entry_the_hud_never_places_stops_holding_at_the_next_turn(self) -> None:
        # Si llega el menú del turno siguiente sin que el HUD haya leído a
        # quien entró, se deja de esperar: lo narrado sale tal como se leyó,
        # sin switch, y la habilidad sin slot confirmado no se escribe.
        parser = self.pelipper_parser()
        for frame, ts, lines in self.pelipper_opening_frames():
            parser.parse(lines, timestamp_ms=ts, source_frame=frame)

        unreadable_hud = parser.parse(
            (
                line("MOVE TIME", x=0.828, y=0.332, width=0.069),
                line("FIGHT", x=0.902, y=0.706, width=0.058),
                line("POKÉMON", x=0.838, y=0.906, width=0.094),
            ),
            timestamp_ms=355_500,
            source_frame=712,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in unreadable_hud.events],
            [("weather", "RainDance"), ("turn", None)],
        )
        self.assertEqual(parser._unplaced_entries, [])

    def test_with_the_battle_in_view_an_entry_is_placed_when_announced(self) -> None:
        # Capa de armado (COL-102): en la segunda fase la traza ya dice que el
        # HUD confirma a Pelipper en p2a en el frame 712, así que el switch se
        # escribe en el anuncio (frame 669) sin esperar, y su Drizzle se
        # atribuye en cuanto sale, porque Pelipper ya está en el campo.
        parser = self.pelipper_parser()
        # Como en la segunda fase: el roster rival ya se conoce desde el principio.
        parser.bind_preview_team(("Incineroar", "Sneasler", "Pelipper"), side="p2")
        parser.battle_view = BattleView(
            (HudFrame(frame=712, slots={"p2a": "Pelipper", "p2b": "Incineroar"}, opens_turn=True),)
        )
        emitted = {
            frame: [
                (event.kind, event.slot, event.species, event.value)
                for event in parser.parse(lines, timestamp_ms=ts, source_frame=frame).events
            ]
            for frame, ts, lines in self.pelipper_opening_frames()
        }

        self.assertEqual(emitted[669], [("switch", "p2a", "Pelipper", None)])
        self.assertEqual(
            emitted[679],
            [("ability", "p2a", "Pelipper", "Drizzle"), ("weather", None, None, "RainDance")],
        )
        self.assertEqual(parser._unplaced_entries, [])

    def test_a_return_without_the_word_withdrew_still_marks_the_slot(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=1_000, source_frame=2)

        # Volt Switch, U-turn y Parting Shot sacan al Pokémon sin que el juego
        # diga "withdrew". Sin apuntar el momento, el HUD tarda en dejar leer a
        # quien entra y su entrada acaba escrita después de lo que ya le pasó.
        parser.parse(
            (line("Delphox went back to IesYo!", x=0.2, y=0.6, width=0.5),),
            timestamp_ms=5_000,
            source_frame=10,
        )

        self.assertEqual(parser._pending_switch_timestamps.get("p1a"), 5_000)

    def test_reads_mobile_hud_positions_without_fixed_sixteen_nine_bands(self) -> None:
        detections = self.parser().parse(
            (
                line("Steelix", x=0.55, y=0.24),
                line("Drampa", x=0.76, y=0.24),
                line("100%", x=0.60, y=0.31),
                line("100%", x=0.81, y=0.31),
                line("Delphox", x=0.12, y=0.63),
                line("Victreebel", x=0.34, y=0.63),
                line("152/152", x=0.17, y=0.70),
                line("187/187", x=0.39, y=0.70),
                line("FIGHT", x=0.8, y=0.76),
                line("POKÉMON", x=0.8, y=0.84),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(
            [(event.slot, event.species) for event in detections.events if event.kind == "switch"],
            [
                ("p1a", "Delphox"),
                ("p1b", "Victreebel"),
                ("p2a", "Steelix"),
                ("p2b", "Drampa"),
            ],
        )

    def test_phone_orientation_prefers_battle_hud_over_notification_text(self) -> None:
        notification = (line("WhatsApp", x=0.1, y=0.1), line("New message", x=0.1, y=0.2))
        battle = (line("FIGHT", x=0.8, y=0.7), line("POKÉMON", x=0.8, y=0.9))

        notification_score = RapidOcrEngine._orientation_score(notification, landscape=False)
        battle_score = RapidOcrEngine._orientation_score(battle, landscape=True)

        self.assertEqual(notification_score[1], 0)
        self.assertGreater(battle_score[1], 0)
        self.assertGreater(battle_score, notification_score)

    def test_reading_the_hud_decides_nothing(self) -> None:
        # Capa de lectura (COL-102): las placas del HUD salen tal como se ven,
        # en su orden y con su barra; qué slot e identidad les toca lo decide
        # `_hud_observations`, que es lo único que cambia el estado.
        parser = self.parser()
        lines = self.command_frame()
        before = (dict(parser._active), {side: dict(slots) for side, slots in parser._hud_alias_slots.items()})

        plates, _side_readings = parser._read_hud_side(lines, "p2", _health_readings(lines))

        self.assertEqual(
            [(plate.order, plate.species, plate.health) for plate in plates],
            [(0, "Steelix", "100/100"), (1, "Drampa", "100/100")],
        )
        self.assertEqual(
            (dict(parser._active), {side: dict(slots) for side, slots in parser._hud_alias_slots.items()}),
            before,
        )
        self.assertEqual(
            parser._hud_observations(lines),
            {
                "p1a": ("Delphox", "152/152"),
                "p1b": ("Victreebel", "187/187"),
                "p2a": ("Steelix", "100/100"),
                "p2b": ("Drampa", "100/100"),
            },
        )

    def test_the_second_reader_decides_only_lines_with_japanese(self) -> None:
        # COL-102, job 18241f89f82c4e83: lecturas reales de un mismo frame por
        # el modelo PP-OCRv6 `small` y el `medium`, con la misma caja.
        def reading(text: str, *, y: float = 0.72) -> OcrLine:
            return line(text, x=0.15, y=y, width=0.45)

        small = (
            reading("The opposingしごでき used Encore!"),
            reading("The opposing Garchomp protected itself!", y=0.80),
        )
        medium = (
            reading("The opposing しごでき used Encore!"),
            # En inglés el `medium` a veces se queda en dos letras.
            reading("TG", y=0.80),
        )

        merged = _with_japanese_second_opinion(small, medium)

        self.assertEqual(
            [value.text for value in merged],
            ["The opposing しごでき used Encore!", "The opposing Garchomp protected itself!"],
        )

    def test_the_second_reader_cannot_lose_part_of_a_nickname(self) -> None:
        small = (line("The opposing しごでき used Encore!", x=0.15, y=0.72, width=0.45),)
        medium = (line("The opposing でき used Encore!", x=0.15, y=0.72, width=0.45),)
        glued = (line("The opposing しごできused Encore!", x=0.15, y=0.72, width=0.45),)
        elsewhere = (line("The opposing しごでき used Encore!", x=0.15, y=0.30, width=0.45),)

        self.assertEqual(_with_japanese_second_opinion(small, medium), small)
        # Sin el espacio antes de "used" el parser ya no ve el movimiento.
        self.assertEqual(_with_japanese_second_opinion(small, glued), small)
        # Una lectura del `medium` en otro sitio de la pantalla no es la misma línea.
        self.assertEqual(_with_japanese_second_opinion(small, elsewhere), small)

    def test_the_second_reader_ignores_hud_marks_read_as_characters(self) -> None:
        # Job 18241f89f82c4e83: las marcas de la barra de vida salen como
        # "二川" en el 41 % de los frames; no son frases y no se tocan.
        small = (line("二川", x=0.88, y=0.73, width=0.05), line("しこでき", x=0.62, y=0.04, width=0.07))
        medium = (line("二二", x=0.88, y=0.73, width=0.05), line("しごでき", x=0.62, y=0.04, width=0.07))

        self.assertEqual(_with_japanese_second_opinion(small, medium), small)

    def test_parses_move_hp_change_deduplication_and_next_turn(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        action_frame = tuple(
            item
            for item in self.command_frame(p1_health="120/152")
            if item.text not in {"MOVE TIME", "FIGHT", "POKÉMON"}
        ) + (line("The opposing Steelix used Rock Slide!", x=0.12, y=0.7, width=0.42),)

        detections = parser.parse(action_frame, timestamp_ms=1_000, source_frame=1)

        self.assertEqual([event.kind for event in detections.events], ["move", "damage"])
        self.assertEqual(detections.events[0].slot, "p2a")
        self.assertEqual(detections.events[0].move, "Rock Slide")
        self.assertEqual(detections.events[1].slot, "p1a")
        self.assertEqual(detections.events[1].health, "120/152")

        repeated = parser.parse(action_frame, timestamp_ms=1_500, source_frame=2)
        self.assertEqual(repeated.events, ())

        next_turn = parser.parse(
            self.command_frame(p1_health="120/152"),
            timestamp_ms=7_000,
            source_frame=3,
        )
        self.assertEqual([event.turn for event in next_turn.events if event.kind == "turn"], [2])

    def test_move_menu_timer_cannot_create_hp_changes_or_empty_turns(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        action_frame = tuple(
            item
            for item in self.command_frame(p1_health="120/152")
            if item.text not in {"MOVE TIME", "FIGHT", "POKÉMON"}
        ) + (line("The opposing Steelix used Rock Slide!", x=0.12, y=0.7, width=0.42),)
        parser.parse(action_frame, timestamp_ms=1_000, source_frame=1)
        turn_two = parser.parse(
            self.command_frame(p1_health="120/152"),
            timestamp_ms=2_000,
            source_frame=2,
        )

        move_info = parser.parse(
            (
                line("Steelix", x=0.62, y=0.04),
                line("32", x=0.69, y=0.11, width=0.035),
                line("%", x=0.728, y=0.11, width=0.015),
                line("MOVE TIME", x=0.82, y=0.32),
                line("Move Info", x=0.80, y=0.84),
            ),
            timestamp_ms=2_500,
            source_frame=3,
        )
        back_to_fight = parser.parse(
            self.command_frame(p1_health="120/152"),
            timestamp_ms=3_000,
            source_frame=4,
        )

        self.assertEqual(
            [event.turn for event in turn_two.events if event.kind == "turn"],
            [2],
        )
        self.assertEqual(move_info.events, ())
        self.assertEqual(back_to_fight.events, ())

        parser.parse(action_frame, timestamp_ms=4_000, source_frame=5)
        turn_three = parser.parse(
            self.command_frame(p1_health="120/152"),
            timestamp_ms=5_000,
            source_frame=6,
        )
        self.assertEqual(
            [event.turn for event in turn_three.events if event.kind == "turn"],
            [3],
        )

    def test_a_transitional_menu_frame_does_not_leak_a_bad_hp_reading(self) -> None:
        # COL-102, job c5010e62d19e4663: el primer frame del menú sólo
        # enseña Battle Info; OCR pierde el 1 inicial del 100 % del rival.
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        transition = tuple(
            item
            for item in self.command_frame()
            if item.text not in {"MOVE TIME", "FIGHT", "POKÉMON"}
            and not (item.text == "100%" and item.left > 0.8)
        ) + (
            line("00%", x=0.90, y=0.11),
            line("Battle Info", x=0.82, y=0.83),
        )

        first_menu_frame = parser.parse(transition, timestamp_ms=500, source_frame=1)
        menu_open = parser.parse(self.command_frame(), timestamp_ms=1_000, source_frame=2)
        after_menu = parser.parse(
            tuple(
                item
                for item in self.command_frame()
                if item.text not in {"MOVE TIME", "FIGHT", "POKÉMON"}
            ),
            timestamp_ms=1_500,
            source_frame=3,
        )

        self.assertFalse(any(event.kind == "damage" for event in first_menu_frame.events))
        self.assertFalse(any(event.kind == "heal" for event in after_menu.events))
        self.assertFalse(any(event.kind in {"damage", "heal"} for event in menu_open.events))

    def test_pre_battle_weather_does_not_leave_turn_one_empty(self) -> None:
        # COL-102 (reabierta): un clima revelado por la habilidad de un lead,
        # antes de que exista turno 1, marcaba "actividad" que sobrevivía al
        # propio turno|1. En dobles el menú FIGHT/POKÉMON vuelve a aparecer
        # dentro del mismo turno -una vez por cada Pokémon activo- y esa
        # actividad heredada bastaba para cerrar el turno 1 como si ya
        # hubiera terminado, antes de que ninguna de sus acciones reales se
        # hubiera registrado.
        parser = self.parser()
        parser._battle_open = True
        weather = parser.parse(
            (line("A sandstorm kicked up!", x=0.12, y=0.5, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        # Nadie ha entrado todavía: el clima queda represado en vez de
        # narrarse antes que los propios Pokémon (COL-102, mismo hallazgo:
        # las habilidades no pueden salir antes que sus dueños).
        self.assertEqual(weather.events, ())

        turn_one = parser.parse(self.command_frame(), timestamp_ms=500, source_frame=1)
        self.assertEqual(
            [event.kind for event in turn_one.events],
            ["switch", "switch", "switch", "switch", "weather", "turn"],
        )
        self.assertEqual([event.turn for event in turn_one.events if event.kind == "turn"], [1])

        # El menú de selección de movimiento del segundo Pokémon activo:
        # oculta FIGHT/POKÉMON sin que haya ocurrido ninguna acción real.
        move_menu = parser.parse(
            (
                line("Steelix", x=0.62, y=0.04),
                line("32", x=0.69, y=0.11, width=0.035),
                line("%", x=0.728, y=0.11, width=0.015),
                line("MOVE TIME", x=0.82, y=0.32),
                line("Move Info", x=0.80, y=0.84),
            ),
            timestamp_ms=1_000,
            source_frame=2,
        )
        self.assertEqual(move_menu.events, ())

        second_active_pick = parser.parse(self.command_frame(), timestamp_ms=1_500, source_frame=3)
        self.assertEqual(
            [event.turn for event in second_active_pick.events if event.kind == "turn"],
            [],
        )

    def test_messages_before_the_first_switch_are_held_until_it_lands(self) -> None:
        # COL-102 (reapertura, hallazgo de Roku sobre el replay corregido):
        # el HUD tarda varios frames en estabilizar lo suficiente para
        # confirmar el switch de los leads, pero una habilidad que dispara
        # al entrar (Unnerve, un clima) se lee mucho antes. Sin represarla,
        # el replay narraba su efecto antes de que el Pokémon dueño
        # apareciera switcheado.
        parser = self.parser()
        parser._battle_open = True

        first = parser.parse(
            (line("Your side is too nervous to eat Berries!", x=0.15, y=0.73, width=0.45),),
            timestamp_ms=0,
            source_frame=0,
        )
        second = parser.parse(
            (line("A sandstorm kicked up!", x=0.15, y=0.73, width=0.35),),
            timestamp_ms=500,
            source_frame=1,
        )
        self.assertEqual(first.events, ())
        self.assertEqual(second.events, ())

        switched_in = parser.parse(self.command_frame(), timestamp_ms=1_000, source_frame=2)

        self.assertEqual(
            [(event.kind, event.value) for event in switched_in.events],
            [
                ("switch", None),
                ("switch", None),
                ("switch", None),
                ("switch", None),
                ("message", "Your side is too nervous to eat Berries!"),
                ("weather", "Sandstorm"),
                ("turn", None),
            ],
        )

    def test_an_opening_ability_keeps_its_place_ahead_of_its_effect(self) -> None:
        # COL-102, job 82923f56ce264a92, apertura de la Partida 1, con el
        # roster rival conocido desde el principio (segunda fase del job).
        # El banner de Intimidate se lee medio segundo antes que su "Attack
        # fell!", pero la habilidad esperaba aparte a que el HUD colocara a
        # Incineroar y salía detrás de su propio efecto y de la White Herb.
        roku = ("Charizard", "Tyranitar", "Milotic", "Incineroar", "Sneasler", "Sinistcha")
        rival = ("Incineroar", "Sneasler", "Slowking", "Pelipper", "Meganium", "Basculegion")
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="ShotgunDave",
                p1_team=roku,
                p2_team=rival,
                p1_aliases=(("Mate", "Sinistcha"), ("Silveria", "Sneasler")),
            ),
            catalog=ChampionsCatalog(
                species=tuple(dict.fromkeys((*roku, *rival))),
                abilities=("Intimidate",),
                species_abilities=(("Incineroar", ("Blaze", "Intimidate")),),
            ),
        )
        frames = (
            (250, 124_500, (line("ShotgunDave sent out Sneasler and Incineroar!", x=0.152, y=0.728, width=0.397),)),
            (258, 128_500, (line("Go! Mate and Silveria!", x=0.155, y=0.731, width=0.194),)),
            (
                265,
                132_000,
                (
                    line("Incineroar's", x=0.835, y=0.432, width=0.087, height=0.041),
                    line("Intimidate", x=0.843, y=0.466, width=0.083, height=0.053),
                ),
            ),
            (
                266,
                132_500,
                (
                    line("Incineroar's", x=0.835, y=0.431, width=0.087, height=0.043),
                    line("Intimidate", x=0.844, y=0.468, width=0.081, height=0.050),
                    line("Mate and Silveria's Attack fell!", x=0.155, y=0.732, width=0.259),
                ),
            ),
            (
                275,
                137_000,
                (line("Silveria returned its stats to normal using its White Herb!", x=0.154, y=0.73, width=0.475),),
            ),
        )
        for frame, ts, lines in frames:
            self.assertEqual(parser.parse(lines, timestamp_ms=ts, source_frame=frame).events, ())

        leads = parser.parse(
            (
                line("Incineroar", x=0.625, y=0.050, width=0.070),
                line("Sneasler", x=0.831, y=0.051, width=0.059),
                line("100%", x=0.689, y=0.108, width=0.066),
                line("100%", x=0.897, y=0.111, width=0.062),
                line("MOVE TIME", x=0.826, y=0.334, width=0.074),
                line("FIGHT", x=0.902, y=0.708, width=0.057),
                line("Mate", x=0.081, y=0.866, width=0.040),
                line("Silveria", x=0.287, y=0.867, width=0.053),
                line("POKÉMON", x=0.839, y=0.906, width=0.093),
                line("178/178", x=0.129, y=0.926, width=0.083),
                line("157/157", x=0.334, y=0.926, width=0.082),
            ),
            timestamp_ms=139_000,
            source_frame=279,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in leads.events if event.kind != "switch"],
            [
                ("ability", "p2a", "Intimidate"),
                ("message", None, "Sinistcha and Sneasler's Attack fell!"),
                ("message", None, "Sneasler returned its stats to normal using its White Herb!"),
                ("turn", None, None),
            ],
        )
        self.assertEqual([event.kind for event in leads.events[:4]], ["switch"] * 4)

    def test_reset_battle_state_allows_the_same_opening_in_a_second_battle(self) -> None:
        parser = self.parser()

        first = parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        parser.parse(
            (line("WIN", x=0.25, y=0.2, width=0.2),),
            timestamp_ms=1_000,
            source_frame=1,
        )
        parser.reset_battle_state()
        second = parser.parse(self.command_frame(), timestamp_ms=2_000, source_frame=2)

        self.assertEqual(
            [(event.kind, event.slot, event.species, event.turn) for event in first.events],
            [(event.kind, event.slot, event.species, event.turn) for event in second.events],
        )
        self.assertEqual(second.events[-1].turn, 1)

    def test_team_preview_learns_nicknames_and_pick_order_without_fake_hp(self) -> None:
        team = ("Blaziken", "Kingambit", "Basculegion", "Sylveon", "Torkoal", "Venusaur")
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=team),
            catalog=ChampionsCatalog(species=team),
        )
        # La lectura del Team Preview confirmó que las filas van en este orden.
        parser.bind_preview_team(team, side="p1")
        preview = parser.parse(
            (
                line("Roku", x=0.21, y=0.055),
                line("Latte", x=0.79, y=0.055),
                line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
                line("to send into battle.", x=0.38, y=0.215, width=0.16),
                line("Tonatiuh", x=0.145, y=0.125),
                line("Kingambit", x=0.145, y=0.24),
                line("Revenant", x=0.145, y=0.36),
                line("Nico", x=0.145, y=0.475),
                line("Gridnel", x=0.145, y=0.59),
                line("Venusaur", x=0.145, y=0.71),
                line("1", x=0.125, y=0.125, width=0.02),
                line("2", x=0.125, y=0.36, width=0.02),
                line("4", x=0.125, y=0.475, width=0.02),
                line("3", x=0.125, y=0.71, width=0.02),
                line("4/4", x=0.17, y=0.83, width=0.04),
            ),
            timestamp_ms=45_500,
            source_frame=91,
        )
        leads = parser.parse(
            (
                line(
                    "Go! Tonatiuh and Revenant the Paldea Champion!",
                    x=0.15,
                    y=0.72,
                    width=0.55,
                ),
            ),
            timestamp_ms=92_500,
            source_frame=185,
        )
        hud = parser.parse(
            (
                line("Tonatiuh", x=0.12, y=0.63),
                line("Revenant", x=0.34, y=0.63),
                line("180/180", x=0.17, y=0.70),
                line("198/198", x=0.39, y=0.70),
            ),
            timestamp_ms=93_000,
            source_frame=186,
        )

        self.assertTrue(preview.team_preview)
        self.assertFalse(preview.battle_started)
        self.assertEqual(preview.events, ())
        self.assertEqual(preview.p1_name, "Roku")
        self.assertEqual(preview.p2_name, "Latte")
        self.assertEqual(
            preview.p1_selected,
            ("Blaziken", "Basculegion", "Venusaur", "Sylveon"),
        )
        self.assertEqual(leads.events, ())
        self.assertEqual(
            [(event.slot, event.species) for event in hud.events],
            [("p1a", "Blaziken"), ("p1b", "Basculegion")],
        )

    def test_own_preview_rows_are_not_the_saved_team_order(self) -> None:
        # COL-102, job 8b7488cb5914449f (13 s): el equipo del job guarda
        # Rillaboom antes que Blaziken; la pantalla los muestra al revés.
        # Atando la fila 4 ("Tonatiuh", con Blazikenite) a roster[3], Blaziken
        # pasó toda la batalla como Rillaboom. Sin confirmar el orden, ni motes
        # ni picks; con las filas confirmadas, cada mote con su especie.
        saved = ("Indeedee-F", "Gardevoir", "Basculegion", "Rillaboom", "Blaziken", "Kingambit")
        on_screen = ("Indeedee-F", "Gardevoir", "Basculegion", "Blaziken", "Rillaboom", "Kingambit")
        panel = (
            line("Roku", x=0.21, y=0.055),
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
            line("Dee Dee", x=0.072, y=0.135),
            line("Suzuko", x=0.072, y=0.252),
            line("Revenant", x=0.073, y=0.371),
            line("Tonatiuh", x=0.073, y=0.486),
            line("Gori", x=0.072, y=0.602),
            line("Tomoe", x=0.072, y=0.717),
            line("1", x=0.125, y=0.486, width=0.02),
            line("1/4", x=0.17, y=0.83, width=0.04),
        )
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=saved),
            catalog=ChampionsCatalog(species=saved),
        )

        unconfirmed = parser.parse(panel, timestamp_ms=13_000, source_frame=27)
        self.assertNotIn("tonatiuh", parser.resolved_aliases()["p1"])
        self.assertEqual(unconfirmed.p1_selected, ())

        parser.bind_preview_team(on_screen, side="p1")
        confirmed = parser.parse(panel, timestamp_ms=20_000, source_frame=41)

        self.assertEqual(parser.resolved_aliases()["p1"]["tonatiuh"], "Blaziken")
        self.assertEqual(parser.resolved_aliases()["p1"]["gori"], "Rillaboom")
        self.assertEqual(confirmed.p1_selected, ("Blaziken",))

    def test_team_preview_emits_the_visual_opponent_roster(self) -> None:
        opponent = (
            "Swampert",
            "Metagross",
            "Pelipper",
            "Archaludon",
            "Sableye",
            "Basculegion",
        )
        parser = ChampionsTextParser(catalog=ChampionsCatalog(species=opponent))

        self.assertEqual(parser.bind_preview_team(opponent), opponent)
        preview = parser.parse(
            (
                line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
                line("to send into battle.", x=0.38, y=0.215, width=0.16),
            ),
            timestamp_ms=45_500,
            source_frame=91,
        )

        self.assertEqual(preview.p2_team, opponent)

    def test_visual_roster_removes_an_early_alias_inference_outside_the_team(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(
                species=("Sableye", "Ninetales-Alola"),
                species_moves=(
                    ("Sableye", ("Light Screen",)),
                    ("Ninetales-Alola", ("Light Screen",)),
                ),
            )
        )
        identity = parser._new_identity("p2", "Helper", "p2a")
        parser._bind_alias("p2", "Helper", "Ninetales-Alola")

        parser.bind_preview_team(("Sableye",))
        parser._infer_alias_from_move("p2", "Helper", "Light Screen")

        self.assertEqual(parser.resolved_identities(), {identity: "Sableye"})

    def test_a_move_cannot_overwrite_a_species_the_game_already_named(self) -> None:
        """COL-101: Psychic Fangs convirtió a Metagross en Lycanroc-Dusk.

        El juego había escrito "Mega Metagross" en pantalla minutos antes. Una
        deducción a partir del movimiento no puede pisar esa evidencia.
        """

        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(
                species=("Metagross", "Lycanroc-Dusk"),
                species_moves=(
                    ("Metagross", ("Psychic Fangs",)),
                    ("Lycanroc-Dusk", ("Psychic Fangs",)),
                ),
                species_teammates=(("Lycanroc-Dusk", "Sableye", 40),),
            )
        )
        identity = parser._new_identity("p2", "せんtせl", "p2a")
        parser._bind_alias("p2", "せんtせl", "Metagross", evidence="explicit")

        parser._infer_alias_from_move("p2", "せんtl)", "Psychic Fangs")

        self.assertEqual(parser.resolved_identities(), {identity: "Metagross"})

    def test_an_ocr_variant_of_a_known_nickname_inherits_its_species(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Metagross", "Lycanroc-Dusk"))
        )
        parser._new_identity("p2", "せんtせl", "p2a")
        parser._bind_alias("p2", "せんtせl", "Metagross", evidence="explicit")

        parser._bind_alias("p2", "せんtl)", "Lycanroc-Dusk", evidence="inferred")

        self.assertEqual(set(parser._aliases["p2"].values()), {"Metagross"})

    def test_the_game_text_still_corrects_a_misread_team_preview(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Sableye", "Spiritomb"))
        )
        identity = parser._new_identity("p2", "しでき", "p2a")
        parser._bind_alias("p2", "しでき", "Spiritomb", evidence="preview")

        parser._bind_alias("p2", "しでき", "Sableye", evidence="explicit")

        self.assertEqual(parser.resolved_identities(), {identity: "Sableye"})

    def test_both_sides_running_the_same_species_keep_their_moves(self) -> None:
        """El mensaje sin "The opposing" es del jugador, aunque el rival lleve lo mismo.

        Con Gardevoir en los dos equipos, el ataque del jugador acababa
        atribuido al rival porque el lado se volvía a deducir de la especie.
        """

        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="Hisagi-",
                p1_team=("Gardevoir",),
                p2_team=("Gardevoir",),
            ),
            catalog=ChampionsCatalog(species=("Gardevoir",), moves=("Hyper Voice",)),
        )
        parser.bind_preview_labels((("Suzuko", "Gardevoir"),), side="p1")
        parser._active["p1a"] = "Gardevoir"
        parser._active["p2a"] = "Gardevoir"

        own = parser.parse(
            (line("Suzuko used Hyper Voice!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=1_000,
            source_frame=2,
        )
        rival = parser.parse(
            (line("The opposing Gardevoir used Hyper Voice!", x=0.15, y=0.72, width=0.5),),
            timestamp_ms=2_000,
            source_frame=4,
        )

        self.assertTrue(own.events, "el ataque del jugador no produjo evento")
        self.assertTrue(rival.events)
        self.assertTrue(own.events[0].slot.startswith("p1"), own.events[0])
        self.assertTrue(rival.events[0].slot.startswith("p2"), rival.events[0])

    def test_the_preview_card_ties_each_nickname_to_its_species(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Gardevoir", "Sneasler"))
        )

        bound = parser.bind_preview_labels(
            (("Suzuko", "Gardevoir"), ("Silveria", "Sneasler")), side="p1"
        )

        self.assertEqual(bound, 2)
        self.assertEqual(set(parser._aliases["p1"].values()), {"Gardevoir", "Sneasler"})

    def test_mega_stone_reveals_an_unknown_opponent_nickname_and_slot(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_name="Rival"),
            catalog=ChampionsCatalog(
                species=("Metagross", "Metagross-Mega"),
                mega_stones=(("Metagrossite", "Metagross", "Metagross-Mega"),),
            ),
        )
        opening = parser.parse(
            (line("Rival sent out Helper and Sensei!", x=0.15, y=0.72, width=0.5),),
            timestamp_ms=1_000,
            source_frame=2,
        )
        revealed = parser.parse(
            (
                line(
                    "The opposing Sensei's Metagrossite is reacting to Rival's Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.7,
                ),
            ),
            timestamp_ms=2_000,
            source_frame=4,
        )

        self.assertEqual(opening.events, ())
        self.assertEqual(
            [(event.kind, event.slot, event.species, event.forme) for event in revealed.events],
            [
                ("switch", "p2b", "Metagross", None),
                ("mega", "p2b", "Metagross", "Metagross-Mega"),
            ],
        )

    def test_visual_hud_aliases_bind_japanese_nicknames_to_their_icons(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Metagross", "Sableye", "Indeedee", "Indeedee-F")),
        )
        hud = (
            line("せんせい", x=0.629, y=0.05, width=0.052),
            line("しごでき", x=0.799, y=0.05, width=0.051),
            line("100%", x=0.682, y=0.11, width=0.053),
            line("100%", x=0.851, y=0.11, width=0.053),
        )
        parser.parse(
            (line("Rival sent out しごでき and せんせい!", x=0.2, y=0.7, width=0.5),),
            timestamp_ms=88_500,
            source_frame=177,
        )

        self.assertEqual(
            parser.visual_alias_candidates(hud),
            (("p2", "せんせい"), ("p2", "しごでき")),
        )
        applied = parser.bind_visual_aliases(
            (
                HudAlias("p2", "せんせい", "Metagross", "M", 0.98),
                HudAlias("p2", "しごでき", "Sableye", "F", 0.97),
            )
        )
        detections = parser.parse(hud, timestamp_ms=173_000, source_frame=346)

        self.assertEqual([alias.species for alias in applied], ["Metagross", "Sableye"])
        self.assertEqual(
            [(event.slot, event.species) for event in detections.events],
            [("p2a", "Metagross"), ("p2b", "Sableye")],
        )

    def test_visual_hud_gender_selects_the_canonical_gendered_form(self) -> None:
        parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Indeedee", "Indeedee-F")),
        )

        applied = parser.bind_visual_aliases(
            (HudAlias("p2", "Helper", "Indeedee", "F", 0.96),)
        )

        self.assertEqual(applied[0].species, "Indeedee-F")

    def test_ignores_small_top_notification_that_contains_result_words(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)

        notification = parser.parse(
            (
                line("WhatsApp", x=0.03, y=0.02, width=0.12),
                line("You won the battle", x=0.03, y=0.07, width=0.32),
                line("WIN", x=0.82, y=0.08, width=0.04),
            ),
            timestamp_ms=500,
            source_frame=1,
        )

        self.assertIsNone(notification.winner)
        self.assertFalse(notification.battle_complete)

    def test_reconstructs_split_percentages_during_hp_animations(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        split_percentage = (
            line("Steelix", x=0.62, y=0.04),
            line("33", x=0.69, y=0.11, width=0.04),
            line("%", x=0.725, y=0.115, width=0.02),
        )

        detections = parser.parse(
            split_percentage,
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(len(detections.events), 1)
        self.assertEqual(detections.events[0].kind, "damage")
        self.assertEqual(detections.events[0].slot, "p2a")
        self.assertEqual(detections.events[0].health, "33/100")

    def test_a_percent_reread_over_the_tail_of_the_number_is_that_number(self) -> None:
        # COL-102, job 347da1c2ff16491b, frames 379-381 (turno 2, tras el Wave
        # Crash): la barra de Torkoal marca 63 % todo el tramo. En el frame
        # 380 el OCR devolvió "63" y, empezando dentro de su caja, "3%". Ese
        # "3%" se leía como una barra del 3 %: daño y, al frame siguiente, una
        # cura sin causa. Cajas reales de la traza.
        def hud(*readings: OcrLine) -> tuple[OcrLine, ...]:
            return (
                OcrLine(text="Torkoal", confidence=1.0, left=0.6255, top=0.0463, right=0.6802, bottom=0.0852),
                *readings,
            )

        number = OcrLine(text="63", confidence=1.0, left=0.7026, top=0.1111, right=0.7385, bottom=0.1583)
        sign = OcrLine(text="%", confidence=1.0, left=0.7312, top=0.1204, right=0.75, bottom=0.1546)
        reread = OcrLine(text="3%", confidence=0.89, left=0.7276, top=0.1157, right=0.7521, bottom=0.1583)

        self.assertEqual([health for _line, health in _health_readings(hud(number, reread))], ["63/100"])

        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Torkoal", "Indeedee-F")),
            catalog=ChampionsCatalog(species=("Torkoal", "Indeedee-F")),
        )
        parser.parse(hud(number, sign), timestamp_ms=189_000, source_frame=378)
        events = [
            event
            for frame, detections in enumerate(
                (hud(number, reread), hud(number, sign)), start=379
            )
            for event in parser.parse(detections, timestamp_ms=189_500 + 500 * (frame - 379), source_frame=frame).events
            if event.kind in {"damage", "heal"}
        ]

        self.assertEqual(events, [])

    def test_a_focus_sash_message_is_the_item_being_used_up(self) -> None:
        # COL-102, job 4eb88ad277cf4546, frame 357: "The opposing Kratos hung
        # on using its Focus Sash!" (Kratos = Ceruledge) quedaba como mensaje
        # suelto; en Showdown es un -enditem del Sash.
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Ceruledge", "Armarouge"), p2_aliases=(("Kratos", "Ceruledge"),)),
            catalog=ChampionsCatalog(species=("Ceruledge", "Armarouge"), items=("Focus Sash",)),
        )
        parser._battle_open = True
        parser._active["p2a"] = "Ceruledge"

        detections = parser.parse(
            (line("The opposing Kratos hung on using its Focus Sash!", x=0.154, y=0.73, width=0.5),),
            timestamp_ms=178_000,
            source_frame=357,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.species, event.value) for event in detections.events],
            [("enditem", "p2a", "Ceruledge", "Focus Sash")],
        )

    def test_a_flinch_is_a_lost_action_not_a_message(self) -> None:
        # COL-102, job 8b7488cb5914449f, partida 3.
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Pelipper", "Dragonite")),
            catalog=ChampionsCatalog(species=("Pelipper", "Dragonite")),
        )
        parser._battle_open = True
        parser._active["p2b"] = "Pelipper"

        detections = parser.parse(
            (line("The opposing Pelipper flinched and couldn't move!", x=0.154, y=0.73, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in detections.events],
            [("cant", "p2b", "flinch")],
        )

    def test_champions_paralysis_wording_sets_the_status(self) -> None:
        # COL-102, job 5748b289aa5b445b, frame 765: Champions anuncia la
        # parálisis como "…is paralyzed, so it may be unable to move!" y el
        # replay sólo lo copiaba como mensaje, sin -status.
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Sableye", "Sinistcha")),
            catalog=ChampionsCatalog(species=("Sableye", "Sinistcha")),
        )
        parser._battle_open = True
        parser._active["p2b"] = "Sableye"

        detections = parser.parse(
            (
                line(
                    "The opposing Sableye is paralyzed, so it may be unable to move!",
                    x=0.15,
                    y=0.72,
                    width=0.5,
                ),
            ),
            timestamp_ms=382_000,
            source_frame=765,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value) for event in detections.events],
            [("status", "p2b", "par")],
        )

    def test_uses_known_gendered_form_when_hud_omits_the_suffix(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Indeedee-F",)),
            catalog=ChampionsCatalog(species=("Indeedee", "Indeedee-F")),
        )

        detections = parser.parse(
            (
                line("Indeedee", x=0.62, y=0.04),
                line("100%", x=0.69, y=0.11),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(detections.events[0].species, "Indeedee-F")

    def test_parses_mega_reactions_with_nicknames_and_canonical_stones(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_team=("Gardevoir",),
                p2_team=("Gardevoir",),
                p1_aliases=(("Suzuko", "Gardevoir"),),
            ),
            catalog=ChampionsCatalog(
                species=("Gardevoir", "Gardevoir-Mega"),
                mega_stones=(("Gardevoirite", "Gardevoir", "Gardevoir-Mega"),),
            ),
        )
        parser.parse(
            (
                line("Gardevoir", x=0.62, y=0.04),
                line("Gardevoir", x=0.08, y=0.86),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        opposing = parser.parse(
            (
                line(
                    "The opposing Gardevoir's Gardevoirite is reacting to Hisagi-'s Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.62,
                ),
            ),
            timestamp_ms=80_500,
            source_frame=162,
        )
        local = parser.parse(
            (
                line(
                    "Suzuko's Gardevoirite is reacting to Roku's Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.46,
                ),
            ),
            timestamp_ms=173_000,
            source_frame=347,
        )

        self.assertEqual(
            [
                (event.kind, event.slot, event.species, event.forme, event.value)
                for event in (*opposing.events, *local.events)
            ],
            [
                ("mega", "p2a", "Gardevoir", "Gardevoir-Mega", "Gardevoirite"),
                ("mega", "p1a", "Gardevoir", "Gardevoir-Mega", "Gardevoirite"),
            ],
        )

    def test_a_mega_reaction_survives_ocr_dropping_the_space_before_to(self) -> None:
        # COL-102, job f53bd34897b84f86: el primer frame en que aparece el
        # aviso lee "reactingto" pegado ("Charizard's Charizardite X is
        # reactingto Roku's Omni Ring!"). El regex exigía el espacio, así
        # que ese frame caía a -message suelto; y como el overlay dedupe
        # (_visible_messages) ignora espacios, los frames siguientes -ya
        # bien leídos, con el espacio- se descartaban por "ya visto",
        # perdiendo la única oportunidad de resolver X/Y. Charizard nunca
        # llegaba a -mega, aunque Gengar (sin ambigüedad X/Y) sí.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Charizard",)),
            catalog=ChampionsCatalog(
                species=("Charizard", "Charizard-Mega-X"),
                mega_stones=(("Charizardite X", "Charizard", "Charizard-Mega-X"),),
            ),
        )
        parser.parse(
            (line("Charizard", x=0.08, y=0.86),),
            timestamp_ms=0,
            source_frame=0,
        )

        garbled = parser.parse(
            (
                line(
                    "Charizard's Charizardite X is reactingto Roku's Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.6,
                ),
            ),
            timestamp_ms=268_500,
            source_frame=538,
        )

        self.assertEqual(
            [
                (event.kind, event.slot, event.species, event.forme, event.value)
                for event in garbled.events
            ],
            [("mega", "p1a", "Charizard", "Charizard-Mega-X", "Charizardite X")],
        )

    def test_parses_explicit_mega_evolution_when_reaction_was_not_visible(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Gardevoir",)),
            catalog=ChampionsCatalog(
                species=("Gardevoir", "Gardevoir-Mega"),
                mega_stones=(("Gardevoirite", "Gardevoir", "Gardevoir-Mega"),),
            ),
        )
        parser.parse(
            (line("Gardevoir", x=0.62, y=0.04),),
            timestamp_ms=0,
            source_frame=0,
        )

        detections = parser.parse(
            (
                line(
                    "The opposing Gardevoir has Mega Evolved into Mega Gardevoir!",
                    x=0.15,
                    y=0.72,
                    width=0.55,
                ),
            ),
            timestamp_ms=90_000,
            source_frame=181,
        )

        self.assertEqual(detections.events[0].kind, "mega")
        self.assertEqual(detections.events[0].forme, "Gardevoir-Mega")
        self.assertEqual(detections.events[0].value, "Gardevoirite")

    def test_uses_withdrawal_and_send_out_text_for_the_switch_timing(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="Hisagi-",
                p1_team=("Basculegion",),
                p2_team=("Armarouge", "Ninetales-Alola"),
                p1_aliases=(("Revenant", "Basculegion"),),
                p2_aliases=(("Ninetales", "Ninetales-Alola"),),
            ),
            catalog=ChampionsCatalog(
                species=("Basculegion", "Armarouge", "Ninetales-Alola"),
                moves=("Wave Crash",),
            ),
        )
        parser.parse(
            (line("Armarouge", x=0.62, y=0.04),),
            timestamp_ms=0,
            source_frame=0,
        )

        withdrew = parser.parse(
            (line("Hisagi-withdrew Armarouge!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=163_500,
            source_frame=328,
        )
        switched = parser.parse(
            (line("Hisagi- sent out Ninetales!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=167_500,
            source_frame=336,
        )
        moved = parser.parse(
            (line("Revenant used Wave Crash!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=184_000,
            source_frame=369,
        )

        self.assertEqual(withdrew.events, ())
        self.assertEqual(
            [(event.kind, event.slot, event.species) for event in switched.events],
            [("switch", "p2a", "Ninetales-Alola")],
        )
        self.assertEqual(moved.events[0].kind, "move")
        self.assertLess(switched.events[0].timestamp_ms, moved.events[0].timestamp_ms)

        hud_confirmation = parser.parse(
            (line("Ninetales", x=0.62, y=0.04),),
            timestamp_ms=187_000,
            source_frame=375,
        )
        self.assertEqual(hud_confirmation.events, ())

    def test_delays_psychic_surge_and_terrain_until_indeedee_has_a_slot(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="Hisagi-",
                p2_team=("Indeedee-F", "Gardevoir"),
            ),
            catalog=ChampionsCatalog(
                species=("Indeedee", "Indeedee-F", "Gardevoir"),
                abilities=("Psychic Surge",),
            ),
        )

        ability_overlay = parser.parse(
            (
                line("Indeedee's", x=0.838, y=0.363, width=0.085),
                line("Psychic Surge", x=0.818, y=0.399, width=0.108),
            ),
            timestamp_ms=61_500,
            source_frame=124,
        )
        terrain_message = parser.parse(
            (
                line("Indeedee's", x=0.838, y=0.363, width=0.085),
                line("Psychic Surge", x=0.818, y=0.399, width=0.108),
                line("The battlefield got weird!", x=0.153, y=0.727, width=0.223),
            ),
            timestamp_ms=62_000,
            source_frame=125,
        )
        active_hud = parser.parse(
            (
                line("Indeedee", x=0.62, y=0.04),
                line("100%", x=0.69, y=0.11),
            ),
            timestamp_ms=68_500,
            source_frame=138,
        )

        self.assertEqual(ability_overlay.events, ())
        self.assertEqual(terrain_message.events, ())
        self.assertEqual(
            [(event.kind, event.slot, event.species, event.value) for event in active_hud.events],
            [
                ("switch", "p2a", "Indeedee-F", None),
                ("ability", "p2a", "Indeedee-F", "Psychic Surge"),
                ("fieldstart", None, None, "move: Psychic Terrain"),
            ],
        )
        self.assertEqual(
            active_hud.events[2].tags,
            ("[from] ability: Psychic Surge", "[of] p2a: Indeedee-F"),
        )

    def item_parser(self) -> ChampionsTextParser:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="IesYo",
                p2_name="Rival",
                p1_team=("Delphox", "Victreebel"),
                p2_team=("Steelix", "Drampa", "Umbreon"),
            ),
            catalog=ChampionsCatalog(
                species=("Delphox", "Victreebel", "Steelix", "Drampa", "Umbreon"),
                items=("Sitrus Berry", "Leftovers", "Occa Berry"),
            ),
        )
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        return parser

    @staticmethod
    def hud(*, steelix: str = "100%", drampa: str = "100%", delphox: str = "152/152") -> tuple[OcrLine, ...]:
        return (
            line("Steelix", x=0.62, y=0.04),
            line("Drampa", x=0.83, y=0.04),
            line(steelix, x=0.69, y=0.11),
            line(drampa, x=0.90, y=0.11),
            line("Delphox", x=0.08, y=0.86),
            line("Victreebel", x=0.29, y=0.86),
            line(delphox, x=0.13, y=0.93),
            line("187/187", x=0.34, y=0.93),
        )

    @staticmethod
    def banner(owner: str, label: str, *, right: bool) -> tuple[OcrLine, ...]:
        # Posiciones reales: a la derecha en el job 82923f56ce264a92 (16:9),
        # a la izquierda en la grabación de móvil del 18241f89f82c4e83.
        x = 0.835 if right else 0.12
        return (
            line(owner, x=x, y=0.432, width=0.087, height=0.035),
            line(label, x=x, y=0.466, width=0.088, height=0.035),
        )

    def test_an_item_banner_that_looks_like_an_ability_stays_an_item(self) -> None:
        # COL-102, job 347da1c2ff16491b, frame 181: "Silveria's / Psychic
        # Seed" es el objeto de Sneasler, pero se parece a "Psychic Surge" por
        # encima del umbral y el replay le daba a Sneasler esa habilidad.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Sneasler",), p1_aliases=(("Silveria", "Sneasler"),)),
            catalog=ChampionsCatalog(
                species=("Sneasler",),
                abilities=("Psychic Surge",),
                items=("Psychic Seed",),
            ),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Sneasler"

        detections = parser.parse(
            self.banner("Silveria's", "Psychic Seed", right=False),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual([event for event in detections.events if event.kind == "ability"], [])

    def test_a_heal_after_an_item_banner_names_the_item(self) -> None:
        # COL-102, job 82923f56ce264a92, frames 631-637: el juego no narra la
        # Sitrus Berry en el cuadro de texto; muestra "Incineroar's / Sitrus
        # Berry" y la barra sube de 28 % a 52 %. Sin esto la cura no tenía causa.
        parser = self.item_parser()
        parser.parse(self.hud(steelix="28%"), timestamp_ms=1_000, source_frame=1)
        for index in range(3):
            parser.parse(
                self.banner("Steelix's", "Sitrus Berry", right=True),
                timestamp_ms=2_000 + 500 * index,
                source_frame=2 + index,
            )
        healed = parser.parse(self.hud(steelix="52%"), timestamp_ms=4_000, source_frame=5)

        self.assertEqual(
            [(event.kind, event.slot, event.value, event.health, event.tags) for event in healed.events],
            [
                ("enditem", "p2a", "Sitrus Berry", None, ("[eat]",)),
                ("heal", "p2a", None, "52/100", ("[from] item: Sitrus Berry",)),
            ],
        )

    def test_leftovers_on_the_own_side_heal_without_being_eaten(self) -> None:
        # Job 18241f89f82c4e83, frames 520-527: "Gridnel's / Leftovers" a la
        # izquierda y Garchomp sube 1/16 al final del turno.
        parser = self.item_parser()
        parser.parse(self.hud(delphox="100/152"), timestamp_ms=1_000, source_frame=1)
        parser.parse(self.banner("Delphox's", "Leftovers", right=False), timestamp_ms=2_000, source_frame=2)
        healed = parser.parse(self.hud(delphox="109/152"), timestamp_ms=3_500, source_frame=3)

        self.assertEqual(
            [(event.kind, event.slot, event.tags) for event in healed.events],
            [("heal", "p1a", ("[from] item: Leftovers",))],
        )

    def test_an_item_banner_does_not_claim_a_hit_or_someone_elses_heal(self) -> None:
        parser = self.item_parser()
        # Una baya que reduce el daño va seguida del golpe, no de una cura.
        parser.parse(self.banner("Steelix's", "Occa Berry", right=True), timestamp_ms=1_000, source_frame=1)
        hit = parser.parse(self.hud(steelix="70%"), timestamp_ms=1_500, source_frame=2)
        # La Sitrus Berry de Steelix no explica que Drampa se cure.
        parser.parse(self.hud(steelix="70%", drampa="40%"), timestamp_ms=2_000, source_frame=3)
        parser.parse(self.banner("Steelix's", "Sitrus Berry", right=True), timestamp_ms=2_500, source_frame=4)
        other = parser.parse(self.hud(steelix="70%", drampa="60%"), timestamp_ms=3_000, source_frame=5)

        self.assertEqual([(event.kind, event.tags) for event in hit.events], [("damage", ())])
        self.assertEqual([(event.kind, event.slot, event.tags) for event in other.events], [("heal", "p2b", ())])

    def test_parses_terrain_ending_for_all_four_kinds(self) -> None:
        endings = (
            ("The weirdness disappeared from the battlefield!", "Psychic Terrain"),
            ("The electricity disappeared from the battlefield!", "Electric Terrain"),
            ("The grass disappeared from the battlefield!", "Grassy Terrain"),
            ("The mist disappeared from the battlefield!", "Misty Terrain"),
        )
        for index, (text, terrain) in enumerate(endings):
            with self.subTest(terrain=terrain):
                parser = self.parser()
                parser._battle_open = True
                parser._active["p1a"] = "Delphox"
                detections = parser.parse(
                    (line(text, x=0.15, y=0.72, width=0.6),),
                    timestamp_ms=1_000 * index,
                    source_frame=index,
                )
                self.assertEqual(
                    [(event.kind, event.value) for event in detections.events],
                    [("fieldend", f"move: {terrain}")],
                )

    def trick_room_parser(self) -> ChampionsTextParser:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="Potty94",
                p1_team=("Milotic", "Sinistcha"),
                p1_aliases=(("Mate", "Sinistcha"),),
            ),
            catalog=ChampionsCatalog(
                species=("Milotic", "Sinistcha", "Garchomp", "Whimsicott"),
                moves=("Trick Room",),
            ),
        )
        parser._active.update(
            {"p1a": "Milotic", "p1b": "Sinistcha", "p2a": "Garchomp", "p2b": "Whimsicott"}
        )
        parser._battle_open = True
        return parser

    def test_trick_room_starts_and_ends_as_a_field_effect(self) -> None:
        # COL-102, job 82923f56ce264a92, Partida 2: los dos avisos quedaban
        # como -message y el visor nunca mostraba el campo invertido. Textos,
        # posiciones y frames reales de ocr.trace.jsonl.
        parser = self.trick_room_parser()

        used = parser.parse(
            (line("Mate used Trick Room!", x=0.154, y=0.731, width=0.197),),
            timestamp_ms=788_500,
            source_frame=1578,
        )
        started = parser.parse(
            (line("Mate twisted the dimensions!", x=0.155, y=0.733, width=0.247),),
            timestamp_ms=793_000,
            source_frame=1587,
        )
        ended = parser.parse(
            (line("The twisted dimensions returned to normal!", x=0.152, y=0.727, width=0.368),),
            timestamp_ms=1_041_500,
            source_frame=2084,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.move) for event in used.events],
            [("move", "p1b", "Trick Room")],
        )
        # Lo mismo que escribe el servidor de Showdown: quien lo activó va en
        # [of], y sin él el visor deja vacío el nombre del aviso.
        self.assertEqual(
            [(event.kind, event.value, event.tags) for event in started.events],
            [("fieldstart", "move: Trick Room", ("[of] p1b: Sinistcha",))],
        )
        self.assertEqual(
            [(event.kind, event.value, event.tags) for event in ended.events],
            [("fieldend", "move: Trick Room", ())],
        )

    def test_trick_room_does_not_name_a_user_that_is_not_on_the_field(self) -> None:
        parser = self.trick_room_parser()
        parser._active.pop("p2b")

        started = parser.parse(
            (line("The opposing Whimsicott twisted the dimensions!", x=0.152, y=0.73, width=0.4),),
            timestamp_ms=0,
            source_frame=0,
        )

        # El campo sí cambia; a quién atribuirlo no se adivina.
        self.assertEqual(
            [(event.kind, event.value, event.tags) for event in started.events],
            [("fieldstart", "move: Trick Room", ())],
        )

    def test_a_pokemon_buffeted_by_sandstorm_is_narrated_once_per_turn(self) -> None:
        # COL-102: el mismo aviso se relee con el mote un poco distinto
        # entre frames ("Mate"/"Frida" en el job ciego); sin represarlo por
        # slot y turno, cada lectura escribía su propio -message. A
        # diferencia del objeto (una vez por batalla), la tormenta puede
        # volver a golpear al mismo Pokémon cada turno, así que el límite
        # es por turno, no permanente.
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_team=("Milotic",),
                p1_aliases=(("Mate", "Milotic"), ("Mati", "Milotic")),
            ),
            catalog=ChampionsCatalog(species=("Milotic",)),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Milotic"
        parser._turn = 3

        first = parser.parse(
            (line("Mate is buffeted by the sandstorm!", x=0.15, y=0.73, width=0.4),),
            timestamp_ms=0,
            source_frame=0,
        )
        # El mismo golpe, releído con el mote un poco distinto: no se repite.
        reread = parser.parse(
            (line("Mati is buffeted by the sandstorm!", x=0.15, y=0.73, width=0.4),),
            timestamp_ms=500,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in first.events],
            [("message", "Milotic is buffeted by the sandstorm!")],
        )
        self.assertEqual(reread.events, ())

        parser._turn = 4
        next_turn = parser.parse(
            (line("Mate is buffeted by the sandstorm!", x=0.15, y=0.73, width=0.4),),
            timestamp_ms=1_000,
            source_frame=2,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in next_turn.events],
            [("message", "Milotic is buffeted by the sandstorm!")],
        )

    def test_two_different_pokemon_buffeted_back_to_back_both_get_narrated(self) -> None:
        # El hallazgo que tiró abajo el intento de represar mensajes por
        # ventana de tiempo: "Sylveon is buffeted..." y, sin ningún frame
        # vacío entre medio, "Mate is buffeted..." -dos Pokémon distintos,
        # no una relectura del mismo aviso. Separarlos por identidad (slot)
        # en vez de por hueco de pantalla los mantiene a los dos.
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_team=("Milotic",),
                p2_team=("Sylveon",),
                p1_aliases=(("Mate", "Milotic"),),
            ),
            catalog=ChampionsCatalog(species=("Milotic", "Sylveon")),
        )
        parser._battle_open = True
        parser._active["p1a"] = "Milotic"
        parser._active["p2a"] = "Sylveon"
        parser._turn = 6

        sylveon = parser.parse(
            (line("The opposing Sylveon is buffeted by the sandstorm!", x=0.15, y=0.73, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        milotic = parser.parse(
            (line("Mate is buffeted by the sandstorm!", x=0.15, y=0.73, width=0.4),),
            timestamp_ms=500,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in sylveon.events],
            [("message", "The opposing Sylveon is buffeted by the sandstorm!")],
        )
        self.assertEqual(
            [(event.kind, event.value) for event in milotic.events],
            [("message", "Milotic is buffeted by the sandstorm!")],
        )

    def test_a_perish_count_tick_is_narrated_once_per_turn(self) -> None:
        # COL-102, job f53bd34897b84f86: "fell" sale como "fll" o "fel t"
        # en frames distintos del mismo aviso, así que el mismo tic de
        # Perish Song escribía dos o tres -message. Mismo criterio que
        # buffeted: una vez por slot y turno, no por texto exacto.
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=("Politoed",)),
            catalog=ChampionsCatalog(species=("Politoed",)),
        )
        parser._battle_open = True
        parser._active["p2a"] = "Politoed"
        parser._turn = 6

        first = parser.parse(
            (
                line(
                    "The opposing Politoed's perish count fll to 3!",
                    x=0.15,
                    y=0.72,
                    width=0.55,
                ),
            ),
            timestamp_ms=0,
            source_frame=0,
        )
        reread = parser.parse(
            (
                line(
                    "The opposing Politoed's perish count fel t 3!",
                    x=0.15,
                    y=0.72,
                    width=0.55,
                ),
            ),
            timestamp_ms=500,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in first.events],
            [("message", "The opposing Politoed's perish count fell to 3!")],
        )
        self.assertEqual(reread.events, ())

        parser._turn = 7
        next_turn = parser.parse(
            (
                line(
                    "The opposing Politoed's perish count fell to 2!",
                    x=0.15,
                    y=0.72,
                    width=0.55,
                ),
            ),
            timestamp_ms=1_000,
            source_frame=2,
        )
        self.assertEqual(
            [(event.kind, event.value) for event in next_turn.events],
            [("message", "The opposing Politoed's perish count fell to 2!")],
        )

    def test_a_perish_count_hitting_zero_reads_the_letter_o_as_a_digit(self) -> None:
        # El mismo aviso a cero lee "O" (letra) en vez de "0" (dígito).
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Charizard",)),
            catalog=ChampionsCatalog(species=("Charizard",)),
        )
        parser._battle_open = True
        parser._active["p1b"] = "Charizard"
        parser._turn = 9

        detections = parser.parse(
            (line("Charizard's perish count fell to O!", x=0.15, y=0.72, width=0.45),),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(
            [(event.kind, event.value) for event in detections.events],
            [("message", "Charizard's perish count fell to 0!")],
        )

    def test_a_knocked_off_item_becomes_a_structured_enditem(self) -> None:
        # COL-102: el vacío de objetos que Roku pidió auditar. El objeto de un
        # Pokémon sólo se puede perder una vez, pero el OCR repite el aviso con
        # el nombre ligeramente distinto en cada frame ("Strus Berry",
        # "Sitrus Berry"); sin represarlo, el mismo Knock Off escribía su
        # -enditem dos o tres veces.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Tyranitar",), p2_team=("Farigiraf",)),
            catalog=ChampionsCatalog(species=("Tyranitar", "Farigiraf")),
        )
        parser.parse(
            (
                line("Tyranitar", x=0.08, y=0.86),
                line("Farigiraf", x=0.62, y=0.04),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        first = parser.parse(
            (
                line(
                    "Tyranitar knocked off the opposing Farigiraf's Strus Berry!",
                    x=0.15,
                    y=0.72,
                    width=0.6,
                ),
            ),
            timestamp_ms=1_000,
            source_frame=1,
        )
        repeated = parser.parse(
            (
                line(
                    "Tyranitar knocked of the opposing Farigiraf's Sitrus Berry!",
                    x=0.15,
                    y=0.72,
                    width=0.6,
                ),
            ),
            timestamp_ms=1_500,
            source_frame=2,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.value, event.tags) for event in first.events],
            [
                (
                    "enditem",
                    "p2a",
                    "Strus Berry",
                    ("[from] move: Knock Off", "[of] p1a: Tyranitar"),
                )
            ],
        )
        self.assertEqual(repeated.events, ())

    def test_a_knocked_off_item_is_corrected_against_the_catalog(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Tyranitar",), p2_team=("Farigiraf",)),
            catalog=ChampionsCatalog(
                species=("Tyranitar", "Farigiraf"),
                items=("Sitrus Berry",),
            ),
        )
        parser.parse(
            (
                line("Tyranitar", x=0.08, y=0.86),
                line("Farigiraf", x=0.62, y=0.04),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        detections = parser.parse(
            (
                line(
                    "Tyranitar knocked off the opposing Farigiraf's Strus Berry!",
                    x=0.15,
                    y=0.72,
                    width=0.6,
                ),
            ),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(detections.events[0].value, "Sitrus Berry")

    def test_an_evaded_attack_becomes_a_structured_miss(self) -> None:
        # COL-102, job 82923f56ce264a92: "X avoided the attack!" quedaba
        # como -message suelto, sin el feedback de miss que sí tiene el
        # protocolo Showdown. El aviso nombra a quien esquivó -eso ya lo
        # resuelve el parser a slot-, y showdown.py completa después quién
        # atacó buscando el movimiento más reciente de la misma acción.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Milotic",), p2_team=("Incineroar",)),
            catalog=ChampionsCatalog(species=("Milotic", "Incineroar")),
        )
        parser._active["p1b"] = "Milotic"
        parser._battle_open = True

        own_side = parser.parse(
            (line("Milotic avoided the attack!", x=0.15, y=0.72, width=0.45),),
            timestamp_ms=0,
            source_frame=0,
        )
        self.assertEqual(
            [(event.kind, event.slot, event.target_slot) for event in own_side.events],
            [("miss", None, "p1b")],
        )

        parser._active["p2a"] = "Incineroar"
        opposing = parser.parse(
            (line("The opposing Incineroar avoided the attack!", x=0.15, y=0.72, width=0.6),),
            timestamp_ms=500,
            source_frame=1,
        )
        self.assertEqual(
            [(event.kind, event.slot, event.target_slot) for event in opposing.events],
            [("miss", None, "p2a")],
        )

    def test_a_typo_d_name_matches_the_pokemon_already_on_screen(self) -> None:
        # COL-102, job 82923f56ce264a92: sin equipo rival conocido (nunca se
        # vio su Team Preview, sólo sprites sin texto), el resolutor de
        # especies cae al catálogo completo y sólo acepta coincidencia
        # exacta -adivinar entre ~1000 especies es demasiado arriesgado. Un
        # solo fallo de OCR ("Sheasler" por "Sneasler") bastaba entonces
        # para que el HUD leyera un Pokémon nuevo donde ya había uno activo,
        # y el replay escribía un switch fantasma del mismo Sneasler a sí
        # mismo, a full HP, sin que nadie se hubiera ido ni vuelto.
        parser = ChampionsTextParser(
            context=DetectorContext(p2_team=()),
            catalog=ChampionsCatalog(species=("Sneasler",)),
        )
        parser._active["p2b"] = "Sneasler"
        # El slot ya quedó ligado a este mote por un anuncio anterior
        # ("Rival sent out Sneasler!"), igual que en el job real -sin esto,
        # _hud_species descarta la lectura entera por no resolver especie
        # ni identidad, y el bug ni se manifiesta.
        parser._announced_slots["p2"]["sneasler"] = "p2b"
        self.assertFalse(parser._known_teams["p2"])

        detections = parser.parse(
            (
                line("Sheasler", x=0.831, y=0.05),
                line("100%", x=0.9, y=0.05),
            ),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(detections.events, ())

    def test_a_self_targeting_move_names_its_own_slot(self) -> None:
        # COL-102 (reapertura): Protect no le pega a nadie, pero sin la clase
        # de objetivo real del movimiento la heurística de proximidad podía
        # engancharse a daño de otra acción y animar el bloqueo contra el
        # rival equivocado.
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Milotic",)),
            catalog=ChampionsCatalog(
                species=("Milotic",),
                moves=("Protect",),
                move_targets=(("Protect", "self"),),
            ),
        )
        parser._active["p1a"] = "Milotic"

        detections = parser.parse(
            (line("Milotic used Protect!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.target_slot) for event in detections.events],
            [("move", "p1a", "p1a")],
        )

    def test_an_ally_targeting_move_names_the_ally_slot(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(p1_team=("Milotic", "Incineroar")),
            catalog=ChampionsCatalog(
                species=("Milotic", "Incineroar"),
                moves=("Life Dew",),
                move_targets=(("Life Dew", "allies"),),
            ),
        )
        parser._active["p1a"] = "Incineroar"
        parser._active["p1b"] = "Milotic"

        detections = parser.parse(
            (line("Milotic used Life Dew!", x=0.15, y=0.72, width=0.4),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(
            [(event.kind, event.slot, event.target_slot) for event in detections.events],
            [("move", "p1b", "p1a")],
        )

    def test_parses_faint_and_result_without_a_visual_model(self) -> None:
        parser = self.parser()
        parser._active["p2a"] = "Umbreon"
        faint = parser.parse(
            (line("The opposing Umbreon fainted!", x=0.2, y=0.7, width=0.35),),
            timestamp_ms=2_000,
            source_frame=4,
        )
        result = parser.parse(
            (line("WIN", x=0.25, y=0.2, width=0.2),),
            timestamp_ms=3_000,
            source_frame=5,
        )

        self.assertEqual(faint.events[0].kind, "faint")
        self.assertEqual(faint.events[0].slot, "p2a")
        self.assertEqual(result.winner, "p1")
        self.assertTrue(result.battle_complete)

    def test_recognizes_real_loss_message_and_two_sided_result_screen(self) -> None:
        parser = self.parser()
        message_result = parser.parse(
            (line("You lost to Hisagi-!", x=0.25, y=0.7, width=0.3),),
            timestamp_ms=3_000,
            source_frame=5,
        )
        screen_result = self.parser().parse(
            (
                line("LOST...", x=0.2, y=0.3, width=0.15),
                line("WON!", x=0.7, y=0.3, width=0.15),
            ),
            timestamp_ms=3_500,
            source_frame=6,
        )

        self.assertEqual(message_result.winner, "p2")
        self.assertTrue(message_result.battle_complete)
        self.assertEqual(screen_result.winner, "p2")

    def test_uses_explicit_nickname_aliases_without_guessing_unknown_names(self) -> None:
        context = DetectorContext(
            p1_team=("Basculegion",),
            p2_team=("Gardevoir",),
            p1_aliases=(("Revenant", "Basculegion"),),
        )
        catalog = ChampionsCatalog(
            species=("Basculegion", "Gardevoir", "Trevenant"),
            moves=("Wave Crash", "Psywave"),
        )
        parser = ChampionsTextParser(context=context, catalog=catalog)
        detections = parser.parse(
            (
                line("Revenant", x=0.12, y=0.86),
                line("198/198", x=0.14, y=0.93),
                line("Revenant used Wave Crash!", x=0.12, y=0.7, width=0.35),
            ),
            timestamp_ms=0,
            source_frame=0,
        )
        without_alias = ChampionsTextParser(catalog=catalog).parse(
            (line("Revenant", x=0.12, y=0.86),),
            timestamp_ms=0,
            source_frame=0,
        )

        self.assertEqual(detections.events[0].species, "Basculegion")
        self.assertEqual(detections.events[1].move, "Wave Crash")
        self.assertEqual(without_alias.events, ())

    def test_ignores_battle_info_overlay_and_truncated_moves(self) -> None:
        parser = self.parser()
        parser.parse(self.command_frame(), timestamp_ms=0, source_frame=0)
        overlay = (
            line("Close", x=0.7, y=0.92),
            line("Delphox", x=0.08, y=0.86),
            line("06:45", x=0.06, y=0.93),
            line("Delphox used Rock", x=0.12, y=0.7, width=0.3),
        )

        detections = parser.parse(overlay, timestamp_ms=1_000, source_frame=1)

        self.assertEqual(detections.events, ())

    def test_detector_writes_an_optional_jsonl_trace(self) -> None:
        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return (line("The opposing Umbreon fainted!", x=0.2, y=0.7, width=0.35),)

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "ocr.jsonl"
            detector = ChampionsOcrDetector(
                context=DetectorContext(p2_team=("Umbreon",)),
                engine=FakeEngine(),
                trace_path=trace,
            )
            detector.parser._active["p2a"] = "Umbreon"
            detections = detector.detect(
                FramePacket(index=6, timestamp_ms=4_000, image=b"jpeg")
            )
            record = json.loads(trace.read_text(encoding="utf-8"))

        self.assertEqual(detections.events[0].kind, "faint")
        self.assertEqual(record["frame"], 7)
        self.assertEqual(record["ocr"][0]["text"], "The opposing Umbreon fainted!")
        self.assertEqual(record["detections"]["events"][0]["kind"], "faint")

    def test_detector_resets_stable_identities_between_battles(self) -> None:
        class FakeEngine:
            def read(self, image: bytes) -> tuple[OcrLine, ...]:
                return (line(image.decode(), x=0.2, y=0.7),)

        detector = ChampionsOcrDetector(
            context=DetectorContext(p2_team=("Umbreon", "Drampa")),
            engine=FakeEngine(),
        )
        identity = detector.parser._new_identity("p2", "Shade", "p2a")
        detector.parser._bind_alias("p2", "Shade", "Umbreon")
        self.assertEqual(detector.resolved_identities(), {identity: "Umbreon"})
        detector.reset_battle_state()
        detector.parser._active["p2a"] = "Drampa"
        detections = detector.detect(
            FramePacket(
                index=1,
                timestamp_ms=1_000,
                image=b"The opposing Drampa fainted!",
            )
        )

        self.assertEqual(detector.resolved_identities(), {})
        self.assertEqual(detections.events[0].species, "Drampa")

    def test_detector_resolves_stable_unknown_hud_aliases_in_the_background(self) -> None:
        hud = (
            line("せんせい", x=0.629, y=0.05, width=0.052),
            line("しごでき", x=0.799, y=0.05, width=0.051),
            line("100%", x=0.682, y=0.11, width=0.053),
            line("100%", x=0.851, y=0.11, width=0.053),
        )

        class FakeEngine:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                self.calls += 1
                if self.calls == 1:
                    return (
                        line(
                            "Rival sent out しごでき and せんせい!",
                            x=0.2,
                            y=0.7,
                            width=0.5,
                        ),
                    )
                return hud

        class FakeAliasResolver:
            def resolve(
                self,
                _frame: FramePacket,
                _candidates: tuple[tuple[str, str], ...],
            ) -> tuple[HudAlias, ...]:
                return (
                    HudAlias("p2", "せんせい", "Metagross", "M", 0.98),
                    HudAlias("p2", "しごでき", "Sableye", "F", 0.97),
                )

        detector = ChampionsOcrDetector(engine=FakeEngine(), alias_resolver=FakeAliasResolver())
        detector.parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Metagross", "Sableye")),
        )
        try:
            detector.detect(FramePacket(index=0, timestamp_ms=0, image=b"jpeg"))
            detector.detect(FramePacket(index=1, timestamp_ms=500, image=b"jpeg"))
            detector.detect(FramePacket(index=2, timestamp_ms=1_000, image=b"jpeg"))
            self.assertIsNotNone(detector._alias_future)
            detector._alias_future.result(timeout=1)  # type: ignore[union-attr]
            detections = detector.detect(FramePacket(index=3, timestamp_ms=1_500, image=b"jpeg"))
        finally:
            detector.close()

        self.assertEqual(detections.events, ())
        self.assertEqual(
            set(detector.resolved_identities().values()),
            {"Metagross", "Sableye"},
        )

    def test_detector_flushes_pending_visual_aliases_before_battle_reset(self) -> None:
        hud = (
            line("せんせい", x=0.629, y=0.05, width=0.052),
            line("しごでき", x=0.799, y=0.05, width=0.051),
            line("100%", x=0.682, y=0.11, width=0.053),
            line("100%", x=0.851, y=0.11, width=0.053),
        )
        release = threading.Event()

        class FakeEngine:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                self.calls += 1
                if self.calls == 1:
                    return (
                        line(
                            "Rival sent out しごでき and せんせい!",
                            x=0.2,
                            y=0.7,
                            width=0.5,
                        ),
                    )
                return hud

        class DelayedAliasResolver:
            def resolve(
                self,
                _frame: FramePacket,
                _candidates: tuple[tuple[str, str], ...],
            ) -> tuple[HudAlias, ...]:
                release.wait(timeout=1)
                return (
                    HudAlias("p2", "せんせい", "Metagross", "M", 0.98),
                    HudAlias("p2", "しごでき", "Sableye", "F", 0.97),
                )

        detector = ChampionsOcrDetector(engine=FakeEngine(), alias_resolver=DelayedAliasResolver())
        detector.parser = ChampionsTextParser(
            catalog=ChampionsCatalog(species=("Metagross", "Sableye")),
        )
        try:
            detector.detect(FramePacket(index=0, timestamp_ms=0, image=b"jpeg"))
            detector.detect(FramePacket(index=1, timestamp_ms=500, image=b"jpeg"))
            detector.detect(FramePacket(index=2, timestamp_ms=1_000, image=b"jpeg"))
            release.set()
            detections = detector.flush_pending()
        finally:
            release.set()
            detector.close()

        self.assertEqual(detections.events, ())
        self.assertEqual(
            set(detector.resolved_identities().values()),
            {"Metagross", "Sableye"},
        )

    # Pokémon Champions no publica sprite de estas formas en la categoría de
    # Bulbagarden. Cualquier otra ausencia es un fallo de mapeo: cuando
    # "-Female" no casaba con la forma "F" de Showdown, Indeedee-F se quedaba
    # sin sprite y el replay escribía el macho en su lugar.
    def test_every_champions_species_has_a_sprite(self) -> None:
        """Ninguna especie del catálogo puede quedarse sin referencia.

        Las ausencias no se notaban: cuando "-Female" no casaba con la forma
        "F" de Showdown, Indeedee-F se quedaba sin sprite y el replay escribía
        el macho en su lugar. Lo mismo con la tilde de "Poké Ball" borrada en
        vez de normalizada, que dejaba fuera a Vivillon-Pokeball.
        """

        resolver = ChampionsTeamPreviewResolver(())
        catalog = load_champions_catalog()

        missing = sorted(
            species
            for species, _types in catalog.species_types
            if species not in resolver._sprite_sources
        )

        self.assertEqual(
            missing,
            [],
            "faltan sprites; revisa el mapeo de formas en "
            "scripts/update-champions-sprites.mjs",
        )

    def test_formes_drawn_alike_do_not_compete_with_each_other(self) -> None:
        """Champions usa un mismo dibujo para Polteageist y su forma Antique.

        Si compitieran las dos, empatarían siempre y la fila se descartaría.
        """

        resolver = ChampionsTeamPreviewResolver(
            tuple(
                (species, types)
                for species, types in load_champions_catalog().species_types
                if species.startswith(("Polteageist", "Sinistcha"))
            )
        )

        candidates = resolver._every_candidate()

        self.assertIn("Polteageist", candidates)
        self.assertNotIn("Polteageist-Antique", candidates)
        self.assertIn("Sinistcha", candidates)
        self.assertNotIn("Sinistcha-Masterpiece", candidates)

    def test_a_single_readable_type_plate_does_not_exclude_a_dual_type(self) -> None:
        # COL-102, job 82923f56ce264a92: la placa primaria de Slowking
        # (Water/Psychic) salió en blanco en 36 de 37 frames del Team
        # Preview rival; sólo la secundaria, "Water", se leyó. Exigir que
        # el tipo leído fuera el único de la especie dejaba fuera a
        # Slowking en casi todos los frames -la fila resolvía Pokémon casi
        # siempre, salvo el frame suelto donde ambas placas se leyeron.
        resolver = ChampionsTeamPreviewResolver(
            (
                ("Slowking", ("Water", "Psychic")),
                ("Quagsire", ("Water", "Ground")),
                ("Lapras", ("Water", "Ice")),
            )
        )

        one_plate = resolver._candidates_for_types(("Water",))
        self.assertIn("Slowking", one_plate)
        self.assertIn("Quagsire", one_plate)
        self.assertIn("Lapras", one_plate)

        both_plates = resolver._candidates_for_types(("Water", "Psychic"))
        self.assertEqual(both_plates, ("Slowking",))

    def test_the_gender_symbol_picks_the_forme_the_sprite_cannot(self) -> None:
        """Macho y hembra son casi el mismo dibujo; el símbolo de la tarjeta manda."""

        resolver = ChampionsTeamPreviewResolver(
            (
                ("Basculegion", ("Water", "Ghost")),
                ("Basculegion-F", ("Water", "Ghost")),
                ("Gardevoir", ("Psychic", "Fairy")),
            )
        )

        self.assertEqual(resolver._gendered_variant("Basculegion", "F"), "Basculegion-F")
        self.assertEqual(resolver._gendered_variant("Basculegion-F", "M"), "Basculegion")
        self.assertEqual(resolver._gendered_variant("Basculegion", None), "Basculegion")
        # Sin forma hembra en el catálogo no se inventa ninguna.
        self.assertEqual(resolver._gendered_variant("Gardevoir", "F"), "Gardevoir")

    def test_the_gender_symbol_does_not_choose_between_species(self) -> None:
        resolver = ChampionsTeamPreviewResolver(
            (
                ("Farigiraf", ("Normal", "Psychic")),
                ("Indeedee", ("Psychic", "Normal")),
                ("Indeedee-F", ("Psychic", "Normal")),
                ("Oranguru", ("Normal", "Psychic")),
            )
        )

        # Entre las dos formas de una especie, el símbolo decide.
        self.assertEqual(
            resolver._gendered_candidate(("Indeedee", "Indeedee-F"), "M"), "Indeedee"
        )
        # Con varias especies en juego, no: que Indeedee fuera la única con pareja
        # macho y hembra se llevaba la fila sin llegar a puntuar la silueta.
        self.assertIsNone(
            resolver._gendered_candidate(
                ("Farigiraf", "Indeedee", "Indeedee-F", "Oranguru"), "M"
            )
        )

    def test_a_sprite_covering_its_card_does_not_split_the_grid(self) -> None:
        import numpy as np

        # Panel rival sintético: seis tarjetas carmesí con hueco negro entre
        # ellas. La tercera lleva un sprite que tapa casi todo su ancho.
        image = np.zeros((600, 400, 3), dtype=np.uint8)
        for index in range(6):
            top = 40 + index * 90
            image[top : top + 80, 250:390] = (200, 20, 60)
        image[240:280, 255:385] = 0

        cards = ChampionsTeamPreviewResolver._card_boxes(image, "p2")

        self.assertEqual(len(cards), 6)
        self.assertEqual([top for _x1, _x2, top, _bottom in cards], [40 + n * 90 for n in range(6)])

    def test_the_player_card_does_not_swallow_a_full_bleed_frame(self) -> None:
        import numpy as np

        # Captura de PC a pantalla completa: no hay fondo oscuro a los lados que
        # pare el crecimiento. Midiendo "lo que esté iluminado", la tarjeta
        # acababa ocupando el ancho entero, el recorte del sprite caía sobre el
        # panel contrario y las seis filas salían vacías.
        image = np.full((600, 800, 3), 120, dtype=np.uint8)
        for index in range(6):
            top = 40 + index * 90
            image[top : top + 80, 60:300] = (120, 60, 220)

        cards = ChampionsTeamPreviewResolver._card_boxes(image, "p1")

        self.assertEqual(len(cards), 6)
        self.assertEqual({(x1, x2) for x1, x2, _top, _bottom in cards}, {(60, 300)})

    def test_the_card_veil_does_not_become_part_of_the_sprite(self) -> None:
        import numpy as np

        background = np.array([180.0, 20.0, 60.0])
        clean = np.zeros((60, 80, 3), dtype=np.uint8)
        clean[:, :] = (180, 20, 60)
        clean[20:45, 30:55] = (60, 200, 80)
        veiled = clean.copy()
        # La arena que se ve por detrás del carmesí: el mismo color con otro
        # brillo. Midéndola por distancia entraba en la silueta.
        veiled[0:12, :] = (90, 10, 30)
        veiled[50:60, :] = (255, 28, 85)

        box = (0, 80, 0, 60)
        clean_shape = ChampionsTeamPreviewResolver._observed_shape(clean, box, background)
        veiled_shape = ChampionsTeamPreviewResolver._observed_shape(veiled, box, background)

        self.assertIsNotNone(clean_shape)
        self.assertIsNotNone(veiled_shape)
        self.assertEqual(veiled_shape.aspect_ratio, clean_shape.aspect_ratio)
        self.assertTrue(np.array_equal(veiled_shape.mask, clean_shape.mask))

    def test_gender_formes_do_not_compete_in_the_sprite_match(self) -> None:
        resolver = ChampionsTeamPreviewResolver(
            (("Basculegion", ("Water", "Ghost")), ("Basculegion-F", ("Water", "Ghost")))
        )

        candidates = resolver._every_candidate()

        self.assertIn("Basculegion", candidates)
        self.assertNotIn("Basculegion-F", candidates)

    @staticmethod
    def job_frame(name: str, left: int, top: int) -> FramePacket:
        """Un recorte real del vídeo de un job, en su sitio de un frame 1080p."""

        import io

        from PIL import Image

        frame = Image.new("RGB", (1920, 1080))
        frame.paste(Image.open(Path(__file__).parent / "data" / "champions" / name), (left, top))
        encoded = io.BytesIO()
        frame.save(encoded, format="JPEG", quality=92)
        return FramePacket(index=0, timestamp_ms=0, image=encoded.getvalue())

    def test_the_rival_preview_is_read_by_the_whole_sprite(self) -> None:
        # COL-102, job 347da1c2ff16491b, 30 s: captura de PC. La silueta del
        # sprite rojo y negro de Incineroar se perdía contra el carmesí de la
        # tarjeta y salían Houndoom y Dragonite (mismos tipos que Incineroar y
        # Salamence). Sin roster rival, la batalla se descartaba.
        resolver = ChampionsTeamPreviewResolver(load_champions_catalog().species_types)
        frame = self.job_frame("347da1c2-preview-p2-1540x140.jpg", 1540, 140)

        self.assertEqual(
            resolver.resolve_rows(frame, side="p2"),
            ("Golisopod", "Incineroar", "Indeedee-F", "Salamence", "Hatterene", "Torkoal"),
        )

    def test_own_preview_rows_are_read_from_their_own_sprite(self) -> None:
        # COL-102, job 8b7488cb5914449f, 20 s: nuestro panel en el orden del
        # juego (… Blaziken, Rillaboom …), distinto del orden guardado del
        # equipo. Con el equipo como candidatas, cada fila sale de su sprite.
        resolver = ChampionsTeamPreviewResolver(load_champions_catalog().species_types)
        frame = self.job_frame("8b7488cb-preview-p1-60x140.jpg", 60, 140)
        saved_order = ("Indeedee-F", "Gardevoir", "Basculegion", "Rillaboom", "Blaziken", "Kingambit")

        rows = resolver.resolve_labelled_rows(frame, side="p1", team=saved_order)

        self.assertEqual(
            tuple(species for species, _label in rows),
            ("Indeedee-F", "Gardevoir", "Basculegion", "Blaziken", "Rillaboom", "Kingambit"),
        )

    def test_a_rival_nickname_is_tied_by_its_hud_icon(self) -> None:
        # COL-102, job 347da1c2ff16491b: "Lilith" y "Rapunzel" nunca dicen su
        # especie en un texto; su icono del HUD, sí. Geometría de las lecturas
        # OCR reales de los frames de 101 s y 296 s.
        resolver = ChampionsHudIconResolver(
            ChampionsTeamPreviewResolver(load_champions_catalog().species_types)
        )
        roster = {
            "p2": ("Golisopod", "Incineroar", "Indeedee-F", "Salamence", "Hatterene", "Torkoal")
        }

        def label(text: str, left: float, top: float, bottom: float) -> tuple[str, str, OcrLine]:
            return (
                "p2",
                text,
                OcrLine(text=text, confidence=1.0, left=left, top=top, right=left + 0.05, bottom=bottom),
            )

        leads = resolver.resolve_icons(
            self.job_frame("347da1c2-hud-101s-1100x0.jpg", 1100, 0),
            (label("Lilith", 0.8313, 0.05, 0.0833),),
            roster,
        )
        late = resolver.resolve_icons(
            self.job_frame("347da1c2-hud-296s-1100x0.jpg", 1100, 0),
            (label("Rapunzel", 0.624, 0.0491, 0.0898), label("Lord Drakkon", 0.8313, 0.05, 0.0833)),
            roster,
        )
        # Sin roster no se compara contra nada: el fallo sigue siendo explícito.
        unknown = resolver.resolve_icons(
            self.job_frame("347da1c2-hud-101s-1100x0.jpg", 1100, 0),
            (label("Lilith", 0.8313, 0.05, 0.0833),),
            {"p2": ()},
        )

        self.assertEqual([(alias.nickname, alias.species) for alias in leads], [("Lilith", "Indeedee-F")])
        # Lord Drakkon ya es Mega Salamence en ese frame: su icono es el de la
        # mega y ata igual a la especie del roster.
        self.assertEqual(
            [(alias.nickname, alias.species) for alias in late],
            [("Rapunzel", "Hatterene"), ("Lord Drakkon", "Salamence")],
        )
        self.assertEqual(unknown, ())

    def test_champions_sprites_ship_with_the_repository(self) -> None:
        """Los sprites del Team Preview son datos del repo, no una descarga."""

        resolver = ChampionsTeamPreviewResolver(
            (("Metagross", ("Steel", "Psychic")),),
        )

        self.assertGreater(len(resolver._sprite_sources), 300)
        for species in ("Metagross", "Sableye", "Spiritomb", "Sylveon"):
            paths = resolver._sprite_paths(species)
            self.assertTrue(paths, species)
            for path in paths:
                self.assertTrue(path.is_file(), path)

    def test_a_species_without_a_champions_sprite_is_reported(self) -> None:
        resolver = ChampionsTeamPreviewResolver(())

        with self.assertRaises(DetectionError) as failure:
            resolver._sprite_paths("Missingno")

        self.assertIn("Missingno", str(failure.exception))

    def test_the_sprite_reference_keeps_shape_and_colour(self) -> None:
        resolver = ChampionsTeamPreviewResolver(())

        reference = resolver._sprite_templates("Sableye")[0]
        other = resolver._sprite_templates("Spiritomb")[0]

        # La silueta pesa más que el color justamente para que un shiny, que
        # cambia los colores pero no la forma, siga cayendo en su especie.
        self.assertGreater(
            ChampionsTeamPreviewResolver._SHAPE_WEIGHT,
            ChampionsTeamPreviewResolver._COLOUR_WEIGHT * 2,
        )
        self.assertGreater(
            ChampionsTeamPreviewResolver._shape_score(reference, reference),
            ChampionsTeamPreviewResolver._shape_score(reference, other),
        )

    def test_detector_reads_opponent_preview_on_a_background_thread(self) -> None:
        preview_lines = (
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
        )
        opponent = (
            "Swampert",
            "Metagross",
            "Pelipper",
            "Archaludon",
            "Sableye",
            "Basculegion",
        )

        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return preview_lines

        class FakePreviewResolver:
            def resolve(
                self,
                _frame: FramePacket,
                *,
                rotation_degrees: int = 0,
                side: str = "p2",
            ) -> tuple[str, ...]:
                self.rotation_degrees = rotation_degrees
                if side != "p2":
                    raise DetectionError("este doble sólo conoce el panel rival")
                return opponent

        resolver = FakePreviewResolver()
        detector = ChampionsOcrDetector(
            engine=FakeEngine(),
            team_preview_resolver=resolver,
        )
        try:
            detections = self._pump_team_preview(detector)
        finally:
            detector.close()

        self.assertEqual(detections.p2_team, opponent)
        self.assertEqual(resolver.rotation_degrees, 0)
        self.assertGreaterEqual(detector._preview_votes["p2"][0][opponent[0]], 3)

    @staticmethod
    def _pump_team_preview(detector, *, frames: int = 40):
        """Avanza frames de Team Preview hasta que el roster rival queda fijado."""

        detections = None
        for index in range(frames):
            pending = detector._preview_future
            if pending is not None:
                try:
                    pending.result(timeout=1)
                except Exception:
                    pass  # el detector registra el fallo al hacer poll
            detections = detector.detect(
                FramePacket(index=index, timestamp_ms=index * 500, image=b"jpeg")
            )
            if detections.p2_team:
                break
        assert detections is not None
        return detections

    def test_detector_discards_a_lone_bad_team_preview_reading(self) -> None:
        preview_lines = (
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
        )
        opponent = (
            "Swampert",
            "Metagross",
            "Pelipper",
            "Archaludon",
            "Sableye",
            "Basculegion",
        )
        # Los primeros frames del Team Preview llegan en transición y confunden
        # una silueta; el roster bueno es el que repiten los frames siguientes.
        wrong = opponent[:4] + ("Spiritomb",) + opponent[5:]

        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return preview_lines

        class FlakyPreviewResolver:
            def __init__(self) -> None:
                self.calls = 0

            def resolve(
                self,
                _frame: FramePacket,
                *,
                rotation_degrees: int = 0,
                side: str = "p2",
            ) -> tuple[str, ...]:
                if side != "p2":
                    raise DetectionError("este doble sólo conoce el panel rival")
                self.calls += 1
                if self.calls <= 2:
                    return wrong
                return opponent

        resolver = FlakyPreviewResolver()
        detector = ChampionsOcrDetector(
            engine=FakeEngine(),
            team_preview_resolver=resolver,
        )
        try:
            detections = self._pump_team_preview(detector)
        finally:
            detector.close()

        self.assertEqual(detections.p2_team, opponent)

    def test_detector_reports_a_single_warning_when_no_preview_reading_works(self) -> None:
        preview_lines = (
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
        )

        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return preview_lines

        class BrokenPreviewResolver:
            def resolve(
                self,
                _frame: FramePacket,
                *,
                rotation_degrees: int = 0,
                side: str = "p2",
            ) -> tuple[str, ...]:
                raise DetectionError("no se pudo leer un par de tipos válido en la fila rival 2")

        detector = ChampionsOcrDetector(
            engine=FakeEngine(),
            team_preview_resolver=BrokenPreviewResolver(),
        )
        try:
            self._pump_team_preview(detector, frames=8)
            self.assertFalse(detector.flush_pending().p2_team)
            warnings = list(detector._visual_warnings)
        finally:
            detector.close()

        self.assertEqual(len(warnings), 1)
        self.assertIn("lecturas", warnings[0])

    def test_a_row_with_no_strong_votes_falls_back_to_a_unanimous_weak_guess(self) -> None:
        # COL-102, job 82923f56ce264a92: una placa de tipo ilegible dejaba a
        # Slowking fuera del margen calibrado del resolver en 36 de 37
        # lecturas reales -nunca un voto fuerte, pero la silueta lo señaló
        # sin ninguna discrepancia en las 37. Antes, una sola fila así tiraba
        # el roster de seis Pokémon entero.
        preview_lines = (
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
        )
        confident_rows = ("Incineroar", "Sneasler", None, "Pelipper", "Meganium", "Basculegion")

        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return preview_lines

        class WeakRowPreviewResolver:
            def resolve_rows_with_guesses(
                self,
                _frame: FramePacket,
                *,
                rotation_degrees: int = 0,
                side: str = "p2",
            ) -> tuple[tuple[str | None, str | None], ...]:
                if side != "p2":
                    raise DetectionError("este doble sólo conoce el panel rival")
                return tuple(
                    (species, species) if species else (None, "Slowking")
                    for species in confident_rows
                )

        detector = ChampionsOcrDetector(
            engine=FakeEngine(),
            team_preview_resolver=WeakRowPreviewResolver(),
        )
        try:
            # Suficientes frames para agotar los intentos y acumular las diez
            # conjeturas débiles que pide el respaldo -la fila 3 nunca junta
            # un voto fuerte, así que la aceptación temprana nunca la fija.
            self._pump_team_preview(detector, frames=60)
            self.assertFalse(detector._preview_team["p2"])
            flushed = detector.flush_pending()
        finally:
            detector.close()

        self.assertEqual(
            flushed.p2_team,
            ("Incineroar", "Sneasler", "Slowking", "Pelipper", "Meganium", "Basculegion"),
        )

    def test_a_split_weak_guess_does_not_get_rescued(self) -> None:
        # Sin acuerdo entre las conjeturas -mitad y mitad-, no hay evidencia
        # real detrás y la fila se queda sin resolver en vez de adivinar.
        preview_lines = (
            line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15),
            line("to send into battle.", x=0.38, y=0.215, width=0.16),
        )
        confident_rows = ("Incineroar", "Sneasler", None, "Pelipper", "Meganium", "Basculegion")

        class FakeEngine:
            def read(self, _image: bytes) -> tuple[OcrLine, ...]:
                return preview_lines

        class SplitRowPreviewResolver:
            def __init__(self) -> None:
                self.calls = 0

            def resolve_rows_with_guesses(
                self,
                _frame: FramePacket,
                *,
                rotation_degrees: int = 0,
                side: str = "p2",
            ) -> tuple[tuple[str | None, str | None], ...]:
                if side != "p2":
                    raise DetectionError("este doble sólo conoce el panel rival")
                self.calls += 1
                guess = "Slowking" if self.calls % 2 else "Slowbro"
                return tuple(
                    (species, species) if species else (None, guess)
                    for species in confident_rows
                )

        detector = ChampionsOcrDetector(
            engine=FakeEngine(),
            team_preview_resolver=SplitRowPreviewResolver(),
        )
        try:
            self._pump_team_preview(detector, frames=60)
            flushed = detector.flush_pending()
        finally:
            detector.close()

        self.assertFalse(flushed.p2_team)

    def test_cli_uses_ocr_by_default_and_keeps_ollama_as_an_option(self) -> None:
        parser = build_parser()
        default = parser.parse_args(["video", "battle.mp4", "--output", "replay"])
        ollama = parser.parse_args(
            ["video", "battle.mp4", "--output", "replay", "--detector", "ollama"]
        )

        self.assertEqual(default.detector, "ocr")
        self.assertEqual(default.model, "qwen3-vl:4b")
        self.assertEqual(ollama.detector, "ollama")

    def test_cli_accepts_trace_and_windows_bom_context_with_aliases(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["trace", "battle.trace.jsonl", "--output", "replay"])
        with tempfile.TemporaryDirectory() as directory:
            context_path = Path(directory) / "context.json"
            context_path.write_text(
                '\ufeff{"aliases":{"p1":{"Revenant":"Basculegion"}}}',
                encoding="utf-8",
            )
            _seed, context = _seed_from_context(_load_mapping(context_path), "video")

        self.assertEqual(args.command, "trace")
        self.assertEqual(context.p1_aliases, (("Revenant", "Basculegion"),))

    def test_replays_ocr_trace_without_loading_an_ocr_engine(self) -> None:
        record = {
            "frame": 9,
            "timestamp_ms": 4_000,
            "ocr": [
                {
                    "text": "You lost to Rival!",
                    "confidence": 0.99,
                    "left": 0.2,
                    "top": 0.7,
                    "right": 0.5,
                    "bottom": 0.74,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "battle.trace.jsonl"
            trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
            source = OcrTraceFrameSource(trace)
            frame = next(iter(source))
            detections = OcrTraceDetector().detect(frame)
            frame_count = source.estimated_frame_count()

        self.assertEqual(frame_count, 1)
        self.assertEqual(frame.index, 8)
        self.assertEqual(detections.winner, "p2")

    def test_ocr_trace_reuses_visual_aliases_without_calling_ollama_again(self) -> None:
        hud = (
            line("せんせい", x=0.629, y=0.05, width=0.052),
            line("100%", x=0.682, y=0.11, width=0.053),
        )
        record = {
            "frame": 200,
            "timestamp_ms": 99_500,
            "ocr": [
                {
                    "text": value.text,
                    "confidence": value.confidence,
                    "left": value.left,
                    "top": value.top,
                    "right": value.right,
                    "bottom": value.bottom,
                }
                for value in hud
            ],
            "visual_aliases": [
                {
                    "side": "p2",
                    "nickname": "せんせい",
                    "species": "Metagross",
                    "gender": "M",
                    "confidence": 0.98,
                }
            ],
        }

        detections = OcrTraceDetector().detect(
            FramePacket(index=199, timestamp_ms=99_500, image=json.dumps(record).encode())
        )

        self.assertEqual(
            [(event.slot, event.species) for event in detections.events],
            [("p2a", "Metagross")],
        )

    def test_ocr_trace_reuses_the_recorded_visual_opponent_roster(self) -> None:
        opponent = (
            "Swampert",
            "Metagross",
            "Pelipper",
            "Archaludon",
            "Sableye",
            "Basculegion",
        )
        record = {
            "battle_index": 0,
            "ocr": [
                asdict(line("Select 4 Pokémon", x=0.38, y=0.17, width=0.15)),
                asdict(line("to send into battle.", x=0.38, y=0.215, width=0.16)),
            ],
            "detections": {
                "teams": {"p1": [], "p2": list(opponent)},
                "team_preview": True,
            },
        }

        detections = OcrTraceDetector().detect(
            FramePacket(index=0, timestamp_ms=0, image=json.dumps(record).encode())
        )

        self.assertEqual(detections.p2_team, opponent)

    def test_trace_preloads_final_aliases_before_the_first_hud_frame(self) -> None:
        detector = OcrTraceDetector(
            context=DetectorContext(p2_team=("Metagross", "Sableye")),
            aliases_by_battle={
                0: {
                    "p1": (),
                    "p2": (("せんせい", "Metagross"), ("しごでき", "Sableye")),
                }
            },
        )
        opening = {
            "battle_index": 0,
            "ocr": [
                asdict(line("しごでき", x=0.799, y=0.05, width=0.051)),
                asdict(line("せんせい", x=0.629, y=0.05, width=0.052)),
                asdict(line("100%", x=0.851, y=0.11, width=0.053)),
                asdict(line("100%", x=0.682, y=0.11, width=0.053)),
            ],
        }

        detections = detector.detect(
            FramePacket(index=0, timestamp_ms=500, image=json.dumps(opening).encode())
        )

        self.assertEqual(
            [(event.slot, event.species) for event in detections.events],
            [("p2a", "Metagross"), ("p2b", "Sableye")],
        )

    def test_trace_alias_maps_are_isolated_between_battles(self) -> None:
        detector = OcrTraceDetector(
            context=DetectorContext(p2_team=("Metagross", "Sableye")),
            aliases_by_battle={
                0: {"p1": (), "p2": (("Ace", "Metagross"),)},
                1: {"p1": (), "p2": (("Ace", "Sableye"),)},
            },
        )

        def trace_frame(battle_index: int) -> bytes:
            return json.dumps(
                {
                    "battle_index": battle_index,
                    "ocr": [
                        asdict(line("Ace", x=0.629, y=0.05, width=0.052)),
                        asdict(line("100%", x=0.682, y=0.11, width=0.053)),
                    ],
                }
            ).encode()

        first = detector.detect(FramePacket(index=0, timestamp_ms=0, image=trace_frame(0)))
        second = detector.detect(FramePacket(index=1, timestamp_ms=0, image=trace_frame(1)))

        self.assertEqual(first.events[0].species, "Metagross")
        self.assertEqual(second.events[0].species, "Sableye")

    def test_loads_the_last_resolved_alias_map_for_each_battle(self) -> None:
        records = (
            {"battle_index": 0, "resolved_aliases": {"p1": {}, "p2": {"Ace": "Metagross"}}},
            {"battle_index": 0, "resolved_aliases": {"p1": {}, "p2": {"Shade": "Sableye"}}},
            {"battle_index": 1, "resolved_aliases": {"p1": {}, "p2": {"Ace": "Sableye"}}},
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "battle.trace.jsonl"
            trace.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            aliases = load_trace_aliases(trace)

        self.assertEqual(dict(aliases[0]["p2"]), {"Ace": "Metagross", "Shade": "Sableye"})
        self.assertEqual(dict(aliases[1]["p2"]), {"Ace": "Sableye"})

    def test_single_video_pass_joins_the_timeline_with_late_aliases(self) -> None:
        frames = tuple(
            FramePacket(index=index, timestamp_ms=index * 500, image=str(index).encode())
            for index in range(6)
        )
        ocr = {
            0: (line("Rival sent out しごでき and せんせい!", x=0.2, y=0.7, width=0.5),),
            1: (
                line("せんせい", x=0.629, y=0.05, width=0.052),
                line("しごでき", x=0.799, y=0.05, width=0.051),
                line("100%", x=0.682, y=0.11, width=0.053),
                line("100%", x=0.851, y=0.11, width=0.053),
                line("Venusaur", x=0.08, y=0.86),
                line("Sylveon", x=0.29, y=0.86),
                line("200/200", x=0.13, y=0.93),
                line("190/190", x=0.34, y=0.93),
            ),
            2: (
                line("FIGHT", x=0.86, y=0.70),
                line("POKÉMON", x=0.84, y=0.90),
            ),
            3: (
                line("しごでき's", x=0.72, y=0.30, width=0.12),
                line("Prankster", x=0.72, y=0.36, width=0.12),
            ),
            4: (
                line(
                    "The opposing せんせい used Psychic Fangs!",
                    x=0.2,
                    y=0.7,
                    width=0.5,
                ),
            ),
            5: (line("You won the battle!", x=0.2, y=0.7, width=0.35),),
        }

        class FakeEngine:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, image: bytes) -> tuple[OcrLine, ...]:
                self.calls += 1
                return ocr[int(image.decode())]

        context = DetectorContext(
            p1_name="Player",
            p2_name="Rival",
            p1_team=("Venusaur", "Sylveon"),
            p2_team=("Metagross", "Sableye"),
        )
        engine = FakeEngine()
        captures = ReplayCapturePipeline(
            frames,
            ChampionsOcrDetector(context=context, engine=engine),
            CaptureSeed(
                p1_name="Player",
                p2_name="Rival",
                p1_team=context.p1_team,
                p2_team=context.p2_team,
            ),
        ).capture()

        log = build_replay_document(captures[0]).log
        turn = log.index("|turn|1")
        self.assertEqual(engine.calls, len(frames))
        self.assertLess(log.index("|switch|p2a: Metagross"), turn)
        self.assertLess(log.index("|switch|p2b: Sableye"), turn)
        self.assertLess(
            log.index("|-ability|p2b: Sableye|Prankster"),
            log.index("|move|p2a: Metagross|Psychic Fangs"),
        )

    def test_real_alias_variants_freeze_events_then_replace_identities_once(self) -> None:
        frames = tuple(
            FramePacket(index=index, timestamp_ms=index * 500, image=str(index).encode())
            for index in range(7)
        )
        ocr = {
            0: (line("Rival sent out しごでき and せんせい!", x=0.2, y=0.7, width=0.5),),
            1: (
                line("せんせい", x=0.629, y=0.05, width=0.052),
                line("しごでき", x=0.799, y=0.05, width=0.051),
                line("100%", x=0.682, y=0.11, width=0.053),
                line("100%", x=0.851, y=0.11, width=0.053),
                line("Venusaur", x=0.08, y=0.86),
                line("Sylveon", x=0.29, y=0.86),
                line("200/200", x=0.13, y=0.93),
                line("190/190", x=0.34, y=0.93),
            ),
            2: (
                line("FIGHT", x=0.86, y=0.70),
                line("POKÉMON", x=0.84, y=0.90),
            ),
            3: (
                line(
                    "The opposing せんtせL's Metagrossite is reacting to Rival's Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.75,
                ),
            ),
            4: (line("The opposing しでき used Light Screen!", x=0.2, y=0.7, width=0.5),),
            5: (line("The opposing しでき used Encore!", x=0.2, y=0.7, width=0.5),),
            6: (line("You won the battle!", x=0.2, y=0.7, width=0.35),),
        }

        class FakeEngine:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, image: bytes) -> tuple[OcrLine, ...]:
                self.calls += 1
                return ocr[int(image.decode())]

        context = DetectorContext(
            p1_name="Player",
            p2_name="Rival",
            p1_team=("Venusaur", "Sylveon"),
        )
        engine = FakeEngine()
        detector = ChampionsOcrDetector(context=context, engine=engine)
        detector.parser = ChampionsTextParser(
            context=context,
            catalog=ChampionsCatalog(
                species=("Venusaur", "Sylveon", "Metagross", "Metagross-Mega", "Sableye", "Grimmsnarl"),
                moves=("Light Screen", "Encore"),
                species_moves=(
                    ("Sableye", ("lightscreen", "encore")),
                    ("Grimmsnarl", ("lightscreen",)),
                ),
                mega_stones=(("Metagrossite", "Metagross", "Metagross-Mega"),),
            ),
        )
        capture = ReplayCapturePipeline(
            frames,
            detector,
            CaptureSeed(
                p1_name="Player",
                p2_name="Rival",
                p1_team=context.p1_team,
            ),
        ).capture()[0]

        opponent_events = [event for event in capture.events if event.slot and event.slot.startswith("p2")]
        light_screen = next(event for event in opponent_events if event.move == "Light Screen")
        encore = next(event for event in opponent_events if event.move == "Encore")
        self.assertEqual(engine.calls, len(frames))
        self.assertTrue(light_screen.species.startswith("__champions_actor_"))
        self.assertLess(capture.events.index(light_screen), capture.events.index(encore))
        self.assertEqual(dict(capture.identities)[light_screen.species], "Sableye")
        self.assertEqual(capture.p2.team, ("Metagross", "Sableye"))

        log = build_replay_document(capture).log
        turn = log.index("|turn|1")
        self.assertNotIn("__champions_actor_", log)
        self.assertLess(log.index("|switch|p2a: Metagross"), turn)
        self.assertLess(log.index("|switch|p2b: Sableye"), turn)
        self.assertLess(
            log.index("|move|p2b: Sableye|Light Screen|"),
            log.index("|move|p2b: Sableye|Encore|"),
        )

    def test_single_visible_opponent_keeps_its_hud_slot_and_restores_the_other_lead(self) -> None:
        frames = tuple(
            FramePacket(index=index, timestamp_ms=index * 500, image=str(index).encode())
            for index in range(6)
        )
        ocr = {
            0: (line("Rival sent out Shade and Sensei!", x=0.2, y=0.7, width=0.5),),
            # RapidOCR missed Sensei's label, but both HP bars are visible and
            # Shade is geometrically the right-hand opponent (p2b).
            1: (
                line("Shade", x=0.799, y=0.05, width=0.051),
                line("100%", x=0.682, y=0.11, width=0.053),
                line("100%", x=0.851, y=0.11, width=0.053),
                line("Venusaur", x=0.08, y=0.86),
                line("Sylveon", x=0.29, y=0.86),
                line("200/200", x=0.13, y=0.93),
                line("190/190", x=0.34, y=0.93),
            ),
            2: (
                line("FIGHT", x=0.86, y=0.70),
                line("POKÉMON", x=0.84, y=0.90),
            ),
            3: (
                line(
                    "The opposing Sensei's Metagrossite is reacting to Rival's Omni Ring!",
                    x=0.15,
                    y=0.72,
                    width=0.7,
                ),
            ),
            4: (
                line(
                    "The opposing Shade used Light Screen!",
                    x=0.2,
                    y=0.7,
                    width=0.5,
                ),
            ),
            5: (line("You won the battle!", x=0.2, y=0.7, width=0.35),),
        }

        class FakeEngine:
            def read(self, image: bytes) -> tuple[OcrLine, ...]:
                return ocr[int(image.decode())]

        context = DetectorContext(
            p1_name="Player",
            p2_name="Rival",
            p1_team=("Venusaur", "Sylveon"),
            p2_team=("Metagross", "Sableye"),
            p2_aliases=(("Sensei", "Metagross"), ("Shade", "Sableye")),
        )
        capture = ReplayCapturePipeline(
            frames,
            ChampionsOcrDetector(context=context, engine=FakeEngine()),
            CaptureSeed(
                p1_name="Player",
                p2_name="Rival",
                p1_team=context.p1_team,
                p2_team=context.p2_team,
            ),
        ).capture()[0]

        log = build_replay_document(capture).log
        turn = log.index("|turn|1")
        self.assertLess(log.index("|switch|p2a: Metagross"), turn)
        self.assertLess(log.index("|switch|p2b: Sableye"), turn)
        self.assertIn("|-mega|p2a: Metagross|Metagross|Metagrossite", log)
        self.assertIn("|move|p2b: Sableye|Light Screen|", log)
        self.assertNotIn("Sableye|Metagrossite", log)

    def test_final_garbled_aliases_restore_the_missing_lead_before_turn_one(self) -> None:
        parser = ChampionsTextParser(
            context=DetectorContext(
                p1_name="Roku",
                p2_name="Rival",
                p1_team=("Venusaur", "Sylveon"),
                p2_aliases=(
                    ("せんtせl", "Metagross"),
                    ("tんtl)", "Metagross"),
                    ("しでき", "Sableye"),
                ),
            ),
            catalog=ChampionsCatalog(
                species=("Venusaur", "Sylveon", "Metagross", "Sableye"),
            ),
        )
        parser.parse(
            (line("Rival sent out しごでき and せんせい!", x=0.2, y=0.7, width=0.5),),
            timestamp_ms=0,
            source_frame=0,
        )
        leads = parser.parse(
            (
                line("しでき", x=0.799, y=0.05, width=0.051),
                line("100%", x=0.682, y=0.11, width=0.053),
                line("100%", x=0.851, y=0.11, width=0.053),
                line("Venusaur", x=0.08, y=0.86),
                line("Sylveon", x=0.29, y=0.86),
                line("200/200", x=0.13, y=0.93),
                line("190/190", x=0.34, y=0.93),
            ),
            timestamp_ms=500,
            source_frame=1,
        )
        turn = parser.parse(
            (
                line("FIGHT", x=0.86, y=0.70),
                line("POKÉMON", x=0.84, y=0.90),
            ),
            timestamp_ms=1_000,
            source_frame=2,
        )

        opponent_switches = [
            event
            for event in leads.events
            if event.kind == "switch" and event.slot.startswith("p2")
        ]
        self.assertEqual([event.slot for event in opponent_switches], ["p2a", "p2b"])
        self.assertTrue(opponent_switches[0].species.startswith("__champions_actor_"))
        self.assertEqual(opponent_switches[1].species, "Sableye")
        self.assertEqual(
            parser.resolved_identities()[opponent_switches[0].species],
            "Metagross",
        )
        self.assertEqual(
            [event.turn for event in turn.events if event.kind == "turn"],
            [1],
        )

    def test_final_trace_pass_places_both_leads_before_turn_one(self) -> None:
        def record(
            frame: int,
            timestamp_ms: int,
            lines: tuple[OcrLine, ...],
            *,
            resolved_aliases: dict[str, dict[str, str]] | None = None,
        ) -> dict[str, object]:
            return {
                "frame": frame,
                "timestamp_ms": timestamp_ms,
                "battle_index": 0,
                "ocr": [asdict(value) for value in lines],
                "resolved_aliases": resolved_aliases or {"p1": {}, "p2": {}},
            }

        aliases = {
            "p1": {},
            "p2": {"せんせい": "Metagross", "しごでき": "Sableye"},
        }
        records = (
            record(
                1,
                0,
                (line("Rival sent out しごでき and せんせい!", x=0.2, y=0.7, width=0.5),),
            ),
            record(
                2,
                500,
                (
                    line("せんせい", x=0.629, y=0.05, width=0.052),
                    line("しごでき", x=0.799, y=0.05, width=0.051),
                    line("100%", x=0.682, y=0.11, width=0.053),
                    line("100%", x=0.851, y=0.11, width=0.053),
                    line("Venusaur", x=0.08, y=0.86),
                    line("Sylveon", x=0.29, y=0.86),
                    line("200/200", x=0.13, y=0.93),
                    line("190/190", x=0.34, y=0.93),
                ),
            ),
            record(
                3,
                1_000,
                (
                    line("FIGHT", x=0.86, y=0.70),
                    line("POKÉMON", x=0.84, y=0.90),
                ),
            ),
            record(
                4,
                1_500,
                (line("You won the battle!", x=0.2, y=0.7, width=0.35),),
                resolved_aliases=aliases,
            ),
        )
        context = DetectorContext(
            p1_name="Player",
            p2_name="Rival",
            p1_team=("Venusaur", "Sylveon"),
            p2_team=("Metagross", "Sableye"),
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "battle.trace.jsonl"
            trace.write_text(
                "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records),
                encoding="utf-8",
            )
            source = OcrTraceFrameSource(trace)
            captures = ReplayCapturePipeline(
                source,
                OcrTraceDetector(
                    context=context,
                    aliases_by_battle=load_trace_aliases(trace),
                ),
                CaptureSeed(
                    p1_name="Player",
                    p2_name="Rival",
                    p1_team=context.p1_team,
                    p2_team=context.p2_team,
                ),
            ).capture()

        log = build_replay_document(captures[0]).log
        turn = log.index("|turn|1")
        self.assertLess(log.index("|switch|p2a: Metagross"), turn)
        self.assertLess(log.index("|switch|p2b: Sableye"), turn)

    def test_the_trace_phase_reads_a_reread_notice_once_with_its_best_reading(self) -> None:
        # COL-102, job 82923f56ce264a92: el OCR relee "Speed fell!" como
        # "Speed fel!" en los frames siguientes del mismo aviso, y leído frame
        # a frame cada variante salía como otro -message.
        def record(frame: int, lines: tuple[OcrLine, ...]) -> dict[str, object]:
            return {
                "frame": frame,
                "timestamp_ms": frame * 500,
                "battle_index": 0,
                "phase": "frame",
                "ocr": [asdict(value) for value in lines],
                "resolved_aliases": {"p1": {}, "p2": {}},
            }

        def message(text: str) -> tuple[OcrLine, ...]:
            return (line(text, x=0.15, y=0.72, width=0.35),)

        records = (
            record(1, self.command_frame()),
            record(2, message("Delphox's Attack fell!")),
            record(3, message("Delphox's Attack fell!")),
            record(4, ()),
            record(5, message("The opposing Steelix's Speed fell!")),
            record(6, message("The opposing Steelix's Speed fel!")),
            record(7, message("The opposing Steelix's Speed fel!")),
        )
        context = DetectorContext(
            p1_name="IesYo",
            p2_name="Rival",
            p1_team=("Delphox", "Victreebel"),
            p2_team=("Steelix", "Drampa", "Umbreon"),
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "battle.trace.jsonl"
            trace.write_text(
                "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records),
                encoding="utf-8",
            )
            detector = OcrTraceDetector.from_trace(trace, context=context)
            messages = [
                (frame.index, event.value)
                for frame in OcrTraceFrameSource(trace)
                for event in detector.detect(frame).events
                if event.kind == "message"
            ]

        # Una vez cada aviso, en su primer frame, con la lectura buena.
        self.assertEqual(
            messages,
            [(1, "Delphox's Attack fell!"), (4, "The opposing Steelix's Speed fell!")],
        )

    def test_a_roster_resolved_when_its_battle_closes_stays_in_that_battle(self) -> None:
        # COL-102, job 82923f56ce264a92: el Team Preview de la Partida 1 sólo
        # se resolvió al cerrarla, y en vivo entró en la Partida 1. La traza
        # lo guarda detrás de su último frame; reprocesarla lo aplicaba como
        # un frame más, ya con la Partida 2 abierta: la Partida 1 quedaba con
        # 4 de 6 y la Partida 2 con el equipo de otro rival.
        def record(
            frame: int,
            timestamp_ms: int,
            battle_index: int,
            lines: tuple[OcrLine, ...],
            *,
            phase: str = "frame",
            p2_team: tuple[str, ...] = (),
        ) -> dict[str, object]:
            return {
                "frame": frame,
                "timestamp_ms": timestamp_ms,
                "battle_index": battle_index,
                "phase": phase,
                "ocr": [asdict(value) for value in lines],
                "resolved_aliases": {"p1": {}, "p2": {}},
                "detections": {"teams": {"p1": [], "p2": list(p2_team)}},
            }

        def battle(
            first_frame: int,
            battle_index: int,
            p2_leads: tuple[str, str],
            *,
            p2_team: tuple[str, ...] = (),
        ) -> tuple[dict[str, object], ...]:
            timestamp_ms = first_frame * 500
            return (
                record(
                    first_frame,
                    timestamp_ms,
                    battle_index,
                    (
                        line(p2_leads[0], x=0.62, y=0.04),
                        line(p2_leads[1], x=0.83, y=0.04),
                        line("100%", x=0.69, y=0.11),
                        line("100%", x=0.90, y=0.11),
                        line("Venusaur", x=0.08, y=0.86),
                        line("Sylveon", x=0.29, y=0.86),
                        line("200/200", x=0.13, y=0.93),
                        line("190/190", x=0.34, y=0.93),
                    ),
                    p2_team=p2_team,
                ),
                record(
                    first_frame + 1,
                    timestamp_ms + 500,
                    battle_index,
                    (line("FIGHT", x=0.86, y=0.70), line("POKÉMON", x=0.84, y=0.90)),
                ),
                record(
                    first_frame + 2,
                    timestamp_ms + 1_000,
                    battle_index,
                    (line("You won the battle!", x=0.2, y=0.7, width=0.35),),
                ),
            )

        first_rival = ("Incineroar", "Sneasler", "Slowking", "Pelipper", "Meganium", "Basculegion")
        second_rival = ("Garchomp", "Whimsicott", "Mimikyu", "Lucario", "Charizard", "Venusaur")
        records = (
            *battle(1, 0, ("Incineroar", "Sneasler")),
            # Lo que `flush_pending` escribió al cerrar la Partida 1: el
            # roster, junto con el OCR de la pantalla de selección de donde
            # salió, igual que en la traza real (frame 156).
            record(
                2,
                1_000,
                0,
                (
                    line("Select 4 Pokémon", x=0.36, y=0.20, width=0.2),
                    line("to send into battle.", x=0.36, y=0.24, width=0.2),
                ),
                phase="preview_team_flush",
                p2_team=first_rival,
            ),
            *battle(10, 1, ("Garchomp", "Whimsicott"), p2_team=second_rival),
        )
        context = DetectorContext(
            p1_name="Player",
            p2_name="Rival",
            p1_team=("Venusaur", "Sylveon"),
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "battle.trace.jsonl"
            trace.write_text(
                "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records),
                encoding="utf-8",
            )
            captures = ReplayCapturePipeline(
                OcrTraceFrameSource(trace),
                OcrTraceDetector.from_trace(trace, context=context),
                CaptureSeed(p1_name="Player", p2_name="Rival", p1_team=context.p1_team),
            ).capture(max_battles=0)
            # Y la segunda fase ya lo sabe al leer el primer frame de la
            # Partida 1, no al cerrarla como el recorrido del vídeo.
            detector = OcrTraceDetector.from_trace(trace, context=context)
            detector.detect(next(iter(OcrTraceFrameSource(trace))))

        self.assertEqual([capture.p2.team for capture in captures], [first_rival, second_rival])
        self.assertEqual(detector.parser._teams["p2"], first_rival)
        self.assertTrue(detector.parser._known_teams["p2"])


if __name__ == "__main__":
    unittest.main()
