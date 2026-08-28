import { getShowdownNames, saveShowdownNames } from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return Response.json({ showdownNames: await getShowdownNames() });
  } catch (error) {
    return apiError(error);
  }
}

export async function PUT(request: Request) {
  try {
    const payload = (await request.json()) as { showdownNames?: string[] };
    const showdownNames = await saveShowdownNames(payload.showdownNames ?? []);
    return Response.json({ showdownNames });
  } catch (error) {
    return apiError(error);
  }
}
