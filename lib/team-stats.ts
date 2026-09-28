import { getConditionalEffect, toId } from "./pokemon-data";
import { effectiveness, POKEMON_TYPES } from "./type-chart";
import type {
  LeadStat,
  PokemonSet,
  TypeAnalysisResult,
  MatchRecord,
} from "./types";

export interface OpponentPokemonStat {
  species: string;
  games: number;
  wins: number;
  winRate: number;
  attendanceRate: number;
}

export function winRate(wins: number, games: number) {
  return games === 0 ? 0 : Math.round((wins / games) * 1000) / 10;
}

export function decoratePokemonPerformance(
  pokemon: PokemonSet[],
  matches: MatchRecord[],
) {
  return pokemon.map((set) => {
    const selected = matches.filter((match) => match.selected.includes(set.species));
    const led = matches.filter((match) => match.lead.includes(set.species));
    const trackedMoves = selected.flatMap((match) => {
      if (!match.movesUsed) return [];
      const entry = Object.entries(match.movesUsed).find(([species]) => toId(species) === toId(set.species));
      return entry ? [entry[1]] : [];
    });
    return {
      ...set,
      moves: set.moves.map((move) => ({
        ...move,
        usage: trackedMoves.length
          ? winRate(
              trackedMoves.filter((moves) => moves.some((usedMove) => toId(usedMove) === toId(move.name))).length,
              trackedMoves.length,
            )
          : null,
      })),
      performance: {
        games: selected.length,
        wins: selected.filter((match) => match.result === "win").length,
        leadGames: led.length,
        leadWins: led.filter((match) => match.result === "win").length,
        selectionRate: matches.length
          ? Math.round((selected.length / matches.length) * 1000) / 10
          : 0,
      },
    };
  });
}

export function calculateLeads(matches: MatchRecord[]): LeadStat[] {
  const grouped = new Map<string, LeadStat>();
  for (const match of matches.filter((entry) => entry.lead.length >= 2)) {
    const species = match.lead.slice(0, 2).sort((a, b) => toId(a).localeCompare(toId(b)));
    const key = species.map(toId).join("|");
    const current = grouped.get(key) ?? { species, games: 0, wins: 0 };
    current.games += 1;
    if (match.result === "win") current.wins += 1;
    grouped.set(key, current);
  }

  return [...grouped.values()].sort(
    (a, b) => b.games - a.games || b.wins - a.wins || a.species.join("|").localeCompare(b.species.join("|")),
  );
}

export const MIN_BEST_LEAD_GAMES = 3;

// Lower bound of the 95% Wilson interval: an isolated win has much less
// evidence than a sustained record, while the displayed win rate stays raw.
function leadConfidenceScore({ wins, games }: LeadStat) {
  const z = 1.96;
  const zSquared = z * z;
  const rate = wins / games;
  return (rate + zSquared / (2 * games)
    - z * Math.sqrt(rate * (1 - rate) / games + zSquared / (4 * games * games)))
    / (1 + zSquared / games);
}

export function rankBestLeads(leads: LeadStat[]): LeadStat[] {
  return leads.filter((lead) => lead.games >= MIN_BEST_LEAD_GAMES).sort((a, b) =>
    leadConfidenceScore(b) - leadConfidenceScore(a)
    || b.wins / b.games - a.wins / a.games
    || b.games - a.games
    || a.species.join("|").localeCompare(b.species.join("|")),
  );
}

export function rankOpponentMatchups(
  stats: OpponentPokemonStat[],
  outcome: "best" | "worst",
): OpponentPokemonStat[] {
  const count = (entry: OpponentPokemonStat) => outcome === "best"
    ? entry.wins
    : entry.games - entry.wins;
  const oppositeCount = (entry: OpponentPokemonStat) => outcome === "best"
    ? entry.games - entry.wins
    : entry.wins;

  return stats.filter((entry) => count(entry) > oppositeCount(entry)).sort((a, b) =>
    count(b) - count(a)
    || b.games - a.games
    || a.species.localeCompare(b.species),
  );
}

export function calculateOpponentPokemonStats(
  matches: MatchRecord[],
  source: "opponentSelected" | "opponentPicks" = "opponentSelected",
): OpponentPokemonStat[] {
  const grouped = new Map<string, { species: string; games: number; wins: number }>();

  for (const match of matches) {
    const seen = new Set(
      (match[source] ?? []).map((species) => species.trim()).filter(Boolean),
    );

    for (const species of seen) {
      const key = species.toLocaleLowerCase();
      const current = grouped.get(key) ?? { species, games: 0, wins: 0 };
      current.games += 1;
      if (match.result === "win") current.wins += 1;
      grouped.set(key, current);
    }
  }

  return [...grouped.values()].map((entry) => ({
    ...entry,
    winRate: winRate(entry.wins, entry.games),
    attendanceRate: winRate(entry.games, matches.length),
  }));
}

export function analyzeTypes(
  pokemon: PokemonSet[],
  useTera = false,
): TypeAnalysisResult {
  const coverage = POKEMON_TYPES.map((type) => ({
    type,
    count: pokemon.filter((set) =>
      set.moves.some(
        (move) =>
          move.damaging &&
          move.type &&
          effectiveness(move.type, [type]) > 1,
      ),
    ).length,
  }));

  const defense = POKEMON_TYPES.map((type) => {
    const multipliers = pokemon.map((set) =>
      effectiveness(type, useTera && set.teraType ? [set.teraType] : set.types),
    );
    return {
      type,
      count: multipliers.filter((value) => value > 1).length,
      resistances: multipliers.filter((value) => value > 0 && value < 1).length,
      immunities: multipliers.filter((value) => value === 0).length,
    };
  });

  return {
    coverage,
    defense,
    resistances: defense
      .filter((entry) => entry.resistances > 0)
      .map((entry) => ({ type: entry.type, count: entry.resistances })),
    immunities: defense
      .filter((entry) => entry.immunities > 0)
      .map((entry) => ({ type: entry.type, count: entry.immunities })),
    blindSpots: coverage.filter((entry) => entry.count === 0).map((entry) => entry.type),
    conditionals: [...new Set(pokemon.flatMap((set) => getConditionalEffect(set.ability, set.item)))],
  };
}
