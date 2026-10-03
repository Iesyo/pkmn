from __future__ import annotations

import copy
import io
import json
import os
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

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_F621"), "Requiere diagnóstico f621")
    def test_empty_detector_names_do_not_block_corroborated_ocr_and_observed_participants(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_F621"])
        with tempfile.TemporaryDirectory() as directory, zipfile.ZipFile(path) as archive:
            out = Path(directory)
            job = json.loads(archive.read("job.json"))
            trace = archive.read("output/ocr.trace.jsonl")
            (out / "ocr.trace.jsonl").write_bytes(trace)
            documents = documents_from_trace(out / "ocr.trace.jsonl", job, out / "output")
            self.assertEqual(len(documents), 1)
            document = documents[0]
            self.assertEqual((document.p1, document.p2), ("Roku", "coacoaboy"))
            self.assertEqual(document.log.splitlines()[-1], "|win|Roku")
            self.assertEqual(sum(line.startswith("|poke|p2|") for line in document.log.splitlines()), 4)
            self.assertIn("|switch|p1a: Kingambit|Kingambit, L50|177/177", document.log)
            report = json.loads((out / "output/ledger-report.json").read_text())
            self.assertEqual((report["status"], report["replay_count"], report["blocked_battles"]), ("ready", 1, 0))
            self.assertFalse(report["battles"][0]["issues"])
            self.assertEqual((out / "ocr.trace.jsonl").read_bytes(), trace)

    def transition_rows(self, *, message=True):
        stale = trace_battle(0)[-1]
        stale["battle_index"] = 1
        stale.update(frame=6, timestamp_ms=3000)
        stale["ocr"][0]["text"] = "You lost to Benji!"
        stale["detections"]["winner"] = "p2"
        stale["detections"]["events"] = ([{
            "kind": "message", "value": "You lost to Benji!", "confidence": 1,
            "timestamp_ms": stale["timestamp_ms"], "source_frame": stale["frame"] - 1,
        }] if message else [])
        stale["resolved_aliases"]["p1"] = {"old nickname": "Gardevoir"}
        return [stale, *trace_battle(1)]

    def test_previous_result_is_excluded_only_after_confirmed_new_preview_and_start(self):
        for message in (True, False):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                rows = self.transition_rows(message=message)
                write_trace(out / "ocr.trace.jsonl", rows)
                original = (out / "ocr.trace.jsonl").read_bytes()
                documents = documents_from_trace(out / "ocr.trace.jsonl", self.job(), out)
                self.assertEqual(len(documents), 1)
                self.assertEqual(documents[0].source_battle_index, 1)
                self.assertTrue(documents[0].log.endswith("|win|Roku"))
                self.assertNotIn("You lost to Benji!", documents[0].log)
                ledger = json.loads((out / "ledger-battle-002.json").read_text())
                self.assertEqual(ledger["first_frame"], 11)
                self.assertEqual(ledger["transition_prefix"]["result_evidence"][0]["text"], "You lost to Benji!")
                self.assertEqual(ledger["transition_prefix"]["preview_frames"], [11, 12])
                self.assertEqual(ledger["transition_prefix"]["battle_start_frame"], 13)
                self.assertIn("Transición de la partida anterior excluida", (out / "ledger-battle-002.md").read_text())
                from pkmn_vgc.champions_replay.prototype.ledger_replay import build_trace_context
                context = build_trace_context(self.job(), rows, ledger)
                self.assertNotIn("old nickname", context.aliases["p1"])
                self.assertEqual((out / "ocr.trace.jsonl").read_bytes(), original)

    def test_uncorroborated_transition_does_not_hide_a_result_before_more_events(self):
        for defect in ("single_preview", "no_start", "prior_event", "prior_ocr_action"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                rows = self.transition_rows()
                if defect == "single_preview":
                    rows[1]["detections"]["team_preview"] = False
                elif defect == "no_start":
                    for row in rows:
                        row["detections"]["battle_started"] = False
                elif defect == "prior_event":
                    rows[0]["detections"]["events"].insert(0, {
                        "kind": "turn", "turn": 1, "timestamp_ms": 2000, "confidence": 1,
                    })
                else:
                    rows[0]["ocr"].append({"text": "Blaziken used Protect!", "confidence": 1, "top": .7})
                write_trace(out / "ocr.trace.jsonl", rows)
                with self.assertRaises(CaptureIncompleteError):
                    documents_from_trace(out / "ocr.trace.jsonl", self.job(), out)
                ledger = json.loads((out / "ledger-battle-002.json").read_text())
                self.assertNotIn("transition_prefix", ledger)
                self.assertTrue(any(e["kind"] == "battle_end" and e["frame"] == 6 for e in ledger["events"]))

    def test_truncated_new_battle_cannot_reuse_previous_result_even_against_same_opponent(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            write_trace(out / "ocr.trace.jsonl", self.transition_rows()[:-1])
            with self.assertRaises(CaptureIncompleteError):
                documents_from_trace(out / "ocr.trace.jsonl", self.job(), out)
            ledger = json.loads((out / "ledger-battle-002.json").read_text())
            self.assertIn("transition_prefix", ledger)
            self.assertFalse(any(e["kind"] == "battle_end" for e in ledger["events"]))
            report = json.loads((out / "ledger-report.json").read_text())
            self.assertIn("cierre", report["battles"][0]["error"])

    @unittest.skipUnless(os.environ.get("CHAMPIONS_DIAGNOSTIC_BOUNDARY_137129"), "Requiere ZIP de reanálisis 137129")
    def test_137129_reanalysis_exports_two_battles_with_independent_results(self):
        path = Path(os.environ["CHAMPIONS_DIAGNOSTIC_BOUNDARY_137129"])
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            trace = archive.read("output/ocr.trace.jsonl")
            (out / "ocr.trace.jsonl").write_bytes(trace)
            job = json.loads(archive.read("job.json"))
            original = json.loads(archive.read("output/replay.json"))
            documents = documents_from_trace(out / "ocr.trace.jsonl", job, out)
            self.assertEqual([d.source_battle_index for d in documents], [0, 1])
            self.assertEqual(documents[0].log, original["log"])
            self.assertIn("|player|p2|LemonLime|", documents[1].log)
            self.assertTrue(documents[1].log.endswith("|-message|You defeated LemonLime!\n|win|Roku"))
            self.assertNotIn("eaSy", documents[1].log)
            second = json.loads((out / "ledger-battle-002.json").read_text())
            self.assertEqual(second["first_frame"], 1214)
            self.assertEqual(second["transition_prefix"]["preview_frames"], [1214, 1215])
            ends = [e for e in second["events"] if e["kind"] == "battle_end"]
            self.assertEqual([(e["frame"], e["turn"]) for e in ends], [(1825, 4)])
            report = json.loads((out / "ledger-report.json").read_text())
            self.assertEqual((report["status"], report["replay_count"], report["blocked_battles"]), ("ready", 2, 0))
            self.assertEqual((out / "ocr.trace.jsonl").read_bytes(), trace)

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
        rows = trace_battle(0) + self.transition_rows()
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
                    second = json.loads(archive.read("output/ledger-battle-002.json"))
                    self.assertIn("transition_prefix", second)
                rebuilt = build_replay(battle, load_trace_context(archive_path, battle))
                self.assertEqual(rebuilt["log"], manager.replay_document(job["id"], 1)["log"])
                rebuilt_second = build_replay(second, load_trace_context(archive_path, second))
                self.assertEqual(rebuilt_second["log"], manager.replay_document(job["id"], 2)["log"])
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
                self.assertTrue(manager.get_job(job["id"])["canRebuildAutomata"])
                ocr_runs = len(detectors)
                with patch.object(manager, "_enqueue"):
                    manager.rebuild_from_trace(job["id"])
                manager._process_job(job["id"])
                self.assertEqual(manager.get_job(job["id"])["status"], "error")
                self.assertEqual(len(detectors), ocr_runs)
            finally:
                manager.close(wait=True)

    def test_rebuild_from_saved_trace_without_video_or_ocr(self):
        rows = trace_battle(0)
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
                patch("pkmn_vgc.champions_jobs.VideoFrameSource", return_value=source) as video_source, \
                patch("pkmn_vgc.champions_jobs.ChampionsOcrDetector", side_effect=detector_factory):
            manager = ChampionsJobManager(Path(directory))
            try:
                job = manager.create_job(filename="video.mp4", size_bytes=5, team_version_id="team", context=CONTEXT)
                with patch.object(manager, "_enqueue"):
                    manager.append_chunk(job["id"], offset=0, data=b"video")
                manager._process_job(job["id"])
                original = manager.replay_document(job["id"], 1)["log"]
                self.assertTrue(manager.get_job(job["id"])["canRebuildAutomata"])
                manager.set_protected(job["id"], False)
                manager.compact_job(job["id"])
                self.assertFalse(manager.get_job(job["id"])["canRetry"])
                self.assertTrue(manager.get_job(job["id"])["canRebuildAutomata"])

                with patch.object(manager, "_enqueue"):
                    queued = manager.rebuild_from_trace(job["id"])
                self.assertEqual((queued["status"], queued["analysisMode"]), ("queued", "trace"))
                manager.close(wait=True)
                resumed = ChampionsJobManager(Path(directory))
                with patch.object(resumed, "_enqueue") as enqueue:
                    resumed.resume_pending()
                    enqueue.assert_called_once_with(job["id"])
                resumed._process_job(job["id"])
                self.assertEqual(resumed.get_job(job["id"])["status"], "ready")
                self.assertEqual(resumed.replay_document(job["id"], 1)["log"], original)
                self.assertEqual(video_source.call_count, 1)
                self.assertEqual(len(detectors), 1)
                with zipfile.ZipFile(io.BytesIO(resumed.diagnostics_archive(job["id"]))) as archive:
                    self.assertIn("output/ocr.trace.jsonl", archive.namelist())
                    self.assertTrue(any(name.startswith("output/history/") and
                                        name.endswith("/ocr.trace.jsonl") for name in archive.namelist()))

                (Path(directory) / job["id"] / "output" / "ledger-report.json").unlink()
                self.assertFalse(resumed.get_job(job["id"])["canRebuildAutomata"])
                with self.assertRaisesRegex(ValueError, "traza OCR completa"):
                    resumed.rebuild_from_trace(job["id"])
                self.assertEqual(resumed.replay_document(job["id"], 1)["log"], original)
                resumed.close(wait=True)
            finally:
                manager.close(wait=True)


if __name__ == "__main__":
    unittest.main()
