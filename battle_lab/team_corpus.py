"""Load and schedule auditable Pokémon Showdown team corpora for Battle Lab."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS_MANIFEST = (
    Path(__file__).resolve().parent
    / "corpus"
    / "champions-m-c"
    / "manifest.json"
)
MAX_TEAM_BYTES = 64 * 1024
MAX_EXTRA_TEAMS = 100
SUPPORTED_EXTRA_SUFFIXES = {".txt", ".team"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


def normalize_team_text(value: str) -> str:
    """Canonicalize line endings and insignificant trailing whitespace."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines()).strip()
    if not normalized:
        raise ValueError("el archivo no contiene un equipo")
    return normalized + "\n"


def team_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_string(value: Any, *, field: str, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} debe ser texto")
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"{field} no puede estar vacío")
    return cleaned


def extract_roster(team_text: str) -> tuple[str, ...]:
    """Extract six display species from a standard Showdown export."""

    blocks = [block for block in re.split(r"\n\s*\n", team_text.strip()) if block]
    roster: list[str] = []
    for block in blocks:
        header = block.splitlines()[0].split(" @ ", 1)[0].strip()
        gender = re.search(r"\s+\(([MF])\)$", header)
        if gender:
            header = header[: gender.start()].rstrip()
        nickname = re.search(r"\(([^()]+)\)$", header)
        roster.append(nickname.group(1).strip() if nickname else header)
    return tuple(roster)


def _safe_relative_file(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise ValueError(f"la ruta del equipo debe ser relativa: {relative}")
    root = root.resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"la ruta del equipo sale del corpus: {relative}")
    if not candidate.is_file():
        raise ValueError(f"no existe el archivo del equipo: {relative}")
    if candidate.stat().st_size > MAX_TEAM_BYTES:
        raise ValueError(f"el equipo excede {MAX_TEAM_BYTES} bytes: {relative}")
    return candidate


@dataclass(frozen=True)
class TeamRecord:
    id: str
    description: str
    player: str
    team_text: str
    sha256: str
    roster: tuple[str, ...]
    origin: str
    path: Path
    metadata: dict[str, Any]

    def result_metadata(self, validation_message: str = "") -> dict[str, Any]:
        try:
            display_path = str(self.path.resolve().relative_to(PROJECT_ROOT.resolve()))
        except ValueError:
            display_path = str(self.path.resolve())
        return {
            "id": self.id,
            "description": self.description,
            "player": self.player,
            "origin": self.origin,
            "path": display_path,
            "sha256": self.sha256,
            "roster": list(self.roster),
            "validation": {
                "status": "valid",
                "message": validation_message or None,
            },
            **self.metadata,
        }


@dataclass(frozen=True)
class TeamPairing:
    alpha: TeamRecord
    beta: TeamRecord

    @property
    def canonical_id(self) -> str:
        return "--vs--".join(sorted((self.alpha.id, self.beta.id)))


@dataclass
class TeamCorpus:
    metadata: dict[str, Any]
    teams: list[TeamRecord]
    ignored: list[dict[str, str]]


def load_bundled_corpus(manifest_path: Path) -> TeamCorpus:
    manifest_path = manifest_path.resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"no se pudo leer el manifiesto {manifest_path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("el corpus requiere schemaVersion 1")
    records = payload.get("teams")
    if not isinstance(records, list) or len(records) < 2:
        raise ValueError("el corpus debe contener al menos dos equipos")

    teams: list[TeamRecord] = []
    ids: set[str] = set()
    hashes: set[str] = set()
    for index, value in enumerate(records):
        if not isinstance(value, dict):
            raise ValueError(f"teams[{index}] debe ser un objeto")
        team_id = _clean_string(value.get("id"), field=f"teams[{index}].id")
        if not SAFE_ID.fullmatch(team_id):
            raise ValueError(f"ID de equipo inválido: {team_id!r}")
        if team_id.casefold() in ids:
            raise ValueError(f"ID de equipo duplicado: {team_id}")
        ids.add(team_id.casefold())
        relative_file = _clean_string(
            value.get("file"), field=f"teams[{index}].file"
        )
        path = _safe_relative_file(manifest_path.parent, relative_file)
        team_text = normalize_team_text(path.read_text(encoding="utf-8"))
        actual_sha256 = team_sha256(team_text)
        expected_sha256 = _clean_string(
            value.get("sha256"), field=f"teams[{index}].sha256"
        ).lower()
        if len(expected_sha256) != 64 or actual_sha256 != expected_sha256:
            raise ValueError(
                f"hash inválido para {team_id}: {actual_sha256}; "
                f"se esperaba {expected_sha256}"
            )
        if actual_sha256 in hashes:
            raise ValueError(f"equipo duplicado por contenido: {team_id}")
        hashes.add(actual_sha256)
        roster_value = value.get("roster")
        if (
            not isinstance(roster_value, list)
            or len(roster_value) != 6
            or not all(isinstance(species, str) and species.strip() for species in roster_value)
        ):
            raise ValueError(f"roster inválido para {team_id}")
        extracted = extract_roster(team_text)
        if len(extracted) != 6:
            raise ValueError(
                f"{team_id} contiene {len(extracted)} sets; se esperaban seis"
            )
        metadata = {
            key: value[key]
            for key in (
                "dateShared",
                "event",
                "placing",
                "pokepasteUrl",
                "originalSource",
            )
            if key in value
        }
        teams.append(
            TeamRecord(
                id=team_id,
                description=_clean_string(
                    value.get("description"),
                    field=f"teams[{index}].description",
                ),
                player=_clean_string(
                    value.get("player"), field=f"teams[{index}].player"
                ),
                team_text=team_text,
                sha256=actual_sha256,
                roster=tuple(species.strip() for species in roster_value),
                origin="bundled-vgcpastes",
                path=path,
                metadata=metadata,
            )
        )

    metadata = {
        key: payload[key]
        for key in (
            "schemaVersion",
            "id",
            "format",
            "regulation",
            "snapshotAt",
            "source",
            "selection",
        )
        if key in payload
    }
    try:
        metadata["manifest"] = str(
            manifest_path.relative_to(PROJECT_ROOT.resolve())
        )
    except ValueError:
        metadata["manifest"] = str(manifest_path)
    return TeamCorpus(metadata=metadata, teams=teams, ignored=[])


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:40] or "team"


def _extra_team_paths(directories: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for directory in directories:
        root = directory.resolve()
        if not root.exists():
            continue
        if not root.is_dir():
            raise ValueError(f"--extra-teams-dir no es un directorio: {root}")
        for candidate in sorted(root.rglob("*")):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                continue
            if resolved.is_file() and resolved.suffix.casefold() in SUPPORTED_EXTRA_SUFFIXES:
                paths.append(resolved)
    if len(paths) > MAX_EXTRA_TEAMS:
        raise ValueError(
            f"se encontraron {len(paths)} equipos externos; el máximo es "
            f"{MAX_EXTRA_TEAMS}"
        )
    return paths


def add_extra_teams(corpus: TeamCorpus, directories: Iterable[Path]) -> TeamCorpus:
    teams = list(corpus.teams)
    ignored = list(corpus.ignored)
    known_hashes = {team.sha256 for team in teams}
    known_ids = {team.id.casefold() for team in teams}

    for path in _extra_team_paths(directories):
        try:
            if path.stat().st_size > MAX_TEAM_BYTES:
                raise ValueError(f"excede {MAX_TEAM_BYTES} bytes")
            team_text = normalize_team_text(path.read_text(encoding="utf-8"))
            digest = team_sha256(team_text)
            roster = extract_roster(team_text)
            if len(roster) != 6:
                raise ValueError(f"contiene {len(roster)} sets; se esperaban seis")
            if digest in known_hashes:
                ignored.append(
                    {
                        "path": str(path.resolve()),
                        "reason": "contenido duplicado",
                    }
                )
                continue
            base_id = f"user:{_slug(path.stem)}:{digest[:10]}"
            team_id = base_id
            suffix = 2
            while team_id.casefold() in known_ids:
                team_id = f"{base_id}:{suffix}"
                suffix += 1
            teams.append(
                TeamRecord(
                    id=team_id,
                    description=path.stem,
                    player="Usuario",
                    team_text=team_text,
                    sha256=digest,
                    roster=roster,
                    origin="team-builder-drive",
                    path=path.resolve(),
                    metadata={"originalSource": "Team Builder / Google Drive"},
                )
            )
            known_ids.add(team_id.casefold())
            known_hashes.add(digest)
        except (OSError, UnicodeError, ValueError) as error:
            ignored.append({"path": str(path.resolve()), "reason": str(error)})

    return TeamCorpus(metadata=dict(corpus.metadata), teams=teams, ignored=ignored)


def explicit_pair(team_a: Path, team_b: Path) -> TeamCorpus:
    teams: list[TeamRecord] = []
    for side, path in (("alpha", team_a.resolve()), ("beta", team_b.resolve())):
        if not path.is_file():
            raise ValueError(f"no existe --team-{side[0]}: {path}")
        if path.stat().st_size > MAX_TEAM_BYTES:
            raise ValueError(f"--team-{side[0]} excede {MAX_TEAM_BYTES} bytes")
        team_text = normalize_team_text(path.read_text(encoding="utf-8"))
        roster = extract_roster(team_text)
        if len(roster) != 6:
            raise ValueError(
                f"--team-{side[0]} contiene {len(roster)} sets; se esperaban seis"
            )
        digest = team_sha256(team_text)
        teams.append(
            TeamRecord(
                id=f"explicit:{side}:{digest[:10]}",
                description=path.stem,
                player="Usuario",
                team_text=team_text,
                sha256=digest,
                roster=roster,
                origin="explicit-cli",
                path=path,
                metadata={"originalSource": "CLI explícita"},
            )
        )
    if teams[0].sha256 == teams[1].sha256:
        raise ValueError("--team-a y --team-b no pueden contener el mismo equipo")
    return TeamCorpus(
        metadata={
            "schemaVersion": 1,
            "id": "explicit-cli-pair",
            "format": None,
            "regulation": None,
            "selection": "Pareja explícita; no rota.",
        },
        teams=teams,
        ignored=[],
    )


def build_pairing_schedule(
    teams: Sequence[TeamRecord], *, count: int, seed: int
) -> list[TeamPairing]:
    """Build balanced, deterministic round-robin pairings.

    No unordered pair repeats until every possible pair has played. Appearances
    and Alpha/Beta assignments differ by at most one for every partial schedule.
    """

    if count < 1:
        raise ValueError("count debe ser mayor que cero")
    if len(teams) < 2:
        raise ValueError("se requieren al menos dos equipos válidos")
    if len({team.sha256 for team in teams}) != len(teams):
        raise ValueError("la rotación recibió equipos duplicados")

    rng = random.Random(seed)
    unordered: list[tuple[str, str]] = []
    appearance_counts = {team.id: 0 for team in teams}
    previous_pair: frozenset[str] | None = None

    while len(unordered) < count:
        rotation: list[TeamRecord | None] = list(teams)
        rng.shuffle(rotation)
        if len(rotation) % 2:
            rotation.append(None)
        for _round_index in range(len(rotation) - 1):
            pairs = [
                (rotation[index], rotation[-1 - index])
                for index in range(len(rotation) // 2)
            ]
            pairs = [(left, right) for left, right in pairs if left and right]
            rng.shuffle(pairs)
            pairs.sort(
                key=lambda pair: (
                    max(
                        appearance_counts[pair[0].id],
                        appearance_counts[pair[1].id],
                    ),
                    appearance_counts[pair[0].id]
                    + appearance_counts[pair[1].id],
                )
            )
            if previous_pair and pairs:
                first = frozenset((pairs[0][0].id, pairs[0][1].id))
                if first == previous_pair and len(pairs) > 1:
                    pairs[0], pairs[1] = pairs[1], pairs[0]
            for left, right in pairs:
                unordered.append((left.id, right.id))
                appearance_counts[left.id] += 1
                appearance_counts[right.id] += 1
                previous_pair = frozenset((left.id, right.id))
                if len(unordered) == count:
                    break
            if len(unordered) == count:
                break
            rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    oriented = _orient_edges_balanced(unordered, rng)
    by_id = {team.id: team for team in teams}
    return [
        TeamPairing(alpha=by_id[alpha_id], beta=by_id[beta_id])
        for alpha_id, beta_id in oriented
    ]


def _orient_edges_balanced(
    edges: Sequence[tuple[str, str]], rng: random.Random
) -> list[tuple[str, str]]:
    """Orient a multigraph so every vertex has Alpha/Beta difference <= 1."""

    adjacency: dict[str, list[tuple[int, str]]] = {}
    edge_records: list[tuple[str, str, int | None]] = []

    def add_edge(left: str, right: str, original_index: int | None) -> None:
        edge_index = len(edge_records)
        edge_records.append((left, right, original_index))
        adjacency.setdefault(left, []).append((edge_index, right))
        adjacency.setdefault(right, []).append((edge_index, left))

    for index, (left, right) in enumerate(edges):
        add_edge(left, right, index)

    visited_vertices: set[str] = set()
    for root in list(adjacency):
        if root in visited_vertices:
            continue
        component: list[str] = []
        stack = [root]
        visited_vertices.add(root)
        while stack:
            vertex = stack.pop()
            component.append(vertex)
            for _edge_index, neighbor in adjacency[vertex]:
                if neighbor not in visited_vertices:
                    visited_vertices.add(neighbor)
                    stack.append(neighbor)
        odd = [vertex for vertex in component if len(adjacency[vertex]) % 2]
        rng.shuffle(odd)
        for index in range(0, len(odd), 2):
            add_edge(odd[index], odd[index + 1], None)

    for neighbors in adjacency.values():
        rng.shuffle(neighbors)

    used_edges: set[int] = set()
    directions: dict[int, tuple[str, str]] = {}
    for root in list(adjacency):
        if all(edge_index in used_edges for edge_index, _ in adjacency[root]):
            continue
        vertex_stack = [root]
        traversed_stack: list[tuple[int, str, str]] = []
        while vertex_stack:
            vertex = vertex_stack[-1]
            while adjacency[vertex] and adjacency[vertex][-1][0] in used_edges:
                adjacency[vertex].pop()
            if adjacency[vertex]:
                edge_index, neighbor = adjacency[vertex].pop()
                if edge_index in used_edges:
                    continue
                used_edges.add(edge_index)
                vertex_stack.append(neighbor)
                traversed_stack.append((edge_index, vertex, neighbor))
            else:
                vertex_stack.pop()
                if traversed_stack:
                    edge_index, left, right = traversed_stack.pop()
                    original_index = edge_records[edge_index][2]
                    if original_index is not None:
                        directions[original_index] = (left, right)

    if len(directions) != len(edges):
        raise RuntimeError("no fue posible orientar toda la agenda")
    return [directions[index] for index in range(len(edges))]


def pairing_statistics(schedule: Sequence[TeamPairing]) -> dict[str, Any]:
    appearances: dict[str, dict[str, int]] = {}
    for pairing in schedule:
        for side, team in (("alpha", pairing.alpha), ("beta", pairing.beta)):
            entry = appearances.setdefault(
                team.id, {"total": 0, "alpha": 0, "beta": 0}
            )
            entry["total"] += 1
            entry[side] += 1
    unique_pairings = len({pairing.canonical_id for pairing in schedule})
    return {
        "mode": "balanced-round-robin",
        "requested": len(schedule),
        "uniquePairings": unique_pairings,
        "repeatedPairings": len(schedule) - unique_pairings,
        "appearances": appearances,
    }
