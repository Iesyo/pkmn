from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkmn_vgc.champions_jobs import ChampionsJobManager, _default_processor
from pkmn_vgc.champions_replay.models import ReplayDocument
from pkmn_vgc.champions_replay.pipeline import CaptureProgress


def fake_processor(
    _video_path: Path,
    _context: object,
    _output_directory: Path,
    _sample_fps: float,
    _max_battles: int,
    _ocr_workers: int,
    on_progress: object,
    _on_warning: object,
) -> tuple[ReplayDocument, ...]:
    on_progress(  # type: ignore[operator]
        CaptureProgress(
            processed_frames=10,
            total_frames=20,
            timestamp_ms=5_000,
            elapsed_seconds=1.0,
            events_detected=4,
            skipped_frames=0,
            battles_detected=1,
        )
    )
    return (
        ReplayDocument("|turn|1\n|win|Player", "", 1, "Player", "Rival A", "champions"),
        ReplayDocument("|turn|1\n|win|Rival B", "", 2, "Player", "Rival B", "champions"),
    )


class ChampionsJobTests(unittest.TestCase):
    @patch("pkmn_vgc.champions_jobs.ReplayCapturePipeline")
    @patch("pkmn_vgc.champions_jobs.ChampionsOcrDetector")
    @patch("pkmn_vgc.champions_jobs.VideoFrameSource")
    def test_web_processor_uses_deterministic_ocr_without_ollama(
        self,
        source_type: MagicMock,
        detector_type: MagicMock,
        pipeline_type: MagicMock,
    ) -> None:
        source_type.return_value.estimated_frame_count.return_value = 0
        pipeline_type.return_value.capture.return_value = ()

        with tempfile.TemporaryDirectory() as directory:
            documents = _default_processor(
                Path(directory) / "video.mp4",
                {},
                Path(directory),
                2.0,
                0,
                2,
                MagicMock(),
                MagicMock(),
            )

        self.assertEqual(documents, ())
        self.assertNotIn("alias_resolver", detector_type.call_args.kwargs)

    def test_retries_atomic_metadata_replace_when_windows_temporarily_denies_access(self) -> None:
        attempts = 0
        real_replace = os.replace

        def flaky_replace(source: object, target: object) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(source, target)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            manager = ChampionsJobManager(Path(directory), processor=fake_processor)
            with patch("pkmn_vgc.champions_jobs.os.replace", side_effect=flaky_replace):
                job = manager.create_job(
                    filename="session.mp4",
                    size_bytes=5,
                    team_version_id="version-1",
                    context={},
                )

            job_directory = Path(directory) / job["id"]
            self.assertEqual(attempts, 3)
            self.assertTrue((job_directory / "job.json").is_file())
            self.assertEqual(tuple(job_directory.glob("job.*.tmp")), ())
            manager.close(wait=True)

    def test_uploads_in_resumable_chunks_and_persists_multiple_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ChampionsJobManager(Path(directory), processor=fake_processor)
            payload = b"0123456789"
            job = manager.create_job(
                filename="session.mp4",
                size_bytes=len(payload),
                team_version_id="version-1",
                context={"teams": {"p1": ["Kleavor"], "p2": []}},
                max_battles=0,
            )

            first = manager.append_chunk(job["id"], offset=0, data=payload[:4])
            repeated = manager.append_chunk(job["id"], offset=0, data=payload[:4])
            manager.append_chunk(job["id"], offset=4, data=payload[4:])

            self.assertEqual(first["uploadedBytes"], 4)
            self.assertEqual(repeated["uploadedBytes"], 4)
            deadline = time.monotonic() + 2
            completed = manager.get_job(job["id"])
            while completed["status"] not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(0.01)
                completed = manager.get_job(job["id"])

            self.assertEqual(completed["status"], "ready")
            self.assertEqual(completed["replayCount"], 2)
            self.assertEqual(completed["battlesDetected"], 2)
            self.assertEqual(completed["ocrWorkers"], 2)
            self.assertEqual(manager.replay_document(job["id"], 1)["p2"], "Rival A")
            self.assertEqual(manager.replay_document(job["id"], 2)["p2"], "Rival B")
            self.assertTrue((Path(directory) / job["id"] / "output" / "replay-001.html").is_file())
            self.assertTrue((Path(directory) / job["id"] / "output" / "replay-002.html").is_file())
            manager.close(wait=True)

            restored = ChampionsJobManager(Path(directory), processor=fake_processor)
            self.assertEqual(restored.get_job(job["id"])["replayCount"], 2)
            restored.close(wait=True)

    def test_rejects_out_of_order_or_oversized_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ChampionsJobManager(Path(directory), processor=fake_processor, max_upload_bytes=10)
            job = manager.create_job(
                filename="session.mkv",
                size_bytes=4,
                team_version_id="version-1",
                context={},
            )

            with self.assertRaisesRegex(ValueError, "Offset inválido"):
                manager.append_chunk(job["id"], offset=2, data=b"ab")
            with self.assertRaisesRegex(ValueError, "tamaño declarado"):
                manager.append_chunk(job["id"], offset=0, data=b"abcde")
            with self.assertRaisesRegex(ValueError, "límite local"):
                manager.create_job(
                    filename="large.mp4",
                    size_bytes=11,
                    team_version_id="version-1",
                    context={},
                )
            manager.close(wait=True)

    def test_resumes_an_interrupted_upload_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_manager = ChampionsJobManager(root, processor=fake_processor)
            job = first_manager.create_job(
                filename="session.webm",
                size_bytes=6,
                team_version_id="version-1",
                context={},
            )
            first_manager.append_chunk(job["id"], offset=0, data=b"abc")
            first_manager.close(wait=True)

            resumed_manager = ChampionsJobManager(root, processor=fake_processor)
            self.assertEqual(resumed_manager.get_job(job["id"])["uploadedBytes"], 3)
            resumed_manager.append_chunk(job["id"], offset=3, data=b"def")
            deadline = time.monotonic() + 2
            resumed = resumed_manager.get_job(job["id"])
            while resumed["status"] not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(0.01)
                resumed = resumed_manager.get_job(job["id"])

            self.assertEqual(resumed["status"], "ready")
            resumed_manager.close(wait=True)

    def test_retries_analysis_without_uploading_the_video_again(self) -> None:
        attempts = 0

        def flaky_processor(*args: object) -> tuple[ReplayDocument, ...]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("OCR temporal")
            return fake_processor(*args)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as directory:
            manager = ChampionsJobManager(Path(directory), processor=flaky_processor)
            job = manager.create_job(
                filename="mobile.mp4",
                size_bytes=5,
                team_version_id="version-1",
                context={},
            )
            manager.append_chunk(job["id"], offset=0, data=b"video")

            deadline = time.monotonic() + 2
            failed = manager.get_job(job["id"])
            while failed["status"] not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(0.01)
                failed = manager.get_job(job["id"])
            self.assertEqual(failed["status"], "error")

            retried = manager.retry_job(job["id"], ocr_workers=4)
            self.assertEqual(retried["status"], "queued")
            deadline = time.monotonic() + 2
            completed = manager.get_job(job["id"])
            while completed["status"] not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(0.01)
                completed = manager.get_job(job["id"])

            self.assertEqual(completed["status"], "ready")
            self.assertEqual(completed["uploadedBytes"], 5)
            self.assertEqual(completed["ocrWorkers"], 4)
            self.assertEqual(attempts, 2)
            manager.close(wait=True)

    def test_reanalyzes_a_ready_job_without_uploading_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ChampionsJobManager(Path(directory), processor=fake_processor)
            job = manager.create_job(
                filename="two-battles.mp4",
                size_bytes=5,
                team_version_id="version-1",
                context={},
            )
            manager.append_chunk(job["id"], offset=0, data=b"video")
            deadline = time.monotonic() + 2
            completed = manager.get_job(job["id"])
            while completed["status"] not in {"ready", "error"} and time.monotonic() < deadline:
                time.sleep(0.01)
                completed = manager.get_job(job["id"])
            self.assertEqual(completed["status"], "ready")

            queued = manager.retry_job(job["id"], ocr_workers=1)

            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["uploadedBytes"], 5)
            self.assertEqual(queued["ocrWorkers"], 1)
            manager.close(wait=True)


if __name__ == "__main__":
    unittest.main()
