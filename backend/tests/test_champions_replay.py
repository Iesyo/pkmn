from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pkmn_vgc.champions_replay.detector import DetectorContext, OllamaVisionDetector, _extract_json
from pkmn_vgc.champions_replay.models import BattleEvent, BattleSide, CapturedBattle, FrameDetections
from pkmn_vgc.champions_replay.pipeline import (
    CaptureAccumulator,
    CaptureSeed,
    ReplayCapturePipeline,
    review_capture,
)
from pkmn_vgc.champions_replay.showdown import (
    build_replay_document,
    render_replay_html,
    write_replay_artifacts,
)
from pkmn_vgc.champions_replay.sources import FramePacket, LiveFrameSource, VideoFrameSource, iter_mjpeg

DATA = Path(__file__).with_name("data")


class ChampionsReplayTests(unittest.TestCase):
    def capture(self) -> CapturedBattle:
        return CapturedBattle.from_mapping(
            json.loads((DATA / "champions_capture.json").read_text(encoding="utf-8"))
        )

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

    def test_rejects_remote_ollama_endpoints(self) -> None:
        with self.assertRaisesRegex(ValueError, "localmente"):
            OllamaVisionDetector(endpoint="https://example.com")

    def test_calls_local_ollama_with_image_and_structured_output(self) -> None:
        received: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - nombre definido por BaseHTTPRequestHandler
                size = int(self.headers.get("content-length", "0"))
                received.update(json.loads(self.rfile.read(size)))
                body = json.dumps({
                    "response": json.dumps({
                        "battle_started": True,
                        "events": [{"kind": "turn", "turn": 1, "confidence": 0.95}],
                    })
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

        self.assertEqual(received["model"], "qwen3-vl:4b")
        self.assertEqual(received["format"], "json")
        self.assertEqual(received["think"], False)
        self.assertTrue(received["images"])
        self.assertEqual(detections.events[0].timestamp_ms, 700)


if __name__ == "__main__":
    unittest.main()
