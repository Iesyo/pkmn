from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

SideId = Literal["p1", "p2"]
BattleSlot = Literal["p1a", "p1b", "p2a", "p2b"]
SourceMode = Literal["video", "live", "fixture"]
EventKind = Literal[
    "turn",
    "switch",
    "drag",
    "move",
    "damage",
    "heal",
    "status",
    "curestatus",
    "faint",
    "ability",
    "item",
    "enditem",
    "mega",
    "terastallize",
    "crit",
    "weather",
    "fieldstart",
    "fieldend",
    "sidestart",
    "sideend",
    "message",
]

VALID_SLOTS = {"p1a", "p1b", "p2a", "p2b"}
VALID_EVENT_KINDS = {
    "turn",
    "switch",
    "drag",
    "move",
    "damage",
    "heal",
    "status",
    "curestatus",
    "faint",
    "ability",
    "item",
    "enditem",
    "mega",
    "terastallize",
    "crit",
    "weather",
    "fieldstart",
    "fieldend",
    "sidestart",
    "sideend",
    "message",
}


def _clean_text(value: object, *, limit: int = 120) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("|", " ").strip().split())
    return cleaned[:limit] or None


def _text_id(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _clean_species(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        species = _clean_text(value, limit=80)
        key = _text_id(species or "")
        if not species or not key or key in seen:
            continue
        seen.add(key)
        result.append(species)
    return tuple(result[:6])


@dataclass(frozen=True, slots=True)
class BattleSide:
    name: str
    team: tuple[str, ...]
    selected: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = _clean_text(self.name, limit=80)
        if not name:
            raise ValueError("Cada lado del combate necesita un nombre.")
        team = _clean_species(self.team)
        selected = _clean_species(self.selected)
        canonical = {_text_id(species): species for species in team}
        selected = tuple(canonical.get(_text_id(species), species) for species in selected)
        if not 1 <= len(team) <= 6:
            raise ValueError("Cada lado debe contener entre uno y seis Pokémon conocidos.")
        if len(selected) > 4:
            raise ValueError("Un combate de dobles puede seleccionar como máximo cuatro Pokémon.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "team", team)
        object.__setattr__(self, "selected", selected)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BattleSide:
        return cls(
            name=_clean_text(value.get("name"), limit=80) or "Rival",
            team=_clean_species(value.get("team")),
            selected=_clean_species(value.get("selected"))[:4],
        )


@dataclass(frozen=True, slots=True)
class BattleEvent:
    kind: EventKind
    timestamp_ms: int
    confidence: float = 1.0
    slot: BattleSlot | None = None
    target_slot: BattleSlot | None = None
    species: str | None = None
    forme: str | None = None
    move: str | None = None
    health: str | None = None
    value: str | None = None
    turn: int | None = None
    amount: int | None = None
    tags: tuple[str, ...] = ()
    source_frame: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "species", _clean_text(self.species, limit=80))
        object.__setattr__(self, "forme", _clean_text(self.forme, limit=80))
        object.__setattr__(self, "move", _clean_text(self.move, limit=100))
        object.__setattr__(self, "health", _clean_text(self.health, limit=30))
        object.__setattr__(self, "value", _clean_text(self.value, limit=160))
        object.__setattr__(
            self,
            "tags",
            tuple(tag for item in self.tags if (tag := _clean_text(item, limit=120))),
        )
        if self.kind not in VALID_EVENT_KINDS:
            raise ValueError(f"Evento Champions no reconocido: {self.kind}.")
        if self.timestamp_ms < 0:
            raise ValueError("El timestamp del evento no puede ser negativo.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("La confianza debe estar entre 0 y 1.")
        if self.slot is not None and self.slot not in VALID_SLOTS:
            raise ValueError(f"Slot de combate inválido: {self.slot}.")
        if self.target_slot is not None and self.target_slot not in VALID_SLOTS:
            raise ValueError(f"Slot objetivo inválido: {self.target_slot}.")
        if self.kind == "turn" and (self.turn is None or self.turn < 1):
            raise ValueError("Un evento de turno necesita un número positivo.")
        if self.kind in {"switch", "drag"} and (not self.slot or not self.species):
            raise ValueError("Un cambio necesita slot y especie.")
        if self.kind == "move" and (not self.slot or not self.move):
            raise ValueError("Un movimiento necesita slot y nombre.")
        if self.kind in {"damage", "heal"} and (not self.slot or not self.health):
            raise ValueError("Un cambio de HP necesita slot y lectura de vida.")
        if self.kind in {"status", "curestatus", "ability", "item", "enditem", "terastallize"} and (
            not self.slot or not self.value
        ):
            raise ValueError(f"El evento {self.kind} necesita slot y valor.")
        if self.kind == "mega" and (
            not self.slot or not self.species or not self.forme or not self.value
        ):
            raise ValueError("Una Mega Evolución necesita slot, especie, forma y megapiedra.")
        if self.kind in {"faint", "crit"} and not self.slot:
            raise ValueError(f"El evento {self.kind} necesita un slot.")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, fallback_timestamp_ms: int = 0) -> BattleEvent:
        kind = _clean_text(value.get("kind"), limit=30)
        if kind not in VALID_EVENT_KINDS:
            raise ValueError(f"Evento Champions no reconocido: {kind or 'vacío'}.")
        raw_confidence = value.get("confidence", 1.0)
        raw_timestamp = value.get("timestamp_ms", fallback_timestamp_ms)
        raw_turn = value.get("turn")
        raw_amount = value.get("amount")
        raw_frame = value.get("source_frame")
        raw_tags = value.get("tags")
        return cls(
            kind=kind,  # type: ignore[arg-type]
            timestamp_ms=max(0, int(raw_timestamp or 0)),
            confidence=max(0.0, min(1.0, float(raw_confidence or 0))),
            slot=value.get("slot") if value.get("slot") in VALID_SLOTS else None,
            target_slot=value.get("target_slot") if value.get("target_slot") in VALID_SLOTS else None,
            species=_clean_text(value.get("species"), limit=80),
            forme=_clean_text(value.get("forme"), limit=80),
            move=_clean_text(value.get("move"), limit=100),
            health=_clean_text(value.get("health"), limit=30),
            value=_clean_text(value.get("value"), limit=160),
            turn=int(raw_turn) if raw_turn is not None else None,
            amount=int(raw_amount) if raw_amount is not None else None,
            tags=tuple(
                tag
                for item in raw_tags if (tag := _clean_text(item, limit=120))
            ) if isinstance(raw_tags, (list, tuple)) else (),
            source_frame=int(raw_frame) if raw_frame is not None else None,
        )

    def signature(self) -> tuple[object, ...]:
        """Firma usada para quitar lecturas repetidas del mismo frame/mensaje."""
        return (
            self.kind,
            self.slot,
            self.target_slot,
            self.species,
            self.forme,
            self.move,
            self.health,
            self.value,
            self.turn,
            self.amount,
            self.tags,
        )


@dataclass(frozen=True, slots=True)
class FrameDetections:
    p1_name: str | None = None
    p2_name: str | None = None
    p1_team: tuple[str, ...] = ()
    p2_team: tuple[str, ...] = ()
    p1_selected: tuple[str, ...] = ()
    p2_selected: tuple[str, ...] = ()
    events: tuple[BattleEvent, ...] = ()
    winner: SideId | None = None
    team_preview: bool = False
    battle_started: bool = False
    battle_complete: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, timestamp_ms: int = 0) -> FrameDetections:
        players = value.get("players") if isinstance(value.get("players"), Mapping) else {}
        teams = value.get("teams") if isinstance(value.get("teams"), Mapping) else {}
        selected = value.get("selected") if isinstance(value.get("selected"), Mapping) else {}
        events_value = value.get("events")
        events: list[BattleEvent] = []
        if isinstance(events_value, list):
            for event in events_value:
                if isinstance(event, Mapping):
                    events.append(BattleEvent.from_mapping(event, fallback_timestamp_ms=timestamp_ms))
        winner = value.get("winner")
        return cls(
            p1_name=_clean_text(players.get("p1"), limit=80),
            p2_name=_clean_text(players.get("p2"), limit=80),
            p1_team=_clean_species(teams.get("p1")),
            p2_team=_clean_species(teams.get("p2")),
            p1_selected=_clean_species(selected.get("p1"))[:4],
            p2_selected=_clean_species(selected.get("p2"))[:4],
            events=tuple(events),
            winner=winner if winner in {"p1", "p2"} else None,
            team_preview=bool(value.get("team_preview")),
            battle_started=bool(value.get("battle_started")),
            battle_complete=bool(value.get("battle_complete")),
        )


@dataclass(frozen=True, slots=True)
class CapturedBattle:
    p1: BattleSide
    p2: BattleSide
    events: tuple[BattleEvent, ...]
    winner: SideId
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    format: str = "gen9championsvgc2026regmc"
    source_mode: SourceMode = "video"

    def __post_init__(self) -> None:
        if self.winner not in {"p1", "p2"}:
            raise ValueError("El combate necesita un ganador identificado.")
        if self.started_at.tzinfo is None:
            raise ValueError("La fecha del combate debe incluir zona horaria.")
        if not self.events:
            raise ValueError("El combate no contiene eventos observados.")
        clean_format = _clean_text(self.format, limit=100)
        if not clean_format:
            raise ValueError("El combate necesita un formato.")
        object.__setattr__(self, "format", clean_format)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CapturedBattle:
        raw_started_at = value.get("started_at")
        if isinstance(raw_started_at, str):
            started_at = datetime.fromisoformat(raw_started_at.replace("Z", "+00:00"))
        else:
            started_at = datetime.now(timezone.utc)
        raw_events = value.get("events")
        events = tuple(
            BattleEvent.from_mapping(event)
            for event in raw_events
            if isinstance(event, Mapping)
        ) if isinstance(raw_events, list) else ()
        winner = value.get("winner")
        if winner not in {"p1", "p2"}:
            raise ValueError("El combate necesita winner = p1 o p2.")
        source_mode = value.get("source_mode", "fixture")
        if source_mode not in {"video", "live", "fixture"}:
            source_mode = "fixture"
        return cls(
            p1=BattleSide.from_mapping(value.get("p1") if isinstance(value.get("p1"), Mapping) else {}),
            p2=BattleSide.from_mapping(value.get("p2") if isinstance(value.get("p2"), Mapping) else {}),
            events=events,
            winner=winner,
            started_at=started_at,
            format=_clean_text(value.get("format"), limit=100) or "gen9championsvgc2026regmc",
            source_mode=source_mode,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["started_at"] = self.started_at.isoformat()
        return result


@dataclass(frozen=True, slots=True)
class ReplayDocument:
    log: str
    inputlog: str
    uploadtime: int
    p1: str
    p2: str
    format: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
