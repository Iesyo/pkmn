import { createTeamVersion } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    const payload = (await request.json()) as { paste?: string };
    const version = await createTeamVersion(id, payload.paste ?? "");
    return Response.json({ version }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
