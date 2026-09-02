import { reorderTeamByTarget } from "@/db/team-folders";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function PATCH(request: Request) {
  try {
    const payload = (await request.json()) as {
      teamId?: string;
      targetTeamId?: string;
      position?: "before" | "after";
    };
    const teamId = payload.teamId?.trim() ?? "";
    const targetTeamId = payload.targetTeamId?.trim() ?? "";
    if (!teamId || !targetTeamId) {
      return Response.json({ error: "Indica los equipos a reordenar." }, { status: 400 });
    }
    const organization = await reorderTeamByTarget(teamId, targetTeamId, payload.position);
    return Response.json({ organization });
  } catch (error) {
    return apiError(error);
  }
}
