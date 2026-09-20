from __future__ import annotations

import html
import json
from pathlib import Path

from .models import BattleEvent, BattleSide, CapturedBattle, ReplayDocument


def _identifier(slot: str, species: str) -> str:
    return f"{slot}: {species}"


def _health(value: str | None) -> str:
    return value or "100/100"


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
        species = active.get(event.slot) or event.species or "Pokémon"
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


def _event_order(event: BattleEvent) -> tuple[int, int, int]:
    """Preserva tiempo/frame, pero da prioridad causal a entradas al campo."""

    switch_priority = 0 if event.kind in {"switch", "drag"} else 1
    return event.timestamp_ms, switch_priority, event.source_frame or -1


def _activate_missing_slot(event: BattleEvent, active: dict[str, str]) -> list[str]:
    """Evita protocolo inválido si OCR vio una acción antes que el send-out."""

    if (
        not event.slot
        or not event.species
        or event.slot in active
        or event.kind in {"switch", "drag"}
    ):
        return []
    active[event.slot] = event.species
    return [f"|switch|{_identifier(event.slot, event.species)}|{event.species}, L50|100/100"]


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
    for event in sorted(battle.events, key=_event_order):
        lines.extend(_activate_missing_slot(event, active))
        lines.extend(_event_lines(event, active, side_names, mega_formes))
    winner_name = battle.p1.name if battle.winner == "p1" else battle.p2.name
    lines.append(f"|win|{winner_name}")

    choices = []
    for side_id, side in (("p1", battle.p1), ("p2", battle.p2)):
        code = _selection_code(side)
        if code:
            choices.append(f">{side_id} team {code}")

    return ReplayDocument(
        log="\n".join(lines),
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
