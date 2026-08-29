import { deleteMatch } from "@/db/match-actions";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await context.params;
    await deleteMatch(id);
    return new Response(null, { status: 204 });
  } catch (error) {
    return apiError(error);
  }
}
