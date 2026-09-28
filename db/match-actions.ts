import { DomainError } from "./queries";
import { MAX_MATCH_NOTES_LENGTH } from "@/lib/match-history";
import { getDatabase } from "./raw";

export async function updateMatchNotes(matchId: string, notes: unknown) {
  const id = matchId.trim();
  if (!id) throw new DomainError("La partida es obligatoria.");
  if (typeof notes !== "string" || notes.length > MAX_MATCH_NOTES_LENGTH) {
    throw new DomainError(`Las notas deben ser texto de hasta ${MAX_MATCH_NOTES_LENGTH} caracteres.`);
  }

  const db = await getDatabase();
  const match = await db.prepare("SELECT id FROM matches WHERE id = ?").bind(id).first<{ id: string }>();
  if (!match) throw new DomainError("No encontramos esa partida.", 404);

  const value = notes.trim();
  await db.prepare("UPDATE matches SET notes = ? WHERE id = ?").bind(value, id).run();
  return value;
}

export async function deleteMatch(matchId: string) {
  const id = matchId.trim();
  if (!id) throw new DomainError("La partida es obligatoria.");

  const db = await getDatabase();
  const match = await db
    .prepare("SELECT id FROM matches WHERE id = ?")
    .bind(id)
    .first<{ id: string }>();

  if (!match) throw new DomainError("No encontramos esa partida.", 404);

  await db.batch([
    db.prepare("DELETE FROM scouting_analyses WHERE match_id = ?").bind(id),
    db.prepare("DELETE FROM matches WHERE id = ?").bind(id),
  ]);
}
