from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .detector import DetectorContext, OllamaVisionDetector
from .models import CapturedBattle
from .pipeline import CaptureSeed, ReplayCapturePipeline, review_capture
from .showdown import build_replay_document, write_replay_artifacts
from .sources import LiveFrameSource, VideoFrameSource


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"No pudimos leer {path}: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} debe contener un objeto JSON.")
    return value


def _species(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())[:6]


def _seed_from_context(value: Mapping[str, Any], source_mode: str) -> tuple[CaptureSeed, DetectorContext]:
    players = value.get("players") if isinstance(value.get("players"), Mapping) else {}
    teams = value.get("teams") if isinstance(value.get("teams"), Mapping) else {}
    p1_name = str(players.get("p1") or "Jugador")
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


def _common_capture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context", type=Path, help="JSON con Team conocido, jugadores, idioma y formato.")
    parser.add_argument("--output", type=Path, required=True, help="Ruta base de los archivos de salida.")
    parser.add_argument("--sample-fps", type=float, default=2.0, help="Frames por segundo enviados al detector.")
    parser.add_argument("--max-frames", type=int, help="Límite opcional de frames para una prueba.")
    parser.add_argument("--max-battles", type=int, default=1, help="Cantidad máxima de batallas a producir.")
    parser.add_argument("--model", default="qwen3-vl:4b", help="Modelo visual disponible en Ollama.")
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

    video = subparsers.add_parser("video", help="Lee una grabación mediante FFmpeg y Ollama local.")
    video.add_argument("video", type=Path)
    _common_capture_arguments(video)

    live = subparsers.add_parser("live", help="Lee en vivo una capturadora, cámara virtual o ventana.")
    live.add_argument("source", help="Nombre o ruta de la fuente para FFmpeg.")
    live.add_argument("--backend", choices=["auto", "dshow", "v4l2", "avfoundation", "lavfi"], default="auto")
    _common_capture_arguments(live)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "events":
        battle = CapturedBattle.from_mapping(_load_mapping(args.capture))
        return _write_captures((battle,), args.output, args.force)

    context_value = _load_mapping(args.context) if args.context else {}
    seed, detector_context = _seed_from_context(context_value, args.command)
    detector = OllamaVisionDetector(model=args.model, endpoint=args.ollama_url, context=detector_context)
    if args.command == "video":
        source = VideoFrameSource(
            path=args.video,
            sample_fps=args.sample_fps,
            max_frames=args.max_frames,
        )
    else:
        source = LiveFrameSource(
            input_name=args.source,
            backend=args.backend,
            sample_fps=args.sample_fps,
            max_frames=args.max_frames,
        )
    captures = ReplayCapturePipeline(source, detector, seed).capture(max_battles=args.max_battles)
    return _write_captures(captures, args.output, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
