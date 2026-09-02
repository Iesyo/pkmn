import type { TeamFolder } from "@/lib/types";

import { DomainError } from "./queries";
import { getDatabase } from "./raw";

interface TeamFolderRow {
  id: string;
  name: string;
  sort_order: number;
  created_at: string;
}

interface TeamOrganizationRow {
  id: string;
  folder_id: string | null;
  sort_order: number;
}

interface TeamFolderAssignmentRow {
  id: string;
  folder_id: string | null;
}

export interface TeamOrganization {
  folderId: string | null;
  sortOrder: number;
}

export type TeamDropPosition = "before" | "after";

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

function normalizeTeamDropPosition(position: unknown): TeamDropPosition {
  if (position !== "before" && position !== "after") {
    throw new DomainError("La posición del equipo no es válida.");
  }
  return position;
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

async function orderedTeamIds(
  db: D1Database,
  folderId: string | null,
  excludingId?: string,
): Promise<string[]> {
  const where = folderId ? "folder_id = ?" : "folder_id IS NULL";
  const exclusion = excludingId ? " AND id <> ?" : "";
  const statement = db.prepare(
    `SELECT id FROM teams WHERE ${where}${exclusion} ORDER BY sort_order ASC, updated_at DESC, name COLLATE NOCASE ASC, id ASC`,
  );
  const params = [
    ...(folderId ? [folderId] : []),
    ...(excludingId ? [excludingId] : []),
  ];
  const result = await statement.bind(...params).all<{ id: string }>();
  return result.results.map((row) => row.id);
}

function organizationStatements(
  db: D1Database,
  ids: string[],
  folderId: string | null,
  movedTeamId?: string,
) {
  return ids.map((id, sortOrder) =>
    id === movedTeamId
      ? db
          .prepare("UPDATE teams SET folder_id = ?, sort_order = ? WHERE id = ?")
          .bind(folderId, sortOrder, id)
      : db.prepare("UPDATE teams SET sort_order = ? WHERE id = ?").bind(sortOrder, id),
  );
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

export async function listTeamOrganization(): Promise<Record<string, TeamOrganization>> {
  const db = await getDatabase();
  const result = await db
    .prepare("SELECT id, folder_id, sort_order FROM teams")
    .all<TeamOrganizationRow>();
  return Object.fromEntries(
    result.results.map((row) => [
      row.id,
      { folderId: row.folder_id, sortOrder: row.sort_order },
    ]),
  );
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

  const [unfiledIds, movedIds] = await Promise.all([
    orderedTeamIds(db, null),
    orderedTeamIds(db, id),
  ]);
  const combinedIds = [...unfiledIds, ...movedIds];
  await db.batch([
    ...combinedIds.map((teamId, sortOrder) =>
      movedIds.includes(teamId)
        ? db
            .prepare("UPDATE teams SET folder_id = NULL, sort_order = ? WHERE id = ?")
            .bind(sortOrder, teamId)
        : db.prepare("UPDATE teams SET sort_order = ? WHERE id = ?").bind(sortOrder, teamId),
    ),
    db.prepare("DELETE FROM team_folders WHERE id = ?").bind(id),
  ]);
}

export async function moveTeamToFolder(teamId: string, folderId: string | null) {
  const db = await getDatabase();
  const team = await db
    .prepare("SELECT id, folder_id FROM teams WHERE id = ?")
    .bind(teamId)
    .first<TeamFolderAssignmentRow>();
  if (!team) throw new DomainError("No encontramos ese equipo.", 404);

  if (folderId) {
    const folder = await db
      .prepare("SELECT id FROM team_folders WHERE id = ?")
      .bind(folderId)
      .first<{ id: string }>();
    if (!folder) throw new DomainError("No encontramos esa carpeta.", 404);
  }

  const sourceIds = await orderedTeamIds(db, team.folder_id, teamId);
  const targetIds = team.folder_id === folderId
    ? sourceIds
    : await orderedTeamIds(db, folderId, teamId);
  const nextTargetIds = [...targetIds, teamId];

  await db.batch([
    ...(team.folder_id === folderId ? [] : organizationStatements(db, sourceIds, team.folder_id)),
    ...organizationStatements(db, nextTargetIds, folderId, teamId),
  ]);

  return listTeamOrganization();
}

export async function reorderTeamByTarget(
  teamId: string,
  targetTeamId: string,
  rawPosition: unknown,
) {
  const position = normalizeTeamDropPosition(rawPosition);
  if (!teamId || !targetTeamId || teamId === targetTeamId) {
    return listTeamOrganization();
  }

  const db = await getDatabase();
  const [team, target] = await Promise.all([
    db
      .prepare("SELECT id, folder_id FROM teams WHERE id = ?")
      .bind(teamId)
      .first<TeamFolderAssignmentRow>(),
    db
      .prepare("SELECT id, folder_id FROM teams WHERE id = ?")
      .bind(targetTeamId)
      .first<TeamFolderAssignmentRow>(),
  ]);
  if (!team) throw new DomainError("No encontramos ese equipo.", 404);
  if (!target) throw new DomainError("No encontramos el equipo de destino.", 404);

  const sourceIds = await orderedTeamIds(db, team.folder_id, teamId);
  const targetIds = team.folder_id === target.folder_id
    ? sourceIds
    : await orderedTeamIds(db, target.folder_id, teamId);
  const targetIndex = targetIds.indexOf(targetTeamId);
  if (targetIndex < 0) {
    throw new DomainError("El orden de equipos ya no está actualizado.", 409);
  }
  const insertAt = targetIndex + (position === "after" ? 1 : 0);
  const nextTargetIds = [
    ...targetIds.slice(0, insertAt),
    teamId,
    ...targetIds.slice(insertAt),
  ];

  await db.batch([
    ...(team.folder_id === target.folder_id ? [] : organizationStatements(db, sourceIds, team.folder_id)),
    ...organizationStatements(db, nextTargetIds, target.folder_id, teamId),
  ]);

  return listTeamOrganization();
}
