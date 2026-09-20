from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pkmn_vgc.champions_replay.cli import _load_mapping, _seed_from_context, build_parser
from pkmn_vgc.champions_replay.detector import DetectorContext
from pkmn_vgc.champions_replay.ocr_detector import (
    ChampionsCatalog,
    ChampionsOcrDetector,
    ChampionsTextParser,
    OcrLine,
    OcrTraceDetector,
    _health_value,
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

    def test_cli_uses_ocr_by_default_and_keeps_ollama_as_an_option(self) -> None:
        parser = build_parser()
        default = parser.parse_args(["video", "battle.mp4", "--output", "replay"])
        ollama = parser.parse_args(
            ["video", "battle.mp4", "--output", "replay", "--detector", "ollama"]
        )

        self.assertEqual(default.detector, "ocr")
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


if __name__ == "__main__":
    unittest.main()
