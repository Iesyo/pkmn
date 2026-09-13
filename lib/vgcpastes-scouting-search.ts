import {
  buildVgcPastesScoutingResponse,
  normalizeVgcPastesPokemonFilters,
  type VgcPastesFormat,
  type VgcPastesScoutingResponse,
  type VgcPastesTeam,
} from "@/lib/vgcpastes-scouting";

const MEGA_FORM_SUFFIX = /-Mega(?:-[A-Za-z0-9]+)?$/i;

type VgcPastesScoutingBuildOptions = NonNullable<Parameters<typeof buildVgcPastesScoutingResponse>[2]>;

/**
 * VGCPastes names the in-battle Mega form in its roster columns, but for
 * team-core scouting that form is still the same team slot/species selected
 * at teambuilding time. Keep the raw source label for cards/Inspector while
 * collapsing Mega variants only in the search identity.
 */
export function getVgcPastesScoutingSpeciesIdentity(value: unknown) {
  if (typeof value !== "string") return "";
  return value.trim().replace(MEGA_FORM_SUFFIX, "");
}

export function normalizeVgcPastesScoutingSpeciesFilters(values: unknown): string[] {
  const candidates = Array.isArray(values) ? values : [values];
  return normalizeVgcPastesPokemonFilters(
    candidates.map(getVgcPastesScoutingSpeciesIdentity),
  );
}

export function buildVgcPastesScoutingSearchResponse(
  format: VgcPastesFormat,
  teams: VgcPastesTeam[],
  options: VgcPastesScoutingBuildOptions = {},
): VgcPastesScoutingResponse {
  const rawTeamsById = new Map(teams.map((team) => [team.id, team]));
  const searchTeams = teams.map((team) => ({
    ...team,
    pokemon: team.pokemon.map(getVgcPastesScoutingSpeciesIdentity),
  }));
  const pokemon = normalizeVgcPastesScoutingSpeciesFilters(options.pokemon ?? []);

  const response = buildVgcPastesScoutingResponse(format, searchTeams, {
    ...options,
    pokemon,
  });

  return {
    ...response,
    // Search/pagination use the normalized identity, but the cards and
    // Inspector retain the exact labels supplied by VGCPastes.
    teams: response.teams.map((team) => rawTeamsById.get(team.id) ?? team),
  };
}
