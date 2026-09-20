from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .detector import DetectionError, DetectorContext, OllamaVisionDetector
from .models import CapturedBattle
from .ocr_detector import ChampionsOcrDetector, OcrTraceDetector
from .pipeline import CaptureIncompleteError, CaptureProgress, CaptureSeed, ReplayCapturePipeline, review_capture
from .showdown import build_replay_document, write_replay_artifacts
from .sources import CaptureSourceError, LiveFrameSource, OcrTraceFrameSource, VideoFrameSource


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"No pudimos leer {path}: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} debe contener un objeto JSON.")
    return value


def _species(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())[:6]


def _aliases(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple(
        (alias.strip(), species.strip())
        for raw_alias, raw_species in value.items()
        if (alias := str(raw_alias).strip()) and (species := str(raw_species).strip())
    )


def _seed_from_context(value: Mapping[str, Any], source_mode: str) -> tuple[CaptureSeed, DetectorContext]:
    players = value.get("players") if isinstance(value.get("players"), Mapping) else {}
    teams = value.get("teams") if isinstance(value.get("teams"), Mapping) else {}
    aliases = value.get("aliases") if isinstance(value.get("aliases"), Mapping) else {}
    p1_name = str(players.get("p1") or "Player")
    p2_name = str(players.get("p2") or "Rival")
    p1_team = _species(teams.get("p1"))
    p2_team = _species(teams.get("p2"))
    seed = CaptureSeed(
        p1_name=p1_name,
        p2_name=p2_name,
        p1_team=p1_team,
        p2_team=p2_team,
        format=str(value.get("format") or "gen9championsvgc2026regmc"),
        source_mode=source_mode,  # type: ignore[arg-type]
    )
    context = DetectorContext(
        p1_name=p1_name,
        p2_name=p2_name,
        p1_team=p1_team,
        p2_team=p2_team,
        p1_aliases=_aliases(aliases.get("p1")),
        p2_aliases=_aliases(aliases.get("p2")),
        language=str(value.get("language") or "en"),
    )
    return seed, context


def _output_stem(base: Path, index: int, total: int) -> Path:
    return base if total == 1 else base.with_name(f"{base.name}-{index:03d}")


def _write_captures(captures: Sequence[CapturedBattle], output: Path, force: bool) -> int:
    for index, battle in enumerate(captures, start=1):
        issues = review_capture(battle)
        for issue in issues:
            print(f"[{issue.severity.upper()}] {issue.message}")
        document = build_replay_document(battle)
        stem = _output_stem(output, index, len(captures))
        paths = write_replay_artifacts(document, stem, overwrite=force)
        print("Replay generado:")
        for path in paths:
            print(f"  {path}")
    return 0


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3_600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class _ProgressPrinter:
    def __init__(
        self,
        total_frames: int | None,
        *,
        detector: str,
        sample_fps: float | None,
    ) -> None:
        self.total_frames = total_frames
        self.detector = detector
        self.sample_fps = sample_fps
        self.active_line = False

    def start(self) -> None:
        target = f"{self.total_frames} frames estimados" if self.total_frames else "duración desconocida"
        rate = f" a {self.sample_fps:g} FPS" if self.sample_fps is not None else ""
        print(f"Analizando {target}{rate} con {self.detector}...")

    def update(self, progress: CaptureProgress) -> None:
        elapsed = _format_duration(progress.elapsed_seconds)
        if progress.fraction is None:
            position = f"frame {progress.processed_frames}"
            meter = ""
        else:
            filled = min(20, round(progress.fraction * 20))
            meter = f"[{'#' * filled}{'-' * (20 - filled)}] "
            position = (
                f"{progress.processed_frames}/{progress.total_frames} "
                f"({progress.fraction:.1%})"
            )
        eta = _format_duration(progress.eta_seconds)
        line = (
            f"\r{meter}{position} | transcurrido {elapsed} | ETA {eta} | "
            f"partidas {progress.battles_detected} | eventos {progress.events_detected} | "
            f"omitidos {progress.skipped_frames}"
        )
        print(f"{line:<125}", end="", flush=True)
        self.active_line = True

    def warning(self, message: str) -> None:
        self.finish_line()
        print(f"[WARNING] {message}", file=sys.stderr)

    def finish_line(self) -> None:
        if self.active_line:
            print()
            self.active_line = False


def _common_capture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context", type=Path, help="JSON con Team conocido, jugadores, idioma y formato.")
    parser.add_argument("--output", type=Path, required=True, help="Ruta base de los archivos de salida.")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="Frames por segundo enviados al detector.")
    parser.add_argument("--max-frames", type=int, help="Límite opcional de frames para una prueba.")
    parser.add_argument(
        "--max-battles",
        type=int,
        default=1,
        help="Cantidad máxima de batallas a producir; 0 procesa todas las encontradas.",
    )
    parser.add_argument(
        "--detector",
        choices=["ocr", "ollama"],
        default="ocr",
        help="Detector principal; OCR local es rápido y Ollama queda como compatibilidad.",
    )
    parser.add_argument(
        "--ocr-trace",
        type=Path,
        help="JSONL opcional con texto, coordenadas, tiempos y eventos detectados por frame.",
    )
    parser.add_argument(
        "--ocr-min-confidence",
        type=float,
        default=0.5,
        help="Confianza mínima de texto para el detector OCR (0 a 1).",
    )
    parser.add_argument(
        "--ocr-workers",
        type=int,
        choices=(1, 2, 4),
        default=2,
        help="Workers OCR ordenados para vídeo; 2 ofrece el mejor equilibrio local.",
    )
    parser.add_argument(
        "--no-visual-aliases",
        action="store_true",
        help="Compatibilidad: el OCR determinista ya no usa aliases mediante Ollama.",
    )
    parser.add_argument(
        "--model",
        default="qwen3-vl:4b",
        help="Modelo visual disponible en Ollama.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
        help="Servidor local de Ollama.",
    )
    parser.add_argument("--force", action="store_true", help="Permite reemplazar artefactos existentes.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="champions-replay",
        description="Reconstruye una batalla de Pokémon Champions como replay de Pokémon Showdown.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    events = subparsers.add_parser("events", help="Genera un replay desde un JSON de eventos ya revisado.")
    events.add_argument("capture", type=Path)
    events.add_argument("--output", type=Path, required=True)
    events.add_argument("--force", action="store_true")

    video = subparsers.add_parser("video", help="Lee una grabación mediante FFmpeg y OCR local.")
    video.add_argument("video", type=Path)
    _common_capture_arguments(video)

    live = subparsers.add_parser("live", help="Lee en vivo una capturadora, cámara virtual o ventana.")
    live.add_argument("source", help="Nombre o ruta de la fuente para FFmpeg.")
    live.add_argument("--backend", choices=["auto", "dshow", "v4l2", "avfoundation", "lavfi"], default="auto")
    _common_capture_arguments(live)

    trace = subparsers.add_parser(
        "trace",
        help="Reprocesa una traza OCR existente sin volver a leer el vídeo.",
    )
    trace.add_argument("trace", type=Path)
    trace.add_argument("--context", type=Path, help="JSON con Teams, alias, jugadores, idioma y formato.")
    trace.add_argument("--output", type=Path, required=True, help="Ruta base de los archivos de salida.")
    trace.add_argument(
        "--max-battles",
        type=int,
        default=1,
        help="Cantidad máxima de batallas a producir; 0 procesa todas las encontradas.",
    )
    trace.add_argument("--force", action="store_true", help="Permite reemplazar artefactos existentes.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    progress: _ProgressPrinter | None = None
    try:
        if args.command == "events":
            battle = CapturedBattle.from_mapping(_load_mapping(args.capture))
            return _write_captures((battle,), args.output, args.force)

        context_value = _load_mapping(args.context) if args.context else {}
        source_mode = "video" if args.command == "trace" else args.command
        seed, detector_context = _seed_from_context(context_value, source_mode)
        sample_fps: float | None = None
        if args.command == "trace":
            detector = OcrTraceDetector(context=detector_context)
            source = OcrTraceFrameSource(path=args.trace)
            total_frames = source.estimated_frame_count()
            detector_label = "parser de traza OCR"
        elif args.detector == "ollama":
            detector = OllamaVisionDetector(
                model=args.model,
                endpoint=args.ollama_url,
                context=detector_context,
            )
            detector_label = f"Ollama {args.model}"
        else:
            if args.ocr_trace and args.ocr_trace.exists():
                if not args.force:
                    raise FileExistsError(
                        f"Ya existe {args.ocr_trace}; usa --force para reemplazarlo."
                    )
                args.ocr_trace.unlink()
            detector = ChampionsOcrDetector(
                context=detector_context,
                trace_path=args.ocr_trace,
                min_confidence=args.ocr_min_confidence,
            )
            detector_label = "OCR local determinista"
        if args.command != "trace":
            sample_fps = args.sample_fps
            if args.command == "video":
                source = VideoFrameSource(
                    path=args.video,
                    sample_fps=args.sample_fps,
                    max_frames=args.max_frames,
                )
                total_frames = source.estimated_frame_count()
            else:
                source = LiveFrameSource(
                    input_name=args.source,
                    backend=args.backend,
                    sample_fps=args.sample_fps,
                    max_frames=args.max_frames,
                )
                total_frames = args.max_frames
        progress = _ProgressPrinter(
            total_frames,
            detector=detector_label,
            sample_fps=sample_fps,
        )
        progress.start()
        captures = ReplayCapturePipeline(source, detector, seed).capture(
            max_battles=args.max_battles,
            total_frames=total_frames,
            on_progress=progress.update,
            on_warning=progress.warning,
            ocr_workers=args.ocr_workers if args.command == "video" else 1,
        )
        progress.finish_line()
        return _write_captures(captures, args.output, args.force)
    except (CaptureIncompleteError, CaptureSourceError, DetectionError, FileExistsError, ValueError) as error:
        if progress:
            progress.finish_line()
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
