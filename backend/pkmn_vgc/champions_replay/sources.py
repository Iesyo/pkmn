from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
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
        process = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            try:
                _, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()
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
