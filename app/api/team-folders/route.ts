import { createTeamFolder, listTeamFolders } from "@/db/team-folders";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return Response.json({ folders: await listTeamFolders() });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { name?: string };
    const folder = await createTeamFolder(payload.name ?? "");
    return Response.json({ folder }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
