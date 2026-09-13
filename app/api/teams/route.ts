import { enrichTeamsWithOpponentPicks } from "@/db/opponent-picks";
import { createTeam, listTeamGroups } from "@/db/queries";
import { listOwnedTeamIds } from "@/db/scouting-pastes";
import { listTeamFolders, listTeamOrganization, moveTeamToFolder } from "@/db/team-folders";
import { apiError } from "@/lib/http";
import type { PokemonSet } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [rawTeams, ownedIds, folders, organization] = await Promise.all([
      listTeamGroups(),
      listOwnedTeamIds(),
      listTeamFolders(),
      listTeamOrganization(),
    ]);
    const teams = await enrichTeamsWithOpponentPicks(rawTeams.filter((team) => ownedIds.has(team.id)));
    return Response.json({
      teams: teams.map((team) => ({
        ...team,
        ...(organization[team.id] ?? { folderId: null, sortOrder: 0 }),
      })),
      folders,
    });
  } catch (error) {
    return apiError(error);
  }
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { name?: string; paste?: string; format?: string; mechanics?: string[]; builderSets?: Array<Partial<Pick<PokemonSet, "types" | "moves" | "mechanics">>> };
    const team = await createTeam(payload.name ?? "", payload.paste ?? "", payload.format, payload.mechanics, payload.builderSets);
    const organization = await moveTeamToFolder(team.id, null);
    return Response.json({
      team: {
        ...team,
        ...(organization[team.id] ?? { folderId: null, sortOrder: 0 }),
      },
    }, { status: 201 });
  } catch (error) {
    return apiError(error);
  }
}
