import { normalizeShowdownReplayDocument } from "@/lib/showdown-replay";
import { DomainError } from "./queries";
import { getDatabase } from "./raw";

interface ReplayArtifactRow {
  replay_artifact_json: string | null;
}

export async function getMatchReplayArtifact(matchId: string) {
  const id = matchId.trim();
  if (!id) throw new DomainError("La partida es obligatoria.");

  const db = await getDatabase();
  const row = await db
    .prepare("SELECT replay_artifact_json FROM matches WHERE id = ?")
    .bind(id)
    .first<ReplayArtifactRow>();

  if (!row) throw new DomainError("No encontramos esa partida.", 404);
  if (!row.replay_artifact_json) {
    throw new DomainError("Esta partida no tiene un replay reconstruido.", 404);
  }

  try {
    return normalizeShowdownReplayDocument(JSON.parse(row.replay_artifact_json));
  } catch {
    throw new DomainError("El replay guardado está dañado y no se puede abrir.", 500);
  }
}
