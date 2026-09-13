import { hashPaste, parseShowdownPaste } from "@/lib/paste";
import {
  buildScoutingPasteLibraryResponse,
  type ScoutingPasteDetail,
  type ScoutingPasteSummary,
} from "@/lib/scouting-paste-library";
import { DEFAULT_BATTLE_FORMAT, DEFAULT_BATTLE_MECHANICS, normalizeMechanics } from "@/lib/team-builder";
import type { PokemonSet } from "@/lib/types";

import { DomainError } from "./queries";
import { getDatabase } from "./raw";

interface ScoutingPasteRow {
  id: string;
  name: string;
  creator: string;
  source_url: string;
  source_label: string;
  scouting_format: string;
  scouting_notes: string;
  created_at: string;
  updated_at: string;
  version_id: string;
  paste: string;
}

export interface CreateScoutingPasteInput {
  name?: string;
  creator?: string;
  format?: string;
  sourceUrl?: string;
  sourceLabel?: string;
  notes?: string;
  paste?: string;
}

function normalizePaste(value: unknown) {
  if (typeof value !== "string") throw new DomainError("Pega un equipo válido.");
  const normalized = value.replace(/\r\n?/g, "\n").trim();
  if (!normalized) throw new DomainError("Pega un equipo válido.");
  if (new TextEncoder().encode(normalized).byteLength > 64 * 1024) {
    throw new DomainError("El paste es demasiado grande.", 413);
  }
  return normalized;
}

function clean(value: unknown, max: number) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

function normalizeSourceUrl(value: unknown) {
  const raw = clean(value, 500);
  if (!raw) return "";
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password) throw new Error("bad source");
    return url.toString();
  } catch {
    throw new DomainError("La fuente debe ser una URL https válida.");
  }
}

function defaultName(pokemon: PokemonSet[], creator: string) {
  const core = pokemon.slice(0, 2).map((set) => set.species).filter(Boolean).join(" / ");
  return `${creator ? `${creator} · ` : ""}${core || "Equipo de scouting"}`.slice(0, 80);
}

function latestRowsSql(where = "") {
  return `
    SELECT
      t.id, t.name, t.creator, t.source_url, t.source_label, t.scouting_format,
      t.scouting_notes, t.created_at, t.updated_at,
      v.id AS version_id, v.paste
    FROM teams t
    JOIN team_versions v ON v.id = (
      SELECT v2.id
      FROM team_versions v2
      WHERE v2.team_id = t.id
      ORDER BY v2.version_number DESC, v2.minor_version DESC, v2.created_at DESC
      LIMIT 1
    )
    WHERE t.scope = 'scouting' ${where}
    ORDER BY t.updated_at DESC, t.name COLLATE NOCASE ASC, t.id ASC
  `;
}

function rowToDetail(row: ScoutingPasteRow): ScoutingPasteDetail {
  let pokemon: string[] = [];
  try {
    pokemon = parseShowdownPaste(row.paste).map((set) => set.species);
  } catch {
    pokemon = [];
  }
  return {
    id: row.id,
    name: row.name,
    creator: row.creator,
    format: row.scouting_format,
    sourceUrl: row.source_url,
    sourceLabel: row.source_label,
    notes: row.scouting_notes,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    pokemon,
    versionId: row.version_id,
    paste: row.paste,
  };
}

function summary(detail: ScoutingPasteDetail): ScoutingPasteSummary {
  const { versionId: _versionId, paste: _paste, ...value } = detail;
  return value;
}

export async function listOwnedTeamIds(): Promise<Set<string>> {
  const db = await getDatabase();
  const rows = await db.prepare("SELECT id FROM teams WHERE scope = 'owned'").all<{ id: string }>();
  return new Set(rows.results.map((row) => row.id));
}

export async function listScoutingPasteDetails(): Promise<ScoutingPasteDetail[]> {
  const db = await getDatabase();
  const result = await db.prepare(latestRowsSql()).all<ScoutingPasteRow>();
  return result.results.map(rowToDetail);
}

export async function listScoutingPasteLibrary(options: {
  pokemon?: string | string[];
  format?: string;
  search?: string;
  page?: number;
  pageSize?: number;
} = {}) {
  const records = (await listScoutingPasteDetails()).map(summary);
  return buildScoutingPasteLibraryResponse(records, options);
}

export async function getScoutingPaste(id: string): Promise<ScoutingPasteDetail> {
  const db = await getDatabase();
  const row = await db
    .prepare(latestRowsSql("AND t.id = ?"))
    .bind(id)
    .first<ScoutingPasteRow>();
  if (!row) throw new DomainError("No encontramos ese paste guardado.", 404);
  return rowToDetail(row);
}

function pokemonStatements(db: D1Database, versionId: string, pokemon: PokemonSet[]) {
  return pokemon.map((set) =>
    db
      .prepare(
        "INSERT INTO pokemon_sets (id, team_version_id, slot, nickname, species, item, ability, level, tera_type, mechanics_json, evs, nature, moves_json, types_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        crypto.randomUUID(),
        versionId,
        set.slot,
        set.nickname,
        set.species,
        set.item,
        set.ability,
        set.level,
        set.teraType,
        JSON.stringify(set.mechanics ?? {}),
        set.evs,
        set.nature,
        JSON.stringify(set.moves),
        JSON.stringify(set.types),
      ),
  );
}

export async function createScoutingPaste(input: CreateScoutingPasteInput): Promise<ScoutingPasteDetail> {
  const paste = normalizePaste(input.paste);
  const pokemon = parseShowdownPaste(paste);
  if (!pokemon.length) throw new DomainError("El paste no contiene Pokémon utilizables.");

  const creator = clean(input.creator, 80);
  const name = clean(input.name, 80) || defaultName(pokemon, creator);
  const format = clean(input.format, 80) || "Sin formato";
  const sourceUrl = normalizeSourceUrl(input.sourceUrl);
  const sourceLabel = clean(input.sourceLabel, 80);
  const notes = clean(input.notes, 500);
  const db = await getDatabase();

  const existing = await db
    .prepare(`
      SELECT t.id, v.paste
      FROM teams t
      JOIN team_versions v ON v.team_id = t.id
      WHERE t.scope = 'scouting'
    `)
    .all<{ id: string; paste: string }>();
  const duplicate = existing.results.find((row) => row.paste.replace(/\r\n?/g, "\n").trim() === paste);
  if (duplicate) throw new DomainError("Este paste ya está guardado en Scouting.", 409);

  const teamId = crypto.randomUUID();
  const versionId = crypto.randomUUID();
  const pasteHash = await hashPaste(paste);
  const mechanics = normalizeMechanics([...DEFAULT_BATTLE_MECHANICS]).sort();

  await db.batch([
    db
      .prepare(
        "INSERT INTO teams (id, name, scope, creator, source_url, source_label, scouting_format, scouting_notes, folder_id, sort_order) VALUES (?, ?, 'scouting', ?, ?, ?, ?, ?, NULL, 0)",
      )
      .bind(teamId, name, creator, sourceUrl, sourceLabel, format, notes),
    db
      .prepare(
        "INSERT INTO team_versions (id, team_id, version_number, minor_version, format, mechanics_json, paste, paste_hash) VALUES (?, ?, 1, 0, ?, ?, ?, ?)",
      )
      .bind(versionId, teamId, DEFAULT_BATTLE_FORMAT, JSON.stringify(mechanics), paste, pasteHash),
    ...pokemonStatements(db, versionId, pokemon),
  ]);

  return getScoutingPaste(teamId);
}

export async function deleteScoutingPaste(id: string) {
  const db = await getDatabase();
  const existing = await db
    .prepare("SELECT id FROM teams WHERE id = ? AND scope = 'scouting'")
    .bind(id)
    .first<{ id: string }>();
  if (!existing) throw new DomainError("No encontramos ese paste guardado.", 404);
  await db.prepare("DELETE FROM teams WHERE id = ? AND scope = 'scouting'").bind(id).run();
  return { id };
}
