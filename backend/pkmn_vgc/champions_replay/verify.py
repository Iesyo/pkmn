"""Contrasta un replay generado contra el texto que el juego puso en pantalla.

El replay se construye a partir del OCR, así que la pregunta que importa no es
si el log es válido para Showdown, sino si dice exactamente lo que se leyó. Aquí
se cruzan las dos cosas y se reporta lo que sobra y lo que falta.

Dos cuidados para que la comparación no invente diferencias:

* el OCR repite el mismo mensaje durante varios frames y a menudo con lecturas
  algo distintas del mote, así que un mensaje cuenta una vez por racha de
  frames, agrupando por el evento y no por el texto literal;
* el juego escribe motes y el replay escribe especies, así que los motes se
  traducen con el mapa de alias que el propio detector dejó en la traza.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

# "The opposing" a veces sale pegado al mote, sin el espacio.
_USED = re.compile(r"^(?P<opponent>The opposing ?)?(?P<actor>.+?) used (?P<move>[A-Z][A-Za-z' \-]+)!$")
_FAINTED = re.compile(r"^(?P<opponent>The opposing ?)?(?P<actor>.+?) fainted!$")
_MEGA = re.compile(
    r"^(?P<opponent>The opposing ?)?(?P<actor>.+?) has Mega Evolved into Mega "
    r"(?P<species>[A-Za-z\- ]+)!$"
)

_MOVE_LINE = re.compile(r"^\|move\|(?P<slot>p[12][ab]): (?P<species>[^|]+)\|(?P<move>[^|]+)\|")
_POKE_LINE = re.compile(r"^\|poke\|(?P<side>p[12])\|(?P<species>[^,|]+)")
_FAINT_LINE = re.compile(r"^\|faint\|(?P<slot>p[12][ab]): (?P<species>.+)$")
_MEGA_LINE = re.compile(r"^\|-mega\|(?P<slot>p[12][ab]): (?P<species>[^|]+)\|")

_GAP_FRAMES = 4
_SIMILAR = 0.80


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


@dataclass(frozen=True, slots=True)
class Difference:
    kind: str
    side: str
    value: str
    times: int


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Cuánto del replay está respaldado por la pantalla, y al revés."""

    on_screen: int
    in_replay: int
    matched: int
    missing: tuple[Difference, ...]
    invented: tuple[Difference, ...]
    rosters: tuple[str, ...] = ()

    @property
    def faithful(self) -> bool:
        return not self.invented and not self.missing and not self.rosters


def _screen_events(trace: Path, battle_index: int) -> tuple[Counter, dict[str, str]]:
    aliases: dict[str, str] = {}
    frames: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for raw in trace.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        if record.get("battle_index") != battle_index:
            continue
        for table in (record.get("resolved_aliases") or {}).values():
            for alias, species in table.items():
                aliases[_key(alias)] = species
        frame = record.get("frame", 0)
        for entry in record.get("ocr") or ():
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            for pattern, kind, group in (
                (_USED, "move", "move"),
                (_FAINTED, "faint", "actor"),
                (_MEGA, "mega", "species"),
            ):
                match = pattern.match(text)
                if not match:
                    continue
                side = "p2" if match["opponent"] else "p1"
                frames[(kind, side, match[group].strip())].append(frame)
                break

    # El OCR a veces corta el nombre a media palabra ("Weather Ba"). Esa lectura
    # es el mismo mensaje, no otro, así que se pliega sobre la versión completa
    # antes de contar apariciones; sólo se pliega si una es prefijo de la otra.
    for short in sorted(frames, key=lambda item: len(item[2])):
        for long in frames:
            if short == long or short[:2] != long[:2]:
                continue
            if len(_key(short[2])) < len(_key(long[2])) and _key(long[2]).startswith(_key(short[2])):
                frames[long].extend(frames.pop(short))
                break

    occurrences: Counter = Counter()
    for event, seen in frames.items():
        seen.sort()
        runs = 1 + sum(1 for a, b in zip(seen, seen[1:]) if b - a > _GAP_FRAMES)
        occurrences[event] = runs
    return occurrences, aliases


def _species_for(nickname: str, aliases: dict[str, str]) -> str:
    exact = aliases.get(_key(nickname))
    if exact:
        return exact
    best, score = nickname, _SIMILAR
    for alias, species in aliases.items():
        ratio = SequenceMatcher(None, _key(nickname), alias).ratio()
        if ratio > score:
            best, score = species, ratio
    return best


def _replay_rosters(log: Path) -> dict[str, list[str]]:
    rosters: dict[str, list[str]] = {"p1": [], "p2": []}
    for line in log.read_text(encoding="utf-8").splitlines():
        match = _POKE_LINE.match(line)
        if match:
            rosters[match["side"]].append(match["species"].strip())
    return rosters


def _named_on_screen(trace: Path, battle_index: int, species_names: Iterable[str]) -> dict[str, set[str]]:
    """Especies que el juego escribió, por lado.

    Sólo cuenta lo que se puede atribuir sin dudas: "the opposing X" es del
    rival y "Go! X" del jugador, y un "<entrenador> sent out X" va al lado de
    ese entrenador. Lo que no se pueda atribuir se descarta, porque una falsa
    alarma aquí es peor que un hueco.
    """

    lookup = {_key(name): name for name in species_names}
    named: dict[str, set[str]] = {"p1": set(), "p2": set()}
    players: dict[str, str] = {}
    for raw in trace.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        if record.get("battle_index") != battle_index:
            continue
        for side, name in ((record.get("detections") or {}).get("players") or {}).items():
            if name:
                players[side] = name
        for entry in record.get("ocr") or ():
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            side = _side_of_message(text, players)
            if side is None:
                continue
            for word in re.findall(r"[A-Z][A-Za-z'\-]{3,}", text):
                species = lookup.get(_key(word))
                if species:
                    named[side].add(species)
    return named


def _side_of_message(text: str, players: dict[str, str]) -> str | None:
    if re.search(r"\bthe opposing\b", text, re.IGNORECASE):
        return "p2"
    if re.match(r"\s*go!", text, re.IGNORECASE):
        return "p1"
    for side in ("p1", "p2"):
        # _key descarta lo que no sea ASCII, así que un nombre en japonés queda
        # en cadena vacía y casaría con cualquier texto: hay que exigir que
        # quede algo con lo que comparar.
        name = _key(players.get(side) or "")
        if name and _key(text).startswith(name):
            return side
    return None


def _roster_problems(
    rosters: dict[str, list[str]],
    named: dict[str, set[str]],
) -> tuple[str, ...]:
    problems: list[str] = []
    for side, team in rosters.items():
        if not team:
            continue
        if len(team) != len(set(team)):
            problems.append(f"{side} repite alguna especie en el roster: {team}")
        bases = {_base_species(species) for species in team}
        # El texto usa el nombre base ("Indeedee" para la hembra también), así
        # que se compara por base: esto caza una especie equivocada, no una
        # forma equivocada.
        for species in sorted(named[side]):
            if _base_species(species) not in bases:
                problems.append(
                    f"{side}: el juego nombró a {species} y no está en el roster {team}"
                )
    return tuple(problems)


def _base_species(species: str) -> str:
    return _key(species.split("-", 1)[0])


def _replay_events(log: Path) -> Counter:
    events: Counter = Counter()
    for line in log.read_text(encoding="utf-8").splitlines():
        for pattern, kind, group in (
            (_MOVE_LINE, "move", "move"),
            (_FAINT_LINE, "faint", "species"),
            (_MEGA_LINE, "mega", "species"),
        ):
            match = pattern.match(line)
            if match:
                value = match[group].strip().split(",")[0]
                events[(kind, match["slot"][:2], value)] += 1
                break
    return events


def _pair_up(screen: Counter, replay: Counter) -> tuple[int, Counter, Counter]:
    screen, replay = Counter(screen), Counter(replay)
    matched = 0
    for event in list(screen):
        take = min(screen[event], replay[event])
        if take:
            matched += take
            screen[event] -= take
            replay[event] -= take
    # El OCR recorta nombres ("Weather Ba" por "Weather Ball"): se emparejan por
    # parecido antes de declarar una diferencia.
    for event in [item for item, count in screen.items() if count > 0]:
        for other in [item for item, count in replay.items() if count > 0]:
            if event[0] != other[0] or event[1] != other[1]:
                continue
            if SequenceMatcher(None, _key(event[2]), _key(other[2])).ratio() >= _SIMILAR:
                take = min(screen[event], replay[other])
                matched += take
                screen[event] -= take
                replay[other] -= take
    return matched, +screen, +replay


def verify_replay(
    trace: Path,
    log: Path,
    *,
    battle_index: int = 0,
    species_names: Iterable[str] = (),
) -> VerificationReport:
    occurrences, aliases = _screen_events(trace, battle_index)
    screen: Counter = Counter()
    for (kind, side, value), times in occurrences.items():
        resolved = _species_for(value, aliases) if kind == "faint" else value
        screen[(kind, side, resolved)] += times
    replay = _replay_events(log)
    matched, missing, invented = _pair_up(screen, replay)
    rosters = _replay_rosters(log)
    problems = (
        _roster_problems(rosters, _named_on_screen(trace, battle_index, species_names))
        if species_names
        else ()
    )
    return VerificationReport(
        rosters=problems,
        on_screen=sum(screen.values()),
        in_replay=sum(replay.values()),
        matched=matched,
        missing=tuple(
            Difference(kind, side, value, times)
            for (kind, side, value), times in sorted(missing.items())
        ),
        invented=tuple(
            Difference(kind, side, value, times)
            for (kind, side, value), times in sorted(invented.items())
        ),
    )


def describe(report: VerificationReport) -> Iterable[str]:
    yield (
        f"en pantalla {report.on_screen} | en el replay {report.in_replay} | "
        f"emparejados {report.matched}"
    )
    for label, items in (("no llegaron al replay", report.missing), ("sin respaldo en pantalla", report.invented)):
        for item in items:
            yield f"  {label}: x{item.times} {item.side} {item.kind} {item.value}"
    for problem in report.rosters:
        yield f"  roster: {problem}"
