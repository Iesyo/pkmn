import { deleteScoutingPaste, getScoutingPaste } from "@/db/scouting-pastes";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    return Response.json({ item: await getScoutingPaste(id) }, {
      headers: { "cache-control": "no-store" },
    });
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
    return Response.json(await deleteScoutingPaste(id), {
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    return apiError(error);
  }
}
