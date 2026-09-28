from __future__ import annotations

import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
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
from pkmn_vgc.champions_replay import reconcile
from pkmn_vgc.champions_replay.models import BattleEvent, BattleSide, CapturedBattle, FrameDetections
from pkmn_vgc.champions_replay.pipeline import (
    CaptureAccumulator,
    CaptureSeed,
    ReplayCapturePipeline,
    _revived_identity_issues,
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
        # Comparado tras el mismo viaje por JSON que ya hace
        # `write_replay_artifacts`: las tuplas (`issues`) se comparan como
        # listas, igual que en el artefacto real en disco.
        self.assertEqual(json.loads(json.dumps(document.to_dict())), expected)

    def test_captured_battle_round_trips_its_source_battle_index(self) -> None:
        # COL-102, bloqueante de Roku del 26 sep: persistido explícitamente
        # cuando la traza lo trae; `None` sólo en artefactos viejos que
        # nunca lo escribieron (la fixture de este archivo no lo trae).
        payload = json.loads((DATA / "champions_capture.json").read_text(encoding="utf-8"))
        payload["source_battle_index"] = 3
        battle = CapturedBattle.from_mapping(payload)

        self.assertEqual(battle.source_battle_index, 3)
        self.assertEqual(battle.to_dict()["source_battle_index"], 3)
        self.assertIsNone(self.capture().source_battle_index)

        document = build_replay_document(battle)
        self.assertEqual(document.source_battle_index, 3)
        self.assertEqual(document.to_dict()["source_battle_index"], 3)

    def test_build_replay_document_exposes_review_capture_issues_without_touching_the_log(self) -> None:
        # Segundo corte de Roku, 26 sep: `review_capture` vivía sólo en el
        # CLI -sus incidencias nunca llegaban al replay real que arma
        # `ChampionsJobManager`. Ahora `build_replay_document` las adjunta
        # al documento (para que salgan en Teams), sin tocar el protocolo.
        battle = self.capture()
        incomplete = replace(battle, p1=replace(battle.p1, selected=battle.p1.selected[:1]))

        document = build_replay_document(incomplete)

        self.assertTrue(document.issues)
        self.assertEqual(document.issues, tuple(asdict(issue) for issue in review_capture(incomplete)))
        self.assertEqual(document.log, build_replay_document(battle).log)

    def test_an_incomplete_player_selection_is_only_a_warning(self) -> None:
        # Un vídeo puede confirmar menos de cuatro picks sin invalidar los
        # eventos y el resultado que sí quedaron observados.
        battle = self.capture()
        incomplete = replace(battle, p1=replace(battle.p1, selected=battle.p1.selected[:2]))

        issues = review_capture(incomplete)

        matching = [issue for issue in issues if "selección del jugador" in issue.message]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].severity, "warning")
        self.assertFalse(any(issue.severity == "blocking" for issue in matching))

    def test_an_incomplete_rival_selection_is_only_a_warning(self) -> None:
        # COL-102, sexta vuelta, job real `10a7fba6fda04585`, partida 3
        # (Buss, confirmado por Ies con conocimiento directo del cliente y
        # verificado en los frames 2942/2960/3040/3095 de ese job): la
        # pantalla de selección previa al combate nunca muestra cuáles 4
        # eligió el rival -ni numeración, ni resaltado, ni atenuado en su
        # panel de seis tarjetas, en ningún frame desde el "0/4" propio
        # hasta la pantalla "VS". `p2.selected` sólo se completa observando
        # switches reales durante el combate, así que un combate corto
        # donde el rival nunca usó sus 4 elegidos dejará esa lista
        # incompleta sin que haya ningún fallo de lectura detrás. Ya no
        # bloquea el guardado -sigue como aviso, visible en Teams- porque
        # el resto del replay es fiel a lo que sí se vio en combate.
        battle = self.capture()
        incomplete = replace(battle, p2=replace(battle.p2, selected=battle.p2.selected[:2]))

        issues = review_capture(incomplete)

        matching = [issue for issue in issues if "selección del rival" in issue.message]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].severity, "warning")
        self.assertFalse(any(issue.severity == "blocking" for issue in matching))

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

        # Vuelve al campo: conserva la última vida que se le vio -lectura
        # previa del mismo actor, verificada, sin incidencia de procedencia.
        self.assertIn("|switch|p1a: Basculegion|Basculegion, L50|13/195", document.log)
        # Primera entrada de una identidad sin historial: a tope, con el
        # máximo que el log revela más adelante. Ies revirtió el `blocking`
        # que esto llevaba en `col102-r8` (ver
        # test_a_first_time_entrant_with_only_a_future_health_reading_is_not_flagged):
        # entrar a tope sin historial previo es la regla del juego, no una
        # deducción arriesgada.
        self.assertIn("|switch|p1a: Venusaur|Venusaur, L50|156/156", document.log)
        # Del rival sólo se conoce el porcentaje, y nunca se leyó: queda el
        # relleno de siempre, también sin incidencia tras la reversión (ver
        # test_a_first_time_entrant_with_no_health_reading_at_all_is_not_flagged).
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

    def test_a_switch_never_inherits_a_fainted_zero_health(self) -> None:
        """COL-102, corte de Roku sobre el commit `afee177` (26 sep, job
        real `10a7fba6fda04585`, partidas 1 y 5): esta prueba antes
        afirmaba que un `switch` sin HP propio para la clave (slot,
        especie) cuya última lectura conocida es "0/max" -el valor que
        dejó su propio debilitado- se convertía en "max/max". Roku marcó
        eso como HP inventado sin evidencia: "el test nuevo afirma
        precisamente que una reentrada sin HP propio tras 0/207 se
        convierta en 207/207". Ahora se prueba lo contrario -conservar la
        procedencia real, nunca fabricar el máximo- y que esa evidencia
        sobrevive el serializado: sin ningún `|faint|` que preceda a esta
        entrada (el 0 llegó por daño, no por un debilitado confirmado),
        `reconcile._state_findings` la marca `entrada_a_cero` sobre el
        `.log` ya escrito.
        """

        events = (
            BattleEvent(kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom"),
            BattleEvent(kind="damage", timestamp_ms=2_000, slot="p1a", species="Rillaboom", health="0/207"),
            BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Rillaboom", health=None),
        )

        completed = _with_known_health(events)

        self.assertEqual(completed[0].health, "207/207")
        self.assertEqual(completed[2].health, "0/207")

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom",), ("Rillaboom",)),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        # La primera entrada de Rillaboom es legítima (pisa el campo sin
        # lectura previa, cae al máximo); la segunda -su reentrada tras
        # 0/207- nunca debe convertirse en esa misma línea fabricada.
        self.assertEqual(document.log.count("|switch|p1a: Rillaboom|Rillaboom, L50|207/207"), 1)
        self.assertIn("|switch|p1a: Rillaboom|Rillaboom, L50|0/207", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        matching = [item for item in findings if item.category == "entrada_a_cero"]
        self.assertEqual(len(matching), 1)
        self.assertIn("Rillaboom", matching[0].detail)

    def test_entrada_a_cero_flags_a_different_species_reading_zero_health(self) -> None:
        """(a) COL-102, corte de Roku sobre `afee177`: si el OCR (o cualquier
        entrada fantasma que se le escape a `_drop_ghost_reentries`/
        `_drop_redundant_reswitches`) lee 0 PS en el `switch` de una
        especie DISTINTA a la que se acaba de debilitar en ese slot,
        `reentrada_debilitado` no lo detecta -sólo mira identidad
        repetida-, así que hace falta un hallazgo aparte que mire la vida
        codificada en la propia línea de entrada. Nunca debe convertirse
        en "100/100": la vida real (0) se conserva, y la incidencia
        sobrevive el serializado.
        """

        events = (
            BattleEvent(
                kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom", health="207/207"
            ),
            BattleEvent(kind="faint", timestamp_ms=2_000, slot="p1a", species="Rillaboom"),
            BattleEvent(
                kind="switch", timestamp_ms=3_000, slot="p1a", species="Blaziken", health="0/100"
            ),
        )

        completed = _with_known_health(events)
        self.assertEqual(completed[2].health, "0/100")

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|0/100", document.log)
        self.assertNotIn("100/100", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        matching = [item for item in findings if item.category == "entrada_a_cero"]
        self.assertEqual(len(matching), 1)
        self.assertIn("Blaziken", matching[0].detail)

    def test_a_legitimate_replacement_with_contemporaneous_health_raises_no_finding(self) -> None:
        """(c-positivo) COL-102, corte de Roku sobre `ff9e53f` (defecto #2
        de la revisión): la prueba vieja de "reemplazo legítimo" afirmaba
        como correcto un "156/156" fabricado a partir de sólo una lectura
        FUTURA -eso es exactamente el defecto, no un contraejemplo suyo.
        Este es el contraejemplo POSITIVO real: un reemplazo (especie
        distinta) tras un `faint`, con una lectura de HP REALMENTE
        observada en el propio `switch` -contemporánea, nada deducido-,
        debe entrar limpio: sin `entrada_a_cero`/`reentrada_debilitado` en
        el log servido, y sin ninguna incidencia `blocking` de procedencia
        de HP. (El otro caso "verificado" -última lectura del mismo actor,
        HP positivo heredado de un switch-out anterior- ya lo cubre
        `test_completes_the_health_of_switches_the_hud_did_not_accompany`
        más arriba; no se repite aquí. La entrada de Rillaboom lleva su
        propia lectura contemporánea para que la única variable bajo
        prueba sea la procedencia del HP de Blaziken.)
        """

        events = (
            BattleEvent(
                kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom", health="207/207"
            ),
            BattleEvent(kind="faint", timestamp_ms=2_000, slot="p1a", species="Rillaboom"),
            BattleEvent(
                kind="switch", timestamp_ms=3_000, slot="p1a", species="Blaziken", health="156/156"
            ),
        )

        completed = _with_known_health(events)
        self.assertEqual(completed[2].health, "156/156")

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|156/156", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        self.assertEqual(
            [item for item in findings if item.category in {"entrada_a_cero", "reentrada_debilitado"}], []
        )

    def test_a_first_time_entrant_with_only_a_future_health_reading_is_not_flagged(self) -> None:
        """(c-negativo #1, invertido) COL-102, reversión de Ies sobre el
        corte r8 de Roku (defecto #2 de `ff9e53f`, cuarta vuelta): la ronda
        anterior trataba esto como "deducción sin evidencia" y adjuntaba un
        `blocking`. Pero Blaziken aquí es una identidad SIN historial
        previo en absoluto en este combate -exactamente el caso de los dos
        líderes con los que arranca cada batalla real-, y "quien pisa el
        campo por primera vez entra a tope" es la regla del propio juego,
        no una deducción arriesgada. Ies revirtió ese `blocking`: el máximo
        revelado por el daño POSTERIOR (82/156) vuelve a servirse en
        silencio, sin incidencia.
        """

        events = (
            BattleEvent(
                kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom", health="207/207"
            ),
            BattleEvent(kind="faint", timestamp_ms=2_000, slot="p1a", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Blaziken", health=None),
            BattleEvent(
                kind="damage", timestamp_ms=4_000, slot="p1a", species="Blaziken", health="82/156"
            ),
        )

        completed = _with_known_health(events)
        self.assertEqual(completed[2].health, "156/156")

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|156/156", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        self.assertEqual(
            [item for item in findings if item.category in {"entrada_a_cero", "reentrada_debilitado"}], []
        )

    def test_a_first_time_entrant_with_no_health_reading_at_all_is_not_flagged(self) -> None:
        """(c-negativo #2, invertido) Mismo caso que el anterior, pero sin
        ninguna lectura en absoluto -ni antes ni después- de esta clave en
        todo el combate: `showdown._health(None)` cae a "100/100" por
        relleno puro. Sigue siendo una entrada genuinamente nueva sin
        historial -no el patrón de Rillaboom-, así que tampoco lleva
        incidencia tras la reversión de Ies.
        """

        events = (
            BattleEvent(
                kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom", health="207/207"
            ),
            BattleEvent(kind="faint", timestamp_ms=2_000, slot="p1a", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Blaziken", health=None),
        )

        completed = _with_known_health(events)
        self.assertIsNone(completed[2].health)

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=events,
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
            )
        )

        self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|100/100", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        self.assertEqual(
            [item for item in findings if item.category in {"entrada_a_cero", "reentrada_debilitado"}], []
        )

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

    def test_a_reentry_of_the_same_species_after_its_own_faint_is_dropped(self) -> None:
        """Corrige la suposición de `test_a_reentry_after_a_faint_is_not_dropped`
        (commit `cc74500`, mismo día): en ese momento se asumió que una
        reentrada de la misma especie tras un `faint` en el mismo slot
        tenía que dejarse pasar, para no invadir el terreno de
        `_drop_ghost_reentries`.

        COL-102, reapertura estructural del 26 sep, job real
        `10a7fba6fda04585`, partidas 1 y 5 (Ies, validación visual en ROG):
        el vídeo mostró exactamente este patrón como bug real -Rillaboom
        (Gori) y Rillaboom (Bonkers) "reingresan" a 0 PS 500 ms después de
        su propio `faint`, con el aviso "fainted!" todavía en pantalla. Un
        actor confirmado debilitado no puede volver a entrar a ese slot:
        ninguna regla de Showdown/VGC permite repetir especie en un mismo
        equipo, así que esta reentrada nunca es legítima y debe
        descartarse, no conservarse.
        """

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
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0].timestamp_ms, 100_000)

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

    def test_a_reading_opposite_the_last_hit_settles_too_across_a_turn_boundary(self) -> None:
        # COL-102, reapertura del 26/27 sep, job 90403f16712d4d41, partida 2:
        # Delphox cierra el turno 2 en 28/100 (daño real de Hyper Voice); un
        # solo frame en el límite del turno 3, antes de cualquier acción, lo
        # lee al revés como una cura -un valor que un turno después, también
        # antes de cualquier acción, se vuelve a leer distinto. Exigir el
        # mismo `kind` que la lectura anterior (como en el caso de arriba,
        # que sólo corrige un daño que se corrige a sí mismo) dejaba pasar
        # las dos lecturas como un -heal y un -damage nuevos, con el turno 3
        # vacío salvo por ese -heal fantasma. Ninguna lectura entre un turno
        # y su primera acción es un cambio real, en cualquier dirección: las
        # dos se pliegan sobre el mismo dato, sin turno vacío ni segundo
        # evento sin causa.
        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.apply(
            FrameDetections(
                events=(
                    BattleEvent(kind="move", timestamp_ms=100_000, slot="p1b", species="Gardevoir", move="Hyper Voice"),
                    BattleEvent(kind="damage", timestamp_ms=100_500, slot="p2a", species="Delphox", health="28/100"),
                    BattleEvent(kind="turn", timestamp_ms=140_000, turn=3),
                    BattleEvent(kind="heal", timestamp_ms=140_500, slot="p2a", species="Delphox", health="28/100"),
                    BattleEvent(kind="turn", timestamp_ms=190_000, turn=4),
                    BattleEvent(kind="damage", timestamp_ms=190_500, slot="p2a", species="Delphox", health="3/100"),
                    BattleEvent(kind="move", timestamp_ms=195_000, slot="p2a", species="Delphox", move="Protect"),
                )
            )
        )

        self.assertEqual(
            [(event.kind, event.health or event.move) for event in accumulator.events],
            [
                ("move", "Hyper Voice"),
                ("damage", "3/100"),
                ("turn", None),
                ("turn", None),
                ("move", "Protect"),
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

    def _battle_with_events(self, events: list[BattleEvent], *, winner: str = "p1") -> CapturedBattle:
        return CapturedBattle(
            p1=BattleSide("Player", ("Venusaur",), ("Venusaur",)),
            p2=BattleSide("Rival", ("Pelipper", "Kingambit"), ("Pelipper", "Kingambit")),
            events=tuple(events),
            winner=winner,
        )

    def test_a_switch_in_reading_caught_mid_animation_is_flagged_not_rewritten(self) -> None:
        # Roku, revisión del segundo corte, 26 sep: una corrección
        # automática acá no tiene respaldo de la traza -el caso real
        # (Pelipper, job `10a7fba6fda04585`, partida 5) sólo se confirmó
        # mirando el vídeo, ningún texto OCR de esos frames dice "100 %".
        # Por eso esto ya no reescribe `battle.events`: sólo reporta la
        # sospecha como `ReviewIssue`, con el máximo como hipótesis, no
        # como hecho.
        battle = self._battle_with_events(
            [
                BattleEvent(
                    kind="switch", timestamp_ms=1_000, slot="p2a", species="Pelipper", health="74/100", source_frame=5549
                ),
                BattleEvent(kind="ability", timestamp_ms=1_000, slot="p2a", species="Pelipper", value="Drizzle", source_frame=5549),
                BattleEvent(kind="damage", timestamp_ms=1_500, slot="p2a", species="Pelipper", health="37/100", source_frame=5550),
            ]
        )

        issues = review_capture(battle)

        self.assertEqual([event.health for event in battle.events], ["74/100", None, "37/100"])
        matching = [issue for issue in issues if issue.frame == 5549]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].alternatives, ("74/100",))
        self.assertIn("100/100", matching[0].proposed_change)
        self.assertIn("hipótesis", matching[0].proposed_change)

    def test_an_isolated_switch_in_reading_without_corroboration_is_not_flagged(self) -> None:
        # Un switch con HP reducido y NADA después que lo contradiga en la
        # misma ventana puede ser un caso real (hazard de entrada); sin
        # corroboración de que la barra seguía animando, no se marca.
        battle = self._battle_with_events(
            [
                BattleEvent(
                    kind="switch", timestamp_ms=1_000, slot="p2a", species="Pelipper", health="88/100", source_frame=100
                ),
                BattleEvent(kind="move", timestamp_ms=5_000, slot="p2a", species="Pelipper", move="Hurricane", source_frame=110),
            ]
        )

        issues = review_capture(battle)

        self.assertEqual(battle.events[0].health, "88/100")
        self.assertEqual([issue for issue in issues if issue.frame == 100], [])

    def test_a_legitimate_re_entry_at_partial_hp_is_not_flagged(self) -> None:
        # Una reentrada real a media vida (el Pokémon ya estuvo en el
        # campo, salió con HP reducido y vuelve a entrar) no es la barra
        # animando: es su HP real. Sólo la PRIMERA aparición se marca.
        # Contraejemplo pedido por Roku: aunque un evento posterior
        # cercano exista, la reentrada conserva su valor y no genera aviso.
        battle = self._battle_with_events(
            [
                BattleEvent(
                    kind="switch", timestamp_ms=1_000, slot="p2a", species="Pelipper", health="100/100", source_frame=10
                ),
                BattleEvent(kind="damage", timestamp_ms=2_000, slot="p2a", species="Pelipper", health="60/100", source_frame=12),
                BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Kingambit", health="177/177", source_frame=14),
                BattleEvent(
                    kind="switch", timestamp_ms=10_000, slot="p2a", species="Pelipper", health="60/100", source_frame=50
                ),
                BattleEvent(kind="damage", timestamp_ms=10_500, slot="p2a", species="Pelipper", health="30/100", source_frame=51),
            ]
        )

        issues = review_capture(battle)

        pelipper_switches = [
            event.health for event in battle.events if event.kind == "switch" and event.species == "Pelipper"
        ]
        self.assertEqual(pelipper_switches, ["100/100", "60/100"])
        self.assertEqual([issue for issue in issues if issue.frame in (10, 50)], [])

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

    def test_a_confirmed_faint_reentering_as_its_own_resolved_species_is_dropped(self) -> None:
        """COL-102, reapertura estructural del 26 sep, job real
        `10a7fba6fda04585`, partidas 1 y 5 (Ies, validación visual en ROG,
        confirmado además contra los frames del vídeo fuente): a
        diferencia de `test_an_unresolved_identity_that_faints_instantly_is_the_same_death`
        (identidad SIN resolver, ventana de 2 s desde la lectura de 0 PS),
        aquí el HUD nunca perdió el ícono -el nombre y el aviso "X
        fainted!" seguían perfectamente legibles un frame (500 ms) después
        del `faint` real, y el detector lo leyó como un `switch` que
        devolvía a esa misma especie YA RESUELTA ("Rillaboom") a su slot.
        Ninguna de las dos condiciones de la identidad sin resolver se
        cumplía, y la ventana de 2 s tampoco -el `faint` real llegó ~4 s
        después de la última lectura de 0 PS en ambas partidas reales-, así
        que se sigue además la última especie confirmada debilitada por
        slot, sin límite de tiempo, hasta que una especie distinta lo
        ocupe.

        Reproduce el timing real de la partida 1 (turno 7): daño a 0/207 en
        t=670500 ms, `faint` en t=674000 ms, reentrada fantasma de la misma
        especie sin HP propio en t=674499 ms (~500 ms después, tal como en
        la traza), y el reemplazo real (Blaziken) en t=689999 ms.

        (d) COL-102, corte de Roku sobre `afee177`: además de que la
        limpieza previa siga descartando el switch fantasma, se comprueba
        que el resultado ya limpio -sin la línea fantasma- no arrastra
        ningún hallazgo `entrada_a_cero` ni ningún "max/max" inventado al
        pasar por `build_replay_document`/`_with_known_health` y quedar
        serializado; y se repite el mismo timing real, ahora para la
        partida 5 (turno 5, Bonkers/Rillaboom): daño a 0/207 en
        t=3048000 ms, `faint` en t=3052000 ms, reentrada fantasma 500 ms
        después en t=3052500 ms y el reemplazo real en t=3075500 ms.

        Roku, corte sobre `ff9e53f` (defecto #1 de la revisión): la
        versión anterior de este bloque de partida 5 usaba `p2a`, "0/207"
        y "Farigiraf" -ninguno de los tres coincide con la traza/vídeo
        reales de esa partida (`p2b`, Rillaboom/"Bonkers" a "0/100", y el
        reemplazo real es **Golisopod**, no Farigiraf). Esa diferencia no
        probaba que el fix fallara en el vídeo; probaba que la cobertura
        afirmada no existía. Corregido a la secuencia real, con
        `source_battle_index=4` (el ordinal real de `replay-005.json` en
        el job `10a7fba6fda04585`; la partida 1 de arriba usa
        `source_battle_index=0`, el de `replay-001.json`) y comprobando el
        `.log` ya serializado -no sólo `second.events` en memoria- igual
        que se hace arriba para la partida 1.
        """

        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=670_500, slot="p1a", species="Rillaboom", health="0/207"),
            BattleEvent(kind="faint", timestamp_ms=674_000, slot="p1a", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=674_499, slot="p1a", species="Rillaboom", health=None),
            BattleEvent(kind="switch", timestamp_ms=689_999, slot="p1a", species="Blaziken", health="82/156"),
        ]

        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()
        accumulator._drop_redundant_reswitches({})

        self.assertEqual(
            [(event.kind, event.species, event.health) for event in accumulator.events],
            [
                ("damage", "Rillaboom", "0/207"),
                ("faint", "Rillaboom", None),
                ("switch", "Blaziken", "82/156"),
            ],
        )

        battle = self.capture()
        document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
                p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
                events=tuple(accumulator.events),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
                source_battle_index=0,
            )
        )

        # El fantasma ya se descartó antes de serializar: ningún switch de
        # Rillaboom sobrevive, y nada estampa "207/207" -el máximo que
        # antes se fabricaba sobre esa reentrada.
        self.assertNotIn("|switch|p1a: Rillaboom", document.log)
        self.assertNotIn("207/207", document.log)
        self.assertIn("|-damage|p1a: Rillaboom|0/207", document.log)
        # El reemplazo real (Blaziken) sobrevive intacto, sin incidencia.
        self.assertIn("|switch|p1a: Blaziken|Blaziken, L50|82/156", document.log)
        findings = reconcile._state_findings(document.log.splitlines())
        self.assertEqual(
            [item for item in findings if item.category in {"entrada_a_cero", "reentrada_debilitado"}], []
        )
        self.assertEqual(
            [
                i for i in document.issues
                if i["severity"] == "blocking" and "entra sin ninguna lectura de HP" in i["message"]
            ],
            [],
        )

        # Partida 5 (turno 5, Bonkers/Rillaboom): secuencia real -p2b,
        # Rillaboom a 0/100 (no 0/207), reemplazo real Golisopod (no
        # Farigiraf)- con el mismo patrón y mismo resultado.
        second = CaptureAccumulator(CaptureSeed())
        second.events = [
            BattleEvent(kind="damage", timestamp_ms=3_048_000, slot="p2b", species="Rillaboom", health="0/100"),
            BattleEvent(kind="faint", timestamp_ms=3_052_000, slot="p2b", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=3_052_500, slot="p2b", species="Rillaboom", health=None),
            # HP del reemplazo sin confirmar contra el vídeo -sólo la
            # especie, el slot y el timing son los hechos reales de esta
            # ronda; el número es un valor de prueba para ejercitar el
            # pipeline, no una afirmación sobre la lectura real del HUD.
            BattleEvent(kind="switch", timestamp_ms=3_075_500, slot="p2b", species="Golisopod", health="100/182"),
        ]

        second._drop_ghost_reentries()
        second._reconcile_zero_hp()
        second._drop_redundant_reswitches({})

        self.assertEqual(
            [(event.kind, event.species, event.health) for event in second.events],
            [
                ("damage", "Rillaboom", "0/100"),
                ("faint", "Rillaboom", None),
                ("switch", "Golisopod", "100/182"),
            ],
        )

        second_document = build_replay_document(
            CapturedBattle(
                p1=BattleSide("IesYo", ("Sableye",), ("Sableye",)),
                p2=BattleSide("Rival", ("Rillaboom", "Golisopod"), ("Rillaboom", "Golisopod")),
                events=tuple(second.events),
                winner=battle.winner,
                started_at=battle.started_at,
                format=battle.format,
                source_mode=battle.source_mode,
                source_battle_index=4,
            )
        )

        self.assertEqual(second_document.source_battle_index, 4)
        # Mismas comprobaciones que arriba, ahora sobre el .log serializado
        # de la partida 5 -no sólo sobre `second.events` en memoria.
        self.assertNotIn("|switch|p2b: Rillaboom", second_document.log)
        self.assertNotIn("100/100", second_document.log)
        self.assertIn("|-damage|p2b: Rillaboom|0/100", second_document.log)
        self.assertIn("|switch|p2b: Golisopod|Golisopod, L50|100/182", second_document.log)
        second_findings = reconcile._state_findings(second_document.log.splitlines())
        self.assertEqual(
            [item for item in second_findings if item.category in {"entrada_a_cero", "reentrada_debilitado"}], []
        )
        self.assertEqual(
            [
                i for i in second_document.issues
                if i["severity"] == "blocking" and "entra sin ninguna lectura de HP" in i["message"]
            ],
            [],
        )

    def test_a_fainted_identity_that_returns_after_another_occupant_is_flagged_blocking(self) -> None:
        """Roku, quinta vuelta de COL-102, corte sobre `32da465`: aceptó no
        bloquear una identidad sin historial, pero encontró un hueco real
        distinto -el recuerdo de "quién se debilitó en este slot" se
        borraba en cuanto otro Pokémon entraba de por medio, así que el
        propio debilitado podía "regresar" después sin que nada lo
        atrapara. Contraejemplo exacto de Roku: `faint` confirmado por
        texto SIN ningún `damage 0/x` numérico que lo respalde (así que
        `entrada_a_cero`/`_reconcile_zero_hp` no tienen nada que anclar),
        un ocupante real distinto (Blaziken, con su propia vida) y el
        propio debilitado "volviendo" sin su propia lectura de HP -exactamente
        el patrón que `_drop_ghost_reentries` no cubre porque hubo un
        ocupante real de por medio, no la misma animación.
        """

        battle = CapturedBattle(
            p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
            p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
            events=(
                BattleEvent(
                    kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom",
                    health="207/207", source_frame=10,
                ),
                BattleEvent(
                    kind="damage", timestamp_ms=2_000, slot="p1a", species="Rillaboom",
                    health="40/207", source_frame=11,
                ),
                # Faint confirmado por texto, sin ningún evento 0/x numérico.
                BattleEvent(kind="faint", timestamp_ms=3_000, slot="p1a", species="Rillaboom", source_frame=12),
                BattleEvent(
                    kind="switch", timestamp_ms=4_000, slot="p1a", species="Blaziken",
                    health="156/156", source_frame=13,
                ),
                BattleEvent(
                    kind="switch", timestamp_ms=5_000, slot="p1a", species="Rillaboom",
                    health=None, source_frame=14,
                ),
            ),
            winner="p1",
        )

        issues = _revived_identity_issues(battle)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "blocking")
        self.assertEqual(issues[0].frame, 14)
        self.assertIn("Rillaboom", issues[0].message)
        self.assertIn("vuelve a entrar", issues[0].message)
        # Llega a la vía servida a Teams: `review_capture` alimenta
        # `ReplayDocument.issues`/`hasBlockingIssues` (lib/showdown-replay.ts).
        self.assertIn(issues[0], review_capture(battle))

    def test_a_first_time_entrant_still_raises_no_revived_identity_issue(self) -> None:
        # No repone el bloqueo general de HP que Ies revirtió en `32da465`:
        # una identidad SIN historial de vida en absoluto -los dos líderes
        # con los que arranca el combate- nunca pasa por `fainted_since`
        # (no tiene ningún `faint` propio que la marque), así que sigue
        # sin incidencia.
        battle = CapturedBattle(
            p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
            p2=BattleSide("Rival", ("Sableye", "Pelipper"), ("Sableye", "Pelipper")),
            events=(
                BattleEvent(kind="switch", timestamp_ms=0, slot="p1a", species="Rillaboom", health=None),
                BattleEvent(kind="switch", timestamp_ms=0, slot="p2a", species="Sableye", health=None),
                BattleEvent(kind="turn", timestamp_ms=500, turn=1),
            ),
            winner="p1",
        )

        self.assertEqual(_revived_identity_issues(battle), ())

    def test_a_live_actor_that_leaves_and_returns_without_fainting_is_not_flagged(self) -> None:
        # Un actor vivo que sale del campo (otro Pokémon ocupa su slot) y
        # vuelve, sin ningún `faint` de por medio, no es el patrón que
        # este invariante vigila: no hay falso positivo.
        battle = CapturedBattle(
            p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
            p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
            events=(
                BattleEvent(kind="switch", timestamp_ms=1_000, slot="p1a", species="Rillaboom", health="207/207"),
                BattleEvent(kind="damage", timestamp_ms=2_000, slot="p1a", species="Rillaboom", health="120/207"),
                BattleEvent(kind="switch", timestamp_ms=3_000, slot="p1a", species="Blaziken", health="156/156"),
                BattleEvent(kind="switch", timestamp_ms=4_000, slot="p1a", species="Rillaboom", health="120/207"),
            ),
            winner="p1",
        )

        self.assertEqual(_revived_identity_issues(battle), ())

    def test_revived_identity_issue_is_silent_on_the_real_p1_and_p5_rillaboom_replacements(self) -> None:
        """Job real `10a7fba6fda04585`, partidas 1 (turno 7) y 5 (turno 5):
        el switch fantasma de Rillaboom ya se descarta antes de serializar
        (`_drop_ghost_reentries`), así que el reemplazo real (Blaziken,
        Golisopod) nunca convive con un Rillaboom "revivido" en
        `battle.events` -este invariante nuevo no debe encontrar nada acá.
        Mismo timing real que ya cubre
        `test_a_confirmed_faint_reentering_as_its_own_resolved_species_is_dropped`
        (commits `afee177`/`32da465`); esto sólo comprueba, aparte, que el
        invariante nuevo no dispara sobre ese resultado ya limpio.
        """

        accumulator = CaptureAccumulator(CaptureSeed())
        accumulator.events = [
            BattleEvent(kind="damage", timestamp_ms=670_500, slot="p1a", species="Rillaboom", health="0/207"),
            BattleEvent(kind="faint", timestamp_ms=674_000, slot="p1a", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=674_499, slot="p1a", species="Rillaboom", health=None),
            BattleEvent(kind="switch", timestamp_ms=689_999, slot="p1a", species="Blaziken", health="82/156"),
        ]
        accumulator._drop_ghost_reentries()
        accumulator._reconcile_zero_hp()
        accumulator._drop_redundant_reswitches({})

        battle = CapturedBattle(
            p1=BattleSide("IesYo", ("Rillaboom", "Blaziken"), ("Rillaboom", "Blaziken")),
            p2=BattleSide("Rival", ("Sableye",), ("Sableye",)),
            events=tuple(accumulator.events),
            winner="p1",
        )
        self.assertEqual(_revived_identity_issues(battle), ())

        second = CaptureAccumulator(CaptureSeed())
        second.events = [
            BattleEvent(kind="damage", timestamp_ms=3_048_000, slot="p2b", species="Rillaboom", health="0/100"),
            BattleEvent(kind="faint", timestamp_ms=3_052_000, slot="p2b", species="Rillaboom"),
            BattleEvent(kind="switch", timestamp_ms=3_052_500, slot="p2b", species="Rillaboom", health=None),
            BattleEvent(kind="switch", timestamp_ms=3_075_500, slot="p2b", species="Golisopod", health="100/182"),
        ]
        second._drop_ghost_reentries()
        second._reconcile_zero_hp()
        second._drop_redundant_reswitches({})

        second_battle = CapturedBattle(
            p1=BattleSide("IesYo", ("Sableye",), ("Sableye",)),
            p2=BattleSide("Rival", ("Rillaboom", "Golisopod"), ("Rillaboom", "Golisopod")),
            events=tuple(second.events),
            winner="p1",
        )
        self.assertEqual(_revived_identity_issues(second_battle), ())

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

    def test_captured_battle_keeps_the_detectors_real_battle_index_across_a_discard(
        self,
    ) -> None:
        # COL-102, bloqueante de Roku del 26 sep: el índice persistido tiene
        # que ser el del detector -el mismo que ya se escribe en la traza-,
        # no la posición en `captures`. Aquí la primera "batalla" (una
        # notificación falsa) se descarta pero sí avanza el contador del
        # detector; la batalla real que seis frames después sí se cierra
        # tiene que salir con `source_battle_index=1`, no `0`.
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

        class CountingDetector:
            def __init__(self) -> None:
                self.current_battle_index = 0

            def detect(self, frame: FramePacket) -> FrameDetections:
                return detections[frame.index]

            def reset_battle_state(self) -> None:
                self.current_battle_index += 1

        captures = ReplayCapturePipeline(
            frames,
            CountingDetector(),
            CaptureSeed(p1_team=("Kleavor",)),
        ).capture(max_battles=0)

        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].source_battle_index, 1)

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

    def test_state_findings_flags_a_reentry_into_a_just_fainted_slot(self) -> None:
        """COL-102, reapertura estructural del 26 sep, job real
        `10a7fba6fda04585`: `col102-r5` devolvía `issues: []` para
        replay-001.json y replay-005.json pese a que ambos tienen
        exactamente este patrón -`|switch|...|0/max` de la misma especie
        justo tras su propio `|faint|`. `_state_findings` vivía por slot y
        se borraba en el propio `switch`/`faint`, y la rama de entrada
        nunca miraba la vida codificada en su propia línea contra ese
        estado; ahora sigue la última especie confirmada debilitada por
        slot y compara contra ella en la siguiente entrada. Reproduce
        líneas literales de replay-001.log, turno 7.
        """

        lines = [
            "|move|p2a: Altaria|Ice Beam|p1a: Rillaboom",
            "|-damage|p1a: Rillaboom|0/207",
            "|-message|It's super effective on Rillaboom!",
            "|faint|p1a: Rillaboom",
            "|switch|p1a: Rillaboom|Rillaboom, L50|0/207",
            "|switch|p1a: Blaziken|Blaziken-Mega, L50|82/156",
            "|turn|8",
        ]

        findings = reconcile._state_findings(lines)

        matching = [item for item in findings if item.category == "reentrada_debilitado"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].line, 4)
        self.assertIn("p1a", matching[0].detail)
        self.assertIn("Rillaboom", matching[0].detail)

    def test_state_findings_does_not_flag_a_legitimate_replacement(self) -> None:
        """Contraejemplo: un reemplazo legítimo (especie distinta) tras un
        `faint` no debe generar `reentrada_debilitado` -sólo la reentrada
        de la MISMA especie que se acaba de debilitar en ese slot.
        """

        lines = [
            "|-damage|p1a: Rillaboom|0/207",
            "|faint|p1a: Rillaboom",
            "|switch|p1a: Blaziken|Blaziken-Mega, L50|82/156",
            "|turn|8",
        ]

        findings = reconcile._state_findings(lines)

        self.assertEqual([item for item in findings if item.category == "reentrada_debilitado"], [])


if __name__ == "__main__":
    unittest.main()
