import { createTeam, listTeamGroups } from "@/db/queries";
import { apiError } from "@/lib/http";
import type { PokemonSet } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return Response.json({ teams: await listTeamGroups() });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { name?: string; paste?: string; format?: string; mechanics?: string[]; builderSets?: Array<Partial<Pick<PokemonSet, "types" | "moves" | "mechanics">>> };
    const team = await createTeam(payload.name ?? "", payload.paste ?? "", payload.format, payload.mechanics, payload.builderSets);
    return Response.json({ team }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
