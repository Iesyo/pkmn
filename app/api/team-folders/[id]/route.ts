import { deleteTeamFolder, renameTeamFolder } from "@/db/team-folders";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    const payload = (await request.json()) as { name?: string };
    const folder = await renameTeamFolder(id, payload.name ?? "");
    return Response.json({ folder });
  } catch (error) {
    return apiError(error);
  }
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    await deleteTeamFolder(id);
    return new Response(null, { status: 204 });
  } catch (error) {
    return apiError(error);
  }
}
