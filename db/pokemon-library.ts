import { getMoveData, getSpeciesTypes, toId } from "@/lib/pokemon-data";
import { hashPaste } from "@/lib/paste";
import {
  EV_STATS,
  normalizeMechanics,
  parseEvs,
  serializeShowdownPaste,
} from "@/lib/team-builder";
import type {
  BattleMechanic,
  MoveSet,
  PokemonSet,
  PokemonType,
} from "@/lib/types";
import { POKEMON_TYPES } from "@/lib/types";
import { getDatabase } from "./raw";

interface SnapshotRow {
  id: string;
  team_version_id: string;
  slot: number;
  nickname: string;
  species: string;
  item: string;
  ability: string;
  level: number;
  tera_type: string | null;
  mechanics_json: string;
  evs: string;
  nature: string;
  moves_json: string;
  types_json: string;
  format: string;
  team_mechanics_json: string;
  version_number: number;
  minor_version: number;
  version_created_at: string;
  team_id: string;
  team_name: string;
}

interface LibraryVersionRow {
  id: string;
  entry_id: string;
  version_number: number;
  paste: string;
  set_json: string;
  created_at: string;
}

interface LibraryUsageRow {
  library_version_id: string;
  team_id: string;
  team_name: string;
  team_version_id: string;
  version_number: number;
  minor_version: number;
  slot: number;
}

export interface PokemonLibrarySource {
  teamId: string;
  teamName: string;
  teamVersionId: string;
  teamVersion: string;
  slot: number;
}

export interface PokemonLibraryVersion {
  id: string;
  version: number;
  paste: string;
  createdAt: string;
  set: PokemonSet;
  sources: PokemonLibrarySource[];
}

export interface PokemonLibraryEntry {
  id: string;
  species: string;
  format: string;
  versions: PokemonLibraryVersion[];
}

function parseJson<T>(value: string | null | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

function normalizeMoves(value: string): MoveSet[] {
  return parseJson<Partial<MoveSet>[]>(value, []).map((move) => {
    const name = move.name ?? "Movimiento";
    const data = getMoveData(name);
    return {
      name,
      type: (move.type as PokemonType | null | undefined) ?? data.type,
      damaging: move.damaging ?? data.damaging,
      usage: 0,
    };
  });
}

function toPokemonSet(row: SnapshotRow): PokemonSet {
  const storedTypes = parseJson<PokemonType[]>(row.types_json, []);
  const types = storedTypes
    .filter((type): type is PokemonType => POKEMON_TYPES.includes(type))
    .slice(0, 2);

  return {
    id: row.id,
    slot: row.slot,
    nickname: row.nickname,
    species: row.species,
    item: row.item,
    ability: row.ability,
    level: row.level,
    teraType: row.tera_type as PokemonType | null,
    mechanics: parseJson<PokemonSet["mechanics"]>(row.mechanics_json, {}),
    evs: row.evs,
    nature: row.nature,
    moves: normalizeMoves(row.moves_json),
    types: types.length ? types : getSpeciesTypes(row.species),
    performance: {
      games: 0,
      wins: 0,
      leadGames: 0,
      leadWins: 0,
      selectionRate: 0,
    },
  };
}

function canonicalSignature(row: SnapshotRow) {
  const setMechanics = parseJson<PokemonSet["mechanics"]>(row.mechanics_json, {});
  const teamMechanics = normalizeMechanics(
    parseJson<string[]>(row.team_mechanics_json, []),
  ).sort();
  const allocation = parseEvs(row.evs);
  const moveIds = normalizeMoves(row.moves_json)
    .map((move) => toId(move.name))
    .filter(Boolean)
    .sort();

  // Nickname, slot, derived types and move metadata are intentionally excluded:
  // they do not change the competitive set and must not create duplicate versions.
  return JSON.stringify({
    species: toId(row.species),
    format: row.format,
    mechanics: teamMechanics,
    item: toId(row.item),
    ability: toId(row.ability),
    level: row.level || 50,
    teraType: toId(row.tera_type ?? ""),
    pokemonMechanics: {
      dynamaxLevel: Number(setMechanics?.dynamaxLevel ?? 10),
      gigantamax: Boolean(setMechanics?.gigantamax),
      megaEvolution: Boolean(setMechanics?.megaEvolution),
      zMove: Boolean(setMechanics?.zMove),
    },
    stats: EV_STATS.map((stat) => Number(allocation[stat] ?? 0)),
    nature: toId(row.nature),
    moves: moveIds,
  });
}

function formatTeamVersion(version: number, minorVersion: number) {
  return minorVersion
    ? `${version}.${String(minorVersion).padStart(2, "0")}`
    : String(version);
}

async function syncPokemonLibrary() {
  const db = await getDatabase();
  const snapshots = await db
    .prepare(
      `SELECT
        ps.id, ps.team_version_id, ps.slot, ps.nickname, ps.species, ps.item,
        ps.ability, ps.level, ps.tera_type, ps.mechanics_json, ps.evs,
        ps.nature, ps.moves_json, ps.types_json,
        tv.format, tv.mechanics_json AS team_mechanics_json,
        tv.version_number, tv.minor_version, tv.created_at AS version_created_at,
        t.id AS team_id, t.name AS team_name
      FROM pokemon_sets ps
      JOIN team_versions tv ON tv.id = ps.team_version_id
      JOIN teams t ON t.id = tv.team_id
      ORDER BY tv.created_at ASC, t.id ASC, tv.version_number ASC,
        tv.minor_version ASC, ps.slot ASC`,
    )
    .all<SnapshotRow>();

  for (const row of snapshots.results) {
    const speciesKey = toId(row.species);
    if (!speciesKey) continue;

    await db
      .prepare(
        "INSERT OR IGNORE INTO pokemon_library_entries (id, species, species_key, format, created_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(
        crypto.randomUUID(),
        row.species,
        speciesKey,
        row.format,
        row.version_created_at,
      )
      .run();

    const entry = await db
      .prepare(
        "SELECT id FROM pokemon_library_entries WHERE species_key = ? AND format = ?",
      )
      .bind(speciesKey, row.format)
      .first<{ id: string }>();
    if (!entry) continue;

    const setHash = await hashPaste(canonicalSignature(row));
    let libraryVersion = await db
      .prepare(
        "SELECT id, version_number FROM pokemon_library_versions WHERE entry_id = ? AND set_hash = ?",
      )
      .bind(entry.id, setHash)
      .first<{ id: string; version_number: number }>();

    if (!libraryVersion) {
      const maxVersion = await db
        .prepare(
          "SELECT MAX(version_number) AS max_version FROM pokemon_library_versions WHERE entry_id = ?",
        )
        .bind(entry.id)
        .first<{ max_version: number | null }>();
      const versionNumber = Number(maxVersion?.max_version ?? 0) + 1;
      const set = toPokemonSet(row);
      const mechanics = normalizeMechanics(
        parseJson<string[]>(row.team_mechanics_json, []),
      ) as BattleMechanic[];
      const versionId = crypto.randomUUID();

      await db
        .prepare(
          "INSERT OR IGNORE INTO pokemon_library_versions (id, entry_id, version_number, set_hash, paste, set_json, source_team_version_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(
          versionId,
          entry.id,
          versionNumber,
          setHash,
          serializeShowdownPaste([set], mechanics),
          JSON.stringify(set),
          row.team_version_id,
          row.version_created_at,
        )
        .run();

      libraryVersion = await db
        .prepare(
          "SELECT id, version_number FROM pokemon_library_versions WHERE entry_id = ? AND set_hash = ?",
        )
        .bind(entry.id, setHash)
        .first<{ id: string; version_number: number }>();
    }

    if (!libraryVersion) continue;

    await db
      .prepare(
        `INSERT INTO pokemon_library_usages
          (id, library_version_id, team_version_id, slot)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(team_version_id, slot)
        DO UPDATE SET library_version_id = excluded.library_version_id`,
      )
      .bind(
        crypto.randomUUID(),
        libraryVersion.id,
        row.team_version_id,
        row.slot,
      )
      .run();
  }
}

export async function listPokemonLibrary(format?: string) {
  await syncPokemonLibrary();
  const db = await getDatabase();

  const versionSql = `SELECT
      e.id AS entry_id, e.species, e.format,
      v.id, v.entry_id, v.version_number, v.paste, v.set_json, v.created_at
    FROM pokemon_library_entries e
    JOIN pokemon_library_versions v ON v.entry_id = e.id
    ${format ? "WHERE e.format = ?" : ""}
    ORDER BY e.species COLLATE NOCASE ASC, v.version_number DESC`;
  const versionStatement = db.prepare(versionSql);
  const versionResult = format
    ? await versionStatement.bind(format).all<LibraryVersionRow & { entry_id: string; species: string; format: string }>()
    : await versionStatement.all<LibraryVersionRow & { entry_id: string; species: string; format: string }>();

  const usageResult = await db
    .prepare(
      `SELECT
        u.library_version_id, u.slot,
        t.id AS team_id, t.name AS team_name,
        tv.id AS team_version_id, tv.version_number, tv.minor_version
      FROM pokemon_library_usages u
      JOIN team_versions tv ON tv.id = u.team_version_id
      JOIN teams t ON t.id = tv.team_id
      ORDER BY t.name COLLATE NOCASE ASC, tv.version_number DESC,
        tv.minor_version DESC, u.slot ASC`,
    )
    .all<LibraryUsageRow>();

  const sourcesByVersion = new Map<string, PokemonLibrarySource[]>();
  for (const usage of usageResult.results) {
    const sources = sourcesByVersion.get(usage.library_version_id) ?? [];
    sources.push({
      teamId: usage.team_id,
      teamName: usage.team_name,
      teamVersionId: usage.team_version_id,
      teamVersion: formatTeamVersion(
        usage.version_number,
        usage.minor_version,
      ),
      slot: usage.slot,
    });
    sourcesByVersion.set(usage.library_version_id, sources);
  }

  const entries = new Map<string, PokemonLibraryEntry>();
  for (const row of versionResult.results) {
    const current = entries.get(row.entry_id) ?? {
      id: row.entry_id,
      species: row.species,
      format: row.format,
      versions: [],
    };
    current.versions.push({
      id: row.id,
      version: row.version_number,
      paste: row.paste,
      createdAt: row.created_at,
      set: parseJson<PokemonSet>(row.set_json, {} as PokemonSet),
      sources: sourcesByVersion.get(row.id) ?? [],
    });
    entries.set(row.entry_id, current);
  }

  return [...entries.values()];
}
