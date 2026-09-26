from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from pkmn_vgc.champions_replay.cli import _seed_from_context
from pkmn_vgc.champions_replay.detector import (
    DetectionError,
    DetectorContext,
    OllamaHudAliasResolver,
    OllamaVisionDetector,
    _extract_json,
)
from pkmn_vgc.champions_replay.models import BattleEvent, BattleSide, CapturedBattle, FrameDetections
from pkmn_vgc.champions_replay.pipeline import (
    CaptureAccumulator,
    CaptureSeed,
    ReplayCapturePipeline,
    review_capture,
    risk_windows,
)
from pkmn_vgc.champions_replay.showdown import (
    _with_known_crits,
    _with_known_health,
    _with_known_miss,
    _with_known_target,
    build_replay_document,
    render_replay_html,
    write_replay_artifacts,
)
from pkmn_vgc.champions_replay.sources import (
    FramePacket,
    LiveFrameSource,
    SegmentedVideoFrameSource,
    VideoFrameSource,
    iter_mjpeg,
)

DATA = Path(__file__).with_name("data")


class ChampionsReplayTests(unittest.TestCase):
    def capture(self) -> CapturedBattle:
        return CapturedBattle.from_mapping(
            json.loads((DATA / "champions_capture.json").read_text(encoding="utf-8"))
        )

    def test_uses_english_as_the_capture_language_by_default(self) -> None:
        _seed, context = _seed_from_context({}, "video")

        self.assertEqual(DetectorContext().language, "en")
        self.assertEqual(context.language, "en")

    def test_serializes_capture_to_showdown_contract(self) -> None:
        document = build_replay_document(self.capture())

        self.assertIn("|gametype|doubles", document.log)
        self.assertIn("|move|p1a: Kleavor|Stone Axe|p2a: Miraidon", document.log)
        self.assertIn("|-damage|p2a: Miraidon|65/100", document.log)
        self.assertTrue(document.log.endswith("|win|IesYo"))
        self.assertEqual(document.inputlog, ">p1 team 1243\n>p2 team 5612")
        self.assertEqual(review_capture(self.capture()), ())

        expected = json.loads((DATA / "champions_replay.json").read_text(encoding="utf-8"))
        self.assertEqual(document.to_dict(), expected)

    def test_sanitizes_protocol_fields_and_canonicalizes_selection_names(self) -> None:
        side = BattleSide(
            "Ies|Yo\nlocal",
            ("Urshifu-Rapid-Strike",),
            ("Urshifu Rapid Strike",),
        )
        event = BattleEvent(kind="move", timestamp_ms=0, slot="p1a", move="Stone|Axe\n")

        self.assertEqual(side.name, "Ies Yo local")
        self.assertEqual(side.selected, ("Urshifu-Rapid-Strike",))
        self.assertEqual(event.move, "Stone Axe")
        with self.assertRaisesRegex(ValueError, "movimiento necesita"):
            BattleEvent(kind="move", timestamp_ms=0, slot="p1a", move="|")

    def test_builds_a_replay_html_understood_by_showdown(self) -> None:
        html = render_replay_html(build_replay_document(self.capture()))

        self.assertIn('class="battle-log-data"', html)
        self.assertIn("|switch|p1a: Kleavor", html)
        self.assertIn("https://play.pokemonshowdown.com/js/replay-embed.js", html)
        self.assertIn("upgrade-insecure-requests", html)

    def test_serializes_mega_evolution_as_a_permanent_forme_change(self) -> None:
        battle = self.capture()
        mega = BattleEvent(
            kind="mega",
            timestamp_ms=1_500,
            slot="p1a",
            species="Kleavor",
            forme="Kleavor-Mega",
            value="Kleavorite",
        )
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=battle.p2,
                events=(battle.events[0], mega),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|detailschange|p1a: Kleavor|Kleavor-Mega, L50", document.log)
        self.assertIn("|-mega|p1a: Kleavor|Kleavor|Kleavorite", document.log)

    def test_risk_windows_mark_faints_switches_and_low_health(self) -> None:
        # COL-102, reapertura estructural del 25 sep: cada bug de esta ronda
        # nació en un faint, un switch/drag, o una lectura de HP cerca de 0
        # -no en cualquier turno. Sirve para decidir, sin releer todo el
        # vídeo, qué tramos merecen más fps la próxima vez.
        events = (
            BattleEvent(kind="turn", timestamp_ms=0, turn=1),
            BattleEvent(kind="move", timestamp_ms=10_000, slot="p1a", move="Tackle"),
            BattleEvent(kind="damage", timestamp_ms=50_000, slot="p2a", species="Salamence", health="5/100"),
            BattleEvent(kind="faint", timestamp_ms=120_000, slot="p2b", species="Milotic"),
            BattleEvent(kind="switch", timestamp_ms=200_000, slot="p2b", species="Rillaboom"),
        )

        windows = risk_windows((events,), margin_ms=3_000)

        self.assertEqual(
            windows,
            ((47_000, 53_000), (117_000, 123_000), (197_000, 203_000)),
        )

    def test_risk_windows_merge_overlapping_margins(self) -> None:
        events = (
            BattleEvent(kind="damage", timestamp_ms=10_000, slot="p2a", species="Salamence", health="5/100"),
            BattleEvent(kind="faint", timestamp_ms=12_000, slot="p2a", species="Salamence"),
        )

        windows = risk_windows((events,), margin_ms=3_000)

        self.assertEqual(windows, ((7_000, 15_000),))

    def test_risk_windows_ignore_a_stable_battle(self) -> None:
        events = (
            BattleEvent(kind="turn", timestamp_ms=0, turn=1),
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Tackle"),
            BattleEvent(kind="damage", timestamp_ms=2_000, slot="p2a", species="Salamence", health="80/100"),
        )

        self.assertEqual(risk_windows((events,)), ())

    def test_risk_windows_include_a_battle_that_never_finished_capturing(self) -> None:
        # COL-102: una batalla que se descarta en la pasada barata (por
        # ejemplo, una identidad sin especie) no deja de haber pasado en el
        # vídeo. Si sus eventos crudos no entran también, ese instante nunca
        # queda marcado y la relectura densa nunca la rescata.
        incomplete_battle_events = (
            BattleEvent(kind="switch", timestamp_ms=90_000, slot="p1a", species="Rillaboom"),
        )

        windows = risk_windows((incomplete_battle_events,), margin_ms=3_000)

        self.assertEqual(windows, ((87_000, 93_000),))

    def test_keeps_a_mega_form_after_switching_out_and_back_in(self) -> None:
        battle = self.capture()
        events = (
            BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species="Metagross"),
            BattleEvent(
                kind="mega",
                timestamp_ms=1,
                slot="p2a",
                species="Metagross",
                forme="Metagross-Mega",
                value="Metagrossite",
            ),
            BattleEvent(kind="switch", timestamp_ms=2, slot="p2a", species="Sableye"),
            BattleEvent(kind="switch", timestamp_ms=3, slot="p2a", species="Metagross"),
        )
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Metagross", "Sableye"), ("Metagross", "Sableye")),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|switch|p2a: Metagross|Metagross-Mega, L50|100/100", document.log)

    def test_completes_the_health_of_switches_the_hud_did_not_accompany(self) -> None:
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide(
                    "IesYo",
                    ("Basculegion", "Venusaur"),
                    ("Basculegion", "Venusaur"),
                ),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=(
                    BattleEvent(
                        kind="switch",
                        timestamp_ms=1_000,
                        slot="p1a",
                        species="Basculegion",
                        health="195/195",
                    ),
                    BattleEvent(
                        kind="damage",
                        timestamp_ms=2_000,
                        slot="p1a",
                        species="Basculegion",
                        health="13/195",
                    ),
                    # Los tres cambios de abajo llegan sin HP porque el juego los
                    # anunció por texto y el HUD no acompañó.
                    BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Venusaur"),
                    BattleEvent(kind="switch", timestamp_ms=4_000, slot="p2a", species="Sableye"),
                    BattleEvent(
                        kind="damage",
                        timestamp_ms=5_000,
                        slot="p1a",
                        species="Venusaur",
                        health="104/156",
                    ),
                    BattleEvent(kind="switch", timestamp_ms=6_000, slot="p1a", species="Basculegion"),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        # Vuelve al campo: conserva la última vida que se le vio.
        self.assertIn("|switch|p1a: Basculegion|Basculegion, L50|13/195", document.log)
        # Primera entrada: a tope, con el máximo que el log revela más adelante.
        self.assertIn("|switch|p1a: Venusaur|Venusaur, L50|156/156", document.log)
        # Del rival sólo se conoce el porcentaje, y nunca se leyó: queda el relleno.
        self.assertIn("|switch|p2a: Sableye|Sableye, L50|100/100", document.log)

    def test_a_bad_health_reading_does_not_decide_the_maximum(self) -> None:
        events = (
            BattleEvent(kind="switch", timestamp_ms=1_000, slot="p1a", species="Venusaur"),
            BattleEvent(
                kind="damage", timestamp_ms=2_000, slot="p1a", species="Venusaur", health="104/156"
            ),
            BattleEvent(
                kind="damage", timestamp_ms=3_000, slot="p1a", species="Venusaur", health="56/150"
            ),
            BattleEvent(
                kind="damage", timestamp_ms=4_000, slot="p1a", species="Venusaur", health="7/156"
            ),
        )

        self.assertEqual(_with_known_health(events)[0].health, "156/156")

    def test_fills_the_target_when_only_one_rival_was_hit(self) -> None:
        # El detector sabe quién usó el movimiento y, por separado, a quién le
        # bajó la vida, pero nunca cruzaba las dos cosas: el visor oficial
        # terminaba animando el golpe contra un slot fijo en vez del rival
        # real (lo notó Roku viendo el replay corregido de COL-102).
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Knock Off"),
            BattleEvent(
                kind="damage", timestamp_ms=1_500, slot="p2a", species="Farigiraf", health="0/100"
            ),
        )

        self.assertEqual(_with_known_target(events)[0].target_slot, "p2a")

    def test_does_not_guess_a_target_for_a_spread_move(self) -> None:
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Rock Slide"),
            BattleEvent(
                kind="damage", timestamp_ms=1_500, slot="p2a", species="Farigiraf", health="40/100"
            ),
            BattleEvent(
                kind="damage", timestamp_ms=1_600, slot="p2b", species="Tyranitar", health="60/100"
            ),
        )

        self.assertIsNone(_with_known_target(events)[0].target_slot)

    def test_does_not_guess_a_target_when_nothing_was_hit(self) -> None:
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Protect"),
            BattleEvent(kind="turn", timestamp_ms=2_000, turn=2),
        )

        self.assertIsNone(_with_known_target(events)[0].target_slot)

    def test_stops_looking_for_a_target_at_the_next_action(self) -> None:
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Knock Off"),
            BattleEvent(kind="move", timestamp_ms=1_100, slot="p1b", move="Fake Out"),
            BattleEvent(
                kind="damage", timestamp_ms=1_200, slot="p2a", species="Farigiraf", health="0/100"
            ),
        )

        completed = _with_known_target(events)
        self.assertIsNone(completed[0].target_slot)
        self.assertEqual(completed[1].target_slot, "p2a")

    def test_attributes_a_critical_hit_to_the_pokemon_hit_since_the_last_action(self) -> None:
        # El mensaje del juego, "A critical hit!", no nombra a nadie -Roku lo
        # señaló al pedir la búsqueda de otros vacíos parecidos al del objetivo.
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p2a", move="Shadow Ball"),
            BattleEvent(
                kind="damage", timestamp_ms=1_500, slot="p1a", species="Sinistcha", health="0/178"
            ),
            BattleEvent(kind="message", timestamp_ms=1_600, value="It's super effective on Sinistcha!"),
            BattleEvent(kind="message", timestamp_ms=1_700, value="A critical hit!"),
        )

        completed = _with_known_crits(events)
        self.assertEqual(completed[3].kind, "crit")
        self.assertEqual(completed[3].slot, "p1a")
        self.assertIsNone(completed[3].value)

    def test_does_not_guess_a_critical_hit_with_two_candidates(self) -> None:
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Rock Slide"),
            BattleEvent(
                kind="damage", timestamp_ms=1_500, slot="p2a", species="Farigiraf", health="40/100"
            ),
            BattleEvent(
                kind="damage", timestamp_ms=1_600, slot="p2b", species="Tyranitar", health="60/100"
            ),
            BattleEvent(kind="message", timestamp_ms=1_700, value="A critical hit!"),
        )

        self.assertEqual(_with_known_crits(events)[3].kind, "message")

    def test_does_not_guess_a_critical_hit_across_a_turn_boundary(self) -> None:
        events = (
            BattleEvent(
                kind="damage", timestamp_ms=1_000, slot="p1a", species="Sinistcha", health="0/178"
            ),
            BattleEvent(kind="turn", timestamp_ms=2_000, turn=2),
            BattleEvent(kind="message", timestamp_ms=2_500, value="A critical hit!"),
        )

        self.assertEqual(_with_known_crits(events)[2].kind, "message")

    def test_fills_a_moves_target_from_a_miss_when_nothing_was_hit(self) -> None:
        # COL-102, job 82923f56ce264a92: un ataque esquivado no deja ningún
        # -damage, así que el objetivo del move quedaba vacío pese a que el
        # -miss ya sabe a quién esquivó.
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p2a", move="Will-O-Wisp"),
            BattleEvent(kind="miss", timestamp_ms=1_500, target_slot="p1b"),
        )

        self.assertEqual(_with_known_target(events)[0].target_slot, "p1b")

    def test_completes_the_source_of_a_miss_from_the_last_move(self) -> None:
        # "X avoided the attack!" nombra a quien esquivó, no a quien atacó.
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p2a", move="Will-O-Wisp"),
            BattleEvent(kind="miss", timestamp_ms=1_500, target_slot="p1b"),
        )

        completed = _with_known_miss(events)
        self.assertEqual(completed[1].slot, "p2a")
        self.assertEqual(completed[1].target_slot, "p1b")

    def test_a_spread_moves_misses_all_attach_to_the_same_source(self) -> None:
        # Heat Wave fallando contra los dos rivales genera un -miss por cada
        # uno; los dos deben atarse al mismo movimiento, sin adivinar cuál
        # de los dos fue "el" objetivo.
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p2b", move="Heat Wave"),
            BattleEvent(kind="miss", timestamp_ms=1_500, target_slot="p1a"),
            BattleEvent(kind="miss", timestamp_ms=1_600, target_slot="p1b"),
        )

        completed = _with_known_miss(events)
        self.assertEqual(completed[1].slot, "p2b")
        self.assertEqual(completed[2].slot, "p2b")

    def test_does_not_guess_a_miss_source_across_a_turn_boundary(self) -> None:
        events = (
            BattleEvent(kind="move", timestamp_ms=1_000, slot="p2a", move="Will-O-Wisp"),
            BattleEvent(kind="turn", timestamp_ms=2_000, turn=2),
            BattleEvent(kind="miss", timestamp_ms=2_500, target_slot="p1b"),
        )

        self.assertIsNone(_with_known_miss(events)[2].slot)

    def test_renders_a_resolved_miss_as_showdown_protocol(self) -> None:
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=(
                    BattleEvent(kind="switch", timestamp_ms=100, slot="p1a", species="Kleavor"),
                    BattleEvent(kind="switch", timestamp_ms=500, slot="p2a", species="Sableye"),
                    BattleEvent(
                        kind="move",
                        timestamp_ms=1_000,
                        slot="p2a",
                        species="Sableye",
                        move="Will-O-Wisp",
                    ),
                    BattleEvent(kind="miss", timestamp_ms=1_500, target_slot="p1a"),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|move|p2a: Sableye|Will-O-Wisp|p1a: Kleavor", document.log)
        self.assertIn("|-miss|p2a: Sableye|p1a: Kleavor", document.log)

    def test_renders_a_flinch_as_a_showdown_cant(self) -> None:
        # COL-102, job 8b7488cb5914449f, partida 3: como -message el visor sólo
        # escribía el flinch en el log; como "cant" lo representa.
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Pelipper",), ("Pelipper",)),
                events=(
                    BattleEvent(kind="switch", timestamp_ms=100, slot="p1a", species="Kleavor"),
                    BattleEvent(kind="switch", timestamp_ms=500, slot="p2b", species="Pelipper"),
                    BattleEvent(kind="cant", timestamp_ms=1_000, slot="p2b", species="Pelipper", value="flinch"),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|cant|p2b: Pelipper|flinch", document.log)
        self.assertNotIn("flinched and couldn't move", document.log)

    def test_does_not_reorder_a_late_switch_around_an_existing_move(self) -> None:
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=(
                    BattleEvent(
                        kind="move",
                        timestamp_ms=1_000,
                        source_frame=10,
                        slot="p2a",
                        species="Sableye",
                        move="Light Screen",
                    ),
                    BattleEvent(
                        kind="switch",
                        timestamp_ms=1_000,
                        source_frame=20,
                        slot="p2a",
                        species="Sableye",
                    ),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertLess(
            document.log.index("|move|p2a: Sableye|Light Screen|"),
            document.log.index("|switch|p2a: Sableye"),
        )

    def test_keeps_the_active_species_when_an_event_contains_a_mixed_alias(self) -> None:
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=(
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species="Sableye"),
                    BattleEvent(
                        kind="move",
                        timestamp_ms=1,
                        slot="p2a",
                        species="しごでき",
                        move="Light Screen",
                    ),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|move|p2a: Sableye|Light Screen|", document.log)
        self.assertNotIn("p2a: しごでき", document.log)

    def test_does_not_synthesize_a_switch_for_an_orphan_action(self) -> None:
        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=(
                    BattleEvent(
                        kind="move",
                        timestamp_ms=1_000,
                        slot="p2a",
                        species="Sableye",
                        move="Light Screen",
                    ),
                ),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertNotIn("|switch|p2a: Sableye", document.log)
        self.assertIn("|move|p2a: Sableye|Light Screen|", document.log)

    def test_replaces_stable_actor_identities_only_after_building_the_protocol(self) -> None:
        battle = self.capture()
        metagross = "__champions_actor_p2_0001__"
        sableye = "__champions_actor_p2_0002__"
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=BattleSide("Rival", ("Metagross", "Sableye"), ("Metagross", "Sableye")),
                events=(
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species=metagross),
                    BattleEvent(kind="switch", timestamp_ms=1, slot="p2b", species=sableye),
                    BattleEvent(kind="turn", timestamp_ms=2, turn=1),
                    BattleEvent(
                        kind="move",
                        timestamp_ms=3,
                        slot="p2b",
                        species=sableye,
                        move="Light Screen",
                    ),
                ),
                winner=battle.winner,
                identities=((metagross, "Metagross"), (sableye, "Sableye")),
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertNotIn("__champions_actor_", document.log)
        self.assertLess(document.log.index("|switch|p2b: Sableye"), document.log.index("|turn|1"))
        self.assertLess(document.log.index("|turn|1"), document.log.index("|move|p2b: Sableye"))

    def test_serializes_ability_driven_terrain_with_its_source(self) -> None:
        battle = self.capture()
        events = (
            BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species="Indeedee-F"),
            BattleEvent(
                kind="ability",
                timestamp_ms=1,
                slot="p2a",
                species="Indeedee-F",
                value="Psychic Surge",
            ),
            BattleEvent(
                kind="fieldstart",
                timestamp_ms=2,
                value="move: Psychic Terrain",
                tags=("[from] ability: Psychic Surge", "[of] p2a: Indeedee-F"),
            ),
        )
        document = build_replay_document(
            CapturedBattle(
                p1=battle.p1,
                p2=battle.p2,
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|-ability|p2a: Indeedee-F|Psychic Surge", document.log)
        self.assertIn(
            "|-fieldstart|move: Psychic Terrain|[from] ability: Psychic Surge|[of] p2a: Indeedee-F",
            document.log,
        )

    def test_writes_json_log_and_html_without_overwriting_by_default(self) -> None:
        document = build_replay_document(self.capture())
        with tempfile.TemporaryDirectory() as directory:
            stem = Path(directory) / "battle-001"
            paths = write_replay_artifacts(document, stem)
            self.assertEqual([path.suffix for path in paths], [".json", ".log", ".html"])
            self.assertTrue(all(path.is_file() for path in paths))
            with self.assertRaises(FileExistsError):
                write_replay_artifacts(document, stem)

    def test_accumulator_deduplicates_the_same_visible_message(self) -> None:
        seed = CaptureSeed(
            p1_team=("Kleavor",),
            p2_team=("Miraidon",),
            source_mode="live",
        )
        accumulator = CaptureAccumulator(seed)
        first = BattleEvent(kind="move", timestamp_ms=1_000, slot="p1a", move="Stone Axe")
        repeated = BattleEvent(kind="move", timestamp_ms=1_500, slot="p1a", move="Stone Axe")
        accumulator.apply(FrameDetections(events=(first, repeated)))

        self.assertEqual(len(accumulator.events), 1)

    def test_finalize_drops_a_redundant_reswitch_of_the_same_occupant(self) -> None:
        """COL-102, reapertura estructural del 26 sep, job real
        `10a7fba6fda04585`, partida 5 (Ender): dos vías de lectura
        narraron el mismo regreso de Basculegion a p2a dos veces, diez
        segundos aparte y sin ningún faint/withdrew de por medio -una por
        especie resuelta directamente, otra por una identidad que recién
        se ata a esa misma especie después. `_drop_ghost_reentries` no lo
        atrapa: exige una lectura de 0 PS reciente y una identidad sin
        resolver a la vez, y aquí Basculegion vuelve sano y la segunda
        lectura sólo se resuelve a esa especie al cerrar la batalla, no
        antes -por eso hace falta un pase aparte, después de resolver
        identidades.
        """

        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(
                kind="switch", timestamp_ms=356_000, slot="p2a",
                species="Basculegion", health="100/100",
            ),
            BattleEvent(kind="move", timestamp_ms=360_000, slot="p1a", move="Psychic Terrain"),
            BattleEvent(
                kind="switch", timestamp_ms=366_000, slot="p2a",
                species="__champions_actor_p2_0001__",
            ),
        ]

        accumulator._drop_redundant_reswitches({"__champions_actor_p2_0001__": "Basculegion"})

        switches = [event for event in accumulator.events if event.kind == "switch" and event.slot == "p2a"]
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0].timestamp_ms, 356_000)

    def test_a_switch_to_a_different_species_is_not_dropped(self) -> None:
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(
                kind="switch", timestamp_ms=100_000, slot="p2a",
                species="Basculegion", health="100/100",
            ),
            BattleEvent(
                kind="switch", timestamp_ms=160_000, slot="p2a",
                species="Pelipper", health="100/100",
            ),
        ]

        accumulator._drop_redundant_reswitches({})

        switches = [event for event in accumulator.events if event.kind == "switch" and event.slot == "p2a"]
        self.assertEqual(len(switches), 2)

    def test_a_reentry_after_a_faint_is_not_dropped(self) -> None:
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(
                kind="switch", timestamp_ms=100_000, slot="p2a",
                species="Basculegion", health="100/100",
            ),
            BattleEvent(kind="faint", timestamp_ms=150_000, slot="p2a", species="Basculegion"),
            BattleEvent(
                kind="switch", timestamp_ms=160_000, slot="p2a",
                species="Basculegion", health="100/100",
            ),
        ]

        accumulator._drop_redundant_reswitches({})

        switches = [event for event in accumulator.events if event.kind == "switch" and event.slot == "p2a"]
        self.assertEqual(len(switches), 2)

    def test_accumulator_collapses_interleaved_hp_animation_frames(self) -> None:
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(kind="damage", timestamp_ms=1_000, slot="p2a", health="4/100"),
                    BattleEvent(kind="damage", timestamp_ms=1_000, slot="p2b", health="69/100"),
                    BattleEvent(kind="damage", timestamp_ms=1_500, slot="p2a", health="2/100"),
                )
            )
        )

        self.assertEqual(
            [(event.slot, event.health) for event in accumulator.events],
            [("p2a", "2/100"), ("p2b", "69/100")],
        )

    def test_accumulator_restores_a_health_reading_the_hud_corrected_itself(self) -> None:
        # COL-102, job 82923f56ce264a92, Partida 1, Turno 4: Scald deja a
        # Incineroar en 28% -leído en varios frames seguidos-, un único
        # frame de OCR malo lo lee "3%", y el frame siguiente ya vuelve a
        # leer 28%. Como esa vuelta es una lectura de "cura" (28 > 3) en vez
        # de "daño" (mismo tipo que veníamos viendo), no calzaba con la
        # fusión de arriba y el replay escribía un daño a 3% seguido de una
        # cura a 28% que nunca ocurrió -encima del Sitrus Berry real, que sí
        # curó de 28% a 52% después. Confirmado contra el video: la barra
        # nunca bajó de 28%.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(
                        kind="damage", timestamp_ms=311_000, slot="p2a",
                        species="Incineroar", health="46/100",
                    ),
                    BattleEvent(
                        kind="damage", timestamp_ms=311_500, slot="p2a",
                        species="Incineroar", health="28/100",
                    ),
                    BattleEvent(
                        kind="damage", timestamp_ms=312_500, slot="p2a",
                        species="Incineroar", health="3/100",
                    ),
                    BattleEvent(
                        kind="heal", timestamp_ms=313_000, slot="p2a",
                        species="Incineroar", health="28/100",
                    ),
                    BattleEvent(
                        kind="heal", timestamp_ms=317_500, slot="p2a",
                        species="Incineroar", health="42/100",
                    ),
                    BattleEvent(
                        kind="heal", timestamp_ms=318_000, slot="p2a",
                        species="Incineroar", health="52/100",
                    ),
                )
            )
        )

        self.assertEqual(
            [(event.kind, event.slot, event.health) for event in accumulator.events],
            [("damage", "p2a", "28/100"), ("heal", "p2a", "52/100")],
        )

    def test_a_faint_closes_the_hit_that_caused_it_at_zero(self) -> None:
        # COL-102, job 5748b289aa5b445b, turno 4 (frames 636-645): Zap Cannon
        # baja a Dragonite de 29 % a 0 %, pero el OCR pierde el "0" de "0 %" y
        # sólo queda la lectura de mitad de animación (26 %). Luego llega "The
        # opposing Dragonite fainted!": ese golpe terminó en 0. Un debilitado
        # sin daño leído en su acción (por ejemplo, tras cambiar) no inventa
        # ninguno.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(kind="move", timestamp_ms=317_500, slot="p1b", species="Raichu", move="Zap Cannon"),
                    BattleEvent(kind="damage", timestamp_ms=320_000, slot="p2a", species="Dragonite", health="26/100"),
                    BattleEvent(kind="faint", timestamp_ms=322_000, slot="p2a", species="Dragonite"),
                    BattleEvent(kind="move", timestamp_ms=326_000, slot="p2b", species="Sableye", move="Foul Play"),
                    BattleEvent(kind="damage", timestamp_ms=328_500, slot="p1a", species="Sneasler", health="10/155"),
                    BattleEvent(kind="faint", timestamp_ms=332_000, slot="p1a", species="Sneasler"),
                    BattleEvent(kind="switch", timestamp_ms=346_500, slot="p1a", species="Kingambit", health="177/177"),
                    BattleEvent(kind="faint", timestamp_ms=350_000, slot="p2b", species="Sableye"),
                )
            )
        )

        self.assertEqual(
            [(event.kind, event.slot, event.health) for event in accumulator.events if event.kind == "damage"],
            [("damage", "p2a", "0/100"), ("damage", "p1a", "0/155")],
        )

    def test_a_focus_sash_closes_the_hit_that_triggered_it_at_one(self) -> None:
        # COL-102, job 4eb88ad277cf4546, turno 1 (frames 340-359): el Wave
        # Crash crítico deja a Ceruledge en "1 %", pero el OCR pierde ese 1 y
        # quedaba el 69 % de mitad de animación. "Hung on using its Focus
        # Sash!" sólo pasa si el golpe deja 1 PS. Un debilitado en una acción
        # posterior no vuelve a tocar ese golpe.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(kind="move", timestamp_ms=167_000, slot="p1a", species="Basculegion", move="Wave Crash"),
                    BattleEvent(kind="damage", timestamp_ms=169_500, slot="p2a", species="Ceruledge", health="69/100"),
                    BattleEvent(kind="enditem", timestamp_ms=178_000, slot="p2a", species="Ceruledge", value="Focus Sash"),
                    BattleEvent(kind="move", timestamp_ms=190_000, slot="p2a", species="Ceruledge", move="Phantom Force"),
                    BattleEvent(kind="faint", timestamp_ms=200_000, slot="p2a", species="Ceruledge"),
                )
            )
        )

        self.assertEqual(
            [(event.kind, event.slot, event.health or event.value) for event in accumulator.events if event.kind in {"damage", "enditem"}],
            [("damage", "p2a", "1/100"), ("enditem", "p2a", "Focus Sash")],
        )

    def test_a_bar_read_before_the_turns_first_action_settles_the_previous_hit(self) -> None:
        # COL-102, job 8b7488cb5914449f, partida 2 (frames 1346-1418): Terrain
        # Pulse deja a Venusaur en 1 %, leído a mitad de animación (79 %); el
        # "1%" no se lee hasta el frame 1392, con el turno 2 ya abierto y
        # antes de cualquier movimiento. Era un segundo golpe sin causa: es el
        # final del único golpe. Un cambio leído después de una acción del
        # turno sigue siendo suyo.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(kind="move", timestamp_ms=672_500, slot="p1b", species="Indeedee-F", move="Terrain Pulse"),
                    BattleEvent(kind="damage", timestamp_ms=674_500, slot="p2a", species="Venusaur", health="79/100"),
                    BattleEvent(kind="message", timestamp_ms=676_000, value="It's super effective on the opposing Venusaur!"),
                    BattleEvent(kind="turn", timestamp_ms=681_500, turn=2),
                    BattleEvent(kind="damage", timestamp_ms=695_500, slot="p2a", species="Venusaur", health="1/100"),
                    BattleEvent(kind="move", timestamp_ms=703_000, slot="p1b", species="Indeedee-F", move="Follow Me"),
                    BattleEvent(kind="move", timestamp_ms=716_500, slot="p1a", species="Blaziken", move="Rock Slide"),
                    BattleEvent(kind="damage", timestamp_ms=718_500, slot="p2a", species="Venusaur", health="0/100"),
                )
            )
        )

        self.assertEqual(
            [(event.kind, event.health or event.move) for event in accumulator.events if event.kind in {"move", "damage", "turn"}],
            [
                ("move", "Terrain Pulse"),
                ("damage", "1/100"),
                ("turn", None),
                ("move", "Follow Me"),
                ("move", "Rock Slide"),
                ("damage", "0/100"),
            ],
        )

    def test_a_faint_animation_noise_is_dropped_around_the_real_faint(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `90403f16712d4d41`,
        # partida 1: Close Combat deja a Archaludon en 0/100 -real, con
        # faint real después-, pero entre medio el OCR lee un "9/100"
        # fantasma -ruido de la animación del golpe final. `_close_last_hit`
        # corrige la ÚLTIMA lectura de daño antes del faint, pero no tocaba
        # esta curación fantasma de por medio: sobrevivía en el replay.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=1_000, slot="p2b", species="Archaludon", health="51/100"),
            BattleEvent(kind="damage", timestamp_ms=1_500, slot="p2b", species="Archaludon", health="0/100"),
            BattleEvent(kind="heal", timestamp_ms=3_500, slot="p2b", species="Archaludon", health="9/100"),
            BattleEvent(kind="faint", timestamp_ms=6_000, slot="p2b", species="Archaludon"),
        ]

        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()

        self.assertEqual(
            [(event.kind, event.health) for event in accumulator.events],
            [("damage", "51/100"), ("damage", "0/100"), ("faint", None)],
        )

    def test_a_zero_reading_with_no_faint_at_the_end_synthesizes_one(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `10a7fba6fda04585`,
        # partida 4, turno 8: Wood Hammer deja a Milotic en 0/100 tras un
        # golpe superefectivo confirmado, pero "fainted!" nunca se leyó -
        # ningún otro evento vuelve a tocar ese slot en el resto de la
        # batalla. Sin faint, el Pokémon quedaba "en pie" a 0 PS para
        # siempre.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=1_000, slot="p2b", species="Milotic", health="45/100"),
            BattleEvent(kind="damage", timestamp_ms=1_500, slot="p2b", species="Milotic", health="0/100"),
        ]

        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()

        self.assertEqual(
            [(event.kind, event.slot, event.species) for event in accumulator.events],
            [
                ("damage", "p2b", "Milotic"),
                ("damage", "p2b", "Milotic"),
                ("faint", "p2b", "Milotic"),
            ],
        )

    def test_a_zero_reading_that_later_shows_real_life_was_never_a_faint(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `331e6e783c3e45a4`,
        # partida 3: Sucker Punch deja a Salamence en "0/100" sin faint, y se
        # cura a "65/100" un turno después sin ningún move/item que lo
        # explique -un Pokémon vivo no puede estar en 0 PS. El "0" fue el
        # que estaba mal, no la cura: nunca dejó de estar en pie.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=1_000, slot="p2a", species="Salamence", health="82/100"),
            BattleEvent(kind="damage", timestamp_ms=41_000, slot="p2a", species="Salamence", health="0/100"),
            BattleEvent(kind="heal", timestamp_ms=42_000, slot="p2a", species="Salamence", health="65/100"),
        ]

        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()

        self.assertEqual(
            [(event.kind, event.health) for event in accumulator.events],
            [("damage", "82/100"), ("heal", "65/100")],
        )

    def test_an_unresolved_identity_that_faints_instantly_is_the_same_death(self) -> None:
        # COL-102, reapertura estructural del 25 sep, job `90403f16712d4d41`,
        # partida 3: Indeedee-F llega a 0 PS; 1,5 s después una identidad sin
        # resolver "entra" a su mismo slot y se debilita en el mismo
        # instante -el HUD perdió el ícono un instante en plena animación de
        # debilitado y lo leyó como una entrada nueva. Sin esto, el replay
        # final mostraba a Indeedee-F debilitarse, "volver a entrar" a 0 PS
        # con otro nombre, y debilitarse otra vez.
        ghost = "__champions_actor_p1_0001__"
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=1_000, slot="p1a", species="Indeedee-F", health="0/177"),
            BattleEvent(kind="switch", timestamp_ms=1_500, slot="p1a", species=ghost, health="0/177"),
            BattleEvent(kind="faint", timestamp_ms=1_500, slot="p1a", species=ghost),
            BattleEvent(kind="switch", timestamp_ms=10_000, slot="p1a", species="Kingambit", health="177/177"),
        ]

        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()

        self.assertEqual(
            [(event.kind, event.species, event.health) for event in accumulator.events],
            [
                ("damage", "Indeedee-F", "0/177"),
                ("faint", "Indeedee-F", None),
                ("switch", "Kingambit", "177/177"),
            ],
        )

    def test_pipeline_reorders_detected_selection_with_observed_leads_first(self) -> None:
        frames = [
            FramePacket(index=0, timestamp_ms=0, image=b"first"),
            FramePacket(index=1, timestamp_ms=1_000, image=b"second"),
        ]
        detections = {
            0: FrameDetections(
                p1_team=("Kleavor", "Pelipper", "Venusaur", "Sinistcha", "Archaludon", "Luxray"),
                p2_team=("Incineroar", "Rillaboom", "Urshifu-Rapid-Strike", "Farigiraf", "Miraidon", "Amoonguss"),
                p1_selected=("Venusaur", "Kleavor", "Pelipper", "Sinistcha"),
                p2_selected=("Incineroar", "Miraidon", "Amoonguss", "Rillaboom"),
                events=(
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p1a", species="Kleavor"),
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p1b", species="Pelipper"),
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species="Miraidon"),
                    BattleEvent(kind="switch", timestamp_ms=0, slot="p2b", species="Amoonguss"),
                ),
                battle_started=True,
            ),
            1: FrameDetections(
                events=(BattleEvent(kind="turn", timestamp_ms=1_000, turn=1),),
                winner="p1",
                battle_complete=True,
            ),
        }

        class SequenceDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

        capture = ReplayCapturePipeline(
            frames,
            SequenceDetector(),
            CaptureSeed(p1_name="IesYo", p2_name="Rival"),
        ).capture()[0]

        self.assertEqual(capture.p1.selected[:2], ("Kleavor", "Pelipper"))
        self.assertEqual(capture.p2.selected[:2], ("Miraidon", "Amoonguss"))

    def test_extracts_fenced_visual_json(self) -> None:
        parsed = _extract_json(
            """```json
{"battle_started": true, "events": [{"kind": "turn", "turn": 1, "confidence": 0.9}]}
```"""
        )
        detections = FrameDetections.from_mapping(parsed, timestamp_ms=500)

        self.assertTrue(detections.battle_started)
        self.assertEqual(detections.events[0].turn, 1)
        self.assertEqual(detections.events[0].timestamp_ms, 500)

    def test_extracts_visual_json_after_reasoning_with_unrelated_braces(self) -> None:
        parsed = _extract_json(
            """<think>First consider {\"unrelated\": true}.</think>
```json
{"battle_started": false, "events": []}
```"""
        )

        self.assertEqual(parsed, {"battle_started": False, "events": []})

    def test_pipeline_skips_one_bad_detection_and_reports_progress(self) -> None:
        frames = [
            FramePacket(index=0, timestamp_ms=0, image=b"bad"),
            FramePacket(index=1, timestamp_ms=500, image=b"start"),
            FramePacket(index=2, timestamp_ms=1_000, image=b"finish"),
        ]

        class SequenceDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                if frame.index == 0:
                    raise DetectionError("respuesta sin JSON")
                if frame.index == 1:
                    return FrameDetections(
                        events=(BattleEvent(kind="turn", timestamp_ms=500, turn=1),),
                        battle_started=True,
                    )
                return FrameDetections(winner="p1", battle_complete=True)

        progress = []
        warnings = []
        captures = ReplayCapturePipeline(
            frames,
            SequenceDetector(),
            CaptureSeed(p1_team=("Kleavor",), p2_team=("Miraidon",)),
        ).capture(total_frames=3, on_progress=progress.append, on_warning=warnings.append)

        self.assertEqual(len(captures), 1)
        self.assertEqual([item.processed_frames for item in progress], [1, 2, 3])
        self.assertEqual([item.skipped_frames for item in progress], [1, 1, 1])
        self.assertEqual(progress[-1].fraction, 1.0)
        self.assertEqual(progress[-1].battles_detected, 1)
        self.assertEqual(len(warnings), 1)

    def test_pipeline_captures_all_battles_without_duplicating_the_last_one(self) -> None:
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(5)
        ]
        detections = {
            0: FrameDetections(
                events=(BattleEvent(kind="turn", timestamp_ms=0, turn=1),),
                battle_started=True,
            ),
            1: FrameDetections(winner="p1", battle_complete=True),
            2: FrameDetections(winner="p1", battle_complete=True),
            3: FrameDetections(
                events=(BattleEvent(kind="turn", timestamp_ms=3_000, turn=1),),
                battle_started=True,
            ),
            4: FrameDetections(winner="p2", battle_complete=True),
        }

        class ResettableSequenceDetector:
            def __init__(self) -> None:
                self.reset_count = 0

            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

            def reset_battle_state(self) -> None:
                self.reset_count += 1

        detector = ResettableSequenceDetector()
        captures = ReplayCapturePipeline(
            frames,
            detector,
            CaptureSeed(p1_team=("Kleavor",), p2_team=("Miraidon",)),
        ).capture(max_battles=0)

        self.assertEqual([capture.winner for capture in captures], ["p1", "p2"])
        self.assertEqual([len(capture.events) for capture in captures], [1, 1])
        self.assertEqual(detector.reset_count, 2)

    def test_pipeline_keeps_the_next_team_preview_while_waiting_for_battle_start(self) -> None:
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(6)
        ]
        detections = {
            0: FrameDetections(
                p1_selected=("Kleavor", "Pelipper", "Venusaur", "Sinistcha"),
                team_preview=True,
            ),
            1: FrameDetections(
                events=(BattleEvent(kind="turn", timestamp_ms=1_000, turn=1),),
                battle_started=True,
            ),
            2: FrameDetections(winner="p1", battle_complete=True),
            3: FrameDetections(
                p1_selected=("Archaludon", "Luxray", "Pelipper", "Kleavor"),
                team_preview=True,
            ),
            4: FrameDetections(
                events=(BattleEvent(kind="turn", timestamp_ms=4_000, turn=1),),
                battle_started=True,
            ),
            5: FrameDetections(winner="p2", battle_complete=True),
        }

        class PreviewSequenceDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

            def reset_battle_state(self) -> None:
                return None

        captures = ReplayCapturePipeline(
            frames,
            PreviewSequenceDetector(),
            CaptureSeed(
                p1_team=("Kleavor", "Pelipper", "Venusaur", "Sinistcha", "Archaludon", "Luxray"),
                p2_team=("Miraidon",),
            ),
        ).capture(max_battles=0)

        self.assertEqual(len(captures), 2)
        self.assertEqual(
            [capture.p1.selected for capture in captures],
            [
                ("Kleavor", "Pelipper", "Venusaur", "Sinistcha"),
                ("Archaludon", "Luxray", "Pelipper", "Kleavor"),
            ],
        )

    def test_video_ocr_is_strictly_sequential(self) -> None:
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(4)
        ]

        class SequentialDetector:
            def __init__(self) -> None:
                self.parsed: list[int] = []

            def prepare(self, _frame: FramePacket) -> int:
                raise AssertionError("No debe existir una cola OCR paralela.")

            def detect(self, frame: FramePacket) -> FrameDetections:
                self.parsed.append(frame.index)
                if frame.index == 0:
                    return FrameDetections(
                        events=(BattleEvent(kind="turn", timestamp_ms=0, turn=1),),
                        battle_started=True,
                    )
                if frame.index == 3:
                    return FrameDetections(winner="p1", battle_complete=True)
                return FrameDetections(battle_started=True)

        detector = SequentialDetector()
        captures = ReplayCapturePipeline(
            frames,
            detector,
            CaptureSeed(p1_team=("Kleavor",), p2_team=("Miraidon",)),
        ).capture()

        self.assertEqual(len(captures), 1)
        self.assertEqual(detector.parsed, [0, 1, 2, 3])

    def test_pipeline_flushes_pending_detector_data_before_finalizing(self) -> None:
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(2)
        ]

        class PendingDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                if frame.index == 0:
                    return FrameDetections(
                        events=(BattleEvent(kind="turn", timestamp_ms=0, turn=1),),
                        battle_started=True,
                    )
                return FrameDetections(winner="p1", battle_complete=True)

            def flush_pending(self) -> FrameDetections:
                return FrameDetections(p2_selected=("Metagross", "Sableye"))

        captures = ReplayCapturePipeline(
            frames,
            PendingDetector(),
            CaptureSeed(p1_team=("Kleavor",), p2_team=("Metagross", "Sableye")),
        ).capture()

        self.assertEqual(captures[0].p2.selected, ("Metagross", "Sableye"))

    def test_pipeline_discards_a_false_notification_result_and_keeps_scanning(self) -> None:
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(4)
        ]
        detections = {
            0: FrameDetections(
                events=(BattleEvent(kind="message", timestamp_ms=0, value="WhatsApp notification."),),
                battle_started=True,
            ),
            1: FrameDetections(winner="p1", battle_complete=True),
            2: FrameDetections(
                p2_team=("Miraidon",),
                events=(BattleEvent(kind="turn", timestamp_ms=2_000, turn=1),),
                battle_started=True,
            ),
            3: FrameDetections(winner="p2", battle_complete=True),
        }

        class ResettableSequenceDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

            def reset_battle_state(self) -> None:
                return None

        warnings: list[str] = []
        captures = ReplayCapturePipeline(
            frames,
            ResettableSequenceDetector(),
            CaptureSeed(p1_team=("Kleavor",)),
        ).capture(max_battles=0, on_warning=warnings.append)

        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].winner, "p2")
        self.assertTrue(any("descartado" in warning for warning in warnings))

    def test_pipeline_reports_the_raw_events_of_a_discarded_battle(self) -> None:
        # COL-102: una batalla descartada no deja de haber pasado -sus
        # eventos son la única pista de en qué instante del vídeo se
        # perdió, para que `risk_windows` pueda marcarlo y una relectura
        # densa tenga una oportunidad real de rescatarla.
        frames = [
            FramePacket(index=index, timestamp_ms=index * 1_000, image=str(index).encode())
            for index in range(4)
        ]
        detections = {
            0: FrameDetections(
                events=(BattleEvent(kind="message", timestamp_ms=0, value="WhatsApp notification."),),
                battle_started=True,
            ),
            1: FrameDetections(winner="p1", battle_complete=True),
            2: FrameDetections(
                p2_team=("Miraidon",),
                events=(BattleEvent(kind="turn", timestamp_ms=2_000, turn=1),),
                battle_started=True,
            ),
            3: FrameDetections(winner="p2", battle_complete=True),
        }

        class ResettableSequenceDetector:
            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

            def reset_battle_state(self) -> None:
                return None

        incomplete: list[tuple[BattleEvent, ...]] = []
        ReplayCapturePipeline(
            frames,
            ResettableSequenceDetector(),
            CaptureSeed(p1_team=("Kleavor",)),
        ).capture(max_battles=0, on_incomplete_battle=incomplete.append)

        self.assertEqual(len(incomplete), 1)
        self.assertEqual([event.kind for event in incomplete[0]], ["message"])

    def test_pipeline_rejects_negative_battle_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "usa 0"):
            ReplayCapturePipeline([], MagicMock(), CaptureSeed()).capture(max_battles=-1)

    def test_splits_mjpeg_pipe_without_image_dependencies(self) -> None:
        first = b"\xff\xd8first\xff\xd9"
        second = b"\xff\xd8second\xff\xd9"
        self.assertEqual(list(iter_mjpeg(io.BytesIO(b"junk" + first + second))), [first, second])

    def test_builds_video_and_windows_obs_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "battle.mp4"
            video.touch()
            video_command = VideoFrameSource(path=video, sample_fps=2).command()
        live_command = LiveFrameSource(
            input_name="OBS Virtual Camera",
            backend="dshow",
            sample_fps=3,
        ).command()

        self.assertIn(str(video), video_command)
        self.assertIn("fps=2", video_command)
        self.assertIn("video=OBS Virtual Camera", live_command)
        self.assertIn("fps=3", live_command)

    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffmpeg")
    @patch("pkmn_vgc.champions_replay.sources.subprocess.Popen")
    def test_live_source_keeps_latest_frame_instead_of_building_backlog(
        self,
        popen: MagicMock,
        _which: object,
    ) -> None:
        first = b"\xff\xd8first\xff\xd9"
        second = b"\xff\xd8second\xff\xd9"
        third = b"\xff\xd8third\xff\xd9"
        process = MagicMock()
        process.stdout = io.BytesIO(first + second + third)
        process.poll.return_value = 0
        process.wait.return_value = 0
        process.returncode = 0
        popen.return_value = process

        frames = list(LiveFrameSource(input_name="OBS Virtual Camera", backend="dshow"))

        self.assertEqual([(frame.index, frame.image) for frame in frames], [(2, third)])
        process.terminate.assert_not_called()

    @patch("pkmn_vgc.champions_replay.sources.subprocess.run")
    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffprobe")
    def test_estimates_sampled_video_frames_with_ffprobe(
        self,
        _which: object,
        run: MagicMock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="10.1\n", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "battle.mp4"
            video.touch()
            source = VideoFrameSource(path=video, sample_fps=2, max_frames=15)

            self.assertEqual(source.estimated_frame_count(), 15)

    @patch("pkmn_vgc.champions_replay.sources.subprocess.run")
    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffprobe")
    def test_segmented_source_covers_the_whole_video_around_a_dense_window(
        self, _which: object, run: MagicMock,
    ) -> None:
        # COL-102, reapertura estructural del 25 sep: en vez de subir el fps
        # de punta a punta, sólo se relee más denso alrededor de los
        # instantes de riesgo (`risk_windows`); el resto del vídeo se queda
        # al ritmo de siempre.
        run.return_value = subprocess.CompletedProcess([], 0, stdout="30.0\n", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "battle.mp4"
            video.touch()
            source = SegmentedVideoFrameSource(
                path=video,
                dense_windows=((10_000, 15_000),),
                base_fps=2.0,
                dense_fps=8.0,
            )

            self.assertEqual(
                source._segments(),
                [(0, 10_000, 2.0), (10_000, 15_000, 8.0), (15_000, 30_000, 2.0)],
            )
            self.assertEqual(source.estimated_frame_count(), 90)

    @patch("pkmn_vgc.champions_replay.sources.subprocess.run")
    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffprobe")
    def test_segmented_source_clips_a_window_past_the_end_of_the_video(
        self, _which: object, run: MagicMock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="30.0\n", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "battle.mp4"
            video.touch()
            source = SegmentedVideoFrameSource(
                path=video, dense_windows=((27_000, 40_000),), base_fps=2.0, dense_fps=8.0,
            )

            self.assertEqual(
                source._segments(),
                [(0, 27_000, 2.0), (27_000, 30_000, 8.0)],
            )

    @patch("pkmn_vgc.champions_replay.sources.subprocess.run")
    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffprobe")
    def test_segmented_source_with_no_windows_is_a_single_base_fps_pass(
        self, _which: object, run: MagicMock,
    ) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="30.0\n", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "battle.mp4"
            video.touch()
            source = SegmentedVideoFrameSource(path=video, dense_windows=(), base_fps=2.0, dense_fps=8.0)

            self.assertEqual(source._segments(), [(0, 30_000, 2.0)])

    @patch("pkmn_vgc.champions_replay.sources.shutil.which", return_value="ffmpeg")
    @patch("pkmn_vgc.champions_replay.sources.subprocess.Popen")
    def test_segmented_source_yields_real_video_timestamps_per_segment(
        self, popen: MagicMock, _which: object,
    ) -> None:
        """Cada tramo es su propio proceso de FFmpeg; los timestamps que
        produce son los del vídeo real (no reiniciados por tramo)."""

        jpeg = b"\xff\xd8x\xff\xd9"

        def _process(frame_count: int) -> MagicMock:
            process = MagicMock()
            process.stdout = io.BytesIO(jpeg * frame_count)
            process.poll.return_value = 0
            process.wait.return_value = 0
            process.returncode = 0
            return process

        # Segmentos [0,2000) a 2 fps (4 frames) y [2000,3000) a 8 fps (8 frames).
        popen.side_effect = [_process(4), _process(8)]
        source = SegmentedVideoFrameSource.__new__(SegmentedVideoFrameSource)
        source.path = Path("battle.mp4")
        source.dense_windows = ((2_000, 3_000),)
        source.base_fps = 2.0
        source.dense_fps = 8.0
        source.ffmpeg_binary = "ffmpeg"
        source.ffprobe_binary = "ffprobe"

        with patch.object(SegmentedVideoFrameSource, "_duration_ms", return_value=3_000):
            frames = list(source)

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(
            [frame.timestamp_ms for frame in frames],
            [0, 500, 1_000, 1_500, 2_000, 2_125, 2_250, 2_375, 2_500, 2_625, 2_750, 2_875],
        )
        self.assertEqual([frame.index for frame in frames], list(range(12)))
        first_command, second_command = popen.call_args_list[0].args[0], popen.call_args_list[1].args[0]
        self.assertIn("fps=2", first_command)
        self.assertIn("fps=8", second_command)
        self.assertIn("2.000", second_command)  # -ss del segundo tramo

    def test_rejects_remote_ollama_endpoints(self) -> None:
        with self.assertRaisesRegex(ValueError, "localmente"):
            OllamaVisionDetector(endpoint="https://example.com")
        with self.assertRaisesRegex(ValueError, "localmente"):
            OllamaHudAliasResolver(endpoint="https://example.com")

    @patch("pkmn_vgc.champions_replay.detector.urlopen")
    def test_reads_visual_hud_aliases_and_rejects_unrequested_names(self, urlopen: MagicMock) -> None:
        body = {
            "response": json.dumps(
                {
                    "aliases": [
                        {
                            "candidate_id": "p2-1",
                            "species": "Metagross",
                            "gender": "M",
                            "confidence": 0.98,
                        },
                        {
                            "candidate_id": "p2-99",
                            "species": "Sableye",
                            "gender": None,
                            "confidence": 0.99,
                        },
                    ]
                },
                ensure_ascii=False,
            )
        }
        urlopen.return_value = io.BytesIO(json.dumps(body).encode())

        aliases = OllamaHudAliasResolver().resolve(
            FramePacket(index=346, timestamp_ms=173_000, image=b"jpeg"),
            (("p2", "せんせい"), ("p2", "しごでき")),
        )
        request = json.loads(urlopen.call_args.args[0].data)

        self.assertEqual([(alias.nickname, alias.species) for alias in aliases], [("せんせい", "Metagross")])
        self.assertIn("せんせい", request["prompt"])
        self.assertIn("しごでき", request["prompt"])
        self.assertEqual(request["format"]["required"], ["aliases"])
        self.assertEqual(
            request["format"]["properties"]["aliases"]["items"]["properties"]["candidate_id"]["enum"],
            ["p2-1", "p2-2"],
        )

    @patch("pkmn_vgc.champions_replay.detector.urlopen")
    def test_visual_hud_aliases_retry_an_empty_ollama_response(self, urlopen: MagicMock) -> None:
        empty = {"response": "", "thinking": "", "done_reason": "stop", "eval_count": 0}
        success = {
            "response": json.dumps(
                {
                    "aliases": [
                        {
                            "candidate_id": "p2-1",
                            "species": "Metagross",
                            "gender": "M",
                            "confidence": 0.98,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        }
        urlopen.side_effect = [
            io.BytesIO(json.dumps(empty).encode()),
            io.BytesIO(json.dumps(success).encode()),
        ]

        aliases = OllamaHudAliasResolver().resolve(
            FramePacket(index=346, timestamp_ms=173_000, image=b"jpeg"),
            (("p2", "せんせい"),),
        )
        second_request = json.loads(urlopen.call_args_list[1].args[0].data)

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(aliases[0].species, "Metagross")
        self.assertIn("CORRECCIÓN", second_request["prompt"])

    @patch("pkmn_vgc.champions_replay.detector.urlopen")
    def test_visual_hud_aliases_report_an_ollama_timeout(self, urlopen: MagicMock) -> None:
        urlopen.side_effect = [TimeoutError(), TimeoutError()]

        with self.assertRaisesRegex(
            DetectionError,
            "Ollama agotó 12 segundos leyendo el HUD con qwen3-vl:4b",
        ):
            OllamaHudAliasResolver(timeout_seconds=12).resolve(
                FramePacket(index=196, timestamp_ms=98_000, image=b"jpeg"),
                (("p2", "せんせい"),),
            )

        self.assertEqual(urlopen.call_count, 2)

    @patch("pkmn_vgc.champions_replay.detector.urlopen")
    def test_visual_hud_aliases_report_an_ollama_connection_error(self, urlopen: MagicMock) -> None:
        urlopen.side_effect = [URLError("connection refused"), URLError("connection refused")]

        with self.assertRaisesRegex(
            DetectionError,
            "No pudimos conectar con Ollama para leer el HUD.*connection refused",
        ):
            OllamaHudAliasResolver().resolve(
                FramePacket(index=196, timestamp_ms=98_000, image=b"jpeg"),
                (("p2", "せんせい"),),
            )

        self.assertEqual(urlopen.call_count, 2)

    @patch("pkmn_vgc.champions_replay.detector.urlopen")
    def test_reads_qwen_structured_output_from_thinking_field(self, urlopen: MagicMock) -> None:
        body = {
            "response": "",
            "thinking": json.dumps({
                "battle_started": True,
                "events": [{"kind": "turn", "turn": 1, "confidence": 0.95}],
            }),
            "done_reason": "stop",
            "eval_count": 25,
        }
        urlopen.return_value = io.BytesIO(json.dumps(body).encode())

        detections = OllamaVisionDetector().detect(
            FramePacket(index=0, timestamp_ms=700, image=b"jpeg")
        )

        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(detections.battle_started)
        self.assertEqual(detections.events[0].timestamp_ms, 700)

    def test_calls_local_ollama_with_image_and_structured_output(self) -> None:
        received: list[dict[str, object]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - nombre definido por BaseHTTPRequestHandler
                size = int(self.headers.get("content-length", "0"))
                received.append(json.loads(self.rfile.read(size)))
                response = "no pude estructurar la lectura" if len(received) == 1 else json.dumps({
                    "battle_started": True,
                    "events": [{"kind": "turn", "turn": 1, "confidence": 0.95}],
                })
                body = json.dumps({
                    "response": response,
                }).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            detector = OllamaVisionDetector(
                endpoint=f"http://127.0.0.1:{server.server_port}",
                context=DetectorContext(p1_team=("Kleavor",)),
            )
            detections = detector.detect(FramePacket(index=0, timestamp_ms=700, image=b"jpeg"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(len(received), 2)
        self.assertEqual(received[0]["model"], "qwen3-vl:4b")
        self.assertEqual(received[0]["format"]["type"], "object")
        self.assertEqual(received[0]["think"], False)
        self.assertTrue(received[0]["images"])
        self.assertIn("CORRECCIÓN", received[1]["prompt"])
        self.assertEqual(detections.events[0].timestamp_ms, 700)


if __name__ == "__main__":
    unittest.main()
