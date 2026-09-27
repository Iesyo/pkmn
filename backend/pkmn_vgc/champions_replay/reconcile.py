"""COL-102: reconciliación conservadora de una traza OCR y un replay.

Alcance acotado, pedido por Roku en la Mesa de colaboración el 26 sep:
agrupar avisos de pantalla con sus variantes OCR como evidencia, alinear
episodios contra el `.log` en orden (no por cantidad) y proponer un
borrador reparado sólo donde la evidencia alcanza. Todo resultado queda
`status: "needs_review"` -esta pasada nunca declara un replay fiel por sí
misma, sólo compara acciones, fase e invariantes de HP; no reemplaza una
verificación visual del vídeo.

Deliberadamente no implementa la máquina de fases/beam-search descrita en
el prototipo original (`COL-102-reconciliador.zip`, README.md) -eso queda
fuera de este turno.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping

from .pipeline import ReviewIssue

# Roku, revisión del quinto corte, 26 sep: la ausencia de `issues` en un
# replay servido nunca puede leerse como "revisado, sin hallazgos" -los
# tres jobs protegidos son del 25 sep, de antes de que `ReplayDocument`
# tuviera este campo siquiera, y esa lectura habría dejado pasar
# exactamente el replay corrupto que Ies denunció. `_documents_with_reconcile_issues`
# (`champions_jobs.py`) estampa este valor en `ReplayDocument.reconciliation_version`
# sólo cuando `analyze()` corrió de verdad sobre el documento con el
# código actual; el servidor (`db/queries.ts`) exige que coincida antes
# de confiar en `issues`. Subir este número cuando la lógica de análisis
# cambie de forma que invalide una revisión anterior.
#
# r6, reapertura estructural del 26 sep, job real `10a7fba6fda04585`:
# `_state_findings` ahora detecta una reentrada al slot donde la misma
# especie se acaba de debilitar (partidas 1 y 5 de ese job, exactamente lo
# que `col102-r5` dejaba pasar con `issues: []`). Cualquier replay ya
# marcado con `col102-r5` necesita pasar otra vez por esta versión.
#
# r7, corte de Roku sobre el commit `afee177` (mismo día): ese commit quitó
# la fabricación de HP en `showdown._with_known_health` -un switch/drag con
# "0/max" ya no cae en "max/max"-, pero `_state_findings` sólo detectaba la
# reentrada por IDENTIDAD (misma especie repetida en el slot). Ahora
# también detecta, aparte (`entrada_a_cero`), cualquier switch/drag
# serializado con 0 PS reales que no venga inmediatamente después del
# `faint` confirmado de esa misma especie en ese slot -especie distinta
# leyendo 0, o HP heredado en 0 sin ningún faint que lo explique-, para que
# el bloqueo sobreviva al análisis del `.log` ya escrito y no dependa de
# que la limpieza previa del pipeline sea perfecta. Cualquier replay ya
# marcado con `col102-r6` -o antes- necesita pasar otra vez por esta
# versión.
#
# r8, corte de Roku sobre el commit `ff9e53f` (mismo turno): tres cambios.
# (1) `showdown._resolve_health`/`_unsupported_health_issues` ahora
# adjuntan una incidencia `blocking` a `ReplayDocument.issues` cuando un
# switch/drag sin lectura propia ni previa cae al máximo -o a "100/100"-
# sostenido sólo por una lectura FUTURA o por ninguna lectura en absoluto;
# antes esto quedaba "verificado" sin marca (el defecto que motivó este
# corte). (2) `_state_findings` acepta `line_frames`/`source_battle_index`
# y enlaza `entrada_a_cero`/`reentrada_debilitado` al frame de traza real
# cuando `analyze()` puede correlacionarlo (`_entry_frame_index`); cuando
# no puede, el propio detalle lo dice en vez de dejar `frame=None` sin
# explicación. Ningún replay marcado `col102-r7` -o antes- trae ninguna de
# las dos protecciones; hay que pasarlo otra vez por esta versión.
#
# r9, reversión de Ies sobre el corte r8 (mismo día, cuarta vuelta): el
# punto (1) de arriba resultó demasiado amplio -disparaba también en la
# entrada de cualquier identidad SIN historial previo en absoluto,
# incluidos los dos líderes con los que arranca cada combate, porque el
# HUD de este pipeline nunca muestra un número en el instante neutral del
# `switch`. Con eso, ningún replay del job real pasaba ya el gate
# automático. Ies decidió explícitamente revertir sólo ese punto: una
# entrada genuinamente nueva vuelve a caer al máximo (o a "100/100") sin
# incidencia -"quien pisa el campo por primera vez entra a tope" es una
# regla del juego, no una deducción arriesgada-, mientras que
# `entrada_a_cero`/`reentrada_debilitado` (una identidad que YA tiene
# historial en este combate y reaparece sin lectura que lo sostenga, el
# patrón real del bug de Rillaboom) siguen intactos. Cualquier replay
# marcado `col102-r8` puede llevar una incidencia `blocking` que ya no
# aplica; hay que pasarlo otra vez por esta versión.
#
# r10, corte de Roku sobre `32da465` (mismo día, quinta vuelta): aceptó
# el punto anterior -no bloquear una identidad SIN historial-, pero
# encontró un hueco real distinto: el recuerdo de "qué identidad se
# debilitó en este slot" (`_drop_ghost_reentries` en `pipeline.py`,
# `fainted_species` de `_state_findings` acá mismo) vive sólo mientras
# nadie más ocupe ese slot -se borra en el propio `switch` de cualquier
# otro ocupante, antes de mirar si el propio debilitado regresa después
# de él. `pipeline._revived_identity_issues` (nuevo, corre dentro de
# `review_capture`) sigue la identidad por lado+especie sin depender del
# slot, así que sobrevive a cualquier ocupante intermedio hasta el final
# del combate: una identidad ya confirmada debilitada que vuelve a
# entrar -con HP ausente o positivo, sin ningún `0/x` numérico que
# `entrada_a_cero` pudiera anclar- ahora queda `blocking`. Ningún replay
# marcado `col102-r9` -o antes- trae esta protección; hay que pasarlo
# otra vez por esta versión.
RECONCILE_VERSION = "col102-r10"


_MOVE = re.compile(r"^(.*?) used (.+?)!$", re.IGNORECASE)
_FAINT = re.compile(r"^(.*?) fainted!$", re.IGNORECASE)
_MEGA = re.compile(r"^(.*?) has Mega Evolved into Mega (.+?)!$", re.IGNORECASE)
_LOG_MOVE = re.compile(r"^\|move\|(p[12][ab]): [^|]+\|([^|]+)\|")
_LOG_FAINT = re.compile(r"^\|faint\|(p[12][ab]): ([^|]+)$")
_LOG_MEGA = re.compile(r"^\|-mega\|(p[12][ab]): [^|]+\|([^|]+)\|")
_LOG_HP = re.compile(r"^\|-(damage|heal)\|(p[12][ab]): ([^|]+)\|(\d+)/(\d+)")
_LOG_ENTRY = re.compile(r"^\|(switch|drag)\|(p[12][ab]): ([^|]+)\|[^|]*\|([^|]+)")
_LOG_ENTRY_HP = re.compile(r"^(\d+)/(\d+)")
_OPPOSING = re.compile(r"^(?:the[\s-]+)?([^\s-]+)[\s-]+(.+)$", re.IGNORECASE)


def key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def similar(first: str, second: str) -> float:
    return SequenceMatcher(None, key(first), key(second)).ratio()


def _actor_side(value: str) -> tuple[str | None, str]:
    """Acepta 'The-opposing', 'epposing', 'oposing'; no adivina prefijos raros."""
    match = _OPPOSING.match(value)
    if match:
        prefix, actor = match.groups()
        if similar(prefix, "opposing") >= 0.76:
            return "p2", actor
    if value.casefold().startswith("the ") or value.casefold().startswith("the-"):
        return None, value
    return "p1", value


@dataclass(frozen=True)
class Observation:
    frame: int
    kind: str
    side: str | None
    actor: str
    value: str
    text: str
    confidence: float


@dataclass(frozen=True)
class Episode:
    first_frame: int
    last_frame: int
    kind: str
    side: str | None
    actor: str
    value: str
    support: int
    variants: tuple[str, ...]


@dataclass(frozen=True)
class ReplayEvent:
    line: int
    kind: str
    side: str
    value: str


@dataclass(frozen=True)
class Finding:
    category: str
    detail: str
    frame: int | None = None
    line: int | None = None


@dataclass(frozen=True)
class Edit:
    line: int
    before: str
    after: str | None
    reason: str
    frame: int | None = None


def _parse_observation(frame: int, item: dict[str, Any]) -> Observation | None:
    text = str(item.get("text") or "").strip()
    # Los avisos de batalla están en el cuadro inferior. Sin coordenadas se
    # acepta la lectura para facilitar trazas antiguas y fixtures.
    if "top" in item and not 0.59 <= float(item["top"]) <= 0.86:
        return None
    for pattern, kind in ((_MOVE, "move"), (_FAINT, "faint"), (_MEGA, "mega")):
        match = pattern.match(text)
        if match:
            side, actor = _actor_side(match.group(1).strip())
            value = match.group(2).strip() if kind != "faint" else actor
            return Observation(frame, kind, side, actor, value, text, float(item.get("confidence") or 0))
    return None


def _episodes(observations: list[Observation]) -> list[Episode]:
    # El emparejamiento es por mensaje completo y cercanía temporal, sin
    # dividir primero por lado: 'The-opposing-X' es la misma acción que
    # 'The opposing X', aunque un frame pierda letras del prefijo.
    groups: list[list[Observation]] = []
    for observation in observations:
        current = next(
            (
                group for group in reversed(groups[-4:])
                if observation.frame - group[-1].frame <= 3
                and observation.kind == group[0].kind
                and similar(observation.value, group[0].value) >= 0.78
                and similar(observation.actor, group[0].actor) >= 0.70
            ), None,
        )
        if current is None:
            groups.append([observation])
        else:
            current.append(observation)
    episodes: list[Episode] = []
    for group in groups:
        sides = Counter(item.side for item in group if item.side)
        side = sides.most_common(1)[0][0] if sides and (len(sides) == 1 or sides.most_common(1)[0][1] > len(group) / 2) else None
        readings = Counter((item.actor, item.value) for item in group)
        actor, value = max(readings, key=lambda item: (readings[item], len(key(item[0])) + len(key(item[1]))))
        episodes.append(Episode(group[0].frame, group[-1].frame, group[0].kind, side, actor, value,
                                len({item.frame for item in group}), tuple(sorted(set(item.text for item in group)))))
    return sorted(episodes, key=lambda item: item.first_frame)


def _battle_index_by_rival_name(records: list[dict[str, Any]], lines: list[str]) -> tuple[int | None, str | None]:
    """Respaldo para trazas sin `source_battle_index` persistido.

    COL-102, bloqueante de Roku del 26 sep: el número ordinal `replay-003`
    no identifica `battle_index` si una partida anterior se descartó, y
    votar por el nombre del rival es ambiguo si dos batallas comparten
    oponente. `analyze()` sólo recurre a esto cuando no recibe un
    `source_battle_index` ya persistido (ver `CapturedBattle.source_battle_index`,
    `pipeline.py`).
    """

    name = next((line.split("|")[3] for line in lines if line.startswith("|player|p2|")), None)
    if not name:
        return None, "Falta el jugador p2 del replay"
    names: dict[int, Counter[str]] = defaultdict(Counter)
    for record in records:
        player = (record.get("detections") or {}).get("players") or {}
        if player.get("p2"):
            names[record["battle_index"]][key(str(player["p2"]))] += 1
    matches = [index for index, count in names.items() if count and count.most_common(1)[0][0] == key(name)]
    if len(matches) != 1:
        return None, f"Origen ambiguo para p2={name!r}: {matches}; se requiere source_battle_index"
    return matches[0], None


def _log_events(lines: list[str]) -> list[ReplayEvent]:
    result = []
    for index, line in enumerate(lines):
        for pattern, kind in ((_LOG_MOVE, "move"), (_LOG_FAINT, "faint"), (_LOG_MEGA, "mega")):
            if match := pattern.match(line):
                result.append(ReplayEvent(index, kind, match[1][:2], match[2].strip().split(",")[0]))
                break
    return result


def _aligned(episodes: list[Episode], events: list[ReplayEvent], aliases: dict[str, dict[str, str]],
             rosters: dict[str, list[str]]) -> tuple[list[int], list[int]]:
    # A diferencia de comparar Counters, esta alineación conserva el orden.
    # Un intercambio de movimientos ya no se acepta como replay fiel.
    def matches(a: Episode, b: ReplayEvent) -> bool:
        if a.kind != b.kind or a.side != b.side:
            return False
        value = aliases.get(a.side or "", {}).get(key(a.value), a.value) if a.kind == "faint" else a.value
        if key(value) == key(b.value):
            return True
        if a.kind == "faint" and key(value.split("-")[0]) == key(b.value.split("-")[0]):
            # El aviso omite la forma; sólo es inequívoco si la base aparece
            # exactamente una vez en el roster conocido.
            return sum(key(name.split("-")[0]) == key(value.split("-")[0]) for name in rosters.get(a.side or "", [])) == 1
        return similar(value, b.value) >= 0.80

    n, m = len(episodes), len(events)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = max(table[i + 1][j], table[i][j + 1], 1 + table[i + 1][j + 1] if matches(episodes[i], events[j]) else 0)
    used_screen: set[int] = set()
    used_log: set[int] = set()
    i = j = 0
    while i < n and j < m:
        if matches(episodes[i], events[j]) and table[i][j] == 1 + table[i + 1][j + 1]:
            used_screen.add(i); used_log.add(j); i += 1; j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return sorted(set(range(n)) - used_screen), sorted(set(range(m)) - used_log)


def _phase_ghosts(records: list[dict[str, Any]], battle_index: int) -> set[tuple[str, str, str, int]]:
    selected = [r for r in records if r["battle_index"] == battle_index]
    ghosts: set[tuple[str, str, str, int]] = set()
    for before, record, after in zip(selected, selected[1:], selected[2:]):
        if not before.get("detections", {}).get("team_preview") or not after.get("detections", {}).get("team_preview"):
            continue
        if record.get("detections", {}).get("team_preview"):
            continue
        labels = [key(str(item.get("text") or "")) for item in record.get("ocr") or []]
        if not any(similar(label, "select4pokemon") >= 0.85 for label in labels):
            continue
        if not any(similar(label, "tosendintobattle") >= 0.85 for label in labels):
            continue
        for event in (record.get("detections") or {}).get("events") or []:
            if event.get("kind") in {"switch", "drag"} and event.get("slot") and event.get("species"):
                ghosts.add((str(event["slot"]), str(event["species"]), str(event.get("health") or ""), record["frame"]))
    return ghosts


def _entry_frame_index(records: list[dict[str, Any]], battle_index: int) -> dict[tuple[str, str], list[int]]:
    """Frames, en orden de traza, donde se detectó cada entrada (`switch`/
    `drag`) por (slot, especie base) -sólo para `battle_index`.

    Roku, corte sobre `ff9e53f`, defecto #3 de la revisión: `_state_findings`
    corre sobre el `.log` ya escrito, que no trae ningún frame consigo -sólo
    la traza original lo tiene. `analyze()` consume esto en el mismo orden
    en que aparecen las líneas de entrada para esa combinación, igual que
    `_phase_ghosts`/`_false_zero_candidates` ya correlacionan trazas contra
    el log por posición, no por buscar una coincidencia exacta de HP -un
    `switch` fantasma ya descartado antes de serializar no debe correrle el
    turno a las entradas reales que sí sobrevivieron.
    """

    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for record in records:
        if record["battle_index"] != battle_index:
            continue
        for event in (record.get("detections") or {}).get("events") or []:
            if event.get("kind") not in {"switch", "drag"} or not event.get("slot") or not event.get("species"):
                continue
            index[(str(event["slot"]), key(str(event["species"]).split("-")[0]))].append(record["frame"])
    return index


def _state_findings(
    lines: list[str],
    *,
    line_frames: Mapping[int, int] | None = None,
    source_battle_index: int | None = None,
) -> list[Finding]:
    """Roku, reapertura estructural del 26 sep, job real
    `10a7fba6fda04585`, partidas 1 y 5: `dead_at` vivía por slot y se
    borraba en el propio `switch`/`faint` -antes incluso de mirar si ese
    `switch` reintroducía a la misma especie que se acababa de debilitar
    ahí- y la rama de entrada (`_LOG_ENTRY`) nunca miraba la vida que trae
    codificada su propia línea contra ese estado. Por eso `col102-r5`
    devolvía `issues: []` en ambos replays pese a que "Gori"/"Rillaboom" y
    "Bonkers"/"Rillaboom" volvían a entrar a 0 PS justo tras su propio
    `faint`: no había ningún hallazgo que mirara esa combinación. Se seguía
    además la última especie confirmada debilitada por slot (sin borrarla
    en el propio `faint`, sólo cuando una especie de verdad distinta ocupa
    el slot) para poder comparar contra ella en la entrada siguiente.

    Roku, revisión del commit `afee177` (mismo turno): ese fix quitó la
    fabricación de HP en `showdown._with_known_health`, pero un
    `switch`/`drag` con "0/max" real puede llegar aquí por una vía que
    `reentrada_debilitado` no cubre -especie DISTINTA a la que se acaba de
    debilitar en ese slot (lectura de OCR cruzada, o cualquier entrada
    fantasma que se le escape a la limpieza previa) o directamente sin
    ningún `|faint|` que lo preceda. `reentrada_debilitado` sólo mira la
    identidad (misma especie repetida); esto mira la vida codificada en la
    propia línea de entrada: cualquier `switch`/`drag` con HP resuelto en
    0 que no venga inmediatamente después del `|faint|` confirmado de esa
    MISMA especie en ese slot es imposible -Showdown nunca hace entrar a
    un Pokémon a 0 PS- y se marca aparte (`entrada_a_cero`) para no
    depender de que la identidad además coincida.

    Roku, corte sobre `ff9e53f`, defecto #3 de la revisión: un `Finding`
    sólo con `line` no le dice a Teams a qué fotograma volver -el detalle
    conserva slot/especie, pero no dirige a nadie al vídeo. `line_frames`
    (construido por `analyze()` con `_entry_frame_index`, correlacionando
    la traza contra estas mismas líneas de entrada en orden) lo resuelve
    cuando hay traza de por medio; si no la hay -esta función corriendo
    sola sobre un `.log`, como en las pruebas unitarias, o sin evidencia
    para esta línea en particular- el propio detalle lo dice, y el
    hallazgo bloquea igual: la ausencia de frame nunca se lee como "sin
    problema", sólo como "revisar a mano".
    """

    def _evidence(i: int) -> tuple[int | None, str]:
        frame = (line_frames or {}).get(i)
        if frame is not None:
            origin = f"battle_index={source_battle_index}" if source_battle_index is not None else "traza"
            return frame, f" Evidencia de traza: frame {frame} ({origin})."
        if line_frames is not None:
            return None, " Sin frame de traza para esta línea; revisar manualmente contra el vídeo."
        return None, (
            " Sin traza de origen disponible en este análisis (corrió sólo sobre el .log); "
            "no hay frame que citar, revisar manualmente contra el vídeo."
        )

    result: list[Finding] = []
    occupants: dict[str, str] = {}
    dead_at: dict[str, int] = {}
    fainted_species: dict[str, str] = {}
    for i, line in enumerate(lines):
        if match := _LOG_ENTRY.match(line):
            slot, species = match[2], match[3]
            fainted = fainted_species.get(slot)
            same_species_as_fainted = bool(
                fainted and key(fainted.split("-")[0]) == key(species.split("-")[0])
            )
            if same_species_as_fainted:
                frame, note = _evidence(i)
                result.append(
                    Finding(
                        "reentrada_debilitado",
                        f"{slot}: {species} reingresa al slot donde se debilitó, sin otro ocupante de por medio.{note}",
                        frame=frame,
                        line=i,
                    )
                )
            else:
                fainted_species.pop(slot, None)
            entry_hp = _LOG_ENTRY_HP.match(match[4])
            if (
                entry_hp
                and entry_hp[1] == "0"
                and int(entry_hp[2]) > 0
                and not same_species_as_fainted
            ):
                frame, note = _evidence(i)
                result.append(
                    Finding(
                        "entrada_a_cero",
                        f"{slot}: {species} entra con 0/{entry_hp[2]} PS sin faint confirmado de esa misma especie "
                        f"en ese slot inmediatamente antes; ningún switch/drag puede resolver con 0 PS reales.{note}",
                        frame=frame,
                        line=i,
                    )
                )
            occupants[slot] = species
            dead_at.pop(slot, None)
            continue
        if match := _LOG_FAINT.match(line):
            fainted_species[match[1]] = match[2]
            dead_at.pop(match[1], None)
            occupants.pop(match[1], None)
            continue
        if match := _LOG_HP.match(line):
            kind, slot, species, hp, maximum = match.groups()
            if int(hp) > int(maximum) or int(maximum) <= 0:
                result.append(Finding("hp_invalido", f"{slot}: {hp}/{maximum}", line=i))
            if slot in dead_at and int(hp) > 0:
                result.append(Finding("hp_imposible", f"{slot}: 0 PS en línea {dead_at[slot]+1} y luego {kind} a {hp}/{maximum}", line=i))
            if hp == "0":
                dead_at[slot] = i
            elif int(hp) > 0:
                dead_at.pop(slot, None)
            if occupants.get(slot) and key(species.split("-")[0]) != key(occupants[slot].split("-")[0]):
                result.append(Finding("identidad", f"{slot}: {species} sin cambio; ocupante {occupants[slot]}", line=i))
    for slot, index in dead_at.items():
        result.append(Finding("cero_sin_faint", f"{slot}: llegó a 0 PS sin debilitado confirmado", line=index))
    return result


def _false_zero_candidates(records: list[dict[str, Any]], battle_index: int) -> dict[tuple[str, str], list[int]]:
    """0 aislado entre dos lecturas iguales y positivas del mismo actor.

    No se usa el slot bruto: el propio parser puede rectificar a posteriori
    p2a/p2b. El actor se identifica por especie dentro de una sola batalla;
    si hay dos copias de esa especie, esta prueba no permite reparación.
    """
    entries: dict[tuple[str, str], list[tuple[int, int, int]]] = defaultdict(list)
    team: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record["battle_index"] != battle_index:
            continue
        for side, roster in (record.get("detections") or {}).get("teams", {}).items():
            if roster:
                team[side] = roster
        for event in (record.get("detections") or {}).get("events") or []:
            if event.get("kind") not in {"damage", "heal"} or not event.get("slot") or not event.get("species"):
                continue
            health, _sep, maximum = str(event.get("health") or "").partition("/")
            if health.isdigit() and maximum.isdigit():
                entries[(event["slot"][:2], event["species"])].append((record["frame"], int(health), int(maximum)))
    candidates: dict[tuple[str, str], list[int]] = defaultdict(list)
    for (side, species), observations in entries.items():
        if sum(key(p) == key(species) for p in team[side]) != 1:
            continue
        maximums = Counter(maximum for _frame, _health, maximum in observations if maximum > 0)
        if not maximums:
            continue
        main_max = maximums.most_common(1)[0][0]
        observations = [(frame, health) for frame, health, maximum in observations if maximum == main_max]
        for i, (frame, health) in enumerate(observations):
            if health:
                continue
            previous = next(((f, h) for f, h in reversed(observations[:i]) if h > 0 and frame - f <= 100), None)
            future = next(((f, h) for f, h in observations[i + 1:] if h > 0 and f - frame <= 100), None)
            if previous and future and previous[1] == future[1]:
                candidates[(side, species)].append(previous[1])
    return candidates


def identity_candidates(records: list[dict[str, Any]], battle_index: int) -> list[dict[str, Any]]:
    """Atar un actor huérfano a una especie sólo con faint + roster único."""
    roster: dict[str, list[str]] = defaultdict(list)
    result: list[dict[str, Any]] = []
    for record in records:
        if record["battle_index"] != battle_index:
            continue
        for side, members in (record.get("detections") or {}).get("teams", {}).items():
            if members:
                roster[side] = members
        for event in (record.get("detections") or {}).get("events") or []:
            actor_id = str(event.get("species") or "")
            if event.get("kind") != "faint" or not actor_id.startswith("__champions_actor_"):
                continue
            side = str(event.get("slot") or "")[:2]
            observations = [_parse_observation(record["frame"], item) for item in record.get("ocr") or []]
            matches = [obs for obs in observations if obs and obs.kind == "faint" and obs.side == side]
            for obs in matches:
                members = [name for name in roster[side] if key(name.split("-")[0]) == key(obs.actor)]
                if len(members) == 1:
                    result.append({"actor_id": actor_id, "species": members[0], "frame": record["frame"], "evidence": obs.text})
    return result


def inventory(trace: Path, replays: list[Path]) -> dict[str, Any]:
    """Batallas que la traza registró pero ningún `.log` representa."""

    records = [json.loads(raw) for raw in trace.read_text(encoding="utf-8").splitlines() if raw.strip()]
    mapped = {analyze(trace, replay)["source_battle_index"] for replay in replays}
    with_battle = {r["battle_index"] for r in records if (r.get("detections") or {}).get("battle_started")}
    lost = sorted(with_battle - mapped)
    return {"unrepresented_battles": lost, "identity_candidates": {i: identity_candidates(records, i) for i in lost}}


def analyze(
    trace: Path, replay: Path | str, *, source_battle_index: int | None = None
) -> dict[str, Any]:
    """Compara un `.log` contra su traza y propone un borrador reparado.

    `replay` acepta la ruta de un `.log` ya escrito (CLI, tests) o
    directamente su texto (`ReplayDocument.log`, antes de escribirlo a
    disco) -segundo corte de Roku, 26 sep: para que esto corra en el
    cierre real de la captura no puede depender de que el archivo ya
    exista.

    `source_battle_index`, si se pasa, evita `_battle_index_by_rival_name`
    -úsese el valor ya persistido en `CapturedBattle`/el `.json` hermano
    del `.log` (ver `cli._persisted_battle_index`) siempre que exista.
    """

    records = [json.loads(raw) for raw in trace.read_text(encoding="utf-8").splitlines() if raw.strip()]
    lines = (replay.read_text(encoding="utf-8") if isinstance(replay, Path) else replay).splitlines()
    if source_battle_index is not None:
        # Roku, revisión del cuarto corte, 26 sep: un `source_battle_index`
        # recibido sin verificar podía apuntar a un índice sin ningún
        # frame en esta traza (job equivocado, traza truncada) y `analyze`
        # lo tomaba igual -`selected` salía vacío, sin episodios ni
        # eventos que comparar, así que no había nada que objetar y el
        # resultado parecía "limpio" precisamente cuando no se pudo
        # cotejar nada. Tratado igual que el origen ambiguo por nombre.
        if any(r.get("battle_index") == source_battle_index for r in records):
            source_index, source_error = source_battle_index, None
        else:
            source_index, source_error = None, (
                f"la traza no tiene ningún frame con battle_index={source_battle_index}"
            )
    else:
        source_index, source_error = _battle_index_by_rival_name(records, lines)
    findings = [Finding("origen", source_error)] if source_error else []
    edits: list[Edit] = []
    episodes: list[Episode] = []
    if source_index is not None:
        selected = [r for r in records if r["battle_index"] == source_index]
        observations = [o for r in selected for item in r.get("ocr") or [] if (o := _parse_observation(r["frame"], item))]
        episodes = _episodes(observations)
        aliases: dict[str, dict[str, str]] = defaultdict(dict)
        rosters: dict[str, list[str]] = defaultdict(list)
        for r in selected:
            for side, alias_map in (r.get("resolved_aliases") or {}).items():
                aliases[side].update({key(nick): species for nick, species in alias_map.items()})
            for side, team in ((r.get("detections") or {}).get("teams") or {}).items():
                if team:
                    rosters[side] = team
        events = _log_events(lines)
        missing, invented = _aligned(episodes, events, aliases, rosters)
        for i in invented:
            item = events[i]
            findings.append(Finding("sin_respaldo", f"{item.side} {item.kind} {item.value}", line=item.line))

        for i in missing:
            item = episodes[i]
            resolved = False
            if item.kind == "faint" and item.side:
                # A diferencia del 0 terminal, aquí hay mensaje textual Y
                # el detector registró el faint en ese frame, con slot.
                record = next((r for r in selected if r["frame"] == item.first_frame), None)
                actors = [e for e in (record or {}).get("detections", {}).get("events", [])
                          if e.get("kind") == "faint" and e.get("slot", "").startswith(item.side)
                          and key(str(e.get("species") or "").split("-")[0]) == key(item.actor.split("-")[0])]
                if len(actors) == 1:
                    slot, species = actors[0]["slot"], actors[0]["species"]
                    zeros = [(j, m) for j, line in enumerate(lines) if (m := _LOG_HP.match(line))
                             and m[1] == "damage" and m[2] == slot and m[4] == "0"
                             and key(m[3].split("-")[0]) == key(species.split("-")[0])]
                    if len(zeros) == 1:
                        zero_line = zeros[0][0]
                        occupant = next((entry[3] for line in reversed(lines[:zero_line + 1])
                                         if (entry := _LOG_ENTRY.match(line)) and entry[2] == slot), None)
                        next_entry = next((j for j in range(zero_line + 1, len(lines))
                                           if (entry := _LOG_ENTRY.match(lines[j])) and entry[2] == slot), len(lines))
                        existing_faint = any((faint := _LOG_FAINT.match(lines[j])) and faint[1] == slot
                                             for j in range(zero_line + 1, next_entry))
                        if occupant and key(occupant.split("-")[0]) == key(species.split("-")[0]) and not existing_faint:
                            anchor = zero_line
                            for j in range(zero_line + 1, len(lines)):
                                if lines[j].startswith("|-message|") and key(species.split("-")[0]) in key(lines[j]):
                                    anchor = j
                                else:
                                    break
                            faint_line = f"|faint|{slot}: {species}"
                            edits.append(Edit(anchor, lines[anchor], lines[anchor] + "\n" + faint_line,
                                              "Faint explícito en OCR y detección con slot tras 0 PS", item.first_frame))
                            resolved = True
            if not resolved:
                findings.append(Finding("faltante" if item.side else "ambiguo", f"{item.side or '?'} {item.kind} {item.value}, {item.support} frame(s): {item.variants[0]}", frame=item.first_frame))

        first_turn = next((i for i, line in enumerate(lines) if line.startswith("|turn|")), len(lines))
        lead_slots = Counter(match[2] for line in lines[:first_turn] if (match := _LOG_ENTRY.match(line)))
        ghosts = _phase_ghosts(records, source_index)
        for i, line in enumerate(lines[:first_turn]):
            if match := _LOG_ENTRY.match(line):
                for slot, species, health, frame in ghosts:
                    if lead_slots[slot] > 1 and match[2] == slot and match[3] == species and match[4] == health:
                        edits.append(Edit(i, line, None, "Entrada originada en un frame aislado de Team Preview", frame))
                        break

        # El cero entre dos lecturas idénticas no es el resultado de un golpe.
        # Se acepta sólo si el log muestra una cura imposible sin switch/faint,
        # y el HUD sostuvo el mismo valor a ambos lados del cero.
        evidence = _false_zero_candidates(records, source_index)
        for i, line in enumerate(lines):
            zero_match = _LOG_HP.match(line)
            if not zero_match or zero_match[1] != "damage" or zero_match[4] != "0":
                continue
            slot, species = zero_match[2], zero_match[3]
            next_hp = next((
                (j, match)
                for j in range(i + 1, len(lines))
                if (match := _LOG_HP.match(lines[j])) and match[2] == slot and match[4] != "0"
            ), None)
            boundary = next((
                j for j in range(i + 1, len(lines))
                if (_LOG_ENTRY.match(lines[j]) and _LOG_ENTRY.match(lines[j])[2] == slot)
                or (_LOG_FAINT.match(lines[j]) and _LOG_FAINT.match(lines[j])[1] == slot)
            ), None)
            if not next_hp or (boundary is not None and boundary < next_hp[0]):
                continue
            j, positive = next_hp
            if positive[1] != "heal" or positive[5] != zero_match[5]:
                continue
            end_of_action = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("|move|") or lines[k].startswith("|turn|")), len(lines))
            if any((faint := _LOG_FAINT.match(lines[k])) and faint[1] == slot for k in range(i + 1, end_of_action)):
                continue
            value = int(positive[4])
            if value not in evidence.get((slot[:2], species), []):
                continue
            replacement = line.replace(f"|0/{zero_match[5]}", f"|{value}/{zero_match[5]}", 1)
            edits.append(Edit(i, line, replacement, f"0 aislado entre lecturas del HUD a {value} PS"))
            edits.append(Edit(j, lines[j], None, "Relectura tardía del HP corregido; no hubo curación"))

    # Regla causal exacta: después de 0 PS y antes del faint del mismo slot,
    # una lectura positiva del HUD es un fotograma transitorio, no una cura.
    # Si no hay faint, se deja pendiente: un solo 0 no autoriza inventarlo.
    zero: dict[str, int] = {}
    drop_lines = {edit.line for edit in edits if edit.after is None}
    replacements = {edit.line: edit.after for edit in edits if edit.after is not None}
    for i, line in enumerate(lines):
        if match := _LOG_ENTRY.match(line):
            zero.pop(match[2], None)
        elif match := _LOG_FAINT.match(line):
            zero.pop(match[1], None)
        elif match := _LOG_HP.match(line):
            kind, slot, _species, hp, _max = match.groups()
            if hp == "0":
                zero[slot] = i
            elif slot in zero and kind == "heal":
                boundary = next((k for k in range(i + 1, len(lines)) if (_LOG_FAINT.match(lines[k]) and _LOG_FAINT.match(lines[k])[1] == slot) or (_LOG_ENTRY.match(lines[k]) and _LOG_ENTRY.match(lines[k])[2] == slot)), None)
                if boundary is not None and _LOG_FAINT.match(lines[boundary]):
                    edits.append(Edit(i, line, None, f"Lectura positiva tras 0 PS y antes de faint ({slot})"))
                    drop_lines.add(i)
    patched = "\n".join(replacements.get(i, line) for i, line in enumerate(lines) if i not in drop_lines).splitlines()
    # Roku, corte sobre `ff9e53f`, defecto #3: correlacionar cada línea de
    # entrada del log ya parcheado contra los frames de la traza en que la
    # detección original vio esa misma combinación (slot, especie base),
    # consumidos en orden -mismo criterio posicional que `_phase_ghosts`/
    # `_false_zero_candidates`, nunca por igualdad exacta de HP, para no
    # perder la cuenta si una entrada anterior ya se descartó como edit.
    line_frames: dict[int, int] = {}
    if source_index is not None:
        entry_frames = _entry_frame_index(records, source_index)
        consumed: dict[tuple[str, str], int] = defaultdict(int)
        for i, line in enumerate(patched):
            if match := _LOG_ENTRY.match(line):
                slot_key = (match[2], key(match[3].split("-")[0]))
                frames = entry_frames.get(slot_key, ())
                position = consumed[slot_key]
                if position < len(frames):
                    line_frames[i] = frames[position]
                consumed[slot_key] += 1
    findings.extend(
        _state_findings(patched, line_frames=line_frames or None, source_battle_index=source_index)
    )
    return {
        "source_battle_index": source_index,
        "on_screen_episodes": len(episodes),
        "replay_events": len(_log_events(lines)),
        "draft_events": len(_log_events(patched)),
        "edits": [asdict(edit) for edit in edits],
        "findings": [asdict(finding) for finding in findings],
        # Este módulo sólo compara acciones, fase e invariantes de HP. Aun
        # sin hallazgos no equivale a una verificación visual completa.
        "status": "needs_review",
        "checks_passed": not findings,
        "patched_log": "\n".join(patched) + "\n",
    }


def as_review_issues(result: dict[str, Any]) -> tuple[ReviewIssue, ...]:
    """Convierte el resultado de `analyze` a las incidencias que ya
    expone `ReplayDocument.issues` -la vía para que esto llegue al
    replay real y a Teams, pedida por Roku en el segundo corte, en vez de
    quedar sólo en el subcomando `reconcile`.

    Cada `edit` ya trae una propuesta concreta con evidencia (lo que
    reconcile.py encontró y por qué); se expone como hipótesis a
    confirmar -`proposed_change`-, nunca aplicada al log. Un `finding`
    sin `edit` asociado (falta evidencia suficiente para proponer nada)
    queda igual, sin propuesta.

    Roku, revisión del cuarto corte, 26 sep: todo esto es `"blocking"`,
    no `"warning"` -son exactamente las contradicciones de contenido que
    motivaron COL-102 (switch fantasma, HP/curación imposible, faint
    faltante, evento sin respaldo, origen sin ubicar). `"warning"` queda
    reservado para lo que ya emitía `review_capture` antes de esto y no
    afecta fidelidad (tamaño de roster). Tampoco se descarta más el
    finding de `"origen"`: si no se pudo ubicar la batalla en la traza,
    eso es la razón misma por la que nada más de acá es confiable, y
    antes se perdía en silencio.
    """

    issues: list[ReviewIssue] = []
    for edit in result.get("edits", ()):
        issues.append(
            ReviewIssue(
                "blocking",
                edit["reason"],
                frame=edit.get("frame"),
                alternatives=(edit["before"],),
                proposed_change=edit["after"] or "(eliminar esta línea)",
            )
        )
    for finding in result.get("findings", ()):
        issues.append(
            ReviewIssue(
                "blocking",
                f"[{finding['category']}] {finding['detail']}",
                frame=finding.get("frame"),
            )
        )
    return tuple(issues)
