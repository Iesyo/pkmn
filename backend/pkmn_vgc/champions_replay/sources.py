from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from queue import Empty, Full, Queue
from typing import BinaryIO, Iterator, Literal, Protocol

LiveBackend = Literal["auto", "dshow", "v4l2", "avfoundation", "lavfi"]


class CaptureSourceError(RuntimeError):
    """FFmpeg no pudo abrir o leer la fuente solicitada."""


@dataclass(frozen=True, slots=True)
class FramePacket:
    index: int
    timestamp_ms: int
    image: bytes
    mime_type: str = "image/jpeg"


class FrameSource(Protocol):
    def __iter__(self) -> Iterator[FramePacket]: ...


def iter_mjpeg(stream: BinaryIO, *, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """Separa un pipe MJPEG sin depender de OpenCV o Pillow."""
    buffer = bytearray()
    while chunk := stream.read(chunk_size):
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > chunk_size:
                    del buffer[:-2]
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                break
            yield bytes(buffer[start : end + 2])
            del buffer[: end + 2]


@dataclass(slots=True)
class _FfmpegMjpegSource:
    sample_fps: float = 2.0
    max_frames: int | None = None
    ffmpeg_binary: str = "ffmpeg"

    def __post_init__(self) -> None:
        if self.sample_fps <= 0 or self.sample_fps > 30:
            raise ValueError("sample_fps debe estar entre 0 y 30.")
        if self.max_frames is not None and self.max_frames < 1:
            raise ValueError("max_frames debe ser positivo.")

    def input_arguments(self) -> list[str]:
        raise NotImplementedError

    def command(self) -> list[str]:
        return [
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            *self.input_arguments(),
            "-an",
            "-vf",
            f"fps={self.sample_fps:g}",
            "-q:v",
            "4",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]

    def _timestamp_ms(self, index: int, started: float) -> int:
        return round(index * 1000 / self.sample_fps)

    def __iter__(self) -> Iterator[FramePacket]:
        if not shutil.which(self.ffmpeg_binary):
            raise CaptureSourceError(
                "FFmpeg no está instalado o no aparece en PATH; es necesario para leer vídeo y captura en vivo."
            )
        with tempfile.TemporaryFile() as stderr_stream:
            process = subprocess.Popen(
                self.command(),
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
            )
            assert process.stdout is not None
            started = time.monotonic()
            stopped_early = False
            try:
                for index, image in enumerate(iter_mjpeg(process.stdout)):
                    yield FramePacket(
                        index=index,
                        timestamp_ms=self._timestamp_ms(index, started),
                        image=image,
                    )
                    if self.max_frames is not None and index + 1 >= self.max_frames:
                        stopped_early = True
                        break
            except GeneratorExit:
                stopped_early = True
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                process.stdout.close()
                stderr_stream.seek(0)
                stderr = stderr_stream.read()
        if process.returncode and not stopped_early:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise CaptureSourceError(detail or "FFmpeg no pudo leer la fuente.")


@dataclass(slots=True)
class VideoFrameSource(_FfmpegMjpegSource):
    path: Path = Path()

    def input_arguments(self) -> list[str]:
        if not self.path.is_file():
            raise CaptureSourceError(f"No encontramos el vídeo: {self.path}")
        return ["-i", str(self.path)]

    def estimated_frame_count(self) -> int | None:
        self.input_arguments()
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return self.max_frames
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(self.path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            duration_seconds = float(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return self.max_frames
        if result.returncode or duration_seconds <= 0:
            return self.max_frames
        estimate = max(1, ceil(duration_seconds * self.sample_fps))
        return min(estimate, self.max_frames) if self.max_frames is not None else estimate


def _probe_duration_seconds(path: Path, *, ffprobe_binary: str = "ffprobe") -> float | None:
    ffprobe = shutil.which(ffprobe_binary)
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        duration_seconds = float(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode or duration_seconds <= 0:
        return None
    return duration_seconds


@dataclass(slots=True)
class SegmentedVideoFrameSource:
    """Relee sólo ciertos tramos de un vídeo ya analizado, a más fps.

    COL-102, reapertura estructural del 25 sep: casi toda una batalla es
    estable -nada cambia- y los bugs de esta ronda nacieron todos en el
    mismo puñado de instantes (un `faint`, un `switch`, una barra de HP
    cerca de 0), no repartidos parejo por todo el vídeo. Subir el fps de
    principio a fin gasta la mayor parte del tiempo extra en tramos que ya
    salían bien. `risk_windows` (`pipeline.py`) marca esos instantes desde
    una primera pasada barata; esta fuente vuelve a leer sólo esos tramos,
    a `dense_fps`, y el resto del vídeo a `base_fps` -sin releerlo entero
    dos veces al mismo ritmo.

    Cada tramo es su propia invocación de FFmpeg con `-ss` (siembra rápida,
    por keyframe: puede empezar un poco antes de lo pedido, nunca después
    -no pierde nada, en el peor caso relee un poco de más). Los timestamps
    que produce son los del vídeo real, no reiniciados por tramo, para que
    encajen en la misma traza que la primera pasada.
    """

    path: Path = Path()
    dense_windows: tuple[tuple[int, int], ...] = ()
    base_fps: float = 2.0
    dense_fps: float = 8.0
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"

    def __post_init__(self) -> None:
        if self.base_fps <= 0 or self.base_fps > 30:
            raise ValueError("base_fps debe estar entre 0 y 30.")
        if self.dense_fps <= 0 or self.dense_fps > 30:
            raise ValueError("dense_fps debe estar entre 0 y 30.")
        if not self.path.is_file():
            raise CaptureSourceError(f"No encontramos el vídeo: {self.path}")

    def _duration_ms(self) -> int | None:
        duration_seconds = _probe_duration_seconds(self.path, ffprobe_binary=self.ffprobe_binary)
        return round(duration_seconds * 1000) if duration_seconds is not None else None

    def _segments(self) -> list[tuple[int, int, float]]:
        """(inicio_ms, fin_ms, fps) que cubren todo el vídeo, sin huecos."""

        duration_ms = self._duration_ms()
        windows = sorted(
            (max(0, start), end)
            for start, end in self.dense_windows
            if end > start
        )
        merged: list[list[int]] = []
        for start, end in windows:
            if duration_ms is not None:
                end = min(end, duration_ms)
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        segments: list[tuple[int, int, float]] = []
        cursor = 0
        for start, end in merged:
            if start > cursor:
                segments.append((cursor, start, self.base_fps))
            segments.append((start, end, self.dense_fps))
            cursor = end
        if duration_ms is None:
            if not merged:
                segments.append((cursor, cursor, self.base_fps))
        elif cursor < duration_ms:
            segments.append((cursor, duration_ms, self.base_fps))
        return segments

    def estimated_frame_count(self) -> int | None:
        duration_ms = self._duration_ms()
        if duration_ms is None:
            return None
        total = 0.0
        for start, end, fps in self._segments():
            total += (end - start) / 1000 * fps
        return max(1, ceil(total))

    def __iter__(self) -> Iterator[FramePacket]:
        if not shutil.which(self.ffmpeg_binary):
            raise CaptureSourceError(
                "FFmpeg no está instalado o no aparece en PATH; es necesario para leer vídeo."
            )
        segments = self._segments()
        if not segments:
            return
        index = 0
        for start_ms, end_ms, fps in segments:
            duration_s = (end_ms - start_ms) / 1000
            if duration_s <= 0:
                continue
            command = [
                self.ffmpeg_binary,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_ms / 1000:.3f}",
                "-i",
                str(self.path),
                "-t",
                f"{duration_s:.3f}",
                "-an",
                "-vf",
                f"fps={fps:g}",
                "-q:v",
                "4",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ]
            with tempfile.TemporaryFile() as stderr_stream:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_stream)
                assert process.stdout is not None
                stopped_early = False
                try:
                    for offset, image in enumerate(iter_mjpeg(process.stdout)):
                        timestamp_ms = start_ms + round(offset * 1000 / fps)
                        if timestamp_ms >= end_ms and offset:
                            break
                        yield FramePacket(index=index, timestamp_ms=timestamp_ms, image=image)
                        index += 1
                except GeneratorExit:
                    stopped_early = True
                    raise
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                    process.stdout.close()
                    stderr_stream.seek(0)
                    stderr = stderr_stream.read()
            if process.returncode and not stopped_early:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise CaptureSourceError(detail or "FFmpeg no pudo releer un tramo del vídeo.")


@dataclass(slots=True)
class OcrTraceFrameSource:
    path: Path = Path()

    def _lines(self) -> Iterator[tuple[int, bytes]]:
        if not self.path.is_file():
            raise CaptureSourceError(f"No encontramos la traza OCR: {self.path}")
        with self.path.open("rb") as stream:
            for line_number, raw in enumerate(stream, start=1):
                if raw.strip():
                    yield line_number, raw

    def estimated_frame_count(self) -> int:
        return sum(1 for _line_number, _raw in self._lines())

    def __iter__(self) -> Iterator[FramePacket]:
        for line_number, raw in self._lines():
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
                frame_number = max(1, int(payload.get("frame", line_number)))
                timestamp_ms = max(0, int(payload.get("timestamp_ms", 0)))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise CaptureSourceError(
                    f"La línea {line_number} de {self.path} no es una traza OCR válida: {error}"
                ) from error
            yield FramePacket(
                index=frame_number - 1,
                timestamp_ms=timestamp_ms,
                image=raw,
                mime_type="application/x-ndjson",
            )


@dataclass(slots=True)
class LiveFrameSource(_FfmpegMjpegSource):
    input_name: str = ""
    backend: LiveBackend = "auto"

    def __post_init__(self) -> None:
        _FfmpegMjpegSource.__post_init__(self)
        if not self.input_name.strip():
            raise ValueError("La captura en vivo necesita un dispositivo o fuente.")
        if self.backend not in {"auto", "dshow", "v4l2", "avfoundation", "lavfi"}:
            raise ValueError(f"Backend de captura no soportado: {self.backend}.")

    def input_arguments(self) -> list[str]:
        source = self.input_name.strip()
        if self.backend == "auto":
            return ["-i", source]
        if self.backend == "dshow":
            source = source if source.startswith("video=") else f"video={source}"
        return ["-f", self.backend, "-i", source]

    def _timestamp_ms(self, index: int, started: float) -> int:
        return round((time.monotonic() - started) * 1000)

    def __iter__(self) -> Iterator[FramePacket]:
        """Consume FFmpeg continuamente y entrega sólo el frame live más reciente.

        El OCR puede tardar más que el intervalo entre frames. Un buffer de un
        elemento evita que esa diferencia convierta una sesión de OBS en una
        reproducción atrasada de varios minutos.
        """

        if not shutil.which(self.ffmpeg_binary):
            raise CaptureSourceError(
                "FFmpeg no está instalado o no aparece en PATH; es necesario para leer vídeo y captura en vivo."
            )

        latest: Queue[tuple[int, bytes]] = Queue(maxsize=1)
        reader_done = threading.Event()
        reader_errors: list[BaseException] = []
        stopped_early = False

        with tempfile.TemporaryFile() as stderr_stream:
            process = subprocess.Popen(
                self.command(),
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
            )
            assert process.stdout is not None

            def publish(value: tuple[int, bytes]) -> None:
                while True:
                    try:
                        latest.put_nowait(value)
                        return
                    except Full:
                        try:
                            latest.get_nowait()
                        except Empty:
                            pass

            def read_latest() -> None:
                try:
                    for source_index, image in enumerate(iter_mjpeg(process.stdout)):
                        publish((source_index, image))
                except BaseException as error:  # pragma: no cover - fallo excepcional del pipe
                    reader_errors.append(error)
                finally:
                    reader_done.set()

            reader = threading.Thread(target=read_latest, name="champions-live-reader", daemon=True)
            reader.start()
            started = time.monotonic()
            yielded = 0
            try:
                while True:
                    try:
                        source_index, image = latest.get(timeout=0.1)
                    except Empty:
                        if reader_done.is_set():
                            break
                        continue
                    yield FramePacket(
                        index=source_index,
                        timestamp_ms=self._timestamp_ms(source_index, started),
                        image=image,
                    )
                    yielded += 1
                    if self.max_frames is not None and yielded >= self.max_frames:
                        stopped_early = True
                        break
            except GeneratorExit:
                stopped_early = True
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                reader.join(timeout=3)
                process.stdout.close()
                stderr_stream.seek(0)
                stderr = stderr_stream.read()

        if reader_errors and not stopped_early:
            raise CaptureSourceError(f"FFmpeg no pudo entregar frames: {reader_errors[-1]}")
        if process.returncode and not stopped_early:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise CaptureSourceError(detail or "FFmpeg no pudo leer la fuente.")
