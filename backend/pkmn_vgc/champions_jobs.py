from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .champions_replay.cli import _seed_from_context
from .champions_replay.models import ReplayDocument
from .champions_replay.ocr_detector import (
    ChampionsOcrDetector,
    OcrTraceDetector,
    load_champions_catalog,
)
from .champions_replay.pipeline import CaptureProgress, ReplayCapturePipeline
from .champions_replay.showdown import build_replay_document, write_replay_artifacts
from .champions_replay.sources import OcrTraceFrameSource, VideoFrameSource
from .champions_replay.team_preview import ChampionsTeamPreviewResolver


ALLOWED_VIDEO_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}
MAX_CHUNK_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(value: str) -> tuple[str, str]:
    filename = Path(value.strip()).name
    suffix = Path(filename).suffix.casefold()
    if not filename or suffix not in ALLOWED_VIDEO_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_VIDEO_SUFFIXES))
        raise ValueError(f"El vídeo debe usar una extensión compatible: {allowed}.")
    return filename, suffix


def _default_processor(
    video_path: Path,
    context: Mapping[str, Any],
    output_directory: Path,
    sample_fps: float,
    max_battles: int,
    on_progress: Callable[[CaptureProgress], None],
    on_warning: Callable[[str], None],
) -> tuple[ReplayDocument, ...]:
    seed, detector_context = _seed_from_context(context, "video")
    source = VideoFrameSource(path=video_path, sample_fps=sample_fps)
    trace_path = output_directory / "ocr.trace.jsonl"
    trace_path.unlink(missing_ok=True)
    catalog = load_champions_catalog()
    detector = ChampionsOcrDetector(
        context=detector_context,
        trace_path=trace_path,
        team_preview_resolver=ChampionsTeamPreviewResolver(catalog.species_types),
    )
    # Fase 1, el único recorrido del vídeo: el OCR y todo lo que necesita la
    # imagen (sprites del Team Preview, motes del HUD, dónde empieza y acaba
    # cada batalla) quedan en la traza. Lo que esta fase decide sobre la
    # marcha sólo guía esa lectura; el replay no sale de aquí.
    reported: set[str] = set()

    def report_video_warning(message: str) -> None:
        reported.add(message)
        on_warning(message)

    def report_replay_warning(message: str) -> None:
        if message not in reported:
            on_warning(message)

    ReplayCapturePipeline(source, detector, seed).capture(
        max_battles=max_battles,
        total_frames=source.estimated_frame_count(),
        on_progress=on_progress,
        on_warning=report_video_warning,
    )
    # Fase 2: el replay se decide con la traza ya completa, sabiendo desde el
    # primer frame de cada batalla lo que el vídeo sólo reveló más tarde (el
    # roster rival, los motes finales). Tarda segundos y no vuelve al vídeo.
    captures = ReplayCapturePipeline(
        OcrTraceFrameSource(path=trace_path),
        OcrTraceDetector.from_trace(trace_path, context=detector_context),
        seed,
    ).capture(max_battles=max_battles, on_warning=report_replay_warning)
    return tuple(build_replay_document(capture) for capture in captures)


Processor = Callable[
    [
        Path,
        Mapping[str, Any],
        Path,
        float,
        int,
        Callable[[CaptureProgress], None],
        Callable[[str], None],
    ],
    tuple[ReplayDocument, ...],
]


class ChampionsJobManager:
    """Cola local persistente para vídeos de Pokémon Champions."""

    def __init__(
        self,
        root: Path,
        *,
        processor: Processor = _default_processor,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.root = root
        self.processor = processor
        self.max_upload_bytes = max_upload_bytes
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="champions-video")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._submitted: set[str] = set()
        self._last_progress_save: dict[str, float] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_jobs()

    def _load_jobs(self) -> None:
        for metadata_path in self.root.glob("*/job.json"):
            try:
                value = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(value, dict) or value.get("id") != metadata_path.parent.name:
                continue
            value.pop("ocr_workers", None)
            value.setdefault("is_protected", True)
            part_path = metadata_path.parent / "upload.part"
            source_path = metadata_path.parent / str(value.get("source_path") or "")
            if part_path.is_file():
                value["uploaded_bytes"] = part_path.stat().st_size
            elif source_path.is_file():
                value["uploaded_bytes"] = int(value.get("size_bytes") or source_path.stat().st_size)
            self._jobs[value["id"]] = value

    def resume_pending(self) -> None:
        with self._lock:
            pending = [
                job_id
                for job_id, job in self._jobs.items()
                if job.get("status") in {"queued", "analyzing"}
                and self._source_path(job).is_file()
            ]
            for job_id in pending:
                job = self._jobs[job_id]
                job["status"] = "queued"
                job["stage"] = "Esperando turno"
                job["error"] = None
                self._save_locked(job)
        for job_id in pending:
            self._enqueue(job_id)

    def create_job(
        self,
        *,
        filename: str,
        size_bytes: int,
        team_version_id: str,
        context: Mapping[str, Any],
        sample_fps: float = 2.0,
        max_battles: int = 0,
    ) -> dict[str, Any]:
        safe_filename, suffix = _safe_filename(filename)
        if size_bytes < 1:
            raise ValueError("El vídeo está vacío.")
        if size_bytes > self.max_upload_bytes:
            limit_gib = self.max_upload_bytes / (1024**3)
            raise ValueError(f"El vídeo excede el límite local de {limit_gib:g} GiB.")
        if not team_version_id.strip():
            raise ValueError("Falta la versión del Team asociada al vídeo.")
        if not isinstance(context, Mapping):
            raise ValueError("El contexto Champions no es válido.")
        if not 0.25 <= sample_fps <= 10:
            raise ValueError("sample_fps debe estar entre 0.25 y 10.")
        if max_battles < 0:
            raise ValueError("max_battles no puede ser negativo; usa 0 para procesar todas.")

        job_id = uuid4().hex[:16]
        created_at = _now()
        job = {
            "id": job_id,
            "team_version_id": team_version_id.strip(),
            "filename": safe_filename,
            "suffix": suffix,
            "size_bytes": size_bytes,
            "uploaded_bytes": 0,
            "status": "uploading",
            "stage": "Recibiendo vídeo",
            "context": dict(context),
            "sample_fps": sample_fps,
            "max_battles": max_battles,
            "processed_frames": 0,
            "total_frames": None,
            "elapsed_seconds": 0.0,
            "eta_seconds": None,
            "events_detected": 0,
            "battles_detected": 0,
            "skipped_frames": 0,
            "warnings": [],
            "replay_files": [],
            "source_path": f"source{suffix}",
            "is_protected": True,
            "error": None,
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._job_directory(job_id).mkdir(parents=True)
            self._save_locked(job)
        return self._public(job)

    def append_chunk(self, job_id: str, *, offset: int, data: bytes) -> dict[str, Any]:
        if not data:
            raise ValueError("El fragmento recibido está vacío.")
        if len(data) > MAX_CHUNK_BYTES:
            raise ValueError(f"Cada fragmento puede medir como máximo {MAX_CHUNK_BYTES // (1024**2)} MiB.")

        should_enqueue = False
        with self._lock:
            job = self._require(job_id)
            if job["status"] != "uploading":
                if job["status"] in {"queued", "analyzing", "ready"} and offset + len(data) <= job["uploaded_bytes"]:
                    return self._public(job)
                raise ValueError("Este trabajo ya no acepta fragmentos de vídeo.")
            part_path = self._job_directory(job_id) / "upload.part"
            current_size = part_path.stat().st_size if part_path.exists() else 0
            if offset < current_size and offset + len(data) <= current_size:
                with part_path.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(data)) == data:
                        job["uploaded_bytes"] = current_size
                        return self._public(job)
            if offset != current_size:
                raise ValueError(f"Offset inválido: el servidor espera {current_size} bytes.")
            if current_size + len(data) > job["size_bytes"]:
                raise ValueError("El fragmento excede el tamaño declarado del vídeo.")
            with part_path.open("ab") as stream:
                stream.write(data)
            uploaded_bytes = current_size + len(data)
            job["uploaded_bytes"] = uploaded_bytes
            job["updated_at"] = _now()
            if uploaded_bytes == job["size_bytes"]:
                part_path.replace(self._source_path(job))
                job["status"] = "queued"
                job["stage"] = "Esperando turno"
                should_enqueue = True
            self._save_locked(job)
            result = self._public(job)
        if should_enqueue:
            self._enqueue(job_id)
        return result

    def list_jobs(self, *, team_version_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
            if team_version_id:
                jobs = [job for job in jobs if job["team_version_id"] == team_version_id]
            jobs.sort(key=lambda job: job["created_at"], reverse=True)
            return [self._public(job) for job in jobs]

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._require(job_id))

    def storage_summary(self) -> dict[str, int]:
        with self._lock:
            storage = [self._storage_locked(job) for job in self._jobs.values()]
            return {
                "totalBytes": self._tree_size(self.root),
                "reclaimableBytes": sum(item["reclaimableBytes"] for item in storage),
                "jobCount": len(storage),
            }

    def set_protected(self, job_id: str, protected: bool) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            job["is_protected"] = bool(protected)
            job["updated_at"] = _now()
            self._save_locked(job)
            return self._public(job)

    def compact_job(self, job_id: str) -> dict[str, Any]:
        """Libera vídeo e historiales, conservando la salida actual del replay."""

        with self._lock:
            job = self._require(job_id)
            if job["status"] not in {"ready", "error"}:
                raise ValueError("Sólo se puede liberar espacio de un trabajo terminado.")
            if job.get("is_protected"):
                raise ValueError("Este trabajo está protegido. Desprotégelo antes de liberar espacio.")

            source = self._source_path(job)
            source.unlink(missing_ok=True)
            (self._job_directory(job_id) / "upload.part").unlink(missing_ok=True)
            history = self._job_directory(job_id) / "output" / "history"
            if history.is_dir():
                shutil.rmtree(history)

            job["analysis_archives"] = []
            job["compacted_at"] = _now()
            job["updated_at"] = _now()
            self._save_locked(job)
            return self._public(job)

    def delete_job(self, job_id: str) -> str:
        with self._lock:
            job = self._require(job_id)
            if job["status"] not in {"ready", "error"}:
                raise ValueError("Sólo se puede eliminar un trabajo terminado.")
            if job.get("is_protected"):
                raise ValueError("Este trabajo está protegido. Desprotégelo antes de eliminarlo.")
            directory = self._job_directory(job_id)
            if directory.is_dir():
                shutil.rmtree(directory)
            self._jobs.pop(job_id, None)
            self._submitted.discard(job_id)
            self._last_progress_save.pop(job_id, None)
            return job_id

    def retry_job(self, job_id: str) -> dict[str, Any]:
        """Reutiliza el vídeo ya cargado para repetir sólo el análisis."""

        with self._lock:
            job = self._require(job_id)
            if job["status"] not in {"error", "ready"}:
                raise ValueError("Sólo se puede reanalizar un trabajo terminado.")
            if not self._source_path(job).is_file():
                raise ValueError("El vídeo original ya no está disponible en la ROG.")
            archive = self._archive_output(job_id)
            if archive is not None:
                archives = list(job.get("analysis_archives") or [])
                archives.append(str(archive.relative_to(self._job_directory(job_id))))
                job["analysis_archives"] = archives
            job["status"] = "queued"
            job["stage"] = "Esperando turno"
            job["error"] = None
            job["processed_frames"] = 0
            job["total_frames"] = None
            job["elapsed_seconds"] = 0.0
            job["eta_seconds"] = None
            job["events_detected"] = 0
            job["battles_detected"] = 0
            job["skipped_frames"] = 0
            job["warnings"] = []
            job["replay_files"] = []
            job["updated_at"] = _now()
            self._save_locked(job)
            result = self._public(job)
        self._enqueue(job_id)
        return result

    def replay_document(self, job_id: str, replay_number: int) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            if job["status"] != "ready":
                raise ValueError("Los replays todavía no están listos.")
            replay_files = job.get("replay_files") or []
            if replay_number < 1 or replay_number > len(replay_files):
                raise LookupError("Replay no encontrado.")
            path = self._job_directory(job_id) / replay_files[replay_number - 1]
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("No pudimos leer el replay generado.") from error
        if not isinstance(value, dict):
            raise RuntimeError("El replay generado no contiene un objeto JSON.")
        return value

    def diagnostics_archive(self, job_id: str) -> bytes:
        """Empaqueta metadatos, trazas y replays sin incluir el vídeo."""

        with self._lock:
            self._require(job_id)
            directory = self._job_directory(job_id)
            candidates = [directory / "job.json"]
            output = directory / "output"
            if output.is_dir():
                candidates.extend(
                    path
                    for path in output.rglob("*")
                    if path.is_file() and path.suffix.casefold() in {".jsonl", ".json", ".log"}
                )
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in candidates:
                    if path.is_file():
                        archive.write(path, path.relative_to(directory).as_posix())
            return buffer.getvalue()

    def _enqueue(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._submitted:
                return
            self._submitted.add(job_id)
        self._executor.submit(self._process_job, job_id)

    def _process_job(self, job_id: str) -> None:
        with self._lock:
            job = self._require(job_id)
            job["status"] = "analyzing"
            job["stage"] = "Analizando vídeo"
            job["error"] = None
            job["processed_frames"] = 0
            job["total_frames"] = None
            job["elapsed_seconds"] = 0.0
            job["eta_seconds"] = None
            job["events_detected"] = 0
            job["battles_detected"] = 0
            job["skipped_frames"] = 0
            job["warnings"] = []
            job["updated_at"] = _now()
            self._save_locked(job)
            video_path = self._source_path(job)
            context = dict(job["context"])
            sample_fps = float(job["sample_fps"])
            max_battles = int(job["max_battles"])
            output_directory = self._job_directory(job_id) / "output"
            output_directory.mkdir(exist_ok=True)

        def on_progress(progress: CaptureProgress) -> None:
            self._update_progress(job_id, progress)

        def on_warning(message: str) -> None:
            with self._lock:
                current = self._require(job_id)
                warnings = list(current.get("warnings") or [])
                warnings.append(message)
                current["warnings"] = warnings[-10:]

        try:
            documents = self.processor(
                video_path,
                context,
                output_directory,
                sample_fps,
                max_battles,
                on_progress,
                on_warning,
            )
            replay_files: list[str] = []
            total = len(documents)
            for index, document in enumerate(documents, start=1):
                stem_name = "replay" if total == 1 else f"replay-{index:03d}"
                paths = write_replay_artifacts(document, output_directory / stem_name, overwrite=True)
                json_path = next(path for path in paths if path.suffix == ".json")
                replay_files.append(str(json_path.relative_to(self._job_directory(job_id))))
            with self._lock:
                current = self._require(job_id)
                current["status"] = "ready"
                current["stage"] = "Replays listos"
                current["battles_detected"] = total
                current["replay_files"] = replay_files
                current["eta_seconds"] = 0
                current["updated_at"] = _now()
                self._save_locked(current)
        except Exception as error:
            with self._lock:
                current = self._require(job_id)
                current["status"] = "error"
                current["stage"] = "Error"
                current["error"] = str(error) or error.__class__.__name__
                current["updated_at"] = _now()
                self._save_locked(current)
        finally:
            with self._lock:
                self._submitted.discard(job_id)
                self._last_progress_save.pop(job_id, None)

    def _update_progress(self, job_id: str, progress: CaptureProgress) -> None:
        with self._lock:
            job = self._require(job_id)
            job.update(
                {
                    "processed_frames": progress.processed_frames,
                    "total_frames": progress.total_frames,
                    "elapsed_seconds": progress.elapsed_seconds,
                    "eta_seconds": progress.eta_seconds,
                    "events_detected": progress.events_detected,
                    "battles_detected": progress.battles_detected,
                    "skipped_frames": progress.skipped_frames,
                    "updated_at": _now(),
                }
            )
            now = time.monotonic()
            if now - self._last_progress_save.get(job_id, 0) >= 1:
                self._save_locked(job)
                self._last_progress_save[job_id] = now

    def _public(self, job: Mapping[str, Any]) -> dict[str, Any]:
        status = str(job["status"])
        if status == "uploading":
            denominator = max(1, int(job["size_bytes"]))
            progress = int(job["uploaded_bytes"]) / denominator
        elif status == "ready":
            progress = 1.0
        else:
            total_frames = job.get("total_frames")
            progress = (
                int(job.get("processed_frames") or 0) / int(total_frames)
                if total_frames
                else 0.0
            )
        storage = self._storage_locked(job)
        source_available = self._source_path(job).is_file()
        return {
            "id": job["id"],
            "teamVersionId": job["team_version_id"],
            "filename": job["filename"],
            "sizeBytes": job["size_bytes"],
            "uploadedBytes": job["uploaded_bytes"],
            "status": status,
            "stage": job["stage"],
            "progress": min(1.0, max(0.0, progress)),
            "processedFrames": job.get("processed_frames") or 0,
            "totalFrames": job.get("total_frames"),
            "elapsedSeconds": job.get("elapsed_seconds") or 0,
            "etaSeconds": job.get("eta_seconds"),
            "eventsDetected": job.get("events_detected") or 0,
            "battlesDetected": job.get("battles_detected") or 0,
            "skippedFrames": job.get("skipped_frames") or 0,
            "warnings": list(job.get("warnings") or []),
            "replayCount": len(job.get("replay_files") or []),
            "archivedRunCount": len(job.get("analysis_archives") or []),
            "sourceAvailable": source_available,
            "canRetry": status in {"ready", "error"} and source_available,
            "isProtected": bool(job.get("is_protected")),
            "compacted": bool(job.get("compacted_at")),
            "sourceBytes": storage["sourceBytes"],
            "outputBytes": storage["outputBytes"],
            "historyBytes": storage["historyBytes"],
            "totalBytes": storage["totalBytes"],
            "reclaimableBytes": storage["reclaimableBytes"],
            "error": job.get("error"),
            "createdAt": job["created_at"],
            "updatedAt": job["updated_at"],
        }

    @staticmethod
    def _tree_size(path: Path) -> int:
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        if not path.is_dir():
            return 0
        total = 0
        for candidate in path.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
        return total

    def _storage_locked(self, job: Mapping[str, Any]) -> dict[str, int]:
        directory = self._job_directory(str(job["id"]))
        output = directory / "output"
        history = output / "history"
        source_bytes = self._tree_size(self._source_path(job))
        history_bytes = self._tree_size(history)
        output_bytes = 0
        if output.is_dir():
            for candidate in output.rglob("*"):
                if not candidate.is_file() or history in candidate.parents:
                    continue
                try:
                    output_bytes += candidate.stat().st_size
                except OSError:
                    continue
        return {
            "sourceBytes": source_bytes,
            "outputBytes": output_bytes,
            "historyBytes": history_bytes,
            "totalBytes": self._tree_size(directory),
            "reclaimableBytes": source_bytes + history_bytes,
        }

    def _job_directory(self, job_id: str) -> Path:
        return self.root / job_id

    def _source_path(self, job: Mapping[str, Any]) -> Path:
        return self._job_directory(str(job["id"])) / str(job["source_path"])

    def _archive_output(self, job_id: str) -> Path | None:
        """Conserva la traza y los replays actuales antes de reanalizar."""

        output = self._job_directory(job_id) / "output"
        files = tuple(path for path in output.iterdir() if path.is_file()) if output.is_dir() else ()
        if not files:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        archive = output / "history" / f"{timestamp}-{uuid4().hex[:8]}"
        archive.mkdir(parents=True)
        for path in files:
            path.replace(archive / path.name)
        return archive

    def _require(self, job_id: str) -> dict[str, Any]:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise LookupError("Trabajo Champions no encontrado.") from error

    def _save_locked(self, job: Mapping[str, Any]) -> None:
        directory = self._job_directory(str(job["id"]))
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "job.json"
        # A fixed ``job.json.tmp`` collides when the development server leaves
        # two Python workers alive briefly during a restart. Windows can also
        # hold the destination for a few milliseconds while antivirus or the
        # UI reads it. A unique staging file plus bounded retries keeps the
        # metadata atomic without turning a transient lock into a failed job.
        temporary = directory / f"job.{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            for attempt in range(8):
                try:
                    os.replace(temporary, target)
                    break
                except PermissionError:
                    if attempt == 7:
                        raise
                    time.sleep(0.025 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)

    def close(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
