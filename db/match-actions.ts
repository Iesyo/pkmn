import { DomainError } from "./queries";
import { getDatabase } from "./raw";

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
