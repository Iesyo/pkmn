"""Ruta de producción de Champions Ledger; COL-102 queda como referencia.

La captura sólo conserva observaciones y separa batallas. Los dos autómatas
deciden después qué sucesos están corroborados y qué replays pueden emitirse.
"""

from __future__ import annotations

import json
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping

from .detector import DetectionError, FrameDetector
from .models import ReplayDocument
from .pipeline import CaptureIncompleteError, CaptureProgress
from .prototype.champions_automaton import BattleAutomaton, read_trace, render_markdown
from .prototype.ledger_replay import ReplayEvidenceError, build_replay, build_trace_context
from .sources import FrameSource


# La aplicación acepta esta marca sólo en el documento canónico del job.
LEDGER_VERSION = "champions-ledger-v1"


def capture_ocr_trace(
    source: FrameSource,
    detector: FrameDetector,
    *,
    max_battles: int = 0,
    total_frames: int | None = None,
    on_progress: Callable[[CaptureProgress], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> None:
    """Lee el vídeo una vez, sin finalizar ni validar con el ensamblador viejo."""
    if max_battles < 0:
        raise ValueError("max_battles no puede ser negativo.")
    started = time.monotonic()
    processed = skipped = events = completed = errors = 0
    has_activity = awaiting_start = False

    def warnings() -> None:
        pop = getattr(detector, "pop_warnings", None)
        if callable(pop):
            for message in pop():
                if on_warning:
                    on_warning(message)

    def flush() -> None:
        pending = getattr(detector, "flush_pending", None)
        if callable(pending):
            pending()  # El detector añade a la traza los refuerzos visuales.
        warnings()

    def report(timestamp_ms: int) -> None:
        if on_progress:
            on_progress(CaptureProgress(
                processed_frames=processed, total_frames=total_frames,
                timestamp_ms=timestamp_ms, elapsed_seconds=time.monotonic() - started,
                events_detected=events, skipped_frames=skipped, battles_detected=completed,
            ))

    frames = iter(source)
    try:
        for frame in frames:
            processed += 1
            try:
                detections = detector.detect(frame)
            except DetectionError as error:
                skipped += 1
                errors += 1
                if on_warning:
                    on_warning(f"Frame {frame.index + 1} omitido: {error}")
                report(frame.timestamp_ms)
                if errors >= 3:
                    raise DetectionError(
                        "El detector falló en 3 frames consecutivos; se conserva la traza parcial."
                    ) from error
                continue
            errors = 0
            warnings()
            if awaiting_start:
                if detections.team_preview or not detections.battle_started:
                    report(frame.timestamp_ms)
                    continue
                awaiting_start = False
            events += len(detections.events)
            has_activity = has_activity or any(
                event.kind in {"switch", "drag", "turn", "move"}
                for event in detections.events
            )
            if has_activity and detections.battle_complete and detections.winner:
                # Incluso identidades provisionales o PS contradictorios llegan
                # a Ledger. No se llama a CaptureAccumulator.finalize().
                flush()
                completed += 1
                has_activity = False
                awaiting_start = True
                report(frame.timestamp_ms)
                if max_battles and completed >= max_battles:
                    break
                reset = getattr(detector, "reset_battle_state", None)
                if callable(reset):
                    reset()
                continue
            report(frame.timestamp_ms)
        else:
            flush()  # También conserva aliases de una batalla truncada.
    finally:
        close_frames = getattr(frames, "close", None)
        if callable(close_frames):
            close_frames()
        close_detector = getattr(detector, "close", None)
        if callable(close_detector):
            close_detector()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def documents_from_trace(
    trace_path: Path,
    job: Mapping[str, Any],
    output_directory: Path,
    *,
    on_warning: Callable[[str], None] | None = None,
) -> tuple[ReplayDocument, ...]:
    """Ejecuta ambos autómatas por batalla y archiva también los bloqueos.

    Los números de replay son contiguos; source_battle_index conserva siempre
    el índice original de la traza aunque una batalla intermedia no exporte.
    """
    output_directory.mkdir(parents=True, exist_ok=True)
    report_path = output_directory / "ledger-report.json"
    report: dict[str, Any] = {
        "engine": LEDGER_VERSION, "job_id": job.get("id"), "trace": trace_path.name,
        "status": "running", "sampled_frames": 0, "battles": [],
    }
    _write_json(report_path, report)
    try:
        rows = read_trace(trace_path)
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            index = row.get("battle_index")
            if type(index) is not int or index < 0:
                raise ValueError("La traza tiene un índice de batalla inválido.")
            grouped[index].append(row)
        report["sampled_frames"] = len(rows)
    except Exception as error:
        report.update(status="error", error=str(error))
        _write_json(report_path, report)
        raise

    documents: list[ReplayDocument] = []
    for index, frames in sorted(grouped.items()):
        if not any((row.get("detections") or {}).get("events") for row in frames):
            continue
        stem = f"ledger-battle-{index + 1:03d}"
        entry: dict[str, Any] = {
            "source_battle_index": index, "status": "running", "replay_number": None,
            "ledger_json": f"{stem}.json", "ledger_markdown": f"{stem}.md",
        }
        report["battles"].append(entry)
        try:
            ledger = BattleAutomaton(index, frames, dict(job.get("context") or {})).run()
            _write_json(output_directory / entry["ledger_json"], ledger)
            (output_directory / entry["ledger_markdown"]).write_text(
                render_markdown(ledger, None), encoding="utf-8",
            )
            entry.update(
                candidate_events=ledger["candidate_events"], ledger_events=len(ledger["events"]),
                issues=ledger["issues"], resolved_issues=len(ledger.get("resolved_issues", [])),
            )
            if ledger["issues"]:
                raise ReplayEvidenceError(f"Ledger tiene {len(ledger['issues'])} aviso(s) abiertos.")
            context = build_trace_context(job, frames, ledger)
            replay = build_replay(ledger, context)
            # La constancia aparece sólo después de superar ambos autómatas.
            documents.append(ReplayDocument(**replay, reconciliation_version=LEDGER_VERSION))
            entry.update(status="ready", replay_number=len(documents))
        except Exception as error:
            entry.update(status="blocked" if isinstance(error, ReplayEvidenceError) else "error",
                         error=str(error))
            if not isinstance(error, ReplayEvidenceError):
                error_path = output_directory / f"{stem}-error.log"
                error_path.write_text(traceback.format_exc(), encoding="utf-8")
                entry["error_log"] = error_path.name
            if on_warning:
                on_warning(f"Batalla {index + 1}: {error} Consulta el diagnóstico de Ledger.")
        _write_json(report_path, report)

    blocked = sum(entry["status"] != "ready" for entry in report["battles"])
    report.update(status="partial" if documents and blocked else "ready" if documents else "blocked",
                  replay_count=len(documents), blocked_battles=blocked)
    _write_json(report_path, report)
    if not documents:
        raise CaptureIncompleteError(
            "Ledger no pudo exportar una batalla completa. Descarga el diagnóstico para revisar "
            "la traza, los registros de batalla y los avisos."
        )
    return tuple(documents)
