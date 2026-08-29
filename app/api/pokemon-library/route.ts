import { listPokemonLibrary } from "@/db/pokemon-library";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const format = new URL(request.url).searchParams.get("format")?.trim() || undefined;
    return Response.json({ pokemon: await listPokemonLibrary(format) });
  } catch (error) {
    return apiError(error);
  }
}
