from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkmn_vgc.champions_jobs import ChampionsJobManager, _default_processor
from pkmn_vgc.champions_replay.detector import DetectionError
from pkmn_vgc.champions_replay.ledger_pipeline import (
    LEDGER_VERSION, capture_ocr_trace, documents_from_trace,
)
from pkmn_vgc.champions_replay.models import FrameDetections
from pkmn_vgc.champions_replay.pipeline import CaptureIncompleteError
from pkmn_vgc.champions_replay.prototype.ledger_replay import build_replay, load_trace_context
from pkmn_vgc.champions_replay.sources import FramePacket


P1 = ["Blaziken", "Indeedee-F", "Gardevoir", "Kingambit", "Rillaboom", "Basculegion"]
P2 = ["Garchomp", "Sneasler", "Charizard", "Whimsicott", "Sylveon", "Pelipper"]
CONTEXT = {"format": "gen9championsvgc2026regmc", "teams": {"p1": P1}, "players": {"p1": "Roku"}}


def trace_battle(index=0, *, broken_hp=False, missing_result=False):
    """Observaciones mínimas; los dos autómatas reales hacen la reconstrucción."""
    rows = []
    for number in range(1, 6):
        n = index * 10 + number
        rows.append({
            "frame": n, "timestamp_ms": n * 500, "battle_index": index,
            "resolved_aliases": {"p1": {}, "p2": {}}, "resolved_identities": {}, "ocr": [],
            "detections": {"players": {"p1": "Roku", "p2": "Benji"}, "events": [],
                           "teams": {"p1": P1, "p2": P2}, "team_preview": number < 3,
                           "battle_started": number in (3, 4)},
        })

    def event(row, kind, **values):
        row["detections"]["events"].append({
            "kind": kind, "timestamp_ms": row["timestamp_ms"],
            "source_frame": row["frame"] - 1, "confidence": 1, **values,
        })

    for slot, species, hp, x, y in [
        ("p1a", "Blaziken", "156/156", .14, .92), ("p1b", "Indeedee-F", "177/177", .34, .92),
        ("p2a", "Garchomp", "100/100", .70, .12), ("p2b", "Sneasler", "100/100", .92, .12),
    ]:
        event(rows[2], "switch", slot=slot, species=species, health=hp)
        rows[2]["ocr"].append({"text": hp if slot.startswith("p1") else "100%",
                               "confidence": .999, "left": x, "right": x + .06,
                               "top": y, "bottom": y + .04})
    event(rows[3], "turn", turn=1)
    event(rows[3], "move", slot="p1a", species="Blaziken", move="Detect", target_slot="p1a")
    if broken_hp:
        event(rows[3], "damage", slot="p2a", species="Garchomp", health="1/100")
    event(rows[4], "message", value="The battle has ended due to a forfeit.")
    rows[4]["detections"].update(battle_complete=True, winner="p1")
    if not missing_result:
        rows[4]["ocr"].append({"text": "You defeated Benji!", "confidence": .999,
                               "left": .2, "right": .8, "top": .7, "bottom": .8})
    return rows


def write_trace(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RecordedDetector:
    def __init__(self, rows, path):
        self.rows = rows
        self.path = path
        self.current_battle_index = 0
        self.flushes = []
        self.closed = False

    def detect(self, frame):
        row = copy.deepcopy(self.rows[frame.index])
        row["battle_index"] = self.current_battle_index
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
        return FrameDetections.from_mapping(row["detections"], timestamp_ms=row["timestamp_ms"])

    def flush_pending(self):
        self.flushes.append(self.current_battle_index)

    def reset_battle_state(self):
        self.current_battle_index += 1

    def close(self):
        self.closed = True


class LedgerPipelineTests(unittest.TestCase):
    def job(self):
        return {"id": "fixture", "created_at": "2026-09-28T12:00:00+00:00", "context": CONTEXT}

    def test_reports_both_automata_failures_and_keeps_original_battle_indices(self):
        rows = (trace_battle(0, broken_hp=True) + trace_battle(1) +
                trace_battle(2, missing_result=True) + trace_battle(3))
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            write_trace(out / "ocr.trace.jsonl", rows)
            warnings = []
            documents = documents_from_trace(out / "ocr.trace.jsonl", self.job(), out, on_warning=warnings.append)
            self.assertEqual([d.source_battle_index for d in documents], [1, 3])
            self.assertTrue(all(d.reconciliation_version == LEDGER_VERSION for d in documents))
            self.assertTrue(all(d.log.endswith("|win|Roku") for d in documents))
            report = json.loads((out / "ledger-report.json").read_text())
            self.assertEqual(report["status"], "partial")
            self.assertEqual([r["replay_number"] for r in report["battles"]], [None, 1, None, 2])
            self.assertTrue(report["battles"][0]["issues"])
            self.assertIn("ganador", report["battles"][2]["error"])
            self.assertEqual(len(warnings), 2)
            self.assertEqual(len(list(out.glob("ledger-battle-*.json"))), 4)
            self.assertEqual(len(list(out.glob("ledger-battle-*.md"))), 4)

    def test_no_exportable_battles_keeps_report_and_raw_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            write_trace(out / "ocr.trace.jsonl", trace_battle(broken_hp=True))
            with self.assertRaisesRegex(CaptureIncompleteError, "diagnóstico"):
                documents_from_trace(out / "ocr.trace.jsonl", self.job(), out)
            report = json.loads((out / "ledger-report.json").read_text())
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["replay_count"], 0)
            self.assertTrue((out / "ledger-battle-001.json").is_file())
            self.assertTrue((out / "ocr.trace.jsonl").is_file())

    def test_trace_capture_counts_closures_without_legacy_finalization_and_honors_limit(self):
        rows = trace_battle(0, broken_hp=True) + trace_battle(1)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            detector = RecordedDetector(rows, out / "ocr.trace.jsonl")
            source = [FramePacket(index=i, timestamp_ms=i * 500, image=b"") for i in range(len(rows))]
            progress = []
            with patch("pkmn_vgc.champions_replay.pipeline.CaptureAccumulator.finalize", side_effect=AssertionError):
                capture_ocr_trace(source, detector, max_battles=1, on_progress=progress.append)
            self.assertEqual(len((out / "ocr.trace.jsonl").read_text().splitlines()), 5)
            self.assertEqual(detector.flushes, [0])
            self.assertTrue(detector.closed)
            self.assertEqual(progress[-1].battles_detected, 1)

    def test_capture_stops_after_three_ocr_errors_and_closes_resources(self):
        detector = MagicMock()
        detector.detect.side_effect = DetectionError("OCR falló")
        source = [FramePacket(index=i, timestamp_ms=i * 500, image=b"") for i in range(5)]
        warnings = []
        with self.assertRaisesRegex(DetectionError, "3 frames"):
            capture_ocr_trace(source, detector, on_warning=warnings.append)
        self.assertEqual(detector.detect.call_count, 3)
        self.assertEqual(len(warnings), 3)
        detector.close.assert_called_once()

    def test_job_reanalysis_and_downloaded_diagnostics_preserve_ledger_and_legacy_artifacts(self):
        rows = trace_battle(0) + trace_battle(1)
        source = MagicMock()
        source.__iter__.side_effect = lambda: iter(
            [FramePacket(index=i, timestamp_ms=i * 500, image=b"") for i in range(len(rows))]
        )
        source.estimated_frame_count.return_value = len(rows)
        detectors = []

        def detector_factory(**kwargs):
            detector = RecordedDetector(rows, kwargs["trace_path"])
            detectors.append(detector)
            return detector

        with tempfile.TemporaryDirectory() as directory, \
                patch("pkmn_vgc.champions_jobs.VideoFrameSource", return_value=source), \
                patch("pkmn_vgc.champions_jobs.ChampionsOcrDetector", side_effect=detector_factory), \
                patch("pkmn_vgc.champions_jobs.ReplayCapturePipeline", side_effect=AssertionError("legacy")):
            manager = ChampionsJobManager(Path(directory))
            try:
                self.assertIs(manager.processor, _default_processor)
                job = manager.create_job(filename="video.mp4", size_bytes=5, team_version_id="team", context=CONTEXT)
                with patch.object(manager, "_enqueue"):
                    manager.append_chunk(job["id"], offset=0, data=b"video")
                manager._process_job(job["id"])
                self.assertEqual(manager.get_job(job["id"])["status"], "ready")
                document = manager.replay_document(job["id"], 2)
                self.assertEqual(document["source_battle_index"], 1)
                self.assertEqual(document["reconciliation_version"], LEDGER_VERSION)
                self.assertEqual(document["ledger_source"]["job_id"], job["id"])
                self.assertEqual(detectors[0].flushes[:2], [0, 1])
                self.assertTrue(detectors[0].closed)
                out = Path(directory) / job["id"] / "output"
                (out / "col102-reference.log").write_text("legacy output", encoding="utf-8")
                with patch.object(manager, "_enqueue"):
                    manager.retry_job(job["id"])
                manager._process_job(job["id"])
                archive_path = Path(directory) / f"champions-diagnostics-{job['id']}.zip"
                archive_path.write_bytes(manager.diagnostics_archive(job["id"]))
                with zipfile.ZipFile(archive_path) as archive:
                    names = archive.namelist()
                    self.assertIn("job.json", names)
                    for name in ("ocr.trace.jsonl", "ledger-report.json", "ledger-battle-001.json",
                                 "ledger-battle-001.md", "replay-001.json", "replay-001.log", "replay-001.html"):
                        self.assertIn(f"output/{name}", names)
                        self.assertTrue(any(p.startswith("output/history/") and p.endswith("/" + name) for p in names), name)
                    self.assertTrue(any(p.endswith("/col102-reference.log") for p in names))
                    self.assertNotIn("source.mp4", names)
                    battle = json.loads(archive.read("output/ledger-battle-001.json"))
                rebuilt = build_replay(battle, load_trace_context(archive_path, battle))
                self.assertEqual(rebuilt["log"], manager.replay_document(job["id"], 1)["log"])
                # Una reanálisis bloqueada conserva también su diagnóstico;
                # ningún replay de las corridas anteriores se sirve como nuevo.
                rows[:] = trace_battle(broken_hp=True)
                with patch.object(manager, "_enqueue"):
                    manager.retry_job(job["id"])
                manager._process_job(job["id"])
                self.assertEqual(manager.get_job(job["id"])["status"], "error")
                with self.assertRaisesRegex(ValueError, "no están listos"):
                    manager.replay_document(job["id"], 1)
                with zipfile.ZipFile(io.BytesIO(manager.diagnostics_archive(job["id"]))) as archive:
                    report = json.loads(archive.read("output/ledger-report.json"))
                    self.assertEqual(report["status"], "blocked")
                    self.assertIn("output/ledger-battle-001.md", archive.namelist())
                    self.assertNotIn("output/replay.json", archive.namelist())
                    self.assertNotIn("output/replay-001.json", archive.namelist())
            finally:
                manager.close(wait=True)


if __name__ == "__main__":
    unittest.main()
