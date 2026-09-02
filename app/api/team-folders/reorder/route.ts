import { reorderTeamFolders } from "@/db/team-folders";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function PATCH(request: Request) {
  try {
    const payload = (await request.json()) as { folderIds?: unknown };
    const folders = await reorderTeamFolders(payload.folderIds);
    return Response.json({ folders });
  } catch (error) {
    return apiError(error);
  }
}
