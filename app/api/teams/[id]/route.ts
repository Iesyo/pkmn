import { moveTeamToFolder } from "@/db/team-folders";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    const payload = (await request.json()) as { folderId?: string | null };
    if (!("folderId" in payload)) {
      return Response.json({ error: "Indica la carpeta destino." }, { status: 400 });
    }
    const folderId = typeof payload.folderId === "string" && payload.folderId.trim()
      ? payload.folderId.trim()
      : null;
    await moveTeamToFolder(id, folderId);
    return Response.json({ teamId: id, folderId });
  } catch (error) {
    return apiError(error);
  }
}
