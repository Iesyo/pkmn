import { createMatch, type CreateMatchInput } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as CreateMatchInput;
    const match = await createMatch(payload);
    return Response.json({ match }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
