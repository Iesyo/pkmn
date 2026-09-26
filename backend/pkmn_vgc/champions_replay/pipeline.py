from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

from .detector import DetectionError, FrameDetector
from .models import (
    BattleEvent,
    BattleSide,
    CapturedBattle,
    FrameDetections,
    SideId,
    SourceMode,
    is_actor_identity,
)
from .sources import FramePacket, FrameSource


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
    # Valor que tenía la barra justo antes del último reemplazo dentro de una
    # misma racha de daño o cura -no antes de que la racha empezara, antes de
    # su lectura más reciente. Ver el chequeo de reversión en `apply`.
    _pre_replace_health: dict[tuple[object, ...], str] = field(default_factory=dict)
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
        if detections.team_preview:
            if len(detections.p1_selected) >= len(self.p1_selected):
                self.p1_selected[:] = detections.p1_selected
            if len(detections.p2_selected) >= len(self.p2_selected):
                self.p2_selected[:] = detections.p2_selected
        else:
            _merge_species(self.p1_selected, detections.p1_selected, limit=4)
            _merge_species(self.p2_selected, detections.p2_selected, limit=4)

        for event in detections.events:
            if event.slot and event.species and not is_actor_identity(event.species):
                selected = self.p1_selected if event.slot.startswith("p1") else self.p2_selected
                team = self.p1_team if event.slot.startswith("p1") else self.p2_team
                _merge_species(selected, (event.species,), limit=4)
                _merge_species(team, (event.species,), limit=6)
            if event.kind in {"damage", "heal"} and self._settle_late_reading(event):
                continue
            if event.kind in {"damage", "heal"}:
                health_key = (event.kind, event.slot, event.species)
                previous_health = self._last_health_event.get(health_key)
                if previous_health and event.timestamp_ms - previous_health[1] <= 1_500:
                    previous_index = previous_health[0]
                    previous_event = self.events[previous_index]
                    if previous_event.health:
                        self._pre_replace_health[health_key] = previous_event.health
                    self._last_event_at.pop(previous_event.signature(), None)
                    self.events[previous_index] = event
                    self._last_event_at[event.signature()] = event.timestamp_ms
                    self._last_health_event[health_key] = (previous_index, event.timestamp_ms)
                    continue
                # COL-102, job 82923f56ce264a92: un solo frame de OCR malo
                # leyó "3%" en medio de una racha estable en 28% -el HUD ya
                # se había corregido solo un frame después, pero como el
                # valor siguiente ("28% de nuevo") es una cura y no un daño,
                # no calzaba con la fusión de arriba (que sólo junta lecturas
                # del mismo tipo) y quedaba como una cura real que nunca
                # pasó, encima del daño equivocado. Cuando el tipo contrario
                # tiene una lectura reciente cuyo valor previo al último
                # reemplazo es exactamente éste, el HUD ya se había corregido
                # solo un frame antes: se restaura el evento de antes a ese
                # valor -el daño real sí ocurrió, sólo que no hasta ahí- y
                # éste, que ya no representaría ningún cambio real, no se
                # escribe.
                opposite_kind = "heal" if event.kind == "damage" else "damage"
                opposite_key = (opposite_kind, event.slot, event.species)
                opposite_health = self._last_health_event.get(opposite_key)
                if opposite_health and event.timestamp_ms - opposite_health[1] <= 1_500:
                    opposite_index, _opposite_ts = opposite_health
                    reverted_value = self._pre_replace_health.get(opposite_key)
                    if reverted_value is not None and event.health == reverted_value:
                        opposite_event = self.events[opposite_index]
                        self._last_event_at.pop(opposite_event.signature(), None)
                        self.events[opposite_index] = replace(opposite_event, health=reverted_value)
                        self._last_health_event.pop(opposite_key, None)
                        self._pre_replace_health.pop(opposite_key, None)
                        continue
            signature = event.signature()
            previous = self._last_event_at.get(signature)
            if previous is not None and event.timestamp_ms - previous <= self.dedupe_window_ms:
                self._last_event_at[signature] = event.timestamp_ms
                continue
            self._last_event_at[signature] = event.timestamp_ms
            if event.kind == "faint":
                self._close_last_hit(event, 0)
            elif event.kind == "enditem" and event.value == "Focus Sash":
                self._close_last_hit(event, 1)
            self.events.append(event)
            if event.kind in {"damage", "heal"}:
                self._last_health_event[(event.kind, event.slot, event.species)] = (
                    len(self.events) - 1,
                    event.timestamp_ms,
                )
            if event.kind == "turn":
                self._turn_seen = True
            if event.kind in {"switch", "drag"} and event.slot and event.species:
                if not self._turn_seen:
                    lead = self.p1_lead if event.slot.startswith("p1") else self.p2_lead
                    _merge_species(lead, (event.species,), limit=2)

        if detections.winner:
            self.winner = detections.winner
        self.complete = self.complete or detections.battle_complete

    def _settle_late_reading(self, event: BattleEvent) -> bool:
        """Una barra leída entre el inicio de un turno y su primera acción.

        COL-102, job 8b7488cb5914449f, partida 2: Terrain Pulse deja a
        Venusaur en "1 %" en el turno 1, pero ese 1 es un dígito fino y el OCR
        no lo lee hasta el frame 1392, ya con el turno 2 abierto y antes de
        ningún movimiento. Entraba como un daño nuevo, sin causa, y el visor
        lo animaba como un segundo golpe. Entre el turno y su primera acción
        no puede haber daño ni cura reales: es el final del último cambio de
        ese Pokémon en el turno anterior, y corrige su valor. Si no hay tal
        cambio (sin cruzar su entrada o su debilitado), no se toca nada.
        """

        turn_index = None
        for index in range(len(self.events) - 1, -1, -1):
            kind = self.events[index].kind
            if kind == "turn":
                turn_index = index
                break
            if kind not in {"damage", "heal"}:
                return False
        if turn_index is None:
            return False
        for index in range(turn_index - 1, -1, -1):
            previous = self.events[index]
            if previous.slot != event.slot:
                continue
            if previous.kind in {"switch", "drag", "faint"}:
                return False
            if previous.kind in {"damage", "heal"}:
                if previous.kind != event.kind or previous.species != event.species:
                    return False
                if previous.health != event.health:
                    self._last_event_at.pop(previous.signature(), None)
                    self.events[index] = replace(previous, health=event.health)
                    self._last_event_at[self.events[index].signature()] = previous.timestamp_ms
                return True
        return False

    def _close_last_hit(self, outcome: BattleEvent, remaining: int) -> None:
        """El golpe que precede a un debilitado o a un Focus Sash cierra su barra.

        COL-102, job 5748b289aa5b445b, turnos 4 y 5: las barras de Dragonite
        y Sinistcha llegan a "0 %", pero ese 0 es un solo dígito fino y el
        OCR lo pierde (lee "%" suelto, o "0" sin su "%"). Quedaba la lectura
        de mitad de animación: Zap Cannon salía como un golpe del 3 % en vez
        del 29 %. "Fainted" dice por sí solo que la vida llegó a 0, así que
        el último daño de ese Pokémon dentro de la misma acción se cierra en
        0. No se inventa ningún golpe: si no se leyó ninguno, no se toca nada.

        COL-102, job 4eb88ad277cf4546, turno 1: el Focus Sash de Ceruledge
        deja la barra en "1 %" y el OCR pierde ese 1 igual que el 0; quedaba
        el 69 % de mitad de animación. "Hung on using its Focus Sash!" sólo
        ocurre si el golpe deja 1 PS, así que ese golpe se cierra en 1.
        """

        for index in range(len(self.events) - 1, -1, -1):
            previous = self.events[index]
            if previous.kind in {"move", "switch", "drag", "turn"}:
                return
            # Por slot: sin un cambio de por medio (ver arriba) es el mismo
            # Pokémon, aunque uno de los dos eventos lo nombre por su mote.
            if previous.kind == "damage" and previous.slot == outcome.slot and previous.health:
                _current, _sep, maximum = previous.health.partition("/")
                closed = f"{remaining}/{maximum or 100}"
                if previous.health != closed:
                    self._last_event_at.pop(previous.signature(), None)
                    self.events[index] = replace(previous, health=closed)
                    self._last_event_at[self.events[index].signature()] = previous.timestamp_ms
                return

    def _drop_ghost_reentries(self) -> None:
        """Una identidad sin resolver que entra y se debilita al instante
        no es un Pokémon nuevo: es la propia animación de un debilitado ya
        en curso.

        COL-102, reapertura estructural del 25 sep, job `90403f16712d4d41`,
        partida 3: Indeedee-F llega a 0 PS; 1,5 s después una identidad sin
        resolver "entra" a su mismo slot y se debilita en el mismo frame
        -el HUD perdió el ícono un instante en plena animación de
        debilitado y lo leyó como una entrada nueva en vez de reconocer
        que seguía siendo Indeedee-F. Sin esto, el replay final mostraba a
        Indeedee-F debilitarse, "volver a entrar" a 0 PS con otro nombre, y
        debilitarse otra vez.
        """

        last_zero_at: dict[str, int] = {}
        pending_ghost: dict[str, str] = {}
        drop: set[int] = set()
        for index, event in enumerate(self.events):
            slot = event.slot
            if not slot:
                continue
            if event.kind in {"damage", "heal"} and event.health:
                current, _, _ = event.health.partition("/")
                if current == "0":
                    last_zero_at[slot] = event.timestamp_ms
                else:
                    last_zero_at.pop(slot, None)
                continue
            if event.kind in {"switch", "drag"}:
                zero_ts = last_zero_at.get(slot)
                if (
                    zero_ts is not None
                    and is_actor_identity(event.species)
                    and event.timestamp_ms - zero_ts <= 2_000
                ):
                    drop.add(index)
                    pending_ghost[slot] = event.species or ""
                else:
                    last_zero_at.pop(slot, None)
                    pending_ghost.pop(slot, None)
                continue
            if event.kind == "faint":
                ghost_species = pending_ghost.pop(slot, None)
                if ghost_species is not None and event.species == ghost_species:
                    drop.add(index)
                continue

        if drop:
            self.events = [event for index, event in enumerate(self.events) if index not in drop]

    def _reconcile_zero_hp(self) -> None:
        """0 PS o es un debilitado o fue ruido de OCR; nunca las dos cosas.

        COL-102, reapertura estructural del 25 sep: tres jobs, tres formas
        del mismo hueco -ninguna lectura de HP se contrastaba contra si el
        Pokémon seguía con vida. Los parches puntuales que ya existen
        (`_settle_late_reading`, `_close_last_hit`) corrigen un dígito fino
        perdido en un extremo de la barra, anclados a un turno o a un
        `faint` que ya llegó; ninguno cubre estos tres:

        - Archaludon (90403f16712d4d41): `-damage|0/100` real, seguido de un
          `-heal|9/100` fantasma -ruido de la animación del golpe final-
          antes de su `faint` real. `_close_last_hit` corrige la ÚLTIMA
          lectura de daño antes del faint, pero no toca la curación
          fantasma que quedó de por medio: sobrevivía en el replay.
        - Milotic (10a7fba6fda04585, partida 4, turno 8): `-damage|0/100`
          real tras un golpe superefectivo confirmado por mensaje, pero
          "fainted!" nunca se leyó -ningún otro evento vuelve a tocar ese
          slot en el resto de la batalla. Sin faint, el Pokémon queda
          "en pie" a 0 PS para siempre.
        - Salamence (331e6e783c3e45a4, partida 3): `-damage|0/100` que en
          realidad fue un dígito perdido -no hay faint, y más tarde el
          mismo Pokémon se cura, prueba de que nunca dejó de estar en pie.

        Con la batalla completa ya capturada, se puede distinguir de verdad
        entre las tres: por cada slot, entre un `switch`/`drag` (o el
        principio) y el siguiente, si su primera lectura en 0 PS tiene
        después una lectura de vida real (>0) antes de cualquier `faint`,
        todo lo leído desde ese 0 hasta la lectura real (sin incluirla) fue
        ruido -el Pokémon nunca dejó de estar en pie, como Salamence. Si en
        cambio nunca vuelve a mostrar vida real, el 0 fue real: se
        descarta el ruido posterior y, si ningún `faint` lo confirmó por
        texto, se sintetiza uno -como haría falta para Milotic- justo
        detrás de esa primera lectura; si sí llegó (Archaludon), sólo se
        limpia el ruido de por medio y el faint real queda igual.
        """

        segment_readings: dict[str, list[tuple[int, int]]] = {}
        drop: set[int] = set()
        insert_faint_after: dict[int, str] = {}

        def close_segment(slot: str, *, faint: bool) -> None:
            readings = segment_readings.get(slot) or []
            zero_positions = [pos for pos, (_idx, health) in enumerate(readings) if health == 0]
            if not zero_positions:
                return
            first_zero_pos = zero_positions[0]
            first_zero_index, _ = readings[first_zero_pos]
            if faint:
                # El debilitado real es la verdad final, por encima de
                # cualquier lectura intermedia que lo contradiga: todo lo
                # leído entre la primera lectura en 0 (que se conserva) y
                # el faint fue ruido de su propia animación (Archaludon).
                for idx, _health in readings[first_zero_pos + 1 :]:
                    drop.add(idx)
                return
            revival = next(
                (
                    (idx, health)
                    for idx, health in readings[first_zero_pos + 1 :]
                    if health > 0
                ),
                None,
            )
            if revival is not None:
                for idx, _health in readings[first_zero_pos:]:
                    if idx == revival[0]:
                        break
                    drop.add(idx)
                return
            for idx, _health in readings[first_zero_pos + 1 :]:
                drop.add(idx)
            insert_faint_after[first_zero_index] = self.events[first_zero_index].species or ""

        for index, event in enumerate(self.events):
            slot = event.slot
            if not slot:
                continue
            if event.kind in {"switch", "drag"}:
                close_segment(slot, faint=False)
                segment_readings[slot] = []
                continue
            if event.kind == "faint":
                close_segment(slot, faint=True)
                segment_readings[slot] = []
                continue
            if event.kind in {"damage", "heal"} and event.health:
                current, _, _ = event.health.partition("/")
                if current.isdigit():
                    segment_readings.setdefault(slot, []).append((index, int(current)))

        for slot in list(segment_readings):
            close_segment(slot, faint=False)

        if not drop and not insert_faint_after:
            return

        reconciled: list[BattleEvent] = []
        for index, event in enumerate(self.events):
            if index in drop:
                continue
            reconciled.append(event)
            species = insert_faint_after.get(index)
            if species is not None:
                reconciled.append(
                    BattleEvent(
                        kind="faint",
                        timestamp_ms=event.timestamp_ms,
                        confidence=event.confidence,
                        slot=event.slot,
                        species=species,
                        source_frame=event.source_frame,
                    )
                )
        self.events = reconciled

    def finalize(self, identities: Mapping[str, str] | None = None) -> CapturedBattle:
        self._drop_ghost_reentries()
        self._reconcile_zero_hp()
        if not self.winner:
            raise CaptureIncompleteError("No se pudo identificar el resultado de la batalla.")
        if not self.events:
            raise CaptureIncompleteError("No se detectaron eventos de batalla.")
        identity_map = dict(identities or {})

        def resolved(values: Iterable[str]) -> tuple[str, ...]:
            return tuple(identity_map.get(value, value) for value in values)

        def ordered_selection(lead: list[str], selected: list[str]) -> tuple[str, ...]:
            ordered: list[str] = []
            _merge_species(
                ordered,
                (species for species in resolved(lead) if not is_actor_identity(species)),
                limit=4,
            )
            _merge_species(ordered, resolved(selected), limit=4)
            return tuple(ordered)

        def completed_team(side: str, current: list[str], lead: list[str]) -> tuple[str, ...]:
            canonical_lead = [
                species for species in resolved(lead) if not is_actor_identity(species)
            ]
            current_keys = {
                "".join(character for character in species.lower() if character.isalnum())
                for species in current
            }
            missing_leads = [
                species
                for species in canonical_lead
                if "".join(character for character in species.lower() if character.isalnum())
                not in current_keys
            ]
            completed = [*missing_leads, *current]
            for event in self.events:
                if not event.slot or not event.slot.startswith(side) or not event.species:
                    continue
                species = identity_map.get(event.species, event.species)
                if not is_actor_identity(species):
                    _merge_species(completed, (species,), limit=6)
            return tuple(completed)

        p1_team = completed_team("p1", self.p1_team, self.p1_lead)
        p2_team = completed_team("p2", self.p2_team, self.p2_lead)
        if not p1_team or not p2_team:
            raise CaptureIncompleteError(
                "No se pudo reconstruir el Team Preview de ambos jugadores."
            )

        return CapturedBattle(
            p1=BattleSide(
                self.p1_name,
                p1_team,
                ordered_selection(self.p1_lead, self.p1_selected),
            ),
            p2=BattleSide(
                self.p2_name,
                p2_team,
                ordered_selection(self.p2_lead, self.p2_selected),
            ),
            events=tuple(self.events),
            winner=self.winner,
            identities=tuple(identity_map.items()),
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
            issues.append(
                ReviewIssue(
                    "warning",
                    f"El roster reconstruido del {label} contiene {len(side.team)}/6 Pokémon.",
                )
            )
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


def risk_windows(
    battles: Iterable[CapturedBattle],
    *,
    margin_ms: int = 3_000,
    low_health_ratio: float = 0.15,
) -> tuple[tuple[int, int], ...]:
    """Instantes de vídeo donde vale la pena volver a leer más denso.

    COL-102, reapertura estructural del 25 sep: cada bug de esta ronda
    (Kingambit, Salamence, Archaludon, Milotic, Rillaboom, Indeedee-F,
    Zoroark) nació en el mismo tipo de instante -un `faint`, un
    `switch`/`drag`, o una barra de HP cerca de 0- donde una sola lectura
    de OCR mala, sin otra vecina con la que contrastarla a 2 fps, bastaba
    para torcer el replay. La inmensa mayoría de una batalla es estable
    -nada de eso pasa- así que releer todo el vídeo más denso desperdicia
    la mayor parte del tiempo extra en tramos que ya salen bien. Esto marca
    sólo los instantes de riesgo real, con un margen a cada lado para cubrir
    la animación completa alrededor -no cada `turn`: el propio parpadeo de
    fase de Team Preview/menú (COL-102, commit `868ac09`) ya se corrigió de
    raíz y no depende de la densidad de muestreo.

    Devuelve rangos (inicio_ms, fin_ms) ya fusionados y ordenados, listos
    para pasarle a una fuente de vídeo que sólo re-muestree esos tramos.
    """

    marks: list[int] = []
    for battle in battles:
        for event in battle.events:
            if event.kind in {"faint", "switch", "drag"}:
                marks.append(event.timestamp_ms)
                continue
            if event.kind in {"damage", "heal"} and event.health:
                current, _, maximum = event.health.partition("/")
                if current.isdigit() and maximum.isdigit() and int(maximum) > 0:
                    if int(current) / int(maximum) <= low_health_ratio:
                        marks.append(event.timestamp_ms)
    if not marks:
        return ()

    marks.sort()
    windows: list[list[int]] = []
    for mark in marks:
        start, end = max(0, mark - margin_ms), mark + margin_ms
        if windows and start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return tuple((start, end) for start, end in windows)


class ReplayCapturePipeline:
    def __init__(self, source: FrameSource, detector: FrameDetector, seed: CaptureSeed) -> None:
        self.source = source
        self.detector = detector
        self.seed = seed

    def _detection_tasks(self) -> Iterable[tuple[FramePacket, Callable[[], FrameDetections]]]:
        """Lee y analiza cada frame antes de avanzar al siguiente.

        El parser mantiene estado entre frames (slots, aliases, turnos y HP),
        por lo que una única secuencia es más importante que el throughput.
        """

        try:
            for frame in self.source:
                yield frame, lambda frame=frame: self.detector.detect(frame)
        finally:
            close = getattr(self.detector, "close", None)
            if callable(close):
                close()

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
        incomplete_battles = 0

        def reset_detector_battle_state() -> None:
            reset = getattr(self.detector, "reset_battle_state", None)
            if callable(reset):
                reset()

        def flush_detector_pending() -> None:
            flush = getattr(self.detector, "flush_pending", None)
            if callable(flush):
                pending_detections = flush()
                if isinstance(pending_detections, FrameDetections):
                    accumulator.apply(pending_detections)
            pop_warnings = getattr(self.detector, "pop_warnings", None)
            if callable(pop_warnings) and on_warning:
                for warning in pop_warnings():
                    on_warning(warning)

        def resolved_identities() -> Mapping[str, str]:
            resolve = getattr(self.detector, "resolved_identities", None)
            if not callable(resolve):
                return {}
            identities = resolve()
            return identities if isinstance(identities, Mapping) else {}

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

        for frame, resolve_detection in self._detection_tasks():
            processed_frames += 1
            try:
                detections = resolve_detection()
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
            pop_warnings = getattr(self.detector, "pop_warnings", None)
            if callable(pop_warnings) and on_warning:
                for warning in pop_warnings():
                    on_warning(warning)
            if awaiting_next_start:
                if detections.team_preview:
                    accumulator.apply(detections)
                    report(frame.timestamp_ms)
                    continue
                if not detections.battle_started:
                    report(frame.timestamp_ms)
                    continue
                awaiting_next_start = False
            accumulator.apply(detections)
            if accumulator.complete and accumulator.winner and accumulator.has_battle_data:
                flush_detector_pending()
                try:
                    capture = accumulator.finalize(resolved_identities())
                except (CaptureIncompleteError, ValueError) as error:
                    incomplete_battles += 1
                    if on_warning:
                        on_warning(
                            f"Cierre de batalla descartado ({incomplete_battles}): {error} "
                            "El análisis continuará buscando la siguiente batalla."
                        )
                    accumulator = CaptureAccumulator(self.seed)
                    awaiting_next_start = True
                    reset_detector_battle_state()
                    report(frame.timestamp_ms)
                    continue
                captures.append(capture)
                accumulator = CaptureAccumulator(self.seed)
                report(frame.timestamp_ms)
                if max_battles and len(captures) >= max_battles:
                    return tuple(captures)
                awaiting_next_start = True
                reset_detector_battle_state()
                continue
            report(frame.timestamp_ms)

        if not awaiting_next_start and accumulator.winner and accumulator.has_battle_data:
            flush_detector_pending()
            captures.append(accumulator.finalize(resolved_identities()))
        if not captures:
            raise CaptureIncompleteError("La fuente terminó sin una batalla completa.")
        return tuple(captures if max_battles == 0 else captures[:max_battles])
