import { createTeamVersion } from "@/db/queries";
import { apiError } from "@/lib/http";
import type { PokemonSet } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    const payload = (await request.json()) as { paste?: string; format?: string; mechanics?: string[]; builderSets?: Array<Partial<Pick<PokemonSet, "types" | "moves" | "mechanics">>> };
    const version = await createTeamVersion(id, payload.paste ?? "", payload.format, payload.mechanics, payload.builderSets);
    return Response.json({ version }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
