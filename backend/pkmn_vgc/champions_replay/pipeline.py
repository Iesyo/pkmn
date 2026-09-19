from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .detector import FrameDetector
from .models import BattleEvent, BattleSide, CapturedBattle, FrameDetections, SideId, SourceMode
from .sources import FrameSource


class CaptureIncompleteError(RuntimeError):
    """La fuente terminó antes de reunir un combate utilizable."""


@dataclass(frozen=True, slots=True)
class CaptureSeed:
    p1_name: str = "Jugador"
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
            signature = event.signature()
            previous = self._last_event_at.get(signature)
            if previous is not None and event.timestamp_ms - previous <= self.dedupe_window_ms:
                self._last_event_at[signature] = event.timestamp_ms
                continue
            self._last_event_at[signature] = event.timestamp_ms
            self.events.append(event)
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

    def capture(self, *, max_battles: int = 1) -> tuple[CapturedBattle, ...]:
        if max_battles < 1:
            raise ValueError("max_battles debe ser positivo.")
        captures: list[CapturedBattle] = []
        accumulator = CaptureAccumulator(self.seed)
        awaiting_next_start = False
        for frame in self.source:
            detections = self.detector.detect(frame)
            if awaiting_next_start:
                if not detections.battle_started:
                    continue
                accumulator = CaptureAccumulator(self.seed)
                awaiting_next_start = False
            accumulator.apply(detections)
            if accumulator.complete and accumulator.winner and accumulator.has_battle_data:
                captures.append(accumulator.finalize())
                if len(captures) >= max_battles:
                    return tuple(captures)
                awaiting_next_start = True

        if accumulator.winner and accumulator.has_battle_data:
            captures.append(accumulator.finalize())
        if not captures:
            raise CaptureIncompleteError("La fuente terminó sin una batalla completa.")
        return tuple(captures[:max_battles])
