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
_TURN_LINE = re.compile(r"^\|turn\|(?P<number>\d+)$")
_HP_LINE = re.compile(
    r"^\|-(?P<kind>damage|heal)\|(?P<slot>p[12][ab]): (?P<species>[^|]+)\|(?P<current>\d+)/(?P<max>\d+)"
)
_ENTRY_LINE = re.compile(r"^\|(?:switch|drag)\|(?P<slot>p[12][ab]): ")
_SLOT_SPECIES_LINE = re.compile(r"^\|[A-Za-z-]+\|(?P<slot>p[12][ab]): (?P<species>[^|]+)")
_STATUS_LINE = re.compile(r"^\|-status\|(?P<slot>p[12][ab]): [^|]+\|(?P<status>\w+)")
_CURESTATUS_LINE = re.compile(r"^\|-curestatus\|(?P<slot>p[12][ab]): ")
_PLAYER_P2_LINE = re.compile(r"^\|player\|p2\|(?P<name>[^|]*)\|")

_GAP_FRAMES = 4
_SIMILAR = 0.80


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def resolve_battle_index(trace: Path, log: Path) -> tuple[int | None, str | None]:
    """Ubica a qué `battle_index` de la traza corresponde este replay.

    COL-102, reapertura estructural del 26 sep: `cli.py` asumía que el
    replay N-ésimo (por orden de archivo, `replay-001`, `replay-002`...)
    es la N-ésima batalla de la traza (`enumerate(logs)`). Eso se rompe en
    cuanto una batalla de en medio se descarta y nunca llega a producir su
    propio `.log`. Confirmado contra el job real `90403f16712d4d41`:
    `replay-003.log` es la batalla de `scarlat`, `battle_index=3`, porque
    la de `Warrior96` (`battle_index=2`) se descartó -no es la tercera por
    orden de archivo. Verificar con el índice equivocado compara el
    replay contra el roster y los eventos de otra batalla por completo:
    18 diferencias inventadas y 18 perdidas, contra 1 real con el índice
    correcto.

    Se identifica la batalla por el nombre de jugador p2 que el propio
    replay escribió (`|player|p2|<nombre>|`), buscado entre las
    detecciones de la traza; ambigüedad o ausencia se reporta en vez de
    adivinar -esto es evidencia externa al conteo de eventos, así que no
    puede fallar por la misma razón que el propio verificador.
    """

    lines = log.read_text(encoding="utf-8").splitlines()
    match = next((m for line in lines if (m := _PLAYER_P2_LINE.match(line))), None)
    name = match["name"].strip() if match else ""
    if not name:
        return None, "el replay no declara su jugador p2 (falta |player|p2|...|)"

    votes: dict[int, Counter[str]] = defaultdict(Counter)
    for raw in trace.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        battle_index = record.get("battle_index")
        if battle_index is None:
            continue
        p2 = ((record.get("detections") or {}).get("players") or {}).get("p2")
        if p2:
            votes[battle_index][_key(str(p2))] += 1

    target = _key(name)
    matches = sorted(
        index for index, counted in votes.items() if counted and counted.most_common(1)[0][0] == target
    )
    if len(matches) != 1:
        return None, f"origen ambiguo para p2={name!r}: batallas candidatas {matches}"
    return matches[0], None


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


def _screen_events(
    trace: Path, battle_index: int
) -> tuple[Counter, dict[str, str], tuple[tuple[str, str, str, int], ...]]:
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
    sequence: list[tuple[str, str, str, int]] = []
    for event, seen in frames.items():
        seen.sort()
        run_starts = [seen[0]] + [b for a, b in zip(seen, seen[1:]) if b - a > _GAP_FRAMES]
        occurrences[event] = len(run_starts)
        kind, side, value = event
        sequence.extend((kind, side, value, start) for start in run_starts)
    sequence.sort(key=lambda item: item[3])
    return occurrences, aliases, tuple(sequence)


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


def _turn_problems(log: Path) -> tuple[str, ...]:
    """Un turno vacío antes del final no es un replay fiel, aunque nada se
    haya perdido: sus eventos reales quedaron corriendo bajo el turno
    siguiente (COL-102). El último turno queda fuera porque el vídeo puede
    cortar la grabación justo después de su marcador, antes de que el HUD
    muestre ninguna acción.
    """

    lines = log.read_text(encoding="utf-8").splitlines()
    turn_indexes = [index for index, text in enumerate(lines) if _TURN_LINE.match(text)]
    problems: list[str] = []
    for position, index in enumerate(turn_indexes[:-1]):
        if turn_indexes[position + 1] == index + 1:
            number = _TURN_LINE.match(lines[index])["number"]
            problems.append(f"turno {number} vacío: nada entre su |turn| y el siguiente")
    return tuple(problems)


def _hp_zero_without_faint_problems(log: Path) -> tuple[str, ...]:
    """Un Pokémon en 0 PS que sigue actuando o curándose no estuvo ahí de verdad.

    COL-102, reapertura estructural del 25 sep, job `331e6e783c3e45a4`,
    partida 3: Salamence baja a 0/100 en el turno 6 sin `faint` y se cura a
    65/100 en el turno 7 sin ningún move/item/habilidad que lo explique -un
    Pokémon vivo no puede estar en 0 PS. Si el 0 fue real, el `faint` tiene
    que llegar antes de la siguiente línea que vuelva a tocar a ese Pokémon;
    un `switch`/`drag` al mismo slot también lo cierra -entró otro distinto.
    """

    lines = log.read_text(encoding="utf-8").splitlines()
    pending: dict[str, str] = {}
    problems: list[str] = []
    for line in lines:
        faint = _FAINT_LINE.match(line)
        if faint:
            pending.pop(faint["slot"], None)
            continue
        entry = _ENTRY_LINE.match(line)
        if entry:
            pending.pop(entry["slot"], None)
            continue
        hp = _HP_LINE.match(line)
        if not hp:
            continue
        slot = hp["slot"]
        if slot in pending:
            verb = "se curó" if hp["kind"] == "heal" else "volvió a recibir daño"
            problems.append(f"{slot}: {pending[slot]} {verb} en 0 PS sin faint de por medio")
            pending.pop(slot, None)
        if hp["current"] == "0":
            pending[slot] = hp["species"].strip()
        else:
            pending.pop(slot, None)
    for slot, species in pending.items():
        problems.append(f"{slot}: {species} queda en 0 PS al final del replay sin faint")
    return tuple(problems)


def _slot_occupant_conflict_problems(log: Path) -> tuple[str, ...]:
    """Un slot sólo puede tener un Pokémon a la vez entre un switch y el siguiente.

    COL-102, reapertura estructural del 25 sep: invariante de identidad que
    pedía el mandato original y que esta ficha nunca había construido. Si
    una línea nombra a un Pokémon distinto en un slot sin que un
    `switch`/`drag` lo haya reemplazado antes, el HUD perdió al ocupante
    real y confundió a otro con su lugar -el mismo tipo de fallo que ya
    causó el switch fantasma de Kingambit y la identidad huérfana de
    Indeedee-F esta misma ronda, aquí visto desde el replay final en vez
    de la traza. Compara por especie base: una Mega Evolución o
    Terastalización cambia el nombre mostrado sin que el Pokémon haya
    salido del campo, y eso no es un conflicto.
    """

    lines = log.read_text(encoding="utf-8").splitlines()
    occupant: dict[str, str] = {}
    problems: list[str] = []
    for line in lines:
        if line.startswith("|switch|") or line.startswith("|drag|"):
            match = _SLOT_SPECIES_LINE.match(line)
            if match:
                occupant[match["slot"]] = match["species"].strip()
            continue
        if line.startswith("|faint|"):
            match = _SLOT_SPECIES_LINE.match(line)
            if match:
                occupant.pop(match["slot"], None)
            continue
        match = _SLOT_SPECIES_LINE.match(line)
        if not match:
            continue
        slot, species = match["slot"], match["species"].strip()
        known = occupant.get(slot)
        if known is None:
            occupant[slot] = species
            continue
        if _base_species(species) != _base_species(known):
            problems.append(f"{slot}: {species} aparece sin switch/drag -el slot tenía a {known}")
            occupant[slot] = species
    return tuple(problems)


def _status_conflict_problems(log: Path) -> tuple[str, ...]:
    """Un Pokémon sólo puede tener un estado no volátil a la vez.

    COL-102, reapertura estructural del 25 sep: quemadura, veneno, parálisis,
    sueño y congelación se excluyen entre sí -otro invariante del mandato
    original sin construir todavía. Un `-status` que cambia el estado de un
    slot sin que antes llegara su `-curestatus` (o el Pokémon saliera por
    switch/drag/faint) es una lectura que se perdió o se duplicó, no un
    segundo estado real.
    """

    lines = log.read_text(encoding="utf-8").splitlines()
    active: dict[str, str] = {}
    problems: list[str] = []
    for line in lines:
        if line.startswith("|switch|") or line.startswith("|drag|") or line.startswith("|faint|"):
            match = _SLOT_SPECIES_LINE.match(line)
            if match:
                active.pop(match["slot"], None)
            continue
        cure = _CURESTATUS_LINE.match(line)
        if cure:
            active.pop(cure["slot"], None)
            continue
        status = _STATUS_LINE.match(line)
        if not status:
            continue
        slot, new_status = status["slot"], status["status"]
        existing = active.get(slot)
        if existing and existing != new_status:
            problems.append(f"{slot}: pasa de {existing} a {new_status} sin curarse de por medio")
        active[slot] = new_status
    return tuple(problems)


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


def _replay_event_sequence(log: Path) -> tuple[tuple[str, str, str], ...]:
    """Los mismos eventos que `_replay_events`, en el orden en que el log los escribió."""

    sequence: list[tuple[str, str, str]] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        for pattern, kind, group in (
            (_MOVE_LINE, "move", "move"),
            (_FAINT_LINE, "faint", "species"),
            (_MEGA_LINE, "mega", "species"),
        ):
            match = pattern.match(line)
            if match:
                value = match[group].strip().split(",")[0]
                sequence.append((kind, match["slot"][:2], value))
                break
    return tuple(sequence)


def _replay_events(log: Path) -> Counter:
    return Counter(_replay_event_sequence(log))


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


def _order_problems(
    sequence: tuple[tuple[str, str, str, int], ...],
    events: tuple[tuple[str, str, str], ...],
    aliases: dict[str, str],
) -> tuple[str, ...]:
    """Un intercambio de orden no es lo mismo que un evento perdido o inventado.

    COL-102, reapertura estructural del 26 sep: `_pair_up` compara pantalla
    contra replay como dos bolsas de eventos (`Counter`), así que dos
    eventos reales que cambiaron de orden entre sí -por ejemplo la
    pantalla muestra "Protect" y después "Ice Beam", pero el replay los
    escribió al revés- dan el mismo conteo en los dos lados y `verify` los
    declara emparejados sin más. Aquí se alinean ambas secuencias
    preservando el orden, con el mismo criterio de similitud que
    `_pair_up` (subsecuencia común más larga vía programación dinámica).
    Sólo lo que quede fuera de esa alineación, y que además tenga una
    contraparte de contenido igual del otro lado, se reporta como fuera de
    orden: si no tiene contraparte, ya lo reportan `missing`/`invented` por
    separado y no hay que duplicarlo aquí.
    """

    screen = tuple(
        (kind, side, _species_for(value, aliases) if kind == "faint" else value)
        for kind, side, value, _frame in sequence
    )
    n, m = len(screen), len(events)

    def matches(a: tuple[str, str, str], b: tuple[str, str, str]) -> bool:
        if a[0] != b[0] or a[1] != b[1]:
            return False
        if _key(a[2]) == _key(b[2]):
            return True
        return SequenceMatcher(None, _key(a[2]), _key(b[2])).ratio() >= _SIMILAR

    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = max(
                table[i + 1][j],
                table[i][j + 1],
                1 + table[i + 1][j + 1] if matches(screen[i], events[j]) else 0,
            )

    aligned_screen: set[int] = set()
    aligned_replay: set[int] = set()
    i = j = 0
    while i < n and j < m:
        if matches(screen[i], events[j]) and table[i][j] == 1 + table[i + 1][j + 1]:
            aligned_screen.add(i)
            aligned_replay.add(j)
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1

    unmatched_replay = [events[j] for j in range(m) if j not in aligned_replay]
    problems: list[str] = []
    for i in range(n):
        if i in aligned_screen:
            continue
        side, kind, value = screen[i][1], screen[i][0], screen[i][2]
        if any(matches(screen[i], other) for other in unmatched_replay):
            problems.append(f"{side} {kind} {value}: aparece en el replay en otro orden del que se vio en pantalla")
    return tuple(problems)


def verify_replay(
    trace: Path,
    log: Path,
    *,
    battle_index: int = 0,
    species_names: Iterable[str] = (),
) -> VerificationReport:
    occurrences, aliases, sequence = _screen_events(trace, battle_index)
    screen: Counter = Counter()
    for (kind, side, value), times in occurrences.items():
        resolved = _species_for(value, aliases) if kind == "faint" else value
        screen[(kind, side, resolved)] += times
    replay_sequence = _replay_event_sequence(log)
    replay = Counter(replay_sequence)
    matched, missing, invented = _pair_up(screen, replay)
    rosters = _replay_rosters(log)
    problems = (
        _roster_problems(rosters, _named_on_screen(trace, battle_index, species_names))
        if species_names
        else ()
    ) + (
        _turn_problems(log)
        + _hp_zero_without_faint_problems(log)
        + _slot_occupant_conflict_problems(log)
        + _status_conflict_problems(log)
        + _order_problems(sequence, replay_sequence, aliases)
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
