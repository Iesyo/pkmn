from __future__ import annotations

import html
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .models import ACTOR_IDENTITY_PREFIX, BattleEvent, BattleSide, CapturedBattle, ReplayDocument


def _identifier(slot: str, species: str) -> str:
    return f"{slot}: {species}"


def _base_species(species: str) -> str:
    base = species.split("-", 1)[0]
    return "".join(character for character in base.lower() if character.isalnum())


def _named_species(event: BattleEvent, active: dict[str, str]) -> str:
    """Quién es el Pokémon del evento, dando prioridad a lo que dijo el juego.

    Normalmente manda el ocupante que llevamos apuntado en el slot, porque
    conserva la forma tras una megaevolución. Pero si el mensaje nombró a otro
    Pokémon distinto, el apunte está desfasado y lo escrito en pantalla gana.
    """

    tracked = active.get(event.slot or "")
    if not event.species or not tracked:
        return tracked or event.species or "Pokémon"
    if _base_species(tracked) == _base_species(event.species):
        return tracked
    return event.species


def _health(value: str | None) -> str:
    return value or "100/100"


def _health_key(slot: str, species: str) -> tuple[str, str]:
    return slot[:2], _base_species(species)


def _health_maximums(events: Sequence[BattleEvent]) -> dict[tuple[str, str], str]:
    """Máximo de cada Pokémon, votado entre todas las lecturas del combate.

    El HUD lo repite en cada daño y en cada curación, así que una lectura mala
    pierde frente a las buenas.
    """

    active: dict[str, str] = {}
    readings: dict[tuple[str, str], Counter[str]] = {}
    for event in events:
        if event.kind in {"switch", "drag"} and event.slot and event.species:
            active[event.slot] = event.species
        if not event.slot or not event.health:
            continue
        species = active.get(event.slot) or event.species
        maximum = event.health.partition("/")[2]
        if not species or not maximum.isdigit():
            continue
        readings.setdefault(_health_key(event.slot, species), Counter())[maximum] += 1
    return {key: counts.most_common(1)[0][0] for key, counts in readings.items()}


def _with_known_health(events: Sequence[BattleEvent]) -> tuple[BattleEvent, ...]:
    """Completa la vida de los cambios que el HUD no acompañó.

    Un Pokémon conserva su vida al salir del campo, así que quien vuelve entra
    con la última que se le vio. Y quien pisa el campo por primera vez entra a
    tope, para lo que basta el máximo que el propio log revela en cuanto
    recibe daño. Rellenar con 100/100 lo hacía reaparecer lleno y le cambiaba
    el máximo a mitad del log.
    """

    maximums = _health_maximums(events)
    active: dict[str, str] = {}
    health: dict[tuple[str, str], str] = {}
    completed: list[BattleEvent] = []
    for event in events:
        if event.kind in {"switch", "drag"} and event.slot and event.species:
            active[event.slot] = event.species
            key = _health_key(event.slot, event.species)
            current = event.health or health.get(key)
            if not current:
                maximum = maximums.get(key)
                current = f"{maximum}/{maximum}" if maximum else None
            if current:
                health[key] = current
                event = replace(event, health=current)
        elif event.slot and event.health:
            species = active.get(event.slot) or event.species
            if species:
                health[_health_key(event.slot, species)] = event.health
        completed.append(event)
    return tuple(completed)


def _with_known_target(events: Sequence[BattleEvent]) -> tuple[BattleEvent, ...]:
    """Completa a quién apuntó un movimiento cuando sólo hay un candidato.

    El detector sabe quién usó el movimiento y, por separado, a quién le bajó
    la vida; nunca cruza las dos cosas porque la pantalla no nombra el
    objetivo. Sin él, el visor oficial anima el golpe contra un slot fijo en
    vez del rival real. Cuando un único Pokémon del lado contrario recibió
    daño antes de la próxima acción (otro movimiento, un cambio o un nuevo
    turno), ese es su objetivo sin dudas; con dos o ninguno -un movimiento de
    área, uno que falló- no se adivina.
    """

    completed: list[BattleEvent] = []
    for index, event in enumerate(events):
        if event.kind != "move" or event.target_slot is not None or not event.slot:
            completed.append(event)
            continue
        opposing_prefix = "p2" if event.slot.startswith("p1") else "p1"
        hit_slots: set[str] = set()
        for later in events[index + 1 :]:
            if later.kind in {"move", "switch", "drag", "turn"}:
                break
            if later.kind == "damage" and later.slot and later.slot.startswith(opposing_prefix):
                hit_slots.add(later.slot)
        if len(hit_slots) == 1:
            event = replace(event, target_slot=next(iter(hit_slots)))  # type: ignore[arg-type]
        completed.append(event)
    return tuple(completed)


def _selection_code(side: BattleSide) -> str:
    indexes: list[str] = []
    for species in side.selected:
        species_id = "".join(character for character in species.lower() if character.isalnum())
        index = next((position for position, member in enumerate(side.team) if "".join(
            character for character in member.lower() if character.isalnum()
        ) == species_id), None)
        if index is not None:
            indexes.append(str(index + 1))
    return "".join(indexes[:4])


def _event_lines(
    event: BattleEvent,
    active: dict[str, str],
    side_names: dict[str, str],
    mega_formes: dict[tuple[str, str], str],
) -> list[str]:
    if event.kind == "turn":
        return [f"|turn|{event.turn}"]

    if event.kind in {"switch", "drag"}:
        assert event.slot and event.species
        active[event.slot] = event.species
        details_species = mega_formes.get((event.slot[:2], event.species), event.species)
        return [
            f"|{event.kind}|{_identifier(event.slot, event.species)}|{details_species}, L50|{_health(event.health)}"
        ]

    if event.kind == "move":
        assert event.slot and event.move
        actor_species = active.get(event.slot) or event.species or "Pokémon"
        target_species = active.get(event.target_slot or "", "Pokémon")
        target = _identifier(event.target_slot, target_species) if event.target_slot else ""
        return [f"|move|{_identifier(event.slot, actor_species)}|{event.move}|{target}"]

    if event.kind in {"damage", "heal"}:
        assert event.slot and event.health
        species = active.get(event.slot) or event.species or "Pokémon"
        tags = "".join(f"|{tag}" for tag in event.tags)
        return [f"|-{event.kind}|{_identifier(event.slot, species)}|{event.health}{tags}"]

    if event.kind in {"status", "curestatus", "ability", "item", "enditem", "terastallize"}:
        assert event.slot and event.value
        species = active.get(event.slot) or event.species or "Pokémon"
        return [f"|-{event.kind}|{_identifier(event.slot, species)}|{event.value}"]

    if event.kind == "mega":
        assert event.slot and event.species and event.forme and event.value
        species = active.get(event.slot, event.species)
        identifier = _identifier(event.slot, species)
        mega_formes[(event.slot[:2], event.species)] = event.forme
        return [
            f"|detailschange|{identifier}|{event.forme}, L50",
            f"|-mega|{identifier}|{event.species}|{event.value}",
        ]

    if event.kind in {"faint", "crit"}:
        assert event.slot
        species = _named_species(event, active)
        prefix = "" if event.kind == "faint" else "-"
        return [f"|{prefix}{event.kind}|{_identifier(event.slot, species)}"]

    if event.kind in {"weather", "fieldstart", "fieldend"}:
        value = event.value or ""
        tags = "".join(f"|{tag}" for tag in event.tags)
        return [f"|-{event.kind}|{value}{tags}"]

    if event.kind in {"sidestart", "sideend"}:
        if not event.slot:
            return []
        side = event.slot[:2]
        value = event.value or ""
        return [f"|-{event.kind}|{side}: {side_names[side]}|{value}"]

    if event.kind == "message" and event.value:
        return [f"|-message|{event.value}"]

    return []


def build_replay_document(battle: CapturedBattle) -> ReplayDocument:
    """Convierte la evidencia normalizada en el protocolo público de Showdown."""
    lines = [
        f"|t:|{int(battle.started_at.timestamp())}",
        f"|player|p1|{battle.p1.name}|1|",
        f"|player|p2|{battle.p2.name}|2|",
        "|gametype|doubles",
        "|gen|9",
        f"|tier|{battle.format}",
    ]
    for side_id, side in (("p1", battle.p1), ("p2", battle.p2)):
        lines.append(f"|teamsize|{side_id}|{len(side.team)}")
        lines.extend(f"|poke|{side_id}|{species}, L50|" for species in side.team)
    lines.extend(["|teampreview", "|start"])

    active: dict[str, str] = {}
    mega_formes: dict[tuple[str, str], str] = {}
    side_names = {"p1": battle.p1.name, "p2": battle.p2.name}
    for event in _with_known_health(_with_known_target(battle.events)):
        lines.extend(_event_lines(event, active, side_names, mega_formes))
    winner_name = battle.p1.name if battle.winner == "p1" else battle.p2.name
    lines.append(f"|win|{winner_name}")

    choices = []
    for side_id, side in (("p1", battle.p1), ("p2", battle.p2)):
        code = _selection_code(side)
        if code:
            choices.append(f">{side_id} team {code}")

    protocol = "\n".join(lines)
    for identity, species in sorted(battle.identities, key=lambda item: len(item[0]), reverse=True):
        protocol = protocol.replace(identity, species)
    if ACTOR_IDENTITY_PREFIX in protocol:
        raise ValueError("El replay conserva una identidad sin especie al finalizar.")

    return ReplayDocument(
        log=protocol,
        inputlog="\n".join(choices),
        uploadtime=int(battle.started_at.timestamp()),
        p1=battle.p1.name,
        p2=battle.p2.name,
        format=battle.format,
    )


def render_replay_html(document: ReplayDocument, *, replay_id: str = "champions-reconstructed") -> str:
    """Crea el contenedor HTML reconocido por el visor oficial de Showdown."""
    title = html.escape(f"{document.format}: {document.p1} vs. {document.p2}")
    safe_id = html.escape(replay_id, quote=True)
    protocol = document.log.replace("/", r"\/")
    return f"""<!DOCTYPE html>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="upgrade-insecure-requests" />
<title>{title}</title>
<style>
html,body{{font-family:Verdana,sans-serif;font-size:10pt;margin:0;padding:0;background:#eef2f5}}
body{{padding:12px 0}}.wrapper{{max-width:1180px;margin:0 auto}}
</style>
<div class="wrapper replay-wrapper">
  <input type="hidden" name="replayid" value="{safe_id}" />
  <div class="battle"></div><div class="battle-log"></div>
  <div class="replay-controls"></div><div class="replay-controls-2"></div>
  <h1 style="font-weight:normal;text-align:center">{title}</h1>
  <script type="text/plain" class="battle-log-data">{protocol}</script>
</div>
<script>
let daily=Math.floor(Date.now()/1000/60/60/24);
document.write('<script src="https://play.pokemonshowdown.com/js/replay-embed.js?version'+daily+'"><\\/script>');
</script>
"""


def write_replay_artifacts(
    document: ReplayDocument,
    output_stem: Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path, Path]:
    json_path = output_stem.with_suffix(".json")
    log_path = output_stem.with_suffix(".log")
    html_path = output_stem.with_suffix(".html")
    paths = (json_path, log_path, html_path)
    if not overwrite:
        existing = [path for path in paths if path.exists()]
        if existing:
            raise FileExistsError(f"Ya existe {existing[0]}; usa --force para reemplazarlo.")
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(document.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_path.write_text(document.log + "\n", encoding="utf-8")
    html_path.write_text(render_replay_html(document, replay_id=output_stem.name), encoding="utf-8")
    return paths
