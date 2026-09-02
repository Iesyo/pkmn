import type { TeamFolder } from "@/lib/types";

import { DomainError } from "./queries";
import { getDatabase } from "./raw";

interface TeamFolderRow {
  id: string;
  name: string;
  sort_order: number;
  created_at: string;
}

interface TeamFolderAssignmentRow {
  id: string;
  folder_id: string | null;
}

function normalizeFolderName(name: string) {
  const normalized = name.trim().replace(/\s+/g, " ");
  if (normalized.length < 1 || normalized.length > 40) {
    throw new DomainError("El nombre de la carpeta debe tener entre 1 y 40 caracteres.");
  }
  return normalized;
}

function normalizeFolderOrder(folderIds: unknown): string[] {
  if (!Array.isArray(folderIds) || folderIds.some((id) => typeof id !== "string" || !id.trim())) {
    throw new DomainError("El orden de carpetas no es válido.");
  }
  const normalized = folderIds.map((id) => id.trim());
  if (new Set(normalized).size !== normalized.length) {
    throw new DomainError("El orden de carpetas contiene carpetas duplicadas.");
  }
  return normalized;
}

function toFolder(row: TeamFolderRow): TeamFolder {
  return {
    id: row.id,
    name: row.name,
    sortOrder: row.sort_order,
    createdAt: row.created_at,
  };
}

async function ensureUniqueName(name: string, excludingId?: string) {
  const db = await getDatabase();
  const existing = await db
    .prepare(
      excludingId
        ? "SELECT id FROM team_folders WHERE lower(name) = lower(?) AND id <> ? LIMIT 1"
        : "SELECT id FROM team_folders WHERE lower(name) = lower(?) LIMIT 1",
    )
    .bind(...(excludingId ? [name, excludingId] : [name]))
    .first<{ id: string }>();
  if (existing) throw new DomainError("Ya existe una carpeta con ese nombre.", 409);
}

export async function listTeamFolders(): Promise<TeamFolder[]> {
  const db = await getDatabase();
  const result = await db
    .prepare("SELECT id, name, sort_order, created_at FROM team_folders ORDER BY sort_order ASC, name COLLATE NOCASE ASC")
    .all<TeamFolderRow>();
  return result.results.map(toFolder);
}

export async function reorderTeamFolders(folderIds: unknown): Promise<TeamFolder[]> {
  const orderedIds = normalizeFolderOrder(folderIds);
  const db = await getDatabase();
  const current = await db.prepare("SELECT id FROM team_folders").all<{ id: string }>();
  const currentIds = new Set(current.results.map((row) => row.id));
  if (orderedIds.length !== currentIds.size || orderedIds.some((id) => !currentIds.has(id))) {
    throw new DomainError("La lista de carpetas ya no está actualizada.", 409);
  }
  if (orderedIds.length) {
    await db.batch(
      orderedIds.map((id, sortOrder) =>
        db.prepare("UPDATE team_folders SET sort_order = ? WHERE id = ?").bind(sortOrder, id),
      ),
    );
  }
  return listTeamFolders();
}

export async function listTeamFolderAssignments(): Promise<Record<string, string | null>> {
  const db = await getDatabase();
  const result = await db
    .prepare("SELECT id, folder_id FROM teams")
    .all<TeamFolderAssignmentRow>();
  return Object.fromEntries(result.results.map((row) => [row.id, row.folder_id]));
}

export async function createTeamFolder(name: string): Promise<TeamFolder> {
  const cleanName = normalizeFolderName(name);
  await ensureUniqueName(cleanName);
  const db = await getDatabase();
  const id = crypto.randomUUID();
  const nextOrder = await db
    .prepare("SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM team_folders")
    .first<{ next_order: number }>();
  await db
    .prepare("INSERT INTO team_folders (id, name, sort_order) VALUES (?, ?, ?)")
    .bind(id, cleanName, nextOrder?.next_order ?? 0)
    .run();
  const row = await db
    .prepare("SELECT id, name, sort_order, created_at FROM team_folders WHERE id = ?")
    .bind(id)
    .first<TeamFolderRow>();
  if (!row) throw new DomainError("No pudimos crear la carpeta.", 500);
  return toFolder(row);
}

export async function renameTeamFolder(id: string, name: string): Promise<TeamFolder> {
  const cleanName = normalizeFolderName(name);
  const db = await getDatabase();
  const current = await db
    .prepare("SELECT id FROM team_folders WHERE id = ?")
    .bind(id)
    .first<{ id: string }>();
  if (!current) throw new DomainError("No encontramos esa carpeta.", 404);
  await ensureUniqueName(cleanName, id);
  await db
    .prepare("UPDATE team_folders SET name = ? WHERE id = ?")
    .bind(cleanName, id)
    .run();
  const row = await db
    .prepare("SELECT id, name, sort_order, created_at FROM team_folders WHERE id = ?")
    .bind(id)
    .first<TeamFolderRow>();
  if (!row) throw new DomainError("No encontramos esa carpeta.", 404);
  return toFolder(row);
}

export async function deleteTeamFolder(id: string) {
  const db = await getDatabase();
  const current = await db
    .prepare("SELECT id FROM team_folders WHERE id = ?")
    .bind(id)
    .first<{ id: string }>();
  if (!current) throw new DomainError("No encontramos esa carpeta.", 404);
  await db.batch([
    db.prepare("UPDATE teams SET folder_id = NULL WHERE folder_id = ?").bind(id),
    db.prepare("DELETE FROM team_folders WHERE id = ?").bind(id),
  ]);
}

export async function moveTeamToFolder(teamId: string, folderId: string | null) {
  const db = await getDatabase();
  const team = await db
    .prepare("SELECT id FROM teams WHERE id = ?")
    .bind(teamId)
    .first<{ id: string }>();
  if (!team) throw new DomainError("No encontramos ese equipo.", 404);

  if (folderId) {
    const folder = await db
      .prepare("SELECT id FROM team_folders WHERE id = ?")
      .bind(folderId)
      .first<{ id: string }>();
    if (!folder) throw new DomainError("No encontramos esa carpeta.", 404);
  }

  await db
    .prepare("UPDATE teams SET folder_id = ? WHERE id = ?")
    .bind(folderId, teamId)
    .run();
}
