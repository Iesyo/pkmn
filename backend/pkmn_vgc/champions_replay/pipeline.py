from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

from .detector import DetectionError, FrameDetector
from .models import BattleEvent, BattleSide, CapturedBattle, FrameDetections, SideId, SourceMode
from .sources import FrameSource


class CaptureIncompleteError(RuntimeError):
    """La fuente terminó antes de reunir un combate utilizable."""


@dataclass(frozen=True, slots=True)
class CaptureProgress:
    processed_frames: int
    total_frames: int | None
    timestamp_ms: int
    elapsed_seconds: float
    events_detected: int
    skipped_frames: int
    battles_detected: int

    @property
    def fraction(self) -> float | None:
        if not self.total_frames:
            return None
        return min(1.0, self.processed_frames / self.total_frames)

    @property
    def eta_seconds(self) -> float | None:
        if not self.total_frames or self.processed_frames < 1:
            return None
        remaining = max(0, self.total_frames - self.processed_frames)
        return self.elapsed_seconds / self.processed_frames * remaining


@dataclass(frozen=True, slots=True)
class CaptureSeed:
    p1_name: str = "Player"
    p2_name: str = "Rival"
    p1_team: tuple[str, ...] = ()
    p2_team: tuple[str, ...] = ()
    format: str = "gen9championsvgc2026regmc"
    source_mode: SourceMode = "video"


def _merge_species(current: list[str], incoming: Iterable[str], *, limit: int) -> None:
    keys = {"".join(character for character in species.lower() if character.isalnum()) for species in current}
    for species in incoming:
        key = "".join(character for character in species.lower() if character.isalnum())
        if not key or key in keys:
            continue
        current.append(species)
        keys.add(key)
        if len(current) >= limit:
            return


@dataclass(slots=True)
class CaptureAccumulator:
    seed: CaptureSeed
    dedupe_window_ms: int = 3_000
    p1_name: str = field(init=False)
    p2_name: str = field(init=False)
    p1_team: list[str] = field(init=False)
    p2_team: list[str] = field(init=False)
    p1_selected: list[str] = field(default_factory=list)
    p2_selected: list[str] = field(default_factory=list)
    p1_lead: list[str] = field(default_factory=list)
    p2_lead: list[str] = field(default_factory=list)
    events: list[BattleEvent] = field(default_factory=list)
    winner: SideId | None = None
    complete: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _last_event_at: dict[tuple[object, ...], int] = field(default_factory=dict)
    _last_health_event: dict[tuple[object, ...], tuple[int, int]] = field(default_factory=dict)
    _turn_seen: bool = False

    def __post_init__(self) -> None:
        self.p1_name = self.seed.p1_name
        self.p2_name = self.seed.p2_name
        self.p1_team = list(self.seed.p1_team)
        self.p2_team = list(self.seed.p2_team)

    @property
    def has_battle_data(self) -> bool:
        return bool(self.events or self.p1_selected or self.p2_selected)

    def apply(self, detections: FrameDetections) -> None:
        if detections.p1_name:
            self.p1_name = detections.p1_name
        if detections.p2_name:
            self.p2_name = detections.p2_name
        _merge_species(self.p1_team, detections.p1_team, limit=6)
        _merge_species(self.p2_team, detections.p2_team, limit=6)
        _merge_species(self.p1_selected, detections.p1_selected, limit=4)
        _merge_species(self.p2_selected, detections.p2_selected, limit=4)

        for event in detections.events:
            if event.kind in {"damage", "heal"}:
                health_key = (event.kind, event.slot, event.species)
                previous_health = self._last_health_event.get(health_key)
                if previous_health and event.timestamp_ms - previous_health[1] <= 1_500:
                    previous_index = previous_health[0]
                    previous_event = self.events[previous_index]
                    self._last_event_at.pop(previous_event.signature(), None)
                    self.events[previous_index] = event
                    self._last_event_at[event.signature()] = event.timestamp_ms
                    self._last_health_event[health_key] = (previous_index, event.timestamp_ms)
                    continue
            signature = event.signature()
            previous = self._last_event_at.get(signature)
            if previous is not None and event.timestamp_ms - previous <= self.dedupe_window_ms:
                self._last_event_at[signature] = event.timestamp_ms
                continue
            self._last_event_at[signature] = event.timestamp_ms
            self.events.append(event)
            if event.kind in {"damage", "heal"}:
                self._last_health_event[(event.kind, event.slot, event.species)] = (
                    len(self.events) - 1,
                    event.timestamp_ms,
                )
            if event.kind == "turn":
                self._turn_seen = True
            if event.kind in {"switch", "drag"} and event.slot and event.species:
                selected = self.p1_selected if event.slot.startswith("p1") else self.p2_selected
                team = self.p1_team if event.slot.startswith("p1") else self.p2_team
                _merge_species(selected, (event.species,), limit=4)
                _merge_species(team, (event.species,), limit=6)
                if not self._turn_seen:
                    lead = self.p1_lead if event.slot.startswith("p1") else self.p2_lead
                    _merge_species(lead, (event.species,), limit=2)

        if detections.winner:
            self.winner = detections.winner
        self.complete = self.complete or detections.battle_complete

    def finalize(self) -> CapturedBattle:
        if not self.winner:
            raise CaptureIncompleteError("No se pudo identificar el resultado de la batalla.")
        if not self.events:
            raise CaptureIncompleteError("No se detectaron eventos de batalla.")
        if not self.p1_team or not self.p2_team:
            raise CaptureIncompleteError("No se pudo reconstruir el Team Preview de ambos jugadores.")
        def ordered_selection(lead: list[str], selected: list[str]) -> tuple[str, ...]:
            ordered: list[str] = []
            _merge_species(ordered, lead, limit=4)
            _merge_species(ordered, selected, limit=4)
            return tuple(ordered)

        return CapturedBattle(
            p1=BattleSide(self.p1_name, tuple(self.p1_team), ordered_selection(self.p1_lead, self.p1_selected)),
            p2=BattleSide(self.p2_name, tuple(self.p2_team), ordered_selection(self.p2_lead, self.p2_selected)),
            events=tuple(self.events),
            winner=self.winner,
            started_at=self.started_at,
            format=self.seed.format,
            source_mode=self.seed.source_mode,
        )


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    severity: str
    message: str


def review_capture(battle: CapturedBattle, *, confidence_threshold: float = 0.75) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    for label, side in (("jugador", battle.p1), ("rival", battle.p2)):
        if len(side.team) != 6:
            issues.append(ReviewIssue("warning", f"El Team Preview del {label} contiene {len(side.team)}/6 Pokémon."))
        if len(side.selected) != 4:
            issues.append(ReviewIssue("blocking", f"La selección del {label} contiene {len(side.selected)}/4 Pokémon."))
    critical_kinds = {"switch", "move", "faint", "turn"}
    uncertain = [event for event in battle.events if event.kind in critical_kinds and event.confidence < confidence_threshold]
    if uncertain:
        issues.append(
            ReviewIssue(
                "blocking",
                f"Hay {len(uncertain)} eventos críticos por debajo de {confidence_threshold:.0%} de confianza.",
            )
        )
    return tuple(issues)


class ReplayCapturePipeline:
    def __init__(self, source: FrameSource, detector: FrameDetector, seed: CaptureSeed) -> None:
        self.source = source
        self.detector = detector
        self.seed = seed

    def capture(
        self,
        *,
        max_battles: int = 1,
        total_frames: int | None = None,
        on_progress: Callable[[CaptureProgress], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
        max_consecutive_detection_errors: int = 3,
    ) -> tuple[CapturedBattle, ...]:
        if max_battles < 0:
            raise ValueError("max_battles no puede ser negativo; usa 0 para procesar todas las batallas.")
        if max_consecutive_detection_errors < 1:
            raise ValueError("max_consecutive_detection_errors debe ser positivo.")
        captures: list[CapturedBattle] = []
        accumulator = CaptureAccumulator(self.seed)
        awaiting_next_start = False
        started = time.monotonic()
        processed_frames = 0
        skipped_frames = 0
        consecutive_errors = 0

        def reset_detector_battle_state() -> None:
            reset = getattr(self.detector, "reset_battle_state", None)
            if callable(reset):
                reset()

        def report(frame_timestamp_ms: int) -> None:
            if not on_progress:
                return
            completed_events = sum(len(capture.events) for capture in captures)
            on_progress(
                CaptureProgress(
                    processed_frames=processed_frames,
                    total_frames=total_frames,
                    timestamp_ms=frame_timestamp_ms,
                    elapsed_seconds=time.monotonic() - started,
                    events_detected=completed_events + len(accumulator.events),
                    skipped_frames=skipped_frames,
                    battles_detected=len(captures),
                )
            )

        for frame in self.source:
            processed_frames += 1
            try:
                detections = self.detector.detect(frame)
            except DetectionError as error:
                skipped_frames += 1
                consecutive_errors += 1
                if on_warning:
                    on_warning(f"Frame {frame.index + 1} omitido: {error}")
                report(frame.timestamp_ms)
                if consecutive_errors >= max_consecutive_detection_errors:
                    raise DetectionError(
                        f"El detector falló en {consecutive_errors} frames consecutivos; se detuvo para no "
                        f"procesar el vídeo completo sin datos. Último error: {error}"
                    ) from error
                continue
            consecutive_errors = 0
            if awaiting_next_start:
                if not detections.battle_started:
                    reset_detector_battle_state()
                    report(frame.timestamp_ms)
                    continue
                awaiting_next_start = False
            accumulator.apply(detections)
            if accumulator.complete and accumulator.winner and accumulator.has_battle_data:
                captures.append(accumulator.finalize())
                accumulator = CaptureAccumulator(self.seed)
                report(frame.timestamp_ms)
                if max_battles and len(captures) >= max_battles:
                    return tuple(captures)
                awaiting_next_start = True
                reset_detector_battle_state()
                continue
            report(frame.timestamp_ms)

        if not awaiting_next_start and accumulator.winner and accumulator.has_battle_data:
            captures.append(accumulator.finalize())
        if not captures:
            raise CaptureIncompleteError("La fuente terminó sin una batalla completa.")
        return tuple(captures if max_battles == 0 else captures[:max_battles])
