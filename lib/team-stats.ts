import { getConditionalEffect } from "./pokemon-data";
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
    return {
      ...set,
      moves: set.moves.map((move) => ({
        ...move,
        usage: selected.length ? Math.round(1000 / set.moves.length) / 10 : 0,
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
    const species = match.lead.slice(0, 2);
    const key = species.join("|");
    const current = grouped.get(key) ?? { species, games: 0, wins: 0 };
    current.games += 1;
    if (match.result === "win") current.wins += 1;
    grouped.set(key, current);
  }

  return [...grouped.values()].sort(
    (a, b) => b.games - a.games || b.wins - a.wins,
  );
}

export function calculateOpponentPokemonStats(
  matches: MatchRecord[],
): OpponentPokemonStat[] {
  const grouped = new Map<string, { species: string; games: number; wins: number }>();

  for (const match of matches) {
    const seen = new Set(
      match.opponentSelected.map((species) => species.trim()).filter(Boolean),
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
