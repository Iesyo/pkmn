import {
  getScoutingAnalysis,
  listActiveScoutingAnalyses,
  runScoutingAnalysisStep,
  startScoutingAnalysis,
} from "@/db/queries";
import { apiError } from "@/lib/http";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const matchId = new URL(request.url).searchParams.get("matchId")?.trim();
    if (!matchId) return Response.json({ analyses: await listActiveScoutingAnalyses() });
    return Response.json({ analysis: await getScoutingAnalysis(matchId) });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { matchId?: string; action?: "start" | "step" };
    const matchId = payload.matchId?.trim();
    if (!matchId) return Response.json({ error: "Falta la partida que quieres analizar." }, { status: 400 });
    const analysis = payload.action === "step"
      ? await runScoutingAnalysisStep(matchId)
      : await startScoutingAnalysis(matchId);
    return Response.json({ analysis }, { status: payload.action === "step" ? 200 : 201 });
  } catch (error) {
    return apiError(error);
  }
}
