from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path

from pkmn_vgc.champions_replay.cli import _load_mapping, _seed_from_context, build_parser
from pkmn_vgc.champions_replay.detector import DetectionError, DetectorContext, HudAlias
from pkmn_vgc.champions_replay.ocr_detector import (
    ChampionsCatalog,
    ChampionsOcrDetector,
    ChampionsTextParser,
    OcrLine,
    OcrTraceDetector,
    RapidOcrEngine,
    _health_value,
    load_champions_catalog,
    load_trace_aliases,
)
from pkmn_vgc.champions_replay.pipeline import CaptureSeed, ReplayCapturePipeline
from pkmn_vgc.champions_replay.showdown import build_replay_document
from pkmn_vgc.champions_replay.team_preview import ChampionsTeamPreviewResolver
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

    def test_repairs_common_health_ocr_artifacts(self) -> None:
        self.assertEqual(_health_value("100%"), "100/100")
        self.assertEqual(_health_value("141/198"), "141/198")
        self.assertEqual(_health_value("1871187"), "187/187")
        self.assertIsNone(_health_value("33"))
        self.assertIsNone(_health_value("06:45"))
        self.assertIsNone(_health_value("Battle Info"))

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

        detections = parser.parse(
            (line("Nico protected itself!", x=0.2, y=0.7, width=0.35),),
            timestamp_ms=1_000,
            source_frame=1,
        )

        self.assertEqual(detections.events[0].value, "Sylveon protected itself!")

    def test_serializes_tailwind_messages_as_side_conditions(self) -> None:
        parser = self.parser()
        parser._battle_open = True

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

    def test_parses_faint_and_result_without_a_visual_model(self) -> None:
        parser = self.parser()
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

    def test_gender_formes_do_not_compete_in_the_sprite_match(self) -> None:
        resolver = ChampionsTeamPreviewResolver(
            (("Basculegion", ("Water", "Ghost")), ("Basculegion-F", ("Water", "Ghost")))
        )

        candidates = resolver._every_candidate()

        self.assertIn("Basculegion", candidates)
        self.assertNotIn("Basculegion-F", candidates)

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


if __name__ == "__main__":
    unittest.main()
